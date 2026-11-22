"""Export the reviewed byte edits and appended code for a checked release."""
# SPDX-License-Identifier: GPL-3.0-only

import argparse
import json
from pathlib import Path

from prepare_firmware import BASE_SHA, PATCHES, apply_patch, check_base, codec, sha

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', choices=PATCHES, default='r11')
    parser.add_argument('--base', type=Path, default=ROOT / 'atk_dso_v_4_8.bin')
    parser.add_argument('--image', type=Path)
    args = parser.parse_args()
    image = args.image
    if image is None:
        if args.revision != 'r11':
            parser.error('--image is required for this revision.')
        image = ROOT / 'candidates/DS100-V4.8-input-ack-r11-UNVERIFIED.bin'
    base, target = args.base.read_bytes(), image.read_bytes()
    check_base(base)
    build, target_size, target_sha = PATCHES[args.revision]
    if len(target) != target_size or sha(target) != target_sha:
        raise ValueError('The checked target image differs.')
    original, result = codec(base), codec(target)
    edits = []
    index = 0
    while index < len(original):
        if original[index] == result[index]:
            index += 1
            continue
        end = index + 1
        while end < len(original) and original[end] != result[end]:
            end += 1
        edits.append({'offset': index, 'before': original[index:end].hex(), 'after': result[index:end].hex()})
        index = end
    payload = result[len(original):]
    folder = ROOT / 'firmware/releases' / args.revision
    folder.mkdir(parents=True, exist_ok=True)
    payload_file = f'{args.revision}-payload.bin'
    (folder / payload_file).write_bytes(payload)
    manifest = {'format': f'DS100-{args.revision.upper()}-PATCH-1', 'license': 'GPL-3.0-only for the project patch and appended code. Vendor base rights remain unchanged.',
                'build': build, 'base_sha256': BASE_SHA, 'base_size': len(base),
                'target_sha256': target_sha, 'target_size': len(target), 'payload_file': payload_file,
                'payload_sha256': sha(payload),
                'changed_original_bytes': sum(len(bytes.fromhex(e['after'])) for e in edits), 'edits': edits}
    path = folder / 'patch.json'
    path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    if apply_patch(base, path, revision=args.revision) != target:
        raise ValueError('The exported patch does not reproduce the checked image.')
    print(json.dumps({k: manifest[k] for k in ('format', 'target_sha256', 'target_size', 'changed_original_bytes')}, indent=2))


if __name__ == '__main__':
    main()
