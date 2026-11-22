"""Build a Windows desktop folder with the installed PyInstaller. Do not download tools."""

from pathlib import Path
import argparse
import re
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', default='DS100-Remote')
    parser.add_argument('--output-root', type=Path, default=ROOT,
                        help='Write the build and dist directories below this path.')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9._-]+', args.name) or args.name.endswith('.'):
        parser.error('Use a plain application folder name without a trailing dot.')
    if os.name != "nt":
        raise OSError("This application build requires Windows.")
    output_root = args.output_root.resolve()
    os.chdir(ROOT)
    build = output_root / ("build/windows-app-" + args.name)
    output = output_root / "dist" / args.name
    if build.resolve() != build or output.resolve() != output:
        parser.error('The build and application directories must not be redirected.')
    build.mkdir(parents=True, exist_ok=True)
    arguments = ["--noconfirm", "--onedir", "--windowed", "--name", args.name,
                 "--paths", str(ROOT / "src"), "--distpath", str(output_root / "dist"), "--workpath", str(build),
                 "--specpath", str(build), "--add-data", f"{ROOT / 'assets/fonts'}{os.pathsep}assets/fonts"]
    for name in ("matplotlib", "scipy", "pandas", "pytest", "IPython", "notebook", "PyQt5", "PyQt6", "tkinter"):
        arguments.extend(["--exclude-module", name])
    with (build / "build.log").open("w", encoding="utf-8") as log:
        subprocess.run([sys.executable, "-m", "PyInstaller", *arguments, str(ROOT / "run_app.py")],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "README.md"):
        shutil.copyfile(ROOT / name, output / name)
    notices = output / "third-party-licenses"
    packages = {}
    for name in ("PySide6", "PySide6-Essentials", "PySide6-Addons", "shiboken6", "numpy", "Pillow", "pyinstaller"):
        distribution = importlib.metadata.distribution(name)
        packages[name] = distribution.version
        for item in distribution.files or []:
            if ".dist-info/licenses/" not in str(item).replace("\\", "/"):
                continue
            source = Path(distribution.locate_file(item))
            relative = str(item).replace("\\", "/").split(".dist-info/licenses/", 1)[1]
            target = notices / name / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    (notices / "versions.json").write_text(json.dumps(packages, indent=2) + "\n", encoding="utf-8")
    python_license = Path(sys.prefix) / "LICENSE_PYTHON.txt"
    if python_license.is_file():
        target = notices / "Python/LICENSE_PYTHON.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(python_license, target)
    size = sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
    print(json.dumps({"directory": str(output), "size_bytes": size, "packages": packages}, indent=2))


if __name__ == "__main__":
    main()
