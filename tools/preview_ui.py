"""Save desktop previews without connecting to a physical device."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ['DS100_AUTO_CONNECT'] = '0'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--show', action='store_true', help='Keep an offline demo window open.')
    args = parser.parse_args()
    if not args.show:
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from ds100.app import MainWindow
    from ds100 import __version__

    output = ROOT / 'evidence/ui-compact'
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    window = MainWindow()
    window.discovery_timer.stop()
    window.show()
    captures = []

    def capture(name, size):
        window.resize(*size)
        app.processEvents()
        QTest.qWait(60)
        assert window.grab().save(str(output / (name + '.png')))
        captures.append({'file': name + '.png', 'window': [window.width(), window.height()],
                         'screen_widget': [window.screen_label.width(), window.screen_label.height()]})

    capture('disconnected', (736, 552))
    capture('disconnected-minimum', (640, 510))
    window.source.setCurrentIndex(2)
    window.connect_now()
    deadline = time.monotonic() + 15
    while window.worker is not None and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    if window.worker is not None or window.last_image is None:
        raise RuntimeError('The offline preview did not finish.')
    capture('demo', (736, 552))
    capture('demo-minimum', (640, 510))
    window.set_raw_controls(False)
    capture('quick-actions', (736, 552))
    capture('quick-actions-minimum', (640, 510))
    window.set_raw_controls(True)
    window.read_wave()
    while window.worker is not None and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    capture('waveform', (736, 552))
    window.tabs.setCurrentIndex(2)
    capture('files', (736, 552))
    # This is an existing saved capture, not a new hardware connection.
    saved = ROOT / 'evidence/live-device/r3-background-live/screen.png'
    if saved.is_file():
        window.source.setCurrentIndex(0)
        window.open_file(saved)
        capture('saved-device-screen', (736, 552))
    receipt = {'created_utc': datetime.now(timezone.utc).isoformat(), 'version': __version__,
               'physical_device_test': False, 'automatic_device_connection': False,
               'demo_is_generated_test_data': True, 'captures': captures}
    (output / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    if args.show:
        window.source.setCurrentIndex(2)
        window.connect_now()
        return app.exec()
    window.close()
    app.processEvents()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
