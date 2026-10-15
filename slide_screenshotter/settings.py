"""Impostazioni ricordate tra una sessione e l'altra (QSettings).

Su Windows finiscono nel registro (HKCU\\Software\\SlideScreenshotter), su Linux
in ~/.config/SlideScreenshotter/. I valori vengono salvati appena cambiano,
così non si perdono nemmeno se l'app viene chiusa in modo anomalo.
"""

from __future__ import annotations

import json

from PySide6.QtCore import QByteArray, QSettings
from PySide6.QtWidgets import QWidget

from .monitor import Roi

DEFAULT_THRESHOLD = 5.0
DEFAULT_INTERVAL = 0.5
DEFAULT_COOLDOWN = 3.0
DEFAULT_GALLERY_ZOOM = 240


def roi_to_text(roi: Roi) -> str:
    return f"{roi.left},{roi.top},{roi.width},{roi.height}"


def roi_from_text(text: object) -> Roi | None:
    try:
        left, top, width, height = (int(part) for part in str(text).split(","))
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return Roi(left, top, width, height)


class AppSettings:
    """Accesso tipizzato alle impostazioni dell'applicazione."""

    def __init__(self, store: QSettings | None = None) -> None:
        self._store = store if store is not None else QSettings()

    # ----- Helper -----

    def _float(self, key: str, default: float) -> float:
        try:
            return float(self._store.value(key, default))
        except (TypeError, ValueError):
            return default

    def _int(self, key: str, default: int) -> int:
        try:
            return int(self._store.value(key, default))
        except (TypeError, ValueError):
            return default

    def _bool(self, key: str, default: bool) -> bool:
        # Con il formato INI i booleani tornano come stringhe "true"/"false".
        value = self._store.value(key, default)
        if isinstance(value, str):
            return value.lower() in ("true", "1")
        return bool(value)

    # ----- Rilevamento -----

    @property
    def threshold(self) -> float:
        return self._float("capture/threshold", DEFAULT_THRESHOLD)

    @threshold.setter
    def threshold(self, percent: float) -> None:
        self._store.setValue("capture/threshold", percent)

    @property
    def interval(self) -> float:
        return self._float("capture/interval", DEFAULT_INTERVAL)

    @interval.setter
    def interval(self, seconds: float) -> None:
        self._store.setValue("capture/interval", seconds)

    @property
    def cooldown(self) -> float:
        return self._float("capture/cooldown", DEFAULT_COOLDOWN)

    @cooldown.setter
    def cooldown(self, seconds: float) -> None:
        self._store.setValue("capture/cooldown", seconds)

    # ----- Area di cattura -----

    @property
    def roi(self) -> Roi | None:
        return roi_from_text(self._store.value("capture/roi", ""))

    @roi.setter
    def roi(self, roi: Roi | None) -> None:
        if roi is None:
            self._store.remove("capture/roi")
        else:
            self._store.setValue("capture/roi", roi_to_text(roi))

    def roi_profiles(self) -> dict[str, Roi]:
        """Profili salvati, in ordine alfabetico."""
        # Un unico valore JSON: i nomi scelti dall'utente possono contenere
        # caratteri ("/", "\\") che QSettings interpreterebbe come gruppi.
        try:
            raw = json.loads(str(self._store.value("capture/roi_profiles", "{}")))
        except json.JSONDecodeError:
            return {}
        if not isinstance(raw, dict):
            return {}
        profiles = {name: roi_from_text(text) for name, text in raw.items()}
        return {name: profiles[name] for name in sorted(profiles, key=str.casefold) if profiles[name]}

    def save_roi_profile(self, name: str, roi: Roi) -> None:
        profiles = self.roi_profiles()
        profiles[name] = roi
        self._write_profiles(profiles)

    def delete_roi_profile(self, name: str) -> None:
        profiles = self.roi_profiles()
        if profiles.pop(name, None) is not None:
            self._write_profiles(profiles)

    def _write_profiles(self, profiles: dict[str, Roi]) -> None:
        data = {name: roi_to_text(roi) for name, roi in profiles.items()}
        self._store.setValue("capture/roi_profiles", json.dumps(data, ensure_ascii=False))

    # ----- Galleria ed esportazione -----

    @property
    def gallery_zoom(self) -> int:
        return self._int("gallery/zoom", DEFAULT_GALLERY_ZOOM)

    @gallery_zoom.setter
    def gallery_zoom(self, width: int) -> None:
        self._store.setValue("gallery/zoom", width)

    @property
    def ocr_enabled(self) -> bool:
        return self._bool("export/ocr", True)

    @ocr_enabled.setter
    def ocr_enabled(self, enabled: bool) -> None:
        self._store.setValue("export/ocr", enabled)

    @property
    def export_dir(self) -> str:
        return str(self._store.value("export/directory", ""))

    @export_dir.setter
    def export_dir(self, directory: str) -> None:
        self._store.setValue("export/directory", directory)

    # ----- Finestre -----

    def save_geometry(self, key: str, widget: QWidget) -> None:
        self._store.setValue(f"windows/{key}", widget.saveGeometry())

    def restore_geometry(self, key: str, widget: QWidget) -> bool:
        value = self._store.value(f"windows/{key}")
        return isinstance(value, QByteArray) and widget.restoreGeometry(value)
