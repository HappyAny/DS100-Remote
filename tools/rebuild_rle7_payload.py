"""Recompile the rle7 payload and verify the resulting firmware image."""
# SPDX-License-Identifier: GPL-3.0-only

import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from prepare_firmware import PATCHES, apply_patch, check_base, codec, sha, write_image

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / 'firmware/releases/rle7'
SOURCE = RELEASE / 'source'
ARM_FLAGS = ['-mcpu=cortex-m4', '-mthumb']


def compile_stage(stage, directory):
    elf = directory / 'payload.elf'
    binary = directory / 'payload.bin'
    sources = stage['sources']
    flags = ARM_FLAGS + stage['compiler_flags']

    def gcc(*args):
        subprocess.run(['arm-none-eabi-gcc', *args], cwd=SOURCE, check=True)

    if stage['separate_objects']:
        objects = []
        for index, source in enumerate(sources):
            target = str(directory / f'{index}.o')
            gcc(*flags, '-c', source, '-o', target)
            objects.append(target)
        sources = objects
        flags = ARM_FLAGS + ['-mfloat-abi=soft']
    gcc(*flags, '-nostdlib', '-T', stage['linker'], *stage['linker_flags'],
        *sources, '-o', str(elf))
    subprocess.run(['arm-none-eabi-objcopy', '-O', 'binary', str(elf), str(binary)], check=True)
    return binary.read_bytes()


def rebuild(base, build):
    check_base(base)
    recipe = json.loads((SOURCE / 'build.json').read_text(encoding='utf-8'))
    for name, digest in recipe['sources_sha256'].items():
        if sha((SOURCE / name).read_bytes()) != digest:
            raise ValueError(f'The release source differs: {name}')

    plain = bytearray(codec(base))
    for stage in recipe['stages']:
        name = stage['name']
        if stage['payload_size']:
            payload = compile_stage(stage, build)
            if len(payload) != stage['payload_size'] or sha(payload) != stage['payload_sha256']:
                raise ValueError(f'The compiled payload differs: {name}')
        else:
            payload = b''
        for edit in stage['edits']:
            offset = edit['offset']
            before, after = bytes.fromhex(edit['before']), bytes.fromhex(edit['after'])
            if not 0 <= offset <= len(plain) - len(before) or len(before) != len(after):
                raise ValueError(f'The edit range differs: {name} at {offset:#x}')
            if plain[offset:offset + len(before)] != before:
                raise ValueError(f'The original bytes differ: {name} at {offset:#x}')
            plain[offset:offset + len(after)] = after
        padding = stage['append_offset'] - len(plain)
        if padding < 0:
            raise ValueError(f'The append address overlaps the image: {name}')
        plain.extend(b'\xff' * padding)
        plain.extend(payload)
        plain.extend(b'\xff' * (-len(plain) % 128))
        output = codec(bytes(plain), encode=True)
        if len(output) != stage['image_size'] or sha(output) != stage['image_sha256']:
            raise ValueError(f'The intermediate image differs: {name}')
        print(f'{name}: {len(payload)} linked bytes, image SHA-256 verified', flush=True)

    if output != apply_patch(base, RELEASE / 'patch.json', revision='rle7'):
        raise ValueError('The source build differs from the released patch.')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True, help='Use the pinned official V4.8 image.')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/rle7-from-source/atk_dso.bin')
    args = parser.parse_args()
    build = ROOT / 'build'
    build.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='rle7-compile-', dir=build) as workspace:
        output = rebuild(args.base.read_bytes(), Path(workspace))
    destination = args.output.resolve()
    write_image(destination, output)
    print('Created:', destination)
    print('SHA256:', PATCHES['rle7'][2])


if __name__ == '__main__':
    main()
