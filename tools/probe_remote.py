"""Check the physical DS100 screen service and optionally open the desktop."""

from pathlib import Path
from datetime import datetime, timezone
import argparse
import ctypes
import hashlib
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ds100.protocol import Client, SCREEN_READ, parse_tile
from ds100.storage import find_drives
from ds100.windows_scsi import ScsiTransport


def save_receipt(path, receipt):
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def save_pixels(path, pixels):
    import numpy as np
    from PIL import Image
    values = np.asarray(pixels, dtype=np.uint16)
    rgb = np.stack([((values >> 11) & 31) * 255 // 31,
                    ((values >> 5) & 63) * 255 // 63, (values & 31) * 255 // 31], axis=-1).astype(np.uint8)
    Image.fromarray(rgb).save(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=["info", "tile", "screen"], default="screen")
    parser.add_argument("--show-app", action="store_true")
    parser.add_argument("--exercise-menu", action="store_true", help="Check two M/E cycles in the visible physical-device window.")
    parser.add_argument("--expect-build")
    args = parser.parse_args()
    if args.exercise_menu and not args.show_app:
        parser.error('The menu exercise requires the visible desktop.')
    output = args.output.resolve()
    if not output.is_relative_to(ROOT):
        parser.error("The result directory must be inside the project.")
    output.mkdir(parents=True, exist_ok=True)
    report = output / "receipt.json"
    receipt = {"started_utc": datetime.now(timezone.utc).isoformat(), "physical_device_test": True,
               "administrator": bool(ctypes.windll.shell32.IsUserAnAdmin()), "success": False,
               "phase": "opening-device", "mode": args.mode, "requests": 0}
    save_receipt(report, receipt)
    client, pixels, info = None, None, None
    elapsed = None

    class AuditedTransport(ScsiTransport):
        def exchange(self, cdb):
            started = time.monotonic()
            response = super().exchange(cdb)
            receipt["requests"] += 1
            if not getattr(self, "audit_enabled", True):
                return response
            item = {"command": cdb[1], "cdb_hex": cdb.hex(), "seconds": time.monotonic() - started,
                    "bytes_returned": len(response), "sha256": hashlib.sha256(response).hexdigest()}
            with (output / "requests.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(item) + "\n")
            if receipt["requests"] <= 2:
                (output / f"response-{receipt['requests']}.bin").write_bytes(response)
            if receipt["requests"] % 16 == 0:
                save_receipt(report, receipt)
            return response

    try:
        drives = find_drives()
        if len(drives) != 1:
            raise OSError("Connect one ATK-DSO volume and close other device clients.")
        receipt["volume"] = str(drives[0])
        client = Client(AuditedTransport(drives[0]))
        receipt["phase"] = "information"
        save_receipt(report, receipt)
        info = client.info()
        if info.get("source") != "experimental-v48" or info.get("software") != "4.8":
            raise ValueError("The response is not from the expected physical V4.8 experiment.")
        receipt["info"] = info
        save_receipt(report, receipt)
        if args.expect_build and info.get("build") != args.expect_build:
            raise ValueError(f"The running build is {info.get('build', 'unidentified')}; expected {args.expect_build}.")
        if args.mode != "info":
            receipt["phase"] = "small-screen-tile"
            tile = parse_tile(client.request(SCREEN_READ, (0, 0, 8, 8)))
            import numpy as np
            tile_pixels = np.frombuffer(tile.pixels, dtype="<u2").reshape(8, 8)
            save_pixels(output / "tile.png", tile_pixels)
            receipt["tile"] = {"width": 8, "height": 8, "rgb565_sha256": hashlib.sha256(tile.pixels).hexdigest()}
            save_receipt(report, receipt)
        if args.mode == "screen":
            receipt["phase"] = "full-screen-scan"
            started = time.monotonic()
            pixels = client.screen(cancelled=lambda: time.monotonic() - started > 120)
            elapsed = time.monotonic() - started
            save_pixels(output / "screen.png", pixels)
            np.save(output / "screen-rgb565.npy", pixels)
            receipt["screen"] = {"size": [480, 320], "seconds": elapsed,
                                 "unique_colors": int(np.unique(pixels).size),
                                 "rgb565_sha256": hashlib.sha256(pixels.tobytes()).hexdigest()}
            receipt['client_screen_mode'] = client.screen_mode
        receipt.update(success=True, phase="checks-passed", finished_utc=datetime.now(timezone.utc).isoformat())
        save_receipt(report, receipt)
    except Exception as error:
        receipt.update(error_type=type(error).__name__, error=str(error), errno=getattr(error, "errno", None))
        save_receipt(report, receipt)
        if client:
            client.close()
        return 1
    if args.show_app:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from ds100.app import MainWindow
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.source.setCurrentIndex(1)
        window.drive = drives[0]
        window.experimental_connected((client, info))
        window.update_controls()
        if pixels is not None:
            window.show_pixels(pixels, elapsed)
        window.show()
        app.processEvents()
        window.grab().save(str(output / "application.png"))
        client.transport.audit_enabled = False
        window.mirror.setChecked(True)
        receipt["desktop_opened"] = True
        receipt["phase"] = "desktop-live"
        save_receipt(report, receipt)
        observer = QTimer(window)
        last_saved_frame = [-1]
        observed_start = time.monotonic()
        initial_counts = (window.mirror_count, window.waveform_change_count,
                          window.screen_label.painted_frames, window.screen_label.painted_waveform_frames)

        def observe():
            receipt.update(mirror_frames=window.mirror_count, mirror_active=window.mirror.isChecked(),
                           desktop_status=window.status.text(), screen_note=window.screen_note.text())
            receipt.update(changed_frames=window.changed_mirror_count,
                           waveform_changed_frames=window.waveform_change_count,
                           painted_frames=window.screen_label.painted_frames,
                           painted_changed_frames=window.screen_label.painted_changed_frames,
                           painted_waveform_frames=window.screen_label.painted_waveform_frames,
                           client_screen_mode=client.screen_mode)
            receipt['planned_menu_baselines'] = client.menu_resets
            receipt['drawing_resyncs'] = client.draw_session.resyncs if client.draw_session else 0
            seconds = time.monotonic() - observed_start
            counts = (window.mirror_count, window.waveform_change_count,
                      window.screen_label.painted_frames, window.screen_label.painted_waveform_frames)
            rates = [(count - first) / seconds for count, first in zip(counts, initial_counts)]
            receipt.update(observation_seconds=seconds, delivered_fps=rates[0],
                           waveform_changed_fps=rates[1], painted_fps=rates[2],
                           painted_waveform_changed_fps=rates[3],
                           actual_30_fps_observed=seconds >= 10 and rates[3] >= 30)
            if window.last_image is not None and window.mirror_count != last_saved_frame[0]:
                window.last_image.save(str(output / "latest-screen.png"))
                last_saved_frame[0] = window.mirror_count
            save_receipt(report, receipt)

        observer.timeout.connect(observe)
        observer.start(1000)
        if args.exercise_menu:
            from PySide6.QtCore import Qt
            from PySide6.QtTest import QTest
            receipt['menu_exercise'] = {'scope': 'Two visible desktop M/E cycles; native USB input, not simulator.',
                                        'events': [], 'complete': False}

            def visible_menu():
                from PySide6.QtGui import QImage
                image = window.screen_label.image.convertToFormat(QImage.Format_RGB16)
                pixels = np.frombuffer(image.constBits(), dtype='<u2').reshape(320,480)
                p = pixels[20:285,70:330]
                return int(np.count_nonzero(((p>>11)>15)&(((p>>5)&63)<8)&((p&31)>20)))>2000

            def click_key(key):
                enabled = window.input_panel.available and window.input_panel.key_buttons[key].isEnabled()
                event = {'key': key, 'seconds': time.monotonic() - observed_start,
                         'enabled': enabled, 'frames_before': window.mirror_count,
                         'status': window.status.text()}
                receipt['menu_exercise']['events'].append(event)
                if enabled:
                    QTest.mouseClick(window.input_panel.key_buttons[key], Qt.LeftButton)
                    def verify_menu():
                        event['menu_visible_after'] = visible_menu()
                        event['expected_menu_visible'] = key == 'M'
                        event['actual_state_matches'] = event['menu_visible_after'] == event['expected_menu_visible']
                        save_receipt(report, receipt)
                    QTimer.singleShot(2000, verify_menu)
                save_receipt(report, receipt)

            for seconds, key in ((8, 'M'), (14, 'E'), (20, 'M'), (26, 'E')):
                QTimer.singleShot(seconds * 1000, lambda key=key: click_key(key))

            def finish_menu_exercise():
                exercise = receipt['menu_exercise']
                exercise.update(complete=True, mirror_active=window.mirror.isChecked(),
                                frames_after=window.mirror_count, planned_menu_baselines=client.menu_resets,
                                all_controls_enabled=all(e['enabled'] for e in exercise['events']),
                                all_menu_states_match=all(e.get('actual_state_matches',False) for e in exercise['events']))
                window.grab().save(str(output / 'application-after-menu.png'))
                save_receipt(report, receipt)

            QTimer.singleShot(32000, finish_menu_exercise)
        return app.exec()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
