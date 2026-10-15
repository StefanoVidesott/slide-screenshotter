"""Scorciatoie da tastiera globali: funzionano anche mentre si guarda la lezione.

Su Windows usano RegisterHotKey: nessuna dipendenza esterna e nessun hook che
legga tutti i tasti premuti. Sugli altri sistemi non sono disponibili e
`register` restituisce False (restano le scorciatoie della finestra).
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Qt, Signal
from PySide6.QtGui import QKeySequence

SUPPORTED = sys.platform == "win32"

_WM_HOTKEY = 0x0312
_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_WIN = 0x0008
_MOD_NOREPEAT = 0x4000  # tenere premuto il tasto non ripete l'azione
_VK_F1 = 0x70


def to_win32(sequence: str) -> tuple[int, int] | None:
    """Converte "Ctrl+Alt+S" in (modificatori, virtual key) per RegisterHotKey."""
    keys = QKeySequence(sequence)
    if keys.isEmpty():
        return None
    combo = keys[0]
    key = combo.key().value
    modifiers = combo.keyboardModifiers()

    flags = _MOD_NOREPEAT
    if modifiers & Qt.KeyboardModifier.ControlModifier:
        flags |= _MOD_CONTROL
    if modifiers & Qt.KeyboardModifier.AltModifier:
        flags |= _MOD_ALT
    if modifiers & Qt.KeyboardModifier.ShiftModifier:
        flags |= _MOD_SHIFT
    if modifiers & Qt.KeyboardModifier.MetaModifier:
        flags |= _MOD_WIN

    # Per lettere e cifre il codice Qt coincide con il virtual key code di Windows.
    if Qt.Key.Key_A.value <= key <= Qt.Key.Key_Z.value or Qt.Key.Key_0.value <= key <= Qt.Key.Key_9.value:
        return flags, key
    if Qt.Key.Key_F1.value <= key <= Qt.Key.Key_F24.value:
        return flags, _VK_F1 + key - Qt.Key.Key_F1.value
    return None


class _HotkeyMessageFilter(QAbstractNativeEventFilter):
    """Intercetta WM_HOTKEY dalla coda messaggi del thread della GUI."""

    def __init__(self, owner: GlobalHotkeys) -> None:
        super().__init__()
        self._owner = owner

    def nativeEventFilter(self, event_type, message):  # noqa: N802 - API Qt
        # Registrando senza finestra, WM_HOTKEY arriva come messaggio di thread.
        if bytes(event_type) in (b"windows_dispatcher_MSG", b"windows_generic_MSG"):
            from ctypes import wintypes

            msg = wintypes.MSG.from_address(int(message))
            if msg.message == _WM_HOTKEY and self._owner._dispatch(int(msg.wParam)):
                return True, 0
        return False, 0


class GlobalHotkeys(QObject):
    """Registra scorciatoie di sistema ed emette `activated(nome)` quando vengono premute."""

    activated = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._names: dict[int, str] = {}
        self._next_id = 1
        self._filter: _HotkeyMessageFilter | None = None

    def register(self, name: str, sequence: str) -> bool:
        """True se la scorciatoia è attiva; False se non supportata o già usata da un'altra app."""
        if not SUPPORTED:
            return False
        parsed = to_win32(sequence)
        if parsed is None:
            return False
        import ctypes

        hotkey_id = self._next_id
        if not ctypes.windll.user32.RegisterHotKey(None, hotkey_id, *parsed):
            return False
        self._next_id += 1
        self._names[hotkey_id] = name
        if self._filter is None:
            self._filter = _HotkeyMessageFilter(self)
            QCoreApplication.instance().installNativeEventFilter(self._filter)
        return True

    def unregister_all(self) -> None:
        if SUPPORTED and self._names:
            import ctypes

            for hotkey_id in self._names:
                ctypes.windll.user32.UnregisterHotKey(None, hotkey_id)
        self._names.clear()
        if self._filter is not None:
            QCoreApplication.instance().removeNativeEventFilter(self._filter)
            self._filter = None

    def _dispatch(self, hotkey_id: int) -> bool:
        name = self._names.get(hotkey_id)
        if name is None:
            return False
        self.activated.emit(name)
        return True
