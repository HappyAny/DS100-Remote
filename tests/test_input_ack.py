"""Acknowledge native key reads, rather than only receiving a remote mask."""

import json
import struct
import pytest
from ds100.protocol import Client, INFO, make_response, parse_response
from test_input_sequencer import NativeInputModel, native_clock
from test_arm_input_ready import ReadyArm, initialized, submit, OUT
from test_arm_candidate import unicorn_windows_exception_handler


class AckArm(ReadyArm):
    manifest_name='input-ack-manifest.json'
    image_name='v48-input-ack-decoded.bin'


def test_actual_arm_ack_distinguishes_received_and_reported_key_edges():
    d=initialized(AckArm)
    assert submit(d,4)[0] == 4
    d.call(d.symbols['ds100_v48_read_keys'],OUT)
    assert submit(d,4)[0] == 0x404
    assert submit(d,0)[0] == 0x400
    d.call(d.symbols['ds100_v48_read_keys'],OUT)
    assert submit(d,0)[0] == 0


def test_actual_arm_ack_preserves_physical_ownership_on_remote_release():
    d=initialized(AckArm)
    d.mu.mem_write(0x2002CED8+5,b'\x01')
    d.call(d.symbols['ds100_v48_read_keys'],OUT)
    assert submit(d,0)[0] == 0x440


class DelayedKeyModel(NativeInputModel):
    def __init__(self,clock):
        super().__init__(clock)
        self.edges=[]
        self.delay=.22
    def tick(self):
        if self.clock[0]-self.last_tick < self.delay:
            return
        old=self.reported
        super().tick()
        if old!=self.reported:
            self.edges.append((self.clock[0],self.reported))
    def exchange(self,cdb):
        reply=super().exchange(cdb)
        cmd,seq=cdb[1],struct.unpack_from('<I',cdb,4)[0]
        payload=parse_response(reply,cmd,seq)
        if cmd==INFO:
            info=json.loads(payload)
            info['input']['key_ack']='reported-v1'
            payload=json.dumps(info).encode()
        else:
            keys,lease,a,b=struct.unpack('<HHhh',payload)
            payload=struct.pack('<HHhh',keys | (self.reported<<8),lease,a,b)
        return make_response(cmd,seq,payload)


def test_press_is_not_replaced_by_release_before_native_adoption(native_clock):
    native=DelayedKeyModel(native_clock)
    client=Client(native);client.info()
    client.queue_input(1);client.queue_input(0);client.flush_inputs()
    assert [keys for _,keys in native.edges]==[1,0]
    assert native.remote==native.reported==0
    assert native.edges[0][0]>=.22


def test_E_timer_guard_starts_after_confirmed_native_press(native_clock):
    native=DelayedKeyModel(native_clock)
    client=Client(native);client.info()
    client.queue_input(8,1);client.queue_input(0);client.flush_inputs()
    press=next(t for t,keys in native.edges if keys==8)
    release=next(t for t,keys,a,b in native.log if keys==0)
    assert release-press>=.5
    assert native.offset_s==75e-6 and native.offset_v==.15
    assert native.remote==native.reported==0


def test_native_auto_length_wait_does_not_drop_the_following_release(native_clock):
    native=DelayedKeyModel(native_clock);native.delay=5.3
    client=Client(native);client.info()
    client.queue_input(2);client.queue_input(0);client.flush_inputs()
    assert [keys for _,keys in native.edges]==[2,0]
    assert native.remote==native.reported==0
