"""Thread in background che cattura la ROI e rileva i cambi di slide."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import mss
import numpy as np
from PySide6.QtCore import QThread, Signal

from .image_diff import difference_percent, preprocess
from .storage import SlideStorage


@dataclass(frozen=True)
class Roi:
    """Regione dello schermo in pixel fisici (coordinate del desktop virtuale)."""

    left: int
    top: int
    width: int
    height: int

    def as_mss(self) -> dict[str, int]:
        return {"left": self.left, "top": self.top, "width": self.width, "height": self.height}

    def __str__(self) -> str:
        return f"{self.width}×{self.height} @ ({self.left}, {self.top})"


class CaptureWorker(QThread):
    """Loop di cattura e confronto, eseguito fuori dal thread della GUI.

    Il thread vive per tutta la durata dell'applicazione: "Start" e "Pausa"
    attivano/disattivano solo il rilevamento automatico. Lo scatto manuale
    funziona anche in pausa, perché viene eseguito dallo stesso loop (l'oggetto
    `mss` non è thread-safe e deve restare nel thread che lo ha creato).

    Comunicazione con la GUI: le impostazioni arrivano tramite setter protetti da
    lock; i risultati tornano tramite segnali Qt (consegnati in modo sicuro nel
    thread della GUI).
    """

    slide_captured = Signal(str, int)  # percorso, totale slide
    cooldown_changed = Signal(bool)  # True = in cooldown
    difference_measured = Signal(float)  # differenza % rispetto al riferimento
    monitoring_changed = Signal(bool)  # True = rilevamento automatico attivo
    error_occurred = Signal(str)

    def __init__(self, storage: SlideStorage, parent=None) -> None:
        super().__init__(parent)
        self._storage = storage
        self._lock = threading.Lock()
        self._wake = threading.Event()  # interrompe l'attesa tra due catture

        self._roi: Roi | None = None
        self._threshold = 5.0  # percentuale
        self._interval = 0.5  # secondi
        self._cooldown = 3.0  # secondi
        self._monitoring = False
        self._stop_requested = False
        self._manual_requested = False
        self._reset_requested = False
        self._roi_changed = False

        # Stato interno, usato solo dal thread di cattura.
        self._reference: np.ndarray | None = None
        self._cooldown_until = 0.0
        self._in_cooldown = False

    # ----- API chiamata dal thread della GUI -----

    def set_roi(self, roi: Roi) -> None:
        with self._lock:
            self._roi = roi
            # Nuova area: il riferimento precedente non ha più senso.
            self._roi_changed = True
        self._wake.set()

    def roi(self) -> Roi | None:
        with self._lock:
            return self._roi

    def set_threshold(self, percent: float) -> None:
        with self._lock:
            self._threshold = percent

    def set_interval(self, seconds: float) -> None:
        with self._lock:
            self._interval = seconds

    def set_cooldown(self, seconds: float) -> None:
        with self._lock:
            self._cooldown = seconds

    def start_monitoring(self) -> None:
        with self._lock:
            if self._monitoring:
                return
            self._monitoring = True
        self.monitoring_changed.emit(True)
        self._wake.set()

    def pause_monitoring(self) -> None:
        with self._lock:
            if not self._monitoring:
                return
            self._monitoring = False
        self.monitoring_changed.emit(False)

    def is_monitoring(self) -> bool:
        with self._lock:
            return self._monitoring

    def request_manual_capture(self) -> None:
        with self._lock:
            self._manual_requested = True
        self._wake.set()

    def request_reference_reset(self) -> None:
        """Usa la schermata attuale come riferimento senza salvarla (dopo un annullamento)."""
        with self._lock:
            self._reset_requested = True
        self._wake.set()

    def stop(self) -> None:
        with self._lock:
            self._stop_requested = True
        self._wake.set()

    # ----- Loop del thread -----

    def run(self) -> None:
        with mss.mss() as sct:
            while True:
                with self._lock:
                    if self._stop_requested:
                        return
                    interval = self._interval
                started = time.monotonic()
                try:
                    self._tick(sct)
                except Exception as exc:  # noqa: BLE001 - il thread non deve morire
                    self.pause_monitoring()
                    self.error_occurred.emit(f"Errore di cattura: {exc}")
                elapsed = time.monotonic() - started
                self._wake.wait(max(0.0, interval - elapsed))
                self._wake.clear()

    def _tick(self, sct: mss.base.MSSBase) -> None:
        with self._lock:
            roi = self._roi
            threshold = self._threshold
            cooldown = self._cooldown
            monitoring = self._monitoring
            manual = self._manual_requested
            reset = self._reset_requested
            roi_changed = self._roi_changed
            self._manual_requested = False
            self._reset_requested = False
            self._roi_changed = False

        if roi_changed:
            self._reference = None
        self._update_cooldown_state()
        if roi is None:
            if manual:
                self.error_occurred.emit("Seleziona prima un'area (ROI) da catturare.")
            return
        if not (monitoring or manual or reset):
            return

        frame = self._grab(sct, roi)
        processed = preprocess(frame)

        if manual:
            self._save(frame, processed, cooldown)
            return
        if reset:
            self._reference = processed
            self._start_cooldown(cooldown)
            return
        if self._reference is None:
            # Primo frame dopo Start/nuova ROI: la slide visibile va salvata.
            self._save(frame, processed, cooldown)
            return

        diff = difference_percent(self._reference, processed)
        self.difference_measured.emit(diff)
        if self._in_cooldown:
            # Durante il cooldown ignoriamo i cambiamenti: probabilmente è una
            # transizione/animazione. Al termine il frame verrà confrontato di
            # nuovo con l'ultimo salvato, catturando lo stato finale.
            return
        if diff >= threshold:
            self._save(frame, processed, cooldown)

    @staticmethod
    def _grab(sct: mss.base.MSSBase, roi: Roi) -> np.ndarray:
        shot = sct.grab(roi.as_mss())
        # mss restituisce BGRA; scartiamo il canale alpha (img2pdf non gestisce
        # la trasparenza e non serve per uno screenshot).
        return np.ascontiguousarray(np.asarray(shot)[:, :, :3])

    def _save(self, frame: np.ndarray, processed: np.ndarray, cooldown: float) -> None:
        path = self._storage.save(frame)
        self._reference = processed
        self._start_cooldown(cooldown)
        self.slide_captured.emit(str(path), self._storage.count())

    def _start_cooldown(self, seconds: float) -> None:
        self._cooldown_until = time.monotonic() + seconds
        self._update_cooldown_state()

    def _update_cooldown_state(self) -> None:
        in_cooldown = time.monotonic() < self._cooldown_until
        if in_cooldown != self._in_cooldown:
            self._in_cooldown = in_cooldown
            self.cooldown_changed.emit(in_cooldown)
