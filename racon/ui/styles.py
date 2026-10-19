"""Shared styles and utility widgets for RACoN Lab UI."""
from __future__ import annotations

DARK_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
    font-family: 'Segoe UI', system-ui, sans-serif;
    font-size: 13px;
}
QTabWidget::pane {
    border: 1px solid #45475a;
    border-radius: 4px;
    background-color: #1e1e2e;
}
QTabBar::tab {
    background-color: #313244;
    color: #cdd6f4;
    padding: 8px 20px;
    margin-right: 2px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}
QTabBar::tab:selected {
    background-color: #45475a;
    color: #cba6f7;
    font-weight: bold;
}
QTabBar::tab:hover {
    background-color: #585b70;
}
QPushButton {
    background-color: #45475a;
    color: #cdd6f4;
    border: 1px solid #585b70;
    border-radius: 6px;
    padding: 6px 16px;
    font-weight: 500;
    min-height: 28px;
}
QPushButton:hover {
    background-color: #585b70;
    border-color: #cba6f7;
}
QPushButton:pressed {
    background-color: #6c7086;
}
QPushButton:disabled {
    background-color: #313244;
    color: #585b70;
    border-color: #45475a;
}
QPushButton#primary {
    background-color: #cba6f7;
    color: #1e1e2e;
    border-color: #cba6f7;
    font-weight: bold;
}
QPushButton#primary:hover {
    background-color: #b4befe;
}
QPushButton#primary:disabled {
    background-color: #585b70;
    color: #45475a;
}
QPushButton#danger {
    background-color: #f38ba8;
    color: #1e1e2e;
    border-color: #f38ba8;
}
QPushButton#danger:hover {
    background-color: #eba0ac;
}
QComboBox {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 5px 10px;
    min-height: 26px;
}
QComboBox:hover {
    border-color: #cba6f7;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox QAbstractItemView {
    background-color: #313244;
    color: #cdd6f4;
    selection-background-color: #45475a;
    border: 1px solid #585b70;
}
QGroupBox {
    font-weight: bold;
    color: #cba6f7;
    border: 1px solid #45475a;
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 16px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}
QLabel {
    color: #cdd6f4;
}
QLabel#heading {
    font-size: 16px;
    font-weight: bold;
    color: #cba6f7;
}
QLabel#metric-value {
    font-size: 20px;
    font-weight: bold;
    color: #a6e3a1;
}
QLabel#metric-label {
    font-size: 11px;
    color: #a6adc8;
}
QLabel#status-good {
    color: #a6e3a1;
    font-weight: bold;
}
QLabel#status-warn {
    color: #f9e2af;
    font-weight: bold;
}
QLabel#status-bad {
    color: #f38ba8;
    font-weight: bold;
}
QLabel#simulated {
    color: #f9e2af;
    font-style: italic;
}
QTableWidget {
    background-color: #181825;
    alternate-background-color: #1e1e2e;
    color: #cdd6f4;
    gridline-color: #313244;
    border: 1px solid #45475a;
    border-radius: 4px;
    selection-background-color: #45475a;
}
QTableWidget::item {
    padding: 4px 8px;
}
QHeaderView::section {
    background-color: #313244;
    color: #cba6f7;
    padding: 6px 8px;
    border: none;
    border-right: 1px solid #45475a;
    border-bottom: 1px solid #45475a;
    font-weight: bold;
}
QScrollBar:vertical {
    background-color: #1e1e2e;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background-color: #45475a;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background-color: #585b70;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QProgressBar {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 4px;
    text-align: center;
    color: #cdd6f4;
    min-height: 18px;
}
QProgressBar::chunk {
    background-color: #cba6f7;
    border-radius: 3px;
}
QTextEdit, QPlainTextEdit {
    background-color: #181825;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 4px;
}
QStatusBar {
    background-color: #181825;
    color: #a6adc8;
    border-top: 1px solid #313244;
}
QMessageBox {
    background-color: #1e1e2e;
}
QMessageBox QLabel {
    color: #cdd6f4;
}
"""
