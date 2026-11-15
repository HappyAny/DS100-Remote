"""Load a font from an explicit file when system font discovery is unavailable."""

from pathlib import Path
import sys
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

_loaded = False


def configure_fonts():
    global _loaded
    if _loaded:
        return
    root = Path(__file__).resolve().parents[2]
    bundle = Path(getattr(sys, "_MEIPASS", root))
    candidates = [bundle / "assets/fonts/NotoSansCJKsc-Regular.otf",
                  root / "assets/fonts/NotoSansCJKsc-Regular.otf",
                  Path(sys.prefix) / "share/ds100-remote/fonts/NotoSansCJKsc-Regular.otf",
                  Path("C:/Windows/Fonts/msyh.ttc")]
    for path in candidates:
        if not path.is_file():
            continue
        identifier = QFontDatabase.addApplicationFont(str(path))
        families = QFontDatabase.applicationFontFamilies(identifier) if identifier >= 0 else []
        if families:
            QApplication.setFont(QFont(families[0], 10))
            _loaded = True
            return
