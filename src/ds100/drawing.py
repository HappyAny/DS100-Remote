"""Experimental drawing replay. No installed firmware emits this stream yet."""

import struct
import numpy as np

from .protocol import MAX_PAYLOAD, ProtocolError, SCREEN, parse_tile

PACKET = struct.Struct('<4sIIIH2x')
RECORD = struct.Struct('<BBH')
CLEAR, POINT, FILL, SPAN, PRESENT = range(5)
VSPAN = 5
RESET, BASELINE = 1, 1


class ResyncRequired(ProtocolError):
    pass


def record(command, payload, flags=0):
    if len(payload) > 65535:
        raise ProtocolError('The drawing record exceeds the size limit.')
    return RECORD.pack(command, flags, len(payload)) + payload


def packet(epoch, first_record, records, reset=False):
    body = PACKET.pack(b'DRW1', epoch, first_record, len(records), RESET if reset else 0) + b''.join(records)
    if len(body) > MAX_PAYLOAD:
        raise ProtocolError('The drawing packet exceeds the size limit.')
    return body


class DrawReplay:
    """Commit valid packets and expose only complete, explicitly presented frames.

    A new epoch starts with RESET and CLEAR. Its first PRESENT must have the
    BASELINE flag. The sender must complete a full repaint before that marker.
    Lost records invalidate the image until a new epoch supplies a baseline.
    """

    def __init__(self):
        self.pixels = np.zeros((320, 480), dtype='<u2')
        self.epoch = None
        self.next_record = 0
        self.frame_id = None
        self.ready = False
        self.last_frame = None

    def apply(self, data):
        try:
            return self._apply(data)
        except (ProtocolError, struct.error, ValueError, OverflowError) as error:
            self.ready = False
            self.last_frame = None
            if isinstance(error, ResyncRequired):
                raise
            raise ResyncRequired(str(error)) from error

    def _apply(self, data):
        if not PACKET.size <= len(data) <= MAX_PAYLOAD:
            raise ResyncRequired('The drawing packet length is invalid.')
        magic, epoch, first, count, flags = PACKET.unpack_from(data)
        if magic != b'DRW1' or flags & ~RESET or count > 4096 or data[18:20] != b'\0\0':
            raise ResyncRequired('The drawing packet header is invalid.')
        reset = bool(flags & RESET)
        if reset:
            if first or not count or epoch == self.epoch:
                raise ResyncRequired('A drawing reset requires a new epoch and record zero.')
            ready, frame_id = False, None
        else:
            if self.epoch != epoch or first != self.next_record:
                raise ResyncRequired('Drawing records are missing or out of order.')
            ready, frame_id = self.ready, self.frame_id
        if first + count > 0xFFFFFFFF:
            raise ResyncRequired('The drawing record sequence requires a new epoch.')
        canvas = self.pixels.copy()
        frames, offset, work = [], PACKET.size, 0
        for index in range(count):
            if offset + RECORD.size > len(data):
                raise ResyncRequired('The drawing record header is incomplete.')
            command, options, length = RECORD.unpack_from(data, offset)
            offset += RECORD.size
            if offset + length > len(data):
                raise ResyncRequired('The drawing record is incomplete.')
            body = data[offset:offset + length]
            offset += length
            if options & ~(BASELINE if command == PRESENT else 0):
                raise ResyncRequired('The drawing record flags are invalid.')
            if reset and index == 0 and command != CLEAR:
                raise ResyncRequired('A drawing baseline must start with a clear command.')
            if command == CLEAR:
                if length != 2:
                    raise ResyncRequired('The clear command length is invalid.')
                canvas.fill(struct.unpack('<H', body)[0])
                work += canvas.size
            elif command == POINT:
                if length != 6:
                    raise ResyncRequired('The point command length is invalid.')
                x, y, color = struct.unpack('<HHH', body)
                if x >= 480 or y >= 320:
                    raise ResyncRequired('The drawing point is outside the screen.')
                canvas[y, x] = color
                work += 1
            elif command == FILL:
                if length != 10:
                    raise ResyncRequired('The fill command length is invalid.')
                x0, y0, x1, y1, color = struct.unpack('<5H', body)
                if x0 > x1 or y0 > y1 or x1 >= 480 or y1 >= 320:
                    raise ResyncRequired('The drawing rectangle is invalid.')
                canvas[y0:y1 + 1, x0:x1 + 1] = color
                work += (x1 - x0 + 1) * (y1 - y0 + 1)
            elif command in (SPAN, VSPAN):
                if length < 8:
                    raise ResyncRequired('The pixel span header is incomplete.')
                x, y, width, encoding, reserved = struct.unpack_from('<HHHBB', body)
                if reserved:
                    raise ResyncRequired('The pixel span reserved field is invalid.')
                tile = parse_tile(SCREEN.pack(0, x, y, width if command == SPAN else 1,
                                              1 if command == SPAN else width, encoding, 0, 0) + body[8:])
                if command == SPAN:
                    canvas[y, x:x + width] = np.frombuffer(tile.pixels, dtype='<u2')
                else:
                    canvas[y:y + width, x] = np.frombuffer(tile.pixels, dtype='<u2')
                work += width
            elif command == PRESENT:
                if length != 4 or len(frames) >= 4:
                    raise ResyncRequired('The presentation command is invalid.')
                current = struct.unpack('<I', body)[0]
                if frame_id is not None and not 0 < (current - frame_id) & 0xFFFFFFFF < 0x80000000:
                    raise ResyncRequired('The drawing frame identifier did not advance.')
                if not ready and not options & BASELINE:
                    raise ResyncRequired('A complete drawing baseline is required.')
                if ready and options & BASELINE:
                    raise ResyncRequired('A new baseline requires a new epoch.')
                ready, frame_id = True, current
                frames.append((current, canvas.copy()))
            else:
                raise ResyncRequired('The drawing operation is not supported.')
            if work > 4 * canvas.size:
                raise ResyncRequired('The drawing packet exceeds the pixel work limit.')
        if offset != len(data):
            raise ResyncRequired('The drawing packet has trailing bytes.')
        self.pixels, self.epoch, self.next_record = canvas, epoch, first + count
        self.ready, self.frame_id = ready, frame_id
        if frames:
            self.last_frame = frames[-1][1]
        return frames
