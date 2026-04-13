"""PolicyDB Desktop — PySide6 entry point."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from policydb import db
from policydb import config as cfg
from policydb.queries import get_db_stats

from policydb_desktop import __version__
from policydb_desktop.theme import load_stylesheet
from policydb_desktop.theme.colors import (
    ACCENT,
    ACCENT_MUTED,
    ACCENT_TEXT,
    BG_BASE,
    BG_ELEVATED,
    BG_INSET,
    BG_SURFACE,
    BORDER_DEFAULT,
    BORDER_SUBTLE,
    SIDEBAR_ACTIVE,
    SIDEBAR_BADGE_BG,
    SIDEBAR_BADGE_TEXT,
    STATUS_DANGER,
    STATUS_WARNING,
    TEXT_DIM,
    TEXT_FAINT,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)


class MetricCard(QFrame):
    """Metric display card — dark surface with indigo label."""

    def __init__(self, label: str, value: str, detail: str = "", detail_color: str = TEXT_DIM, parent: QWidget | None = None):
        super().__init__(parent)
        self.setProperty("card", True)
        self.setMinimumWidth(140)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(4)

        lbl = QLabel(label.upper())
        lbl.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 10pt; font-weight: 500; "
            f"letter-spacing: 0.5px;"
        )
        layout.addWidget(lbl)

        val = QLabel(value)
        val.setStyleSheet(
            f"font-size: 28pt; color: {TEXT_PRIMARY}; font-weight: 700; "
            f"letter-spacing: -0.5px;"
        )
        layout.addWidget(val)

        if detail:
            det = QLabel(detail)
            det.setStyleSheet(f"color: {detail_color}; font-size: 10pt;")
            layout.addWidget(det)

        layout.addStretch()


class NavButton(QPushButton):
    """Sidebar navigation button."""

    def __init__(self, text: str, active: bool = False, badge: int = 0, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("navButton")
        self.setText(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("active", active)
        self._badge = badge
        if active:
            self.setStyleSheet(
                f"background: {BG_ELEVATED}; color: {TEXT_PRIMARY}; "
                f"border: none; border-radius: 6px; padding: 7px 10px; "
                f"text-align: left; font-size: 10pt; font-weight: 500;"
            )
        else:
            self.setStyleSheet(
                f"background: transparent; color: {TEXT_MUTED}; "
                f"border: none; border-radius: 6px; padding: 7px 10px; "
                f"text-align: left; font-size: 10pt;"
            )


class LaunchScreen(QWidget):
    """Dashboard-style launch screen showing real DB data."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(f"background: {BG_BASE};")

        conn = db.get_connection()
        stats = get_db_stats(conn)
        conn.close()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Header row
        header = QHBoxLayout()
        title = QLabel("Dashboard")
        title.setStyleSheet(
            f"font-size: 20pt; font-weight: 600; color: {TEXT_PRIMARY}; "
            f"letter-spacing: -0.4px;"
        )
        header.addWidget(title)
        header.addStretch()

        # Search bar placeholder
        search = QFrame()
        search.setFixedSize(220, 32)
        search.setStyleSheet(
            f"background: {BG_SURFACE}; border: 1px solid {BORDER_DEFAULT}; "
            f"border-radius: 8px;"
        )
        search_layout = QHBoxLayout(search)
        search_layout.setContentsMargins(10, 0, 10, 0)
        search_layout.setSpacing(6)
        cmd_k = QLabel("\u2318K")
        cmd_k.setStyleSheet(f"color: {TEXT_FAINT}; font-size: 11pt;")
        search_text = QLabel("Search...")
        search_text.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12pt;")
        search_layout.addWidget(cmd_k)
        search_layout.addWidget(search_text)
        search_layout.addStretch()
        header.addWidget(search)

        layout.addLayout(header)

        # Date subtitle
        from datetime import date
        today = date.today().strftime("%A, %B %-d, %Y")
        date_label = QLabel(today)
        date_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11pt;")
        layout.addWidget(date_label)

        # Metric cards row
        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(12)

        cards = [
            ("Clients", str(stats.get("clients", 0)), "", TEXT_DIM),
            ("Policies", str(stats.get("policies", 0)), "", TEXT_DIM),
            ("Activities", str(stats.get("activity_log", 0)), "", TEXT_DIM),
            ("Premium History", str(stats.get("premium_history", 0)), "", TEXT_DIM),
        ]

        for label, value, detail, color in cards:
            card = MetricCard(label, value, detail, color)
            metrics_row.addWidget(card)

        metrics_row.addStretch()
        layout.addLayout(metrics_row)

        # Config info
        config_info = QLabel(
            f"Account Exec: {cfg.get('default_account_exec', 'N/A')}  \u2022  "
            f"Carriers: {len(cfg.get('carriers', []))}  \u2022  "
            f"Policy Types: {len(cfg.get('policy_types', []))}"
        )
        config_info.setStyleSheet(f"color: {TEXT_DIM}; font-size: 10pt;")
        layout.addWidget(config_info)

        # Status banner
        status = QFrame()
        status.setStyleSheet(
            f"background: rgba(16, 185, 129, 0.1); "
            f"border: 1px solid rgba(16, 185, 129, 0.2); "
            f"border-radius: 8px;"
        )
        status_layout = QHBoxLayout(status)
        status_layout.setContentsMargins(12, 8, 12, 8)
        status_label = QLabel("\u2713  Shared core verified \u2014 database, config, and queries operational")
        status_label.setStyleSheet("color: #6ee7b7; font-size: 10pt;")
        status_layout.addWidget(status_label)
        layout.addWidget(status)

        layout.addStretch()


class MainWindow(QMainWindow):
    """Main application window — Slate Command dark theme."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PolicyDB Desktop")
        self.setMinimumSize(1024, 700)
        self.resize(1280, 800)

        # Central widget
        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Sidebar
        sidebar = QWidget()
        sidebar.setObjectName("navSidebar")
        sidebar.setFixedWidth(200)
        sidebar.setStyleSheet(
            f"background: {BG_SURFACE}; border-right: 1px solid {BORDER_DEFAULT};"
        )
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 20, 12, 16)
        sidebar_layout.setSpacing(1)

        # Logo
        logo = QLabel("PolicyDB")
        logo.setStyleSheet(
            f"font-size: 15pt; font-weight: 700; color: {TEXT_PRIMARY}; "
            f"padding: 0 8px 20px; letter-spacing: -0.3px;"
        )
        sidebar_layout.addWidget(logo)

        # Nav items
        nav_items = [
            ("Dashboard", True, 0),
            ("Clients", False, 0),
            ("Policies", False, 0),
            ("Action Center", False, 3),
            ("Reconcile", False, 0),
            ("Charts", False, 0),
            ("Knowledge Base", False, 0),
            ("Prompt Builder", False, 0),
            ("Programs", False, 0),
        ]
        for text, active, badge in nav_items:
            btn = NavButton(text, active=active, badge=badge)
            sidebar_layout.addWidget(btn)

        sidebar_layout.addStretch()

        # Bottom section
        separator = QFrame()
        separator.setFixedHeight(1)
        separator.setStyleSheet(f"background: {BORDER_DEFAULT};")
        sidebar_layout.addWidget(separator)

        settings_btn = NavButton("Settings")
        settings_btn.setStyleSheet(
            f"background: transparent; color: {TEXT_DIM}; "
            f"border: none; border-radius: 6px; padding: 7px 10px; "
            f"text-align: left; font-size: 10pt;"
        )
        sidebar_layout.addWidget(settings_btn)

        version = QLabel(f"v{__version__}")
        version.setStyleSheet(f"color: {TEXT_FAINT}; font-size: 9pt; padding: 4px 10px;")
        sidebar_layout.addWidget(version)

        main_layout.addWidget(sidebar)

        # Content area
        self.launch_screen = LaunchScreen()
        main_layout.addWidget(self.launch_screen)

        # Status bar
        self.statusBar().showMessage("Ready")


def main():
    """Entry point for policydb-desktop CLI command."""
    db.init_db()

    app = QApplication(sys.argv)
    app.setApplicationName("PolicyDB Desktop")
    app.setApplicationVersion(__version__)

    # Apply theme
    app.setStyleSheet(load_stylesheet())

    # Set default font — SF Pro via system font
    font = QFont(".AppleSystemUIFont", 11)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(font)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
