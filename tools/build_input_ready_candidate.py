"""Build R10 beside installed R9. Do not open or write a physical device."""

from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ds100.firmware import decode, encode, V48_BASE, V48_SHA256
from ds100.thumb import branch
from build_arm import run_linux


def main():
    r6 = json.loads((ROOT / 'candidates/controls-manifest.json').read_text(encoding='utf-8'))
    for name, expected in r6['source_files_sha256'].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError('The installed R6 source changed: ' + name)
    r7 = json.loads((ROOT / 'candidates/draw-stream-manifest.json').read_text(encoding='utf-8'))
    for name, expected in r7['source_files_sha256'].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError('The installed R7 source changed: ' + name)
    r8 = json.loads((ROOT / 'candidates/follow-manifest.json').read_text(encoding='utf-8'))
    for name, expected in r8['source_files_sha256'].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError('The installed R8 source changed: ' + name)
    r9 = json.loads((ROOT / 'candidates/bounded-manifest.json').read_text(encoding='utf-8'))
    for name, expected in r9['source_files_sha256'].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError('The installed R9 source changed: ' + name)
    official = (ROOT / 'atk_dso_v_4_8.bin').read_bytes()
    if hashlib.sha256(official).hexdigest() != V48_SHA256:
        raise ValueError('The official image differs from the pinned V4.8 image.')
    output = ROOT / 'build/arm'
    output.mkdir(exist_ok=True)
    stem = 'v48-input-ready'
    flags = ['-mcpu=cortex-m4', '-mthumb', '-mfloat-abi=soft', '-ffreestanding', '-fno-builtin',
        '-fno-pic', '-fno-pie', '-ffunction-sections', '-fdata-sections', '-Os', '-g',
        '-Wall', '-Wextra', '-Werror', '-DDS100_BACKGROUND_USB=1', '-DDS100_DRAW_PROFILE=1', '-DDS100_CONTROLS=1']
    sources = ['experiments/draw-stream/remote_fast_crc.c', 'experiments/draw-stream/fast_crc.c',
        'firmware/ds100_screen_stream.c', 'firmware/v48_trampoline.S',
        'firmware/v48_profile_trampoline.S', 'experiments/input-ready/input_ready.c',
        'experiments/draw-stream/draw_queue.c', 'experiments/input-ready/native_ready.c',
        'experiments/draw-stream/profile_stream.c', 'experiments/hardware-follow/hooks_follow.c',
        'experiments/input-ready/screen_ready.c', 'experiments/draw-stream/clear_trampoline.S']
    objects = []
    for name in sources:
        target = 'build/arm/r10-' + Path(name).stem + '.o'
        run_linux(['arm-none-eabi-gcc', *flags, '-c', name, '-o', target])
        objects.append(target)
    script = 'experiments/hardware-follow/patch.ld'
    run_linux(['arm-none-eabi-gcc', '-mcpu=cortex-m4', '-mthumb', '-mfloat-abi=soft', '-nostdlib',
        '-Wl,--gc-sections,-T,' + script + ',-Map,build/arm/' + stem + '.map', *objects,
        '-o', 'build/arm/' + stem + '.elf'])
    run_linux(['arm-none-eabi-objcopy', '-O', 'binary', 'build/arm/' + stem + '.elf', 'build/arm/' + stem + '-payload.bin'])
    symbols = {}
    for line in run_linux(['arm-none-eabi-nm', '--defined-only', 'build/arm/' + stem + '.elf']).splitlines():
        fields = line.split()
        if len(fields) == 3:
            symbols[fields[2]] = int(fields[0], 16) & ~1
    plain = decode(official)
    edited = bytearray(plain)
    hooks = []
    sites = [(int(item['address'], 16), bytes.fromhex(item['before']), item['symbol'],
              int(item['address'], 16) in (0x08016f5c, 0x0801030e, 0x08014b90, 0x08014ce4)) for item in r6['hooks']]
    sites.append((0x0803d2c8, bytes.fromhex('70b5ff4e'), 'ds100_stream_clear', False))
    sites.append((0x0803c9e2, bytes.fromhex('f8b4ec4a'), 'ds100_stream_scan_dir', False))
    for address, expected, symbol, link in sites:
        at = address - V48_BASE
        if plain[at:at + len(expected)] != expected:
            raise ValueError('The original instruction differs at ' + hex(address))
        after = branch(address, symbols[symbol], link)
        edited[at:at + 4] = after
        hooks.append({'address': hex(address), 'before': expected.hex(), 'after': after.hex(),
                      'symbol': symbol, 'target': hex(symbols[symbol])})
    extra_edits = r6['extra_edits'] + [{'address': '0x8045244', 'before': 'f0f79efc', 'after': '00bf00bf', 'purpose': 'Keep FatFS mounted while the host storage path is read-only.'}]
    for item in extra_edits:
        at = int(item['address'], 16) - V48_BASE
        expected = bytes.fromhex(item['before'])
        if plain[at:at + len(expected)] != expected:
            raise ValueError('The original USB edit differs.')
        edited[at:at + len(expected)] = bytes.fromhex(item['after'])
    allowed = set()
    for item in hooks + extra_edits:
        at = int(item['address'], 16) - V48_BASE
        allowed.update(range(at, at + len(bytes.fromhex(item['before']))))
    changed = {i for i, (a, b) in enumerate(zip(plain, edited)) if a != b}
    if not changed.issubset(allowed) or len(allowed) != 72:
        raise ValueError('The image changed outside the reviewed instruction sites.')
    payload = (output / (stem + '-payload.bin')).read_bytes()
    edited.extend(b'\xff' * (0x08078800 - V48_BASE - len(edited)))
    edited.extend(payload)
    edited.extend(b'\xff' * (-len(edited) % 128))
    if V48_BASE + len(edited) > 0x08080000:
        raise ValueError('The stream image exceeds the reviewed flash boundary.')
    encoded = encode(bytes(edited))
    if decode(encoded) != edited:
        raise ValueError('The R7 encoding check failed.')
    filename = 'DS100-V4.8-input-ready-r10-UNVERIFIED.bin'
    (ROOT / 'candidates' / filename).write_bytes(encoded)
    (output / (stem + '-decoded.bin')).write_bytes(edited)
    files = set(sources + [script, 'tools/build_input_ready_candidate.py', 'experiments/draw-stream/draw_queue.h',
                            'experiments/draw-stream/native_stream.h', 'firmware/v48_draw_profile.c', 'firmware/ds100_remote.h',
                            'firmware/ds100_remote.c'])
    report = {'revision': 'r10-input-ready', 'status': 'UNVERIFIED; no physical flash performed by this build',
        'candidate_file': filename, 'candidate_sha256': hashlib.sha256(encoded).hexdigest(),
        'candidate_size_bytes': len(encoded), 'source_sha256': V48_SHA256,
        'payload_base': '0x08078800', 'payload_size_bytes': len(payload),
        'decoded_original_bytes_changed': len(changed), 'reviewed_original_bytes': len(allowed),
        'hooks': hooks, 'extra_edits': extra_edits, 'symbols': {k: hex(v) for k, v in symbols.items()},
        'section_sizes': run_linux(['arm-none-eabi-size', 'build/arm/' + stem + '.elf']),
        'source_files_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sorted(files)},
        'ram': {'static_tail_limit_bytes': 112, 'queue_bytes': 16384,
                'allocation': 'Stock allocator, only after explicit stream start. Free on stop or one second without stream requests.',
                'heap_regions': ['0x10000000..0x10010000', '0x20021640..0x20027e40']},
        'redraw_timer': {'constructor_hook': None, 'widget_vtable': '0x08072840',
                'callback': '0x080271f7', 'period_offset': '0x1d4', 'stock_ms': 500, 'stream_ms': 500,
                'policy': 'No timer hook or timer write. Keep original periods and scheduling in all stream states.'},
        'installed_r6_changed': False, 'installed_r7_changed': False, 'installed_r8_changed': False, 'installed_r9_changed': False,
        'backpressure': 'One failed 50 ms drain deadline invalidates capture until a new baseline; do not repeat waits for subsequent drawing records.',
        'input_ready_guard': 'Keep virtual wheels queued until reported modifiers match, and the validated native parent is ready or a modal menu is active. Physical events remain unchanged.',
        'boot_fix': 'Skip the stock USB FatFS unmount and reapply current acquisition settings once after automatic USB initialization. Do not call AUTO.', 'physical_device_validation': False,
        'actual_30_fps_verified': False,
        'limits': ['Drawing coverage and inclusive-edge behavior require physical pixel comparison.',
                   'The initial baseline pauses GUI rendering while reading the LCD; acquisition timing is not validated.',
                   'The queue consumes stock heap space. Allocation failure leaves the legacy screen service available.',
                   'The stream follows native display updates. It does not increase acquisition or redraw timer rates.',
                   'The existing USB 168 MHz policy remains; measurement accuracy is not validated.']}
    (ROOT / 'candidates/input-ready-manifest.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('candidate_file', 'candidate_sha256', 'candidate_size_bytes', 'payload_size_bytes', 'section_sizes')}, indent=2))


if __name__ == '__main__':
    main()
