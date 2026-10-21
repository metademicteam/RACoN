"""Unit tests for RACoN ChatPanel and updated main window tabs."""
import pytest
from PySide6.QtWidgets import QApplication

from racon.ui.chat_panel import ChatPanel
from racon.ui.main_window import MainWindow


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_chat_panel_initialization(qapp):
    panel = ChatPanel()
    assert panel is not None
    assert panel._model_combo.count() >= 3
    assert panel._max_tokens_spin.value() == 128
    assert panel._status_label.text() == "Ready"


def test_chat_panel_render_message(qapp):
    panel = ChatPanel()
    panel._append_message_html("user", "Hello from test")
    html_content = panel._chat_browser.toHtml()
    assert "Hello from test" in html_content

    panel._append_message_html("assistant", "Response from RACoN", {"eval_tps": 15.0, "duration_s": 0.5, "device": "Vulkan"})
    html_content2 = panel._chat_browser.toHtml()
    assert "Response from RACoN" in html_content2
    assert "15.0 t/s" in html_content2


def test_main_window_has_chat_tab(qapp):
    window = MainWindow()
    assert "RACoN v" in window.windowTitle()
    assert "Lab" not in window.windowTitle()

    # Verify Chat is Tab index 1
    tab_text = window._tabs.tabText(1)
    assert "Chat" in tab_text

    # Verify total tabs
    tab_titles = [window._tabs.tabText(i) for i in range(window._tabs.count())]
    assert any("Dashboard" in t for t in tab_titles)
    assert any("Chat" in t for t in tab_titles)
    assert any("Worker" in t for t in tab_titles)
    assert any("Scheduler" in t for t in tab_titles)
    assert any("History" in t for t in tab_titles)
    assert any("Settings" in t for t in tab_titles)

    window.close()
