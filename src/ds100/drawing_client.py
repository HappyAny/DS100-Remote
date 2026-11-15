"""Read complete device drawing presentations. Empty polls do not create frames."""

import time
import numpy as np
from .protocol import DRAW_STREAM, ProtocolError, MenuTransition
from .drawing import DrawReplay, ResyncRequired, PACKET


class DrawingUnavailable(ProtocolError):
    pass


class DrawingSession:
    def __init__(self, client):
        self.client = client
        self.replay = None
        self.presentations = 0
        self.changed_frames = 0
        self.last_pixels = None
        self.last_frame_id = None
        self.resyncs = 0
        self.syncing = False

    def notify_sync(self, active):
        if self.syncing != active:
            self.syncing = active
            if self.client.on_draw_sync is not None:
                self.client.on_draw_sync(active)

    def accept_frames(self, frames):
        for frame_id, pixels in frames:
            self.presentations += 1
            if self.last_pixels is None or not np.array_equal(self.last_pixels, pixels):
                self.changed_frames += 1
            self.last_pixels = pixels
            self.last_frame_id = frame_id
        return frames[-1][1] if frames else None

    def drain_before_stop(self):
        """Reach an empty queue or a presentation before a top-level STOP."""
        if self.replay is None or not self.replay.ready:
            return
        deadline = time.monotonic() + .3
        for _ in range(128):
            try:
                # Do not recurse into flush_inputs, which called this method.
                payload = self.client._request_raw(DRAW_STREAM, (2, 0, 0, 0))
                frames = self.replay.apply(payload)
            except ProtocolError as error:
                if isinstance(error, ResyncRequired) or 'new baseline' in str(error) or 'inactive' in str(error):
                    self.replay = None
                    return
                raise
            self.accept_frames(frames)
            if frames or not PACKET.unpack_from(payload)[3] or time.monotonic() >= deadline:
                return

    def poll_active(self, raw=False):
        """Drain one packet during an input gesture. Do not begin a baseline."""
        if self.replay is None or not self.replay.ready:
            return None, False
        try:
            request = self.client._request_raw if raw else self.client.request
            payload = request(DRAW_STREAM, (2, 0, 0, 0))
            frames = self.replay.apply(payload)
        except ProtocolError as error:
            recoverable = isinstance(error, ResyncRequired) or 'new baseline' in str(error) or 'inactive' in str(error)
            if not recoverable:
                raise
            # A baseline holds rendering and would delay consumption of a
            # pressed key. Recover after this gesture has released all keys.
            self.replay = None
            self.resyncs += 1
            return None, False
        return self.accept_frames(frames), PACKET.unpack_from(payload)[3] != 0

    def next_frame(self, cancelled=None):
        idle_start = time.monotonic()
        failures = 0
        while True:
            if cancelled is not None and cancelled():
                raise ProtocolError('The screen request was cancelled.')
            start = self.replay is None
            if start:
                self.notify_sync(True)
                self.replay = DrawReplay()
            try:
                was_ready = self.replay.ready
                payload = self.client.request(DRAW_STREAM, (1 if start else 2, 0, 0, 0))
                frames = self.replay.apply(payload)
            except ProtocolError as error:
                text = str(error)
                if 'heap has no room' in text:
                    self.notify_sync(False)
                    raise DrawingUnavailable(text) from error
                recoverable = isinstance(error, ResyncRequired) or 'new baseline' in text or 'inactive' in text
                if not recoverable or failures >= 2:
                    raise
                self.replay = None
                failures += 1
                self.resyncs += 1
                if self.client._input_keys:
                    self.notify_sync(False)
                    raise MenuTransition('Use live readback until the held keys are released.') from error
                continue
            if frames:
                self.notify_sync(False)
                if self.client._input_keys and not was_ready:
                    self.client._key_ready_at = time.monotonic() + .08
                if self.client._input_keys & 12 and not was_ready:
                    self.client._modifier_ready_at = time.monotonic() + .08
                    if self.client._input_keys & 8:
                        self.client._e_pressed_at = time.monotonic()
                return self.accept_frames(frames)
            if self.replay.ready and time.monotonic() - idle_start >= .05:
                return None
            # Drain occupied packets immediately. Sleeping after every partial
            # frame would add a delay for each USB packet in a large drawing.
            if PACKET.unpack_from(payload)[3] == 0:
                time.sleep(.002)
