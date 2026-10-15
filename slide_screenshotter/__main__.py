"""Punto di ingresso: `python -m slide_screenshotter [cartella_temporanea]`."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from .settings import AppSettings
from .storage import SlideStorage, StorageError
from .ui.main_window import ControlPanel
from .ui.win32 import set_app_user_model_id

ICON_PATH = Path(__file__).parent / "assets" / "icon.ico"


def _default_temp_dir() -> Path:
    # Con un eseguibile PyInstaller la cwd può essere arbitraria (es. C:\Windows\System32
    # se avviato da collegamento): usiamo la cartella dell'eseguibile.
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
    return base / "temp_slides"


def _show_unhandled_exception(exc_type, exc, tb) -> None:
    """Con pythonw.exe non c'è console: gli errori imprevisti vanno mostrati a video."""
    details = "".join(traceback.format_exception(exc_type, exc, tb))
    if sys.stderr is not None:
        sys.stderr.write(details)
    if QApplication.instance() is not None:
        box = QMessageBox(QMessageBox.Icon.Critical, "Errore imprevisto", str(exc) or exc_type.__name__)
        box.setDetailedText(details)
        box.exec()


def main() -> int:
    sys.excepthook = _show_unhandled_exception
    set_app_user_model_id("SlideScreenshotter")

    app = QApplication(sys.argv)
    app.setOrganizationName("SlideScreenshotter")
    app.setApplicationName("Slide Screenshotter")
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    # Chiudere la galleria o il selettore ROI non deve terminare l'app:
    # si esce solo chiudendo il pannello di controllo (vedi ControlPanel.closeEvent).
    app.setQuitOnLastWindowClosed(False)

    temp_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else _default_temp_dir()
    try:
        storage = SlideStorage(temp_dir)
    except StorageError as exc:
        QMessageBox.critical(None, "Errore", str(exc))
        return 1

    window = ControlPanel(storage, AppSettings())
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
