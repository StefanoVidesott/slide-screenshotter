"""Integrazioni specifiche di Windows (no-op sugli altri sistemi)."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QWidget

# Win10 2004+: la finestra resta visibile a schermo ma è esclusa da qualunque
# cattura (BitBlt usato da mss, registrazione, condivisione schermo).
_WDA_EXCLUDEFROMCAPTURE = 0x00000011


def set_app_user_model_id(app_id: str) -> None:
    """Fa usare alla taskbar l'icona dell'app invece di quella di pythonw.exe."""
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except (AttributeError, OSError):
        pass


def exclude_from_capture(widget: QWidget) -> bool:
    """Impedisce che `widget` compaia negli screenshot. Restituisce True se applicato."""
    if sys.platform != "win32":
        return False
    import ctypes

    hwnd = int(widget.winId())
    try:
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, _WDA_EXCLUDEFROMCAPTURE))
    except (AttributeError, OSError):
        return False  # Windows troppo vecchio: l'overlay va tenuto fuori dalla ROI
