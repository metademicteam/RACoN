"""The desktop shell must start in a console-less (windowed) frozen build.

PyInstaller ``console=False`` sets ``sys.stdout`` and ``sys.stderr`` to ``None``.
uvicorn's formatter constructor calls ``sys.stdout.isatty()``, so the engine thread used
to die with ``AttributeError`` and the product window silently fell back to the lab UI.
"""
import logging
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest


@pytest.fixture()
def clean_logging():
    """Restore the root logger after configure_logging(force=True) rewires it."""
    root = logging.getLogger()
    handlers = root.handlers[:]
    level = root.level
    configured = getattr(root, "_racon_configured", None)
    try:
        yield
    finally:
        for handler in root.handlers:
            if handler not in handlers:
                handler.close()
        root.handlers[:] = handlers
        root.setLevel(level)
        if configured is None:
            if hasattr(root, "_racon_configured"):
                delattr(root, "_racon_configured")
        else:
            setattr(root, "_racon_configured", configured)


def test_configure_logging_replaces_missing_std_streams(monkeypatch, tmp_path, clean_logging):
    from racon import logging_setup

    monkeypatch.setattr(logging_setup, "get_log_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    log_file = logging_setup.configure_logging()

    assert sys.stdout is not None
    assert sys.stderr is not None
    assert sys.stdout.isatty() in (True, False)
    assert log_file == tmp_path / "racon.log"
    assert log_file.parent.is_dir()


def test_uvicorn_config_survives_windowed_streams(monkeypatch, tmp_path, clean_logging):
    from racon import logging_setup

    monkeypatch.setattr(logging_setup, "get_log_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    logging_setup.configure_logging()

    import uvicorn

    from backend.racon.api.app import app

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        log_config=None,
        log_level="warning",
        access_log=False,
        use_colors=False,
    )
    assert config.port == 0
    assert config.access_log is False


def test_logging_writes_diagnostics_to_the_file(monkeypatch, tmp_path, clean_logging):
    from racon import logging_setup

    monkeypatch.setattr(logging_setup, "get_log_dir", lambda: tmp_path)
    log_file = logging_setup.configure_logging()

    logging.getLogger("racon.test").info("diagnostic-marker")

    content = log_file.read_text(encoding="utf-8")
    assert "diagnostic-marker" in content


def test_open_window_reports_detached_browser(monkeypatch):
    """The default-browser fallback cannot be waited on, so it must be supervised."""
    from racon import desktop

    monkeypatch.setitem(sys.modules, "webview", None)
    monkeypatch.setattr(desktop, "_browser_app_command", lambda url: None)
    monkeypatch.setattr("webbrowser.open", lambda url: True)

    assert desktop.open_window("http://127.0.0.1:9/") is True


def test_open_window_fails_when_no_browser_can_open(monkeypatch):
    from racon import desktop

    monkeypatch.setitem(sys.modules, "webview", None)
    monkeypatch.setattr(desktop, "_browser_app_command", lambda url: None)
    monkeypatch.setattr("webbrowser.open", lambda url: False)

    with pytest.raises(RuntimeError, match="Could not open a browser"):
        desktop.open_window("http://127.0.0.1:9/")


def test_browser_app_window_handoff_is_supervised(monkeypatch):
    """Chrome/Chromium hands --app windows to an existing instance and exits.

    The launch process exiting is not the end of the UI session: the engine must stay
    up until the app window stops polling it.
    """
    from racon import desktop

    monkeypatch.setitem(sys.modules, "webview", None)
    monkeypatch.setattr(desktop, "_browser_app_command", lambda url: ["chrome", f"--app={url}"])

    class HandoffProcess:
        def wait(self) -> int:
            return 0

    monkeypatch.setattr(desktop.subprocess, "Popen", lambda command: HandoffProcess())

    assert desktop.open_window("http://127.0.0.1:9/") is True


def test_browser_app_window_owns_the_session_until_it_closes(monkeypatch):
    from racon import desktop

    monkeypatch.setitem(sys.modules, "webview", None)
    monkeypatch.setattr(desktop, "_browser_app_command", lambda url: ["chrome", f"--app={url}"])

    class WindowProcess:
        def wait(self) -> int:
            time.sleep(0.1)
            return 0

    monkeypatch.setattr(desktop.subprocess, "Popen", lambda command: WindowProcess())
    monkeypatch.setattr(desktop, "BROWSER_HANDOFF_SECONDS", 0.05)

    assert desktop.open_window("http://127.0.0.1:9/") is False


def test_detached_browser_keeps_the_engine_reachable(monkeypatch, tmp_path, clean_logging):
    """A browser tab must stay connected after webbrowser.open() returns.

    webbrowser.open() returns immediately, unlike the webview and Chromium --app paths.
    Stopping the engine at that point makes the browser land on a closed port, which is
    the Linux-only "This site can't be reached" failure.
    """
    from racon import desktop

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>RACoN</body></html>", encoding="utf-8")

    monkeypatch.setattr(desktop, "find_dist_dir", lambda: dist)
    monkeypatch.setattr(desktop, "resolve_token", lambda: "test-token")
    monkeypatch.setattr(desktop, "UI_FIRST_REQUEST_TIMEOUT", 5.0)
    monkeypatch.setattr(desktop, "UI_IDLE_TIMEOUT", 1.0)

    browsing = threading.Event()
    closed = threading.Event()
    engine_url: dict[str, str] = {}

    def fake_open_window(url: str) -> bool:
        engine_url["base"] = url.split("?")[0].rstrip("/")

        def browse() -> None:
            while not closed.is_set():
                try:
                    urllib.request.urlopen(f"{engine_url['base']}/", timeout=0.5)
                    browsing.set()
                except urllib.error.HTTPError:
                    browsing.set()
                except Exception:
                    pass
                time.sleep(0.2)

        threading.Thread(target=browse, daemon=True).start()
        return True

    monkeypatch.setattr(desktop, "open_window", fake_open_window)

    result: dict[str, int] = {}
    runner = threading.Thread(target=lambda: result.setdefault("code", desktop.main([])), daemon=True)
    runner.start()

    assert browsing.wait(15.0), "the browser never received a response from the engine"

    time.sleep(0.6)
    with urllib.request.urlopen(f"{engine_url['base']}/health", timeout=1.0) as response:
        assert response.status == 200

    closed.set()
    runner.join(10.0)
    assert not runner.is_alive(), "the shell did not exit after the UI went away"
    assert result["code"] == 0
