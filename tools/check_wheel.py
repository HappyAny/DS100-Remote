"""Install the wheel in a workspace virtual environment and check the desktop."""

from pathlib import Path
import hashlib
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ds100 import __version__


def main():
    wheel = ROOT / ('dist/ds100_remote_research-' + __version__ + '-py3-none-any.whl')
    folder = ROOT / ('build/wheel-venv-' + __version__)
    subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(folder)], check=True)
    python = folder / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    with (ROOT / "build/wheel-install.log").open("w", encoding="utf-8") as log:
        subprocess.run([str(python), "-m", "pip", "install", "--no-index", "--no-deps", "--ignore-installed", str(wheel)],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    output = ROOT / ('evidence/wheel-app-' + __version__)
    code = ("from pathlib import Path;import sys,ds100;from ds100.smoke import main;"
            "assert Path(ds100.__file__).resolve().is_relative_to(Path(sys.prefix).resolve());"
            "raise SystemExit(main(Path(sys.argv[1])))")
    subprocess.run([str(python), "-c", code, str(output)], cwd=ROOT / "build", check=True, timeout=40)
    font = folder / "share/ds100-remote/fonts/NotoSansCJKsc-Regular.otf"
    receipt = json.loads((output / "receipt.json").read_text(encoding="utf-8"))
    receipt["installed_font_sha256"] = hashlib.sha256(font.read_bytes()).hexdigest()
    assert receipt["success"] and receipt["font_family"] == "Noto Sans CJK SC"
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
