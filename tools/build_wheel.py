"""Build the Python wheel and keep the output directory's file access rules."""

from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr
import json
import os
import uuid
import setuptools.build_meta as backend

ROOT = Path(__file__).resolve().parents[1]


def main():
    os.chdir(ROOT)
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    (ROOT / "build").mkdir(exist_ok=True)
    with (ROOT / "build/wheel-build.log").open("w", encoding="utf-8") as log, redirect_stdout(log), redirect_stderr(log):
        name = backend.build_wheel(str(output))
    path = output / name
    # Setuptools can move a temporary file with owner-only Windows access rules.
    # Create a fresh file in dist so the result inherits dist's access rules.
    staged = output / ("wheel-" + uuid.uuid4().hex + ".staged")
    with staged.open("xb") as stream:
        stream.write(path.read_bytes())
    staged.replace(path)
    print(json.dumps({"wheel": name, "size_bytes": path.stat().st_size}, indent=2))


if __name__ == "__main__":
    main()
