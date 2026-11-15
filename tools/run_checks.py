"""Run the project tests and save the test receipt."""

from pathlib import Path
import os
import tempfile
import pytest

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
(ROOT / "evidence/software-tests").mkdir(parents=True, exist_ok=True)

if __name__ == "__main__":
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="pytest-", dir=build)).resolve()
    if not temporary.is_relative_to(ROOT.resolve()):
        raise RuntimeError("The test temporary directory is outside the workspace.")
    raise SystemExit(pytest.main(["-q", "--basetemp=" + str(temporary), "--junitxml=evidence/software-tests/junit.xml"]))
