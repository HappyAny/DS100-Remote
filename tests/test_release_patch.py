"""Check the public patch, exact-image reproduction, and corrupt input rejection."""

import importlib.util
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('public_patch', ROOT / 'tools/prepare_firmware.py')
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


def test_public_patch_rejects_wrong_base():
    with pytest.raises(ValueError, match='base is not'):
        patcher.apply_patch(b'wrong firmware', Path('unused.json'))


@pytest.fixture
def official():
    path = ROOT / 'atk_dso_v_4_8.bin'
    if not path.exists():
        pytest.skip('Fetch the pinned official base to run the image integration checks.')
    return path.read_bytes()


def test_public_patch_reproduces_checked_image(official):
    output = patcher.apply_patch(official, ROOT / 'firmware/releases/r11/patch.json')
    assert len(output) == patcher.TARGET_SIZE
    assert patcher.sha(output) == patcher.TARGET_SHA
    assert patcher.codec(output)[:0x100] == patcher.codec(official)[:0x100]


@pytest.mark.parametrize('mutation', ['payload', 'overlap', 'traversal', 'target'])
def test_public_patch_rejects_corruption(official, tmp_path, mutation):
    source = ROOT / 'firmware/releases/r11'
    manifest = json.loads((source / 'patch.json').read_text(encoding='utf-8'))
    payload = bytearray((source / 'r11-payload.bin').read_bytes())
    if mutation == 'payload':
        payload[-1] ^= 1
    elif mutation == 'overlap':
        manifest['edits'].insert(1, manifest['edits'][0].copy())
    elif mutation == 'traversal':
        manifest['payload_file'] = '../r11-payload.bin'
    else:
        manifest['target_sha256'] = '0' * 64
    (tmp_path / 'r11-payload.bin').write_bytes(payload)
    path = tmp_path / 'patch.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError):
        patcher.apply_patch(official, path)
