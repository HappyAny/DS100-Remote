"""Experimental DS100 remote protocol. The stock firmware does not implement it."""

from dataclasses import dataclass
import json
import struct
import zlib
import time
from collections import deque
from threading import Lock

MAGIC = b"D1RM"
VERSION = 1
HEADER = struct.Struct("<4sBBHIII")
CDB = struct.Struct("<BBBBIHHHH")
SCREEN = struct.Struct("<IHHHHBBH")
WAVE = struct.Struct("<IIddffff")
MAX_PAYLOAD = 16384
INFO, SCREEN_READ, WAVE_READ, SET_RUN, SCREEN_STREAM = 1, 2, 3, 4, 5
DRAW_PROFILE = 6
LINK_PROBE = 7
INPUT_STATE = 8
DRAW_STREAM = 9
RESPONSE, ERROR = 1, 2
RAW565, RLE565 = 0, 1
PALETTE_RLE565 = 2


class ProtocolError(ValueError):
    pass


class MenuTransition(ProtocolError):
    """Finish a native menu transition before starting another drawing epoch."""


def make_cdb(command, sequence, args=(0, 0, 0, 0)):
    if command not in (INFO, SCREEN_READ, WAVE_READ, SET_RUN, SCREEN_STREAM, DRAW_PROFILE, LINK_PROBE, INPUT_STATE, DRAW_STREAM):
        raise ProtocolError("The command is not supported.")
    if len(args) != 4 or any(not 0 <= value <= 65535 for value in args):
        raise ProtocolError("Each command argument must be an unsigned 16-bit integer.")
    return CDB.pack(0xC0, command, VERSION, 0, sequence, *args)


def make_response(command, sequence, payload, error=False):
    if len(payload) > MAX_PAYLOAD:
        raise ProtocolError("The response payload exceeds the size limit.")
    flags = RESPONSE | (ERROR if error else 0)
    return HEADER.pack(MAGIC, VERSION, command, flags, sequence, len(payload), zlib.crc32(payload)) + payload


def parse_response(data, command, sequence):
    if len(data) < HEADER.size:
        raise ProtocolError("The response header is incomplete.")
    magic, version, reply, flags, seq, length, crc = HEADER.unpack_from(data)
    if magic != MAGIC or version != VERSION:
        raise ProtocolError("The device does not implement remote protocol version 1.")
    if reply != command or seq != sequence or not flags & RESPONSE or flags & ~(RESPONSE | ERROR):
        raise ProtocolError("The response does not match the request.")
    if length > MAX_PAYLOAD or len(data) < HEADER.size + length:
        raise ProtocolError("The response length is invalid.")
    payload = data[HEADER.size:HEADER.size + length]
    if zlib.crc32(payload) != crc:
        raise ProtocolError("The response CRC does not match the payload.")
    if flags & ERROR:
        raise ProtocolError(payload.decode("utf-8", errors="replace"))
    return payload


@dataclass(frozen=True)
class ScreenTile:
    frame_id: int
    x: int
    y: int
    width: int
    height: int
    pixels: bytes


def parse_tile(payload, screen_width=480, screen_height=320):
    if len(payload) < SCREEN.size:
        raise ProtocolError("The screen tile header is incomplete.")
    frame, x, y, width, height, encoding, reserved, flags = SCREEN.unpack_from(payload)
    count = width * height
    if not width or not height or x + width > screen_width or y + height > screen_height or reserved or flags:
        raise ProtocolError("The screen tile rectangle is invalid.")
    body = payload[SCREEN.size:]
    if encoding == RAW565:
        if len(body) != count * 2:
            raise ProtocolError("The RGB565 tile length is invalid.")
        pixels = body
    elif encoding == RLE565:
        if len(body) % 4:
            raise ProtocolError("The RLE tile contains an incomplete run.")
        result = bytearray()
        used = 0
        for offset in range(0, len(body), 4):
            length, value = struct.unpack_from("<HH", body, offset)
            if not length or used + length > count:
                raise ProtocolError("The RLE run exceeds the tile size.")
            result.extend(struct.pack("<H", value) * length)
            used += length
        if used != count:
            raise ProtocolError("The RLE tile does not contain all pixels.")
        pixels = bytes(result)
    elif encoding == PALETTE_RLE565:
        if not body or not 1 <= body[0] <= 16 or len(body) < 1 + body[0] * 2:
            raise ProtocolError('The pixel palette is invalid.')
        palette_count = body[0]
        offset = 1 + palette_count * 2
        used = 0
        result = bytearray()
        while offset < len(body):
            token = body[offset]
            offset += 1
            index, length = token >> 4, (token & 15) + 1
            if index >= palette_count:
                raise ProtocolError('The palette index is invalid.')
            if token & 15 == 15:
                if offset >= len(body):
                    raise ProtocolError('The palette run is incomplete.')
                extra = body[offset]
                offset += 1
                if extra & 128:
                    if offset >= len(body) or not 1 <= body[offset] < 128:
                        raise ProtocolError('The palette run length is invalid.')
                    extra = (extra & 127) | (body[offset] << 7)
                    offset += 1
                length = 16 + extra
            if used + length > count:
                raise ProtocolError('The palette run exceeds the tile size.')
            result.extend(body[1 + index * 2:3 + index * 2] * length)
            used += length
        if used != count:
            raise ProtocolError('The palette tile does not contain all pixels.')
        pixels = bytes(result)
    else:
        raise ProtocolError("The screen tile encoding is not supported.")
    return ScreenTile(frame, x, y, width, height, pixels)


def parse_wave(payload):
    import numpy as np
    if len(payload) < WAVE.size:
        raise ProtocolError("The waveform header is incomplete.")
    count, mask, dt, t0, scale_a, zero_a, scale_b, zero_b = WAVE.unpack_from(payload)
    channels = [index for index in (0, 1) if mask & (1 << index)]
    if not 1 <= count <= 4096 or mask not in (1, 2, 3) or not np.isfinite([dt, t0, scale_a, zero_a, scale_b, zero_b]).all() or dt <= 0:
        raise ProtocolError("The waveform metadata is invalid.")
    if len(payload) != WAVE.size + count * len(channels):
        raise ProtocolError("The waveform sample length is invalid.")
    raw = np.frombuffer(payload, dtype=np.uint8, offset=WAVE.size).reshape(len(channels), count)
    scales, zeros = (scale_a, scale_b), (zero_a, zero_b)
    volts = {index: raw[row].astype(float) * scales[index] + zeros[index] for row, index in enumerate(channels)}
    return t0 + np.arange(count) * dt, volts


class Client:
    def __init__(self, transport):
        self.transport = transport
        self.sequence = 0
        self.stream_rows = None
        self.input_supported = False
        self.draw_config = None
        self.draw_session = None
        self.screen_mode = 'legacy'
        self.on_draw_sync = None
        self._inputs = deque()
        self._input_lock = Lock()
        self._menu_transition = False
        self._menu_transition_until = 0.
        self._input_keys = 0
        self.menu_resets = 0
        self.modifier_gesture = False
        self.fast_modifiers = False
        self.on_menu_preview = None
        self._menu_scan = None
        self._menu_scan_tiles = None
        self._menu_readouts_dirty = False
        self._m_wheel_used = False
        self._modifier_ready_at = 0.
        self._last_input_ack = 0.
        self.on_input_frame = None
        self._key_ready_at = 0.
        self._e_pressed_at = 0.
        self._e_wheel_used = False
        self._input_readback = False
        self.reported_key_ack = False
        self._reported_keys = self._physical_keys = 0
        self._e_press_pending = False

    def request(self, command, args=(0, 0, 0, 0)):
        if command != INPUT_STATE:
            self.flush_inputs()
            self._renew_input_lease()
        if command == DRAW_STREAM and args[0] in (1, 2) and self._menu_transition:
            raise MenuTransition('A native menu transition requires a fresh drawing baseline.')
        if command == DRAW_STREAM and args[0] == 2 and self.draw_session is not None and self.draw_session.replay is None:
            raise MenuTransition('The drawing stream requires a new baseline.')
        return self._request_raw(command, args)

    def _request_raw(self, command, args=(0, 0, 0, 0)):
        self.sequence = (self.sequence + 1) & 0xFFFFFFFF
        packet = self.transport.exchange(make_cdb(command, self.sequence, args))
        return parse_response(packet, command, self.sequence)

    def info(self):
        try:
            info = json.loads(self.request(INFO))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ProtocolError("The device information is not valid UTF-8 JSON.") from error
        if not isinstance(info, dict) or info.get("protocol") != VERSION:
            raise ProtocolError("The device information has no supported protocol version.")
        if info.get("screen_width") != 480 or info.get("screen_height") != 320:
            raise ProtocolError("The device screen dimensions are not supported.")
        self.stream_rows = None
        self.draw_config = None
        self.draw_session = None
        self.screen_mode = 'legacy'
        self._menu_transition = False
        self._input_keys = 0
        draw = info.get('draw_stream')
        if draw is not None:
            if (not isinstance(draw, dict) or draw.get('command') != DRAW_STREAM or
                    draw.get('packet_bytes') != 4096 or draw.get('format') != 'DRW1' or
                    draw.get('queue_bytes') != 16384):
                raise ProtocolError('The drawing stream configuration is invalid.')
            self.draw_config = draw
            self.screen_mode = 'draw-stream'
        stream = info.get("screen_stream")
        if stream is not None:
            if (not isinstance(stream, dict) or stream.get("command") != SCREEN_STREAM or
                    type(stream.get("max_rows")) is not int or not 1 <= stream["max_rows"] <= 16 or
                    stream.get("encoding") != "rle565"):
                raise ProtocolError("The fast screen configuration is invalid.")
            self.stream_rows = stream["max_rows"]
        inputs = info.get('input')
        self.input_supported = False
        self.reported_key_ack = False
        if inputs is not None:
            if (not isinstance(inputs, dict) or inputs.get('command') != INPUT_STATE or
                    inputs.get('keys') != ['P', 'A', 'M', 'E'] or inputs.get('wheels') != 2 or
                    inputs.get('lease_ms') != 600):
                raise ProtocolError('The device input configuration is invalid.')
            self.input_supported = 'input' in info.get('capabilities', [])
            if inputs.get('key_ack') not in (None, 'reported-v1'):
                raise ProtocolError('The key acknowledgement format is not supported.')
            self.reported_key_ack = inputs.get('key_ack') == 'reported-v1'
        self.fast_modifiers = info.get('build') in ('background-r9-bounded-follow', 'background-r10-input-ready', 'background-r11-input-ack') or info.get('source') == 'simulator'
        return info

    def queue_input(self, keys, wheel1=0, wheel2=0):
        if not self.input_supported:
            raise ProtocolError('The connected firmware has no key and wheel input service.')
        if type(keys) is not int or not 0 <= keys <= 15 or any(type(value) is not int or not -127 <= value <= 127 for value in (wheel1, wheel2)):
            raise ProtocolError('The key mask or wheel delta is invalid.')
        with self._input_lock:
            if len(self._inputs) >= 64:
                raise ProtocolError('The pending input queue is full.')
            self._inputs.append((keys, wheel1, wheel2))

    def has_pending_input(self):
        with self._input_lock:
            return bool(self._inputs)

    def _send_input(self, keys, wheel1=0, wheel2=0):
        payload = self._request_raw(INPUT_STATE, (keys, wheel1 & 65535, wheel2 & 65535, 0))
        if len(payload) != 8:
            raise ProtocolError('The input acknowledgement length is invalid.')
        mask, lease, pending1, pending2 = struct.unpack('<HHhh', payload)
        accepted = mask & 15 if self.reported_key_ack else mask
        if accepted != keys or lease != 600 or self.reported_key_ack and mask & 0xf000:
            raise ProtocolError('The device did not acknowledge the requested input state.')
        if self.reported_key_ack:
            self._physical_keys = (mask >> 4) & 15
            self._reported_keys = (mask >> 8) & 15
        old = self._input_keys
        self._input_keys = keys
        self._last_input_ack = time.monotonic()
        if keys != old:
            self._key_ready_at = self._last_input_ack + .08
        if keys & 12 and keys != old:
            self._modifier_ready_at = self._last_input_ack + .08
        if keys & 4 and not old & 4:
            self._m_wheel_used = False
        if keys & 8 and not old & 8:
            self._e_pressed_at = self._last_input_ack
            self._e_wheel_used = False
            self._e_press_pending = True
        return pending1, pending2

    def _wait_native_key_edge(self):
        if not self.reported_key_ack:
            return
        deadline = time.monotonic()+12.
        while self._reported_keys != (self._input_keys | self._physical_keys):
            if time.monotonic() >= deadline:
                raise ProtocolError('The native key controller did not read the requested edge.')
            self._input_pause(.001)
            self._send_input(self._input_keys)
        if self._e_press_pending and self._input_keys & 8:
            self._e_pressed_at = time.monotonic()
            self._e_press_pending = False

    def _renew_input_lease(self):
        if self._input_keys and time.monotonic() - self._last_input_ack >= .1:
            self._send_input(self._input_keys)

    def _input_pause(self, duration):
        """Keep pixels and leases moving while the native GUI adopts input."""
        remaining = duration
        while remaining > .000001:
            before = time.monotonic()
            self._renew_input_lease()
            session = self.draw_session
            occupied = False
            if (session is not None and session.replay is not None and session.replay.ready and
                    self.screen_mode == 'draw-stream'):
                pixels, occupied = session.poll_active(raw=True)
                if pixels is not None and self.on_input_frame is not None:
                    self.on_input_frame(pixels, time.monotonic() - before)
            spent = time.monotonic() - before
            streaming = session is not None and session.replay is not None and session.replay.ready
            delay = min(.001 if streaming else .005, max(0, remaining - spent))
            time.sleep(delay)
            remaining -= max(spent + delay, .0001)

    def _prepare_menu_release(self):
        if self.draw_session is None:
            return
        self.draw_session.drain_before_stop()
        self._request_raw(DRAW_STREAM, (0, 0, 0, 0))
        self.draw_session.replay = None
        self._menu_scan = None
        self.menu_resets += 1
        self._menu_transition = True
        self.screen_mode = 'menu-readback'
        self._menu_transition_until = time.monotonic() + .05

    def flush_inputs(self):
        while True:
            with self._input_lock:
                if not self._inputs:
                    return
                keys, wheel1, wheel2 = self._inputs.popleft()
            if self.fast_modifiers:
                session = self.draw_session
                if (session is not None and session.replay is not None and not session.replay.ready and
                        (keys != self._input_keys or wheel1 or wheel2)):
                    # No native key or wheel consumer runs during a baseline.
                    # Queue all edges, including unmodified navigation. Return
                    # to the drawing reader so it can finish and unlock it.
                    with self._input_lock:
                        self._inputs.appendleft((keys, wheel1, wheel2))
                    return
                if keys != self._input_keys:
                    self._input_pause(max(0, self._key_ready_at - time.monotonic()))
                    if self._input_keys & 8 and not keys & 8 and self._e_wheel_used:
                        # Stock E starts a 400 ms single/double-click timer.
                        # Its expiry callback centers when E is already up,
                        # even if a wheel was used. Let it expire while held.
                        # The extra 100 ms covers key adoption after the ack.
                        self._input_pause(max(0, self._e_pressed_at + .5 - time.monotonic()))
                if wheel1 or wheel2:
                    if keys & 8 and (abs(wheel1) > 1 or abs(wheel2) > 1):
                        # The native scale selectors use direction, not the
                        # detent count. Submit each scale step separately.
                        a = (wheel1 > 0) - (wheel1 < 0)
                        b = (wheel2 > 0) - (wheel2 < 0)
                        with self._input_lock:
                            self._inputs.appendleft((keys, wheel1-a, wheel2-b))
                        wheel1, wheel2 = a, b
                    if keys & 12:
                        if self._input_keys != keys:
                            self._send_input(keys)
                            self._wait_native_key_edge()
                        self._input_pause(max(0, self._modifier_ready_at - time.monotonic()))
                        self._modifier_ready_at = time.monotonic()
                    pending = self._send_input(keys, wheel1, wheel2)
                    # The reply contains pending wheel counts. Wait for the
                    # native reader to consume them before releasing E/M;
                    # otherwise E release can be mistaken for quick-center.
                    for _ in range(100):
                        if not any(pending):
                            break
                        self._input_pause(.005)
                        pending = self._send_input(keys)
                    else:
                        raise ProtocolError('The native GUI did not consume the wheel event.')
                    if keys & 4:
                        self._m_wheel_used = True
                    if keys & 8:
                        self._e_wheel_used = True
                    if keys & 12:
                        self._menu_readouts_dirty = True
                    continue
                if self._input_keys & 4 and not keys & 4 and not self._m_wheel_used:
                    # Stock M opens its menu on release (0x080294ee), not on
                    # press. Only that real click needs a fresh menu baseline.
                    self._prepare_menu_release()
                self._send_input(keys)
                self._wait_native_key_edge()
                continue
            if self.draw_session is not None and not self.modifier_gesture and (keys & 4 or self._input_keys & 4):
                if not self._menu_transition:
                    self.draw_session.drain_before_stop()
                    # A menu changes several widget lifetimes at once. Stop
                    # the old epoch before dispatching M. Keep native input
                    # processing active with LCD readback until M is released.
                    self._request_raw(DRAW_STREAM, (0, 0, 0, 0))
                    self.draw_session.replay = None
                    self._menu_scan = None
                    self.menu_resets += 1
                self._menu_transition = True
                self.screen_mode = 'menu-readback'
                self._menu_transition_until = time.monotonic() + .05
            if keys & 4 and (wheel1 or wheel2):
                self._menu_readouts_dirty = True
            self._send_input(keys, wheel1, wheel2)

    def deliver_inputs(self, cancelled=lambda: False):
        """Finish queued gestures, reading a pending baseline when necessary."""
        while self.has_pending_input():
            if cancelled():
                raise ProtocolError('The input operation was cancelled.')
            self.flush_inputs()
            if self.has_pending_input():
                before = time.monotonic()
                pixels = self.screen(cancelled)
                if pixels is not None and self.on_input_frame is not None:
                    self.on_input_frame(pixels, time.monotonic()-before)

    def screen(self, cancelled=None):
        import numpy as np
        self.flush_inputs()
        session = self.draw_session
        if (self.fast_modifiers and session is not None and session.replay is None and
                session.last_pixels is not None and self._input_keys):
            if not self._input_readback:
                # A baseline holds the entire native event loop. Keep held
                # controls live after a stream fault by reading bounded tiles.
                self._request_raw(DRAW_STREAM, (0, 0, 0, 0))
                self._menu_scan = None
                self._input_readback = True
            self.screen_mode = 'input-recovery'
            return self._menu_readback(cancelled)
        if self._input_readback:
            self._input_readback = False
            self._menu_scan = None
        if (self._menu_transition and not self._input_keys & 4 and
                time.monotonic() >= self._menu_transition_until):
            self._menu_transition = False
        if self.draw_config is not None and not self._menu_transition:
            from .drawing_client import DrawingSession, DrawingUnavailable
            if self.draw_session is None:
                self.draw_session = DrawingSession(self)
            self.screen_mode = 'draw-stream'
            try:
                return self.draw_session.next_frame(cancelled)
            except MenuTransition:
                return None
            except DrawingUnavailable:
                self.draw_config = None
                self.draw_session = None
                self.screen_mode = 'legacy'
                return self.screen(cancelled)
        if self._menu_transition:
            self.screen_mode = 'menu-readback'
            if self._input_keys & 4:
                return self._menu_readback(cancelled)
            self._menu_scan = None
            # Once released, avoid a duplicate LCD scan before the baseline.
            time.sleep(.002)
            return None
        output = np.zeros((320, 480), dtype="<u2")
        frame_id = None
        if self.stream_rows:
            y = 0
            while y < 320:
                if cancelled is not None and cancelled():
                    raise ProtocolError("The screen request was cancelled.")
                height = min(self.stream_rows, 320 - y)
                tile = parse_tile(self.request(SCREEN_STREAM, (0, y, 480, height)))
                if tile.x != 0 or tile.y != y or tile.width != 480 or not 1 <= tile.height <= height:
                    raise ProtocolError("The fast screen tile does not match the requested rows.")
                if frame_id is None:
                    frame_id = tile.frame_id
                elif frame_id != tile.frame_id:
                    raise ProtocolError("The frame identifier changed during the screen scan.")
                output[y:y + tile.height] = np.frombuffer(tile.pixels, dtype="<u2").reshape(tile.height, 480)
                y += tile.height
            return output
        for y in range(0, 320, 8):
            for x in range(0, 480, 64):
                if cancelled is not None and cancelled():
                    raise ProtocolError("The screen request was cancelled.")
                width = min(64, 480 - x)
                tile = parse_tile(self.request(SCREEN_READ, (x, y, width, 8)))
                if (tile.x, tile.y, tile.width, tile.height) != (x, y, width, 8):
                    raise ProtocolError("The screen tile does not match the requested rectangle.")
                if frame_id is None:
                    frame_id = tile.frame_id
                elif tile.frame_id != frame_id:
                    raise ProtocolError("The frame identifier changed during the screen scan.")
                output[y:y + 8, x:x + width] = np.frombuffer(tile.pixels, dtype="<u2").reshape(8, width)
        return output

    def _menu_readback(self, cancelled):
        """Read a bounded batch while M is held; publish real partial pixels."""
        import numpy as np
        if self._menu_scan is None:
            previous = self.draw_session.last_pixels
            self._menu_scan = previous.copy() if previous is not None else np.zeros((320, 480), dtype='<u2')
            # Position readouts first, then the remaining sidebar and waveform.
            rows = list(range(144, 200, 8))
            side = rows + [y for y in range(0, 320, 8) if y not in rows]
            tiles = [(416, y, 64) for y in side]
            tiles += [(x, y, min(64, 416 - x)) for y in range(0, 320, 8) for x in range(0, 416, 64)]
            self._menu_scan_tiles = deque(tiles)

        def read_tile(x, y, width):
            tile = parse_tile(self.request(SCREEN_READ, (x, y, width, 8)))
            if not self._input_keys:
                self._menu_scan = None
                return False
            if (tile.x, tile.y, tile.width, tile.height) != (x, y, width, 8):
                raise ProtocolError('The menu tile does not match the requested rectangle.')
            self._menu_scan[y:y+8, x:x+width] = np.frombuffer(tile.pixels, dtype='<u2').reshape(8, width)
            return True

        if self._menu_readouts_dirty:
            self._menu_readouts_dirty = False
            for y in (0, 8):
                for x in range(0, 480, 64):
                    if not read_tile(x, y, min(64, 480-x)):
                        return None
            for y in (144, 152, 176, 184, 192):
                if not read_tile(416, y, 64):
                    return None
        for _ in range(8):
            if cancelled is not None and cancelled():
                raise ProtocolError('The screen request was cancelled.')
            x, y, width = self._menu_scan_tiles.popleft()
            if not read_tile(x, y, width):
                return None
            if not self._menu_scan_tiles:
                output = self._menu_scan
                self._menu_scan = None
                self.draw_session.last_pixels = output.copy()
                return output
        if self.on_menu_preview is not None:
            # Keep the waveform whole between completed scans. Publish only
            # the updated sidebar in a partial preview, avoiding torn strips.
            previous = self.draw_session.last_pixels
            preview = previous.copy() if previous is not None else self._menu_scan.copy()
            preview[:16] = self._menu_scan[:16]
            preview[:, 416:480] = self._menu_scan[:, 416:480]
            self.on_menu_preview(preview)
        return None

    def waveform(self, count=4096, mask=3):
        return parse_wave(self.request(WAVE_READ, (count, mask, 0, 0)))

    def set_run(self, running):
        expected = bytes([int(bool(running))])
        payload = self.request(SET_RUN, (expected[0], 0, 0, 0))
        if payload != expected:
            raise ProtocolError("The device did not acknowledge the requested run state.")
        return payload

    def close(self):
        try:
            if self.draw_session is not None:
                try:
                    self.request(DRAW_STREAM, (0, 0, 0, 0))
                except (OSError, ProtocolError):
                    pass
                self.draw_session = None
        finally:
            self.transport.close()
