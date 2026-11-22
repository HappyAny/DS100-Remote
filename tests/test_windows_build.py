"""Reject Windows application names that can alias another output directory."""

from pathlib import Path
import runpy
import subprocess
import sys

import pytest


@pytest.mark.parametrize("name", [".", "..", "DS100-Remote."])
def test_build_rejects_ambiguous_windows_folder_names(monkeypatch, tmp_path, capsys, name):
    script = Path(__file__).resolve().parents[1] / "tools/build_windows_app.py"
    monkeypatch.setattr(sys, "argv", [str(script), "--name", name, "--output-root", str(tmp_path)])

    def unexpected_build(*args, **kwargs):
        pytest.fail("PyInstaller must not run for an invalid output name.")

    monkeypatch.setattr(subprocess, "run", unexpected_build)
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(script), run_name="__main__")
    assert error.value.code == 2
    assert "without a trailing dot" in capsys.readouterr().err
