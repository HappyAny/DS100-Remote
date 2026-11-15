import struct
import numpy as np
import pytest
from pathlib import Path
import json

from ds100.drawing import (DrawReplay, ResyncRequired, packet, record, CLEAR, POINT, FILL,
                            SPAN, VSPAN, PRESENT, BASELINE)


def baseline(epoch=1):
    return packet(epoch, 0, [record(CLEAR, struct.pack('<H', 0)),
                             record(PRESENT, struct.pack('<I', 0), BASELINE)], reset=True)


def test_drawing_replay_exact_pixels_and_presentation_boundary():
    replay = DrawReplay()
    assert replay.apply(baseline())[0][0] == 0
    records = [record(POINT, struct.pack('<HHH', 479, 319, 0xF800)),
        record(FILL, struct.pack('<5H', 10, 20, 19, 29, 0x07E0)),
        record(SPAN, struct.pack('<HHHBB', 7, 8, 3, 0, 0) + struct.pack('<3H', 1, 2, 3)),
        record(SPAN, struct.pack('<HHHBBHH', 20, 30, 480 - 20, 1, 0, 460, 0x001F))]
    assert replay.apply(packet(1, 2, records)) == []
    assert np.count_nonzero(replay.last_frame) == 0
    frames = replay.apply(packet(1, 6, [record(PRESENT, struct.pack('<I', 1))]))
    expected = np.zeros((320, 480), dtype='<u2')
    expected[319, 479] = 0xF800
    expected[20:30, 10:20] = 0x07E0
    expected[8, 7:10] = [1, 2, 3]
    expected[30, 20:] = 0x001F
    np.testing.assert_array_equal(frames[0][1], expected)


def test_lost_packet_requires_new_epoch_and_full_baseline():
    replay = DrawReplay()
    replay.apply(baseline())
    with pytest.raises(ResyncRequired, match='missing'):
        replay.apply(packet(1, 3, [record(PRESENT, struct.pack('<I', 1))]))
    assert not replay.ready and replay.last_frame is None
    with pytest.raises(ResyncRequired, match='baseline'):
        replay.apply(packet(1, 2, [record(PRESENT, struct.pack('<I', 1))]))
    replay.apply(baseline(2))
    assert replay.ready


def test_packet_failure_does_not_commit_partial_drawing():
    replay = DrawReplay()
    replay.apply(baseline())
    valid = record(POINT, struct.pack('<HHH', 0, 0, 1234))
    bad = record(POINT, struct.pack('<HHH', 480, 0, 1234))
    with pytest.raises(ResyncRequired):
        replay.apply(packet(1, 2, [valid, bad]))
    assert replay.pixels[0, 0] == 0
    assert replay.next_record == 2


@pytest.mark.parametrize('body', [
    record(SPAN, struct.pack('<HHHBBHH', 0, 0, 4, 1, 0, 5, 1)),
    record(SPAN, struct.pack('<HHHBBHH', 0, 0, 4, 1, 0, 0, 1)),
    record(FILL, struct.pack('<5H', 5, 5, 4, 4, 0)),
    record(PRESENT, struct.pack('<I', 0)),
    record(99, b''),
])
def test_drawing_rejects_bad_rle_rectangles_stale_frame_and_opcode(body):
    replay = DrawReplay()
    replay.apply(baseline())
    with pytest.raises(ResyncRequired):
        replay.apply(packet(1, 2, [body]))


def test_drawing_truncations_are_rejected():
    data = baseline()
    for length in range(len(data)):
        with pytest.raises(ResyncRequired):
            DrawReplay().apply(data[:length])


@pytest.mark.parametrize('body', [b'', b'\0', b'\x11', b'\x01\x34\x12\x10',
    b'\x01\x34\x12\x0f', b'\x01\x34\x12\x0f\x80\0', b'\x01\x34\x12\x0f\xff\xff'])
def test_drawing_rejects_bad_palette_indices_lengths_and_truncations(body):
    replay = DrawReplay()
    replay.apply(baseline())
    span = record(VSPAN, struct.pack('<HHHBB', 0, 0, 20, 2, 0) + body)
    with pytest.raises(ResyncRequired):
        replay.apply(packet(1, 2, [span]))


def test_replay_reconstructs_captured_device_image_pixel_for_pixel():
    from PIL import Image
    root = Path(__file__).resolve().parents[1]
    source = root / 'tests/fixtures/r3-screen.png'
    rgb = np.asarray(Image.open(source).convert('RGB'), dtype=np.uint16)
    expected = (((rgb[:, :, 0] >> 3) << 11) | ((rgb[:, :, 1] >> 2) << 5) | (rgb[:, :, 2] >> 3)).astype('<u2')
    replay = DrawReplay()
    sequence, records, size = 0, [record(CLEAR, b'\0\0')], 26
    for y, row in enumerate(expected):
        if y % 2 == 0:
            encoding, body = 0, row.tobytes()
        else:
            encoding, encoded = 1, bytearray()
            start = 0
            for end in range(1, 481):
                if end == 480 or row[end] != row[start]:
                    encoded.extend(struct.pack('<HH', end - start, int(row[start])))
                    start = end
            body = bytes(encoded)
        item = record(SPAN, struct.pack('<HHHBB', 0, y, 480, encoding, 0) + body)
        if size + len(item) > 16000:
            assert replay.apply(packet(7, sequence, records, reset=sequence == 0)) == []
            sequence += len(records)
            records, size = [], 20
        records.append(item)
        size += len(item)
    records.append(record(PRESENT, struct.pack('<I', 0), BASELINE))
    frames = replay.apply(packet(7, sequence, records, reset=sequence == 0))
    np.testing.assert_array_equal(frames[0][1], expected)
    report = {'source': str(source.relative_to(root)).replace('\\', '/'), 'source_is_real_device_capture': True,
              'replay_transport': 'Generated RAW565/RLE565 drawing packets from the saved capture.',
              'physical_live_stream_test': False, 'screen_size': [480, 320],
              'compared_pixels': int(expected.size), 'different_pixels': 0}
    (root / 'evidence/software-tests/captured-image-replay.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
