"""Pannello di controllo principale."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QApplication,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..monitor import CaptureWorker, Roi
from ..storage import SlideStorage, StorageError
from .gallery import GalleryDialog
from .overlay import StatusOverlay
from .roi_selector import RoiSelector

# Lo slider lavora in decimi di punto percentuale: 1 → 0.1%, 500 → 50%.
_SLIDER_SCALE = 10
_DEFAULT_THRESHOLD = 5.0


class ControlPanel(QMainWindow):
    """Collega GUI, thread di cattura e archivio su disco."""

    def __init__(self, storage: SlideStorage) -> None:
        super().__init__()
        self.setWindowTitle("Slide Screenshotter")
        self._storage = storage
        self._selector: RoiSelector | None = None

        self._overlay = StatusOverlay()
        self._worker = CaptureWorker(storage, self)
        self._worker.slide_captured.connect(self._on_slide_captured)
        self._worker.cooldown_changed.connect(self._overlay.set_cooldown)
        self._worker.cooldown_changed.connect(self._on_cooldown_changed)
        self._worker.difference_measured.connect(self._on_difference_measured)
        self._worker.monitoring_changed.connect(self._on_monitoring_changed)
        self._worker.error_occurred.connect(self._on_worker_error)

        self._build_ui()
        self._refresh_count()
        self._on_monitoring_changed(False)

        self._worker.start()
        self._overlay.show()

    # ----- Costruzione UI -----

    def _build_ui(self) -> None:
        self._roi_button = QPushButton("Seleziona ROI")
        self._roi_button.clicked.connect(self._select_roi)
        self._roi_label = QLabel("Nessuna area selezionata")
        self._roi_label.setStyleSheet("color: palette(placeholder-text);")

        self._start_button = QPushButton("▶  Start")
        self._start_button.clicked.connect(self._start)
        self._pause_button = QPushButton("⏸  Pausa")
        self._pause_button.clicked.connect(self._worker.pause_monitoring)

        self._manual_button = QPushButton("Scatto manuale")
        self._manual_button.setToolTip("Salva subito la ROI e avvia il cooldown (Ctrl+Shift+S)")
        self._manual_button.clicked.connect(self._manual_capture)
        self._undo_button = QPushButton("Annulla scatto")
        self._undo_button.setToolTip("Elimina l'ultima slide salvata (Ctrl+Z)")
        self._undo_button.clicked.connect(self._undo_last)

        self._export_button = QPushButton("Esporta PDF…")
        self._export_button.clicked.connect(self._open_gallery)

        QShortcut(QKeySequence("Ctrl+Shift+S"), self, self._manual_capture)
        QShortcut(QKeySequence.StandardKey.Undo, self, self._undo_last)

        # Soglia
        self._threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self._threshold_slider.setRange(1, 50 * _SLIDER_SCALE)
        self._threshold_slider.setValue(int(_DEFAULT_THRESHOLD * _SLIDER_SCALE))
        self._threshold_slider.valueChanged.connect(self._on_threshold_changed)
        self._threshold_label = QLabel()
        self._threshold_label.setMinimumWidth(48)
        self._diff_label = QLabel("Differenza attuale: –")
        self._diff_label.setToolTip(
            "Percentuale di area cambiata rispetto all'ultima slide salvata.\n"
            "Si salva quando supera la soglia."
        )

        threshold_row = QHBoxLayout()
        threshold_row.addWidget(QLabel("Bassa"))
        threshold_row.addWidget(self._threshold_slider, 1)
        threshold_row.addWidget(QLabel("Alta"))
        threshold_row.addWidget(self._threshold_label)

        self._interval_spin = QDoubleSpinBox()
        self._interval_spin.setRange(0.1, 10.0)
        self._interval_spin.setSingleStep(0.1)
        self._interval_spin.setSuffix(" s")
        self._interval_spin.setValue(0.5)
        self._interval_spin.valueChanged.connect(self._worker.set_interval)

        self._cooldown_spin = QDoubleSpinBox()
        self._cooldown_spin.setRange(0.0, 60.0)
        self._cooldown_spin.setSingleStep(0.5)
        self._cooldown_spin.setSuffix(" s")
        self._cooldown_spin.setValue(3.0)
        self._cooldown_spin.valueChanged.connect(self._worker.set_cooldown)

        settings = QGroupBox("Impostazioni")
        form = QFormLayout(settings)
        form.addRow("Soglia cambiamento:", threshold_row)
        form.addRow("", self._diff_label)
        form.addRow("Intervallo cattura:", self._interval_spin)
        form.addRow("Cooldown:", self._cooldown_spin)

        controls = QGridLayout()
        controls.addWidget(self._start_button, 0, 0)
        controls.addWidget(self._pause_button, 0, 1)
        controls.addWidget(self._manual_button, 1, 0)
        controls.addWidget(self._undo_button, 1, 1)

        roi_row = QHBoxLayout()
        roi_row.addWidget(self._roi_button)
        roi_row.addWidget(self._roi_label, 1)

        self._count_label = QLabel()
        self._count_label.setStyleSheet("font-size: 15px; font-weight: 600;")
        self._state_label = QLabel()

        status_row = QHBoxLayout()
        status_row.addWidget(self._count_label)
        status_row.addStretch()
        status_row.addWidget(self._state_label)

        layout = QVBoxLayout()
        layout.addLayout(roi_row)
        layout.addLayout(controls)
        layout.addWidget(settings)
        layout.addLayout(status_row)
        layout.addWidget(self._export_button)

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)
        self.statusBar().showMessage(f"Cartella temporanea: {self._storage.directory}")

        self._on_threshold_changed(self._threshold_slider.value())
        self._worker.set_interval(self._interval_spin.value())
        self._worker.set_cooldown(self._cooldown_spin.value())

    # ----- Azioni -----

    def _select_roi(self) -> None:
        if self._selector is not None:
            return
        self._was_monitoring = self._worker.is_monitoring()
        self._worker.pause_monitoring()
        # Nascondiamo le nostre finestre così non coprono l'area da selezionare.
        self.hide()
        self._overlay.hide()
        self._selector = RoiSelector()
        self._selector.roi_selected.connect(self._on_roi_selected)
        self._selector.cancelled.connect(self._on_roi_finished)
        # Piccolo ritardo per lasciare al window manager il tempo di nascondere le finestre.
        QTimer.singleShot(250, self._selector.start)

    def _on_roi_selected(self, roi: Roi) -> None:
        self._worker.set_roi(roi)
        self._roi_label.setText(f"Area: {roi}")
        self._roi_label.setStyleSheet("")
        self._on_roi_finished()

    def _on_roi_finished(self) -> None:
        self._selector = None
        self.show()
        self._overlay.show()
        if self._was_monitoring:
            self._worker.start_monitoring()

    def _require_roi(self) -> bool:
        if self._worker.roi() is not None:
            return True
        QMessageBox.information(self, "Area non selezionata", "Seleziona prima l'area delle slide (ROI).")
        return False

    def _start(self) -> None:
        if self._require_roi():
            self._worker.start_monitoring()

    def _manual_capture(self) -> None:
        if self._require_roi():
            self._worker.request_manual_capture()

    def _undo_last(self) -> None:
        try:
            removed = self._storage.delete_last()
        except StorageError as exc:
            QMessageBox.critical(self, "Errore", str(exc))
            return
        if removed is None:
            self.statusBar().showMessage("Nessuna slide da annullare.", 3000)
            return
        # La schermata attuale diventa il nuovo riferimento (senza salvarla):
        # altrimenti il falso positivo appena eliminato verrebbe ricatturato subito.
        self._worker.request_reference_reset()
        self._refresh_count()
        self.statusBar().showMessage(f"Eliminata {removed.name}", 3000)

    def _open_gallery(self) -> None:
        self._worker.pause_monitoring()
        if self._storage.count() == 0:
            QMessageBox.information(
                self,
                "Nessuna slide",
                "Non ci sono slide da esportare.\nAvvia il monitoraggio o usa lo scatto manuale.",
            )
            return
        dialog = GalleryDialog(self._storage, self)
        dialog.exec()
        self._refresh_count()

    # ----- Slot dai segnali del worker -----

    def _on_slide_captured(self, path: str, total: int) -> None:
        self._refresh_count(total)
        self.statusBar().showMessage(f"Salvata {Path(path).name}", 3000)

    def _on_difference_measured(self, percent: float) -> None:
        self._diff_label.setText(f"Differenza attuale: {percent:.1f}%")

    def _on_cooldown_changed(self, active: bool) -> None:
        self._update_state_label(cooldown=active)

    def _on_monitoring_changed(self, active: bool) -> None:
        self._overlay.set_monitoring(active)
        self._start_button.setEnabled(not active)
        self._pause_button.setEnabled(active)
        self._update_state_label(monitoring=active)

    def _on_worker_error(self, message: str) -> None:
        self.statusBar().showMessage(message, 8000)
        QMessageBox.warning(self, "Errore di cattura", message)

    def _on_threshold_changed(self, value: int) -> None:
        percent = value / _SLIDER_SCALE
        self._threshold_label.setText(f"{percent:.1f}%")
        self._worker.set_threshold(percent)

    # ----- Utility -----

    def _refresh_count(self, total: int | None = None) -> None:
        if total is None:
            total = self._storage.count()
        self._count_label.setText(f"Slide catturate: {total}")
        self._overlay.set_count(total)
        self._undo_button.setEnabled(total > 0)

    _state_monitoring = False
    _state_cooldown = False
    _was_monitoring = False

    def _update_state_label(self, monitoring: bool | None = None, cooldown: bool | None = None) -> None:
        if monitoring is not None:
            self._state_monitoring = monitoring
        if cooldown is not None:
            self._state_cooldown = cooldown
        if self._state_cooldown:
            text, color = "Cooldown", "#e74c3c"
        elif self._state_monitoring:
            text, color = "In ascolto", "#2ecc71"
        else:
            text, color = "In pausa", "#8a8a8a"
        self._state_label.setText(f"<span style='color:{color}'>●</span> {text}")

    def closeEvent(self, event: QCloseEvent) -> None:
        self._worker.stop()
        self._worker.wait(3000)
        self._overlay.close()
        super().closeEvent(event)
        QApplication.quit()
