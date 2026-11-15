from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".tools"))
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
from ds100.thumb import branch


@pytest.mark.parametrize("source,target,link", [(0x08045476, 0x08078800, False), (0x08016F5C, 0x08078890, True),
                                              (0x08078800, 0x0804547A, False), (0x08070000, 0x0801538A, True)])
def test_branch_target_with_independent_disassembler(source, target, link):
    engine = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    instruction = list(engine.disasm(branch(source, target, link), source))
    assert len(instruction) == 1 and instruction[0].size == 4
    assert instruction[0].mnemonic == ("bl" if link else "b.w")
    assert int(instruction[0].op_str.lstrip("#"), 16) == target


@pytest.mark.parametrize("source,target", [(0x08000001, 0x08000020), (0x08000000, 0x08000021), (0x08000000, 0x0A000000)])
def test_branch_rejects_unrepresentable_targets(source, target):
    with pytest.raises(ValueError):
        branch(source, target)
