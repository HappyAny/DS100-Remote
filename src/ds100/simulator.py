"""Generate marked test data for the desktop application."""

import json
import time
import struct
import numpy as np
from PIL import Image, ImageDraw
from .protocol import CDB, INFO, SCREEN_READ, WAVE_READ, SET_RUN, INPUT_STATE, SCREEN, WAVE, make_response


class SimulatorTransport:
    def __init__(self):
        self.running = True
        self.frame_id = 0
        self.image = np.zeros((320, 480), dtype="<u2")
        self.keys = 0
        self.input_log = []

    def _new_frame(self):
        self.frame_id += 1
        phase = time.monotonic() if self.running else 0.0
        image = Image.new("RGB", (480, 320), (11, 15, 23))
        draw = ImageDraw.Draw(image)
        for x in range(0, 480, 40):
            draw.line((x, 36, x, 292), fill=(42, 49, 62))
        for y in range(36, 293, 32):
            draw.line((0, y, 479, y), fill=(42, 49, 62))
        xs = np.arange(480)
        for amplitude, frequency, offset, color in [(65, 3, 0, "#ffcf48"), (40, 3, 1.2, "#62d5ae")]:
            ys = 165 - amplitude * np.sin(xs / 480 * 2 * np.pi * frequency + phase + offset)
            draw.line(list(zip(xs.tolist(), ys.astype(int).tolist())), fill=color, width=2)
        draw.rectangle((0, 0, 479, 30), fill=(24, 31, 43))
        draw.text((12, 9), "DS100 PROTOCOL SIMULATOR - TEST DATA", fill="white")
        draw.text((12, 301), f"{'RUN' if self.running else 'STOP'}   Frame {self.frame_id}   480 x 320 RGB565", fill="#aab9d0")
        rgb = np.asarray(image, dtype=np.uint16)
        self.image = (((rgb[:, :, 0] >> 3) << 11) | ((rgb[:, :, 1] >> 2) << 5) | (rgb[:, :, 2] >> 3)).astype("<u2")

    def exchange(self, cdb):
        opcode, command, version, flags, sequence, a, b, c, d = CDB.unpack(cdb)
        if opcode != 0xC0 or version != 1 or flags:
            return make_response(command, sequence, b"The command header is invalid.", error=True)
        if command == INFO:
            payload = json.dumps({"protocol": 1, "model": "DS100 protocol simulator", "software": "test-data", "hardware": "simulator", "screen_width": 480, "screen_height": 320,
                                  "capabilities": ["screen", "waveform", "run", "input"], "source": "simulator",
                                  'input': {'command': 8, 'keys': ['P', 'A', 'M', 'E'], 'wheels': 2, 'lease_ms': 600}}).encode()
        elif command == INPUT_STATE:
            wheel1, wheel2 = struct.unpack('<hh', struct.pack('<HH', b, c))
            if a > 15 or not -127 <= wheel1 <= 127 or not -127 <= wheel2 <= 127 or d:
                return make_response(command, sequence, b'The input state is invalid.', error=True)
            if a & 1 and not self.keys & 1:
                self.running = not self.running
            self.keys = a
            self.input_log.append((a, wheel1, wheel2))
            payload = struct.pack('<HHhh', a, 600, wheel1, wheel2)
        elif command == SCREEN_READ:
            if not c or not d or c * d > 512 or a + c > 480 or b + d > 320:
                return make_response(command, sequence, b"The screen rectangle is invalid.", error=True)
            if a == 0 and b == 0:
                self._new_frame()
            payload = SCREEN.pack(self.frame_id, a, b, c, d, 0, 0, 0) + self.image[b:b + d, a:a + c].tobytes()
        elif command == WAVE_READ:
            if not 1 <= a <= 4096 or b not in (1, 2, 3) or c or d:
                return make_response(command, sequence, b"The waveform request is invalid.", error=True)
            t = np.arange(a) / 1_000_000
            channels = [np.clip(np.rint(128 + (70 if i == 0 else 43) * np.sin(2 * np.pi * 1000 * t + i)), 0, 255).astype(np.uint8).tobytes()
                        for i in (0, 1) if b & (1 << i)]
            payload = WAVE.pack(a, b, 1e-6, 0, 0.02, -2.56, 0.02, -2.56) + b"".join(channels)
        elif command == SET_RUN:
            if a not in (0, 1) or b or c or d:
                return make_response(command, sequence, b"The run state is invalid.", error=True)
            self.running = bool(a)
            payload = bytes([a])
        else:
            return make_response(command, sequence, b"The command is not supported.", error=True)
        return make_response(command, sequence, payload)

    def close(self):
        pass
