"""Check the installed application and font resources without a physical device."""

from pathlib import Path
import hashlib
import json
import os
import sys
import time


def main(output):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ['DS100_AUTO_CONNECT'] = '0'
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QRawFont
    from PySide6.QtWidgets import QApplication
    from .app import MainWindow

    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.processEvents()
    window.source.setCurrentIndex(2)
    window.connect_now()
    deadline = time.monotonic() + 15
    result = {"frozen": bool(getattr(sys, "frozen", False)), "physical_device_test": False}
    timer = QTimer()

    def check():
        if window.worker is not None and time.monotonic() < deadline:
            return
        timer.stop()
        try:
            if window.worker is not None:
                raise RuntimeError("The application check exceeded the time limit.")
            font = QRawFont.fromFont(app.font())
            assert font.supportsCharacter(ord("示")) and font.supportsCharacter(ord("A"))
            assert window.info.get("source") == "simulator"
            assert window.last_image is not None and window.last_image.width() == 480
            assert window.screen_button.isEnabled()
            assert set(window.input_panel.holds) == set('PAME')
            assert set(window.input_panel.double_buttons) == set('PAME')
            assert window.grab().save(str(output / "application.png"))
            window.quick_toggle.setChecked(True)
            app.processEvents()
            assert window.quick_panel.isVisible()
            assert window.quick_toggle.isChecked() and window.quick_mode_action.isChecked()
            assert window.quick_panel.buttons['time_up'].isEnabled()
            assert window.grab().save(str(output / 'quick-actions.png'))
            result.update(success=True, font_family=app.font().family(), screen_size=[480, 320],
                          source=window.info["source"])
            if getattr(sys, "frozen", False):
                path = Path(sys._MEIPASS) / "assets/fonts/NotoSansCJKsc-Regular.otf"
                result["bundled_font_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        except Exception as error:
            result.update(success=False, error=str(error))
        finally:
            (output / "receipt.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            window.close()
            app.exit(0 if result.get("success") else 1)

    timer.timeout.connect(check)
    timer.start(50)
    return app.exec()
