"""Export only the R11 edits and appended code. Do not export the vendor image."""
# SPDX-License-Identifier: GPL-3.0-only

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ds100.firmware import decode
from prepare_firmware import apply_patch, BASE_SHA, TARGET_SHA


def main():
    base = (ROOT / 'atk_dso_v_4_8.bin').read_bytes()
    target = (ROOT / 'candidates/DS100-V4.8-input-ack-r11-UNVERIFIED.bin').read_bytes()
    if hashlib.sha256(base).hexdigest() != BASE_SHA or hashlib.sha256(target).hexdigest() != TARGET_SHA:
        raise ValueError('The checked source or target image differs.')
    original, result = decode(base), decode(target)
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
    folder = ROOT / 'firmware/releases/r11'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'r11-payload.bin').write_bytes(payload)
    manifest = {'format': 'DS100-R11-PATCH-1', 'license': 'GPL-3.0-only for the project patch and appended code. Vendor base rights remain unchanged.',
                'build': 'background-r11-input-ack', 'base_sha256': BASE_SHA, 'base_size': len(base),
                'target_sha256': TARGET_SHA, 'target_size': len(target), 'payload_file': 'r11-payload.bin',
                'payload_sha256': hashlib.sha256(payload).hexdigest(),
                'changed_original_bytes': sum(len(bytes.fromhex(e['after'])) for e in edits), 'edits': edits}
    path = folder / 'patch.json'
    path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    if apply_patch(base, path) != target:
        raise ValueError('The exported patch does not reproduce the checked R11 image.')
    print(json.dumps({k: manifest[k] for k in ('format', 'target_sha256', 'target_size', 'changed_original_bytes')}, indent=2))


if __name__ == '__main__':
    main()
