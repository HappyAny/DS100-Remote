"""Check tutorial paths, receipts and cleanup with no media encoder or device."""

import importlib.util
import json
from collections import deque
from pathlib import Path
import subprocess
import sys
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import Mock
import wave

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def render(monkeypatch):
    # OpenCV is optional for the media tool; these tests perform no encoding.
    cv2 = SimpleNamespace(COLOR_RGB2BGR=1, cvtColor=lambda array, code: array, imwrite=Mock())
    monkeypatch.setitem(sys.modules, "cv2", cv2)
    return load_tool("render_tutorial")


def test_external_capture_is_rejected_before_output_or_encoding(monkeypatch, tmp_path, capsys, render):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(render, "ROOT", project)
    output = project / "output"
    monkeypatch.setattr(sys, "argv", ["render_tutorial", "--capture", str(project / ".." / "outside"), "--output", str(output)])
    run, popen = Mock(), Mock()
    monkeypatch.setattr(render.subprocess, "run", run)
    monkeypatch.setattr(render.subprocess, "Popen", popen)
    with pytest.raises(SystemExit) as error:
        render.main()
    assert error.value.code == 2
    assert "project capture directory" in capsys.readouterr().err
    assert not output.exists()
    run.assert_not_called()
    popen.assert_not_called()


def test_render_uses_capture_version_and_normalizes_only_final_audio(monkeypatch, tmp_path, render):
    monkeypatch.setattr(render, "ROOT", tmp_path)
    monkeypatch.setattr(render, "FPS", 1)
    capture = tmp_path / "capture"
    capture.mkdir()
    receipt = {"success": True, "physical_device": True, "settings_restored": True,
               "client": "0.5.5.dev1", "scenes": [], "resyncs": 0}
    (capture / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    media = tmp_path / "media"
    media.mkdir()
    (media / "tutorial-plan.json").write_text(json.dumps({
        "scenes": [{"id": "card", "kind": "card", "seconds": 0, "lines": [], "title": "Example"}],
        "voice_disclosure": "Test", "warning": "Test warning",
    }), encoding="utf-8")
    audio = tmp_path / "build/tutorial-audio"
    audio.mkdir(parents=True)
    (audio / "chunks.json").write_text(json.dumps({"chunks": [{"scene": "card", "file": "one.wav", "text": ""}]}), encoding="utf-8")
    with wave.open(str(audio / "one.wav"), "wb") as source:
        source.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        source.writeframes(b"\0\0")
    labels = []
    monkeypatch.setattr(render, "text", lambda draw, xy, value, *args: labels.append(value))
    encoder = SimpleNamespace(stdin=SimpleNamespace(write=Mock(), close=Mock()), wait=Mock(return_value=0))
    monkeypatch.setattr(render.subprocess, "Popen", Mock(return_value=encoder))
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"software-only encoded output")

    monkeypatch.setattr(render.subprocess, "run", run)
    output = tmp_path / "output"
    monkeypatch.setattr(sys, "argv", ["render_tutorial", "--capture", str(capture), "--output", str(output)])
    render.main()
    assert "DS100 REMOTE  /  R11 + 0.5.5.dev1" in labels
    assert len(commands) == 2
    assert commands[0][-1] == str(tmp_path / "build/tutorial-render/concatenated.mp4")
    assert commands[0][commands[0].index("-c") + 1] == "copy"
    final = commands[1]
    assert final[final.index("-i") + 1] == commands[0][-1]
    assert final[final.index("-c:v") + 1] == "copy"
    assert final[final.index("-af") + 1] == "loudnorm=I=-16:TP=-1.5:LRA=9"
    assert final[final.index("-ar") + 1] == "48000"
    assert final[final.index("-c:a") + 1] == "aac"
    assert final[final.index("-b:a") + 1] == "160k"
    report = json.loads((output / "video-manifest.json").read_text(encoding="utf-8"))
    assert report["physical_capture_receipt"] == str(Path("capture/receipt.json"))


def recording_window():
    window = Mock()
    window.client, window.worker = None, None
    window.action_lock = Lock()
    window.action_pending = "queued action"
    window.pending_motion_stop = Event()
    window.queued_actions = deque(["another action"])
    window.mirror_stop = Event()
    window.close.return_value = True
    return window


@pytest.mark.parametrize("failure", ["close", None])
def test_encoder_cleanup_always_closes_window(failure):
    record = load_tool("record_tutorial")
    window, app, timer = recording_window(), Mock(), Mock()
    sink = SimpleNamespace(stdin=Mock(), wait=Mock(return_value=0))
    if failure == "close":
        sink.stdin.close.side_effect = BrokenPipeError("encoder pipe closed")
    if failure:
        with pytest.raises(BrokenPipeError):
            record.close_recording(window, app, timer, sink)
    else:
        record.close_recording(window, app, timer, sink)
    timer.stop.assert_called_once_with()
    sink.wait.assert_called_once_with(timeout=20)
    window.close.assert_called_once_with()
    app.processEvents.assert_called_once_with()


@pytest.mark.parametrize("needs_kill", [False, True])
def test_encoder_timeout_is_terminated_and_reaped_before_window_cleanup(needs_kill):
    record = load_tool("record_tutorial")
    window, app, timer = recording_window(), Mock(), Mock()
    outcomes = [subprocess.TimeoutExpired("encoder", 20)]
    if needs_kill:
        outcomes.append(subprocess.TimeoutExpired("encoder", 5))
    outcomes.append(0)

    def wait(**kwargs):
        # No mirror, discovery, input, or queued action can run during a slow
        # encoder shutdown. This check uses no Qt event loop or real process.
        timer.stop.assert_called_once_with()
        assert window.action_pending is None
        assert not window.queued_actions
        assert window.mirror_stop.is_set()
        window.setEnabled.assert_called_with(False)
        for action_timer in (window.timer, window.discovery_timer, window.input_timer):
            action_timer.stop.assert_called()
            action_timer.blockSignals.assert_called_with(True)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    sink = SimpleNamespace(stdin=Mock(), wait=Mock(side_effect=wait), terminate=Mock(), kill=Mock())
    with pytest.raises(subprocess.TimeoutExpired):
        record.close_recording(window, app, timer, sink)
    sink.terminate.assert_called_once_with()
    assert sink.wait.call_count == (3 if needs_kill else 2)
    assert [call.kwargs["timeout"] for call in sink.wait.call_args_list] == ([20, 5, 5] if needs_kill else [20, 5])
    if needs_kill:
        sink.kill.assert_called_once_with()
    else:
        sink.kill.assert_not_called()
    assert not outcomes
    window.close.assert_called_once_with()


def test_rejected_close_waits_for_worker_and_retries_without_new_actions():
    record = load_tool("record_tutorial")
    window, app = recording_window(), Mock()
    worker = Mock()
    worker.isRunning.return_value = True
    window.worker = worker
    client = Mock()
    window.client = client
    wait_results = iter([False, False, True])

    def wait(milliseconds):
        finished = next(wait_results)
        worker.isRunning.return_value = not finished
        return finished

    worker.wait.side_effect = wait

    def close():
        assert window.action_pending is None
        assert not window.queued_actions
        assert window.mirror_stop.is_set()
        if worker.isRunning() and not worker.wait(3000):
            # Match MainWindow.closeEvent's rejected-close behavior.
            window.discovery_timer.start()
            window.input_timer.start()
            return False
        client.close()
        window.client = None
        return True

    def process_events():
        # A rejected close restarted the timers; cleanup stopped and blocked
        # them again before it dispatched any queued Qt events.
        for action_timer in (window.discovery_timer, window.input_timer):
            action_timer.stop.assert_called()
            action_timer.blockSignals.assert_called_with(True)

    window.close.side_effect = close
    app.processEvents.side_effect = process_events
    record.close_recording(window, app, Mock(), None)
    assert window.close.call_count == 2
    assert [call.args[0] for call in worker.wait.call_args_list] == [3000, 1000, 3000]
    worker.requestInterruption.assert_called()
    worker.terminate.assert_not_called()
    client.close.assert_called_once_with()
    assert window.client is None


def test_window_refusal_is_bounded_and_does_not_terminate_the_worker():
    record = load_tool("record_tutorial")
    window, app = recording_window(), Mock()
    worker = Mock()
    worker.wait.return_value = False
    window.worker = worker
    client = Mock()
    window.client = client

    def close():
        worker.wait(3000)
        return False

    window.close.side_effect = close
    with pytest.raises(TimeoutError, match="refused to close"):
        record.close_recording(window, app, None, None)
    assert window.close.call_count == 5
    assert sum(call.args[0] for call in worker.wait.call_args_list) == 19000
    worker.requestInterruption.assert_called()
    worker.terminate.assert_not_called()
    client.close.assert_not_called()


def test_info_failure_closes_acquired_client_and_window(monkeypatch, tmp_path):
    record = load_tool("record_tutorial")
    monkeypatch.setattr(record, "ROOT", tmp_path)
    monkeypatch.setattr(record, "QApplication", Mock(return_value=Mock()))
    window = recording_window()
    monkeypatch.setattr(record, "MainWindow", Mock(return_value=window))
    monkeypatch.setattr(record, "find_drives", lambda: [Path("Q:/")])
    monkeypatch.setattr(record, "ScsiTransport", Mock())
    client = Mock()
    client.info.side_effect = OSError("software-only info failure")
    monkeypatch.setattr(record, "Client", Mock(return_value=client))
    monkeypatch.setattr(sys, "argv", ["record_tutorial", "--output", str(tmp_path / "capture")])
    with pytest.raises(OSError, match="software-only info failure"):
        record.main()
    client.close.assert_called_once_with()
    window.close.assert_called_once_with()
    window.experimental_connected.assert_not_called()


def test_recording_receipt_uses_imported_app_version_without_a_real_window(monkeypatch, tmp_path):
    record = load_tool("record_tutorial")
    monkeypatch.setattr(record, "ROOT", tmp_path)
    monkeypatch.setattr(record, "__version__", "future-test-version")
    monkeypatch.setattr(record, "QApplication", Mock(return_value=Mock()))
    window = recording_window()
    window.client, window.mirror_count = None, 0
    window.status.text.return_value = "software-only stop"
    monkeypatch.setattr(record, "MainWindow", Mock(return_value=window))
    monkeypatch.setattr(record, "QTimer", Mock(return_value=Mock()))
    monkeypatch.setattr(record, "QTest", SimpleNamespace(qWait=lambda ms: None))
    monkeypatch.setattr(record, "find_drives", lambda: [Path("Q:/")])
    monkeypatch.setattr(record, "ScsiTransport", Mock())
    client = Mock()
    client.info.return_value = {"build": "background-r11-input-ack"}
    monkeypatch.setattr(record, "Client", Mock(return_value=client))
    output = tmp_path / "capture"
    monkeypatch.setattr(sys, "argv", ["record_tutorial", "--output", str(output)])
    with pytest.raises(RuntimeError, match="software-only stop"):
        record.main()
    receipt = json.loads((output / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["client"] == "future-test-version"
    window.close.assert_called_once_with()
