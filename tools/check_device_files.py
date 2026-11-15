"""Open saved DS100 files in the desktop and save a validation receipt."""

from pathlib import Path
import argparse
import hashlib
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import numpy as np
from PySide6.QtWidgets import QApplication
from ds100.app import MainWindow
from ds100.storage import load_csv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="A directory with copied device CSV and BMP files.")
    args = parser.parse_args()
    output = ROOT / "evidence/software-tests"
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.processEvents()
    receipt = {"source": "saved files from a physical DS100; not live remote data", "files": []}
    for path in sorted(args.input.iterdir()):
        suffix = path.suffix.lower()
        if suffix not in (".csv", ".bmp"):
            continue
        item = {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        window.open_file(path)
        app.processEvents()
        if suffix == ".csv":
            x, channels, meta = load_csv(path)
            assert len(window.plot.x) == len(x)
            assert set(window.plot.channels) == set(channels)
            assert np.array_equal(window.plot.x, x)
            item.update(metadata=meta, time_start_s=float(x[0]), time_last_s=float(x[-1]),
                        channels={str(ch): {"min_v": float(np.min(y)), "max_v": float(np.max(y))}
                                  for ch, y in channels.items()})
            assert window.grab().save(str(output / "device-file-waveform.png"))
        else:
            assert window.last_image is not None and not window.last_image.isNull()
            item["image_size"] = [window.last_image.width(), window.last_image.height()]
            assert window.last_image.save(str(output / "device-saved-screen.png"))
            assert window.grab().save(str(output / "device-file-screen.png"))
        assert not window.run_button.isEnabled() and not window.screen_button.isEnabled()
        receipt["files"].append(item)
    if {Path(item["name"]).suffix.lower() for item in receipt["files"]} != {".csv", ".bmp"}:
        raise ValueError("The check requires both a CSV waveform and a BMP screenshot.")
    window.close()
    app.processEvents()
    (output / "device-files.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
