"""Pannello di controllo principale."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QApplication,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..monitor import CaptureWorker, Roi, roi_on_screen
from ..settings import AppSettings
from ..storage import SlideStorage, StorageError
from .gallery import GalleryDialog
from .hotkeys import SUPPORTED as HOTKEYS_SUPPORTED
from .hotkeys import GlobalHotkeys
from .overlay import StatusOverlay
from .roi_selector import RoiSelector

# Lo slider lavora in decimi di punto percentuale: 1 → 0.1%, 500 → 50%.
_SLIDER_SCALE = 10

# Scorciatoie globali (attive anche mentre la finestra della lezione ha il focus).
# Niente Alt: premuto da solo (o in combinazione) Zoom esce dallo schermo intero.
HOTKEY_CAPTURE = "Ctrl+Shift+F9"
HOTKEY_TOGGLE = "Ctrl+Shift+F10"


class ControlPanel(QMainWindow):
    """Collega GUI, thread di cattura e archivio su disco."""

    def __init__(self, storage: SlideStorage, settings: AppSettings) -> None:
        super().__init__()
        self.setWindowTitle("Slide Screenshotter")
        self._storage = storage
        self._settings = settings
        self._selector: RoiSelector | None = None
        self._gallery_open = False

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
        self._restore_last_roi()
        self._setup_hotkeys()
        settings.restore_geometry("main", self)

        self._worker.start()
        self._overlay.show()

    # ----- Costruzione UI -----

    def _build_ui(self) -> None:
        self._roi_button = QPushButton("Seleziona ROI")
        self._roi_button.clicked.connect(self._select_roi)
        self._roi_label = QLabel("Nessuna area selezionata")
        self._roi_label.setStyleSheet("color: palette(placeholder-text);")

        self._profile_combo = QComboBox()
        self._profile_combo.setPlaceholderText("Nessun profilo")
        self._profile_combo.setToolTip("Aree salvate (es. una per Teams, una per Zoom)")
        self._profile_combo.activated.connect(self._on_profile_activated)
        self._save_profile_button = QPushButton("Salva…")
        self._save_profile_button.setToolTip("Salva l'area attuale come profilo")
        self._save_profile_button.clicked.connect(self._save_profile)
        self._delete_profile_button = QPushButton("Elimina")
        self._delete_profile_button.setToolTip("Elimina il profilo selezionato")
        self._delete_profile_button.clicked.connect(self._delete_profile)
        self._refresh_profiles()

        self._start_button = QPushButton("▶  Start")
        self._start_button.clicked.connect(self._start)
        self._pause_button = QPushButton("⏸  Pausa")
        self._pause_button.clicked.connect(self._worker.pause_monitoring)

        self._manual_button = QPushButton("Scatto manuale")
        self._manual_button.setToolTip(
            f"Salva subito la ROI e avvia il cooldown (Ctrl+Shift+S, ovunque: {HOTKEY_CAPTURE})"
        )
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
        self._threshold_slider.setValue(round(self._settings.threshold * _SLIDER_SCALE))
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
        self._interval_spin.setValue(self._settings.interval)
        self._interval_spin.valueChanged.connect(self._worker.set_interval)
        self._interval_spin.valueChanged.connect(lambda v: setattr(self._settings, "interval", v))

        self._cooldown_spin = QDoubleSpinBox()
        self._cooldown_spin.setRange(0.0, 60.0)
        self._cooldown_spin.setSingleStep(0.5)
        self._cooldown_spin.setSuffix(" s")
        self._cooldown_spin.setValue(self._settings.cooldown)
        self._cooldown_spin.valueChanged.connect(self._worker.set_cooldown)
        self._cooldown_spin.valueChanged.connect(lambda v: setattr(self._settings, "cooldown", v))

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

        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Profilo:"))
        profile_row.addWidget(self._profile_combo, 1)
        profile_row.addWidget(self._save_profile_button)
        profile_row.addWidget(self._delete_profile_button)

        self._hotkey_label = QLabel()
        self._hotkey_label.setWordWrap(True)
        self._hotkey_label.setStyleSheet("color: palette(placeholder-text);")

        self._count_label = QLabel()
        self._count_label.setStyleSheet("font-size: 15px; font-weight: 600;")
        self._state_label = QLabel()

        status_row = QHBoxLayout()
        status_row.addWidget(self._count_label)
        status_row.addStretch()
        status_row.addWidget(self._state_label)

        layout = QVBoxLayout()
        layout.addLayout(roi_row)
        layout.addLayout(profile_row)
        layout.addLayout(controls)
        layout.addWidget(self._hotkey_label)
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
        self._apply_roi(roi)
        self._on_roi_finished()

    def _apply_roi(self, roi: Roi, note: str = "") -> None:
        self._worker.set_roi(roi)
        self._settings.roi = roi
        self._roi_label.setText(f"Area: {roi}{note}")
        self._roi_label.setStyleSheet("")
        self._select_matching_profile(roi)

    def _restore_last_roi(self) -> None:
        roi = self._settings.roi
        if roi is None:
            return
        if roi_on_screen(roi):
            self._apply_roi(roi, "  (ultima usata)")
        else:
            # Monitor scollegato o risoluzione cambiata: l'area non esiste più.
            self._settings.roi = None

    # ----- Profili area -----

    def _refresh_profiles(self, select: str | None = None) -> None:
        self._profile_combo.clear()
        self._profile_combo.addItems(list(self._settings.roi_profiles()))
        self._profile_combo.setCurrentIndex(self._profile_combo.findText(select) if select else -1)
        self._update_profile_buttons()

    def _update_profile_buttons(self) -> None:
        self._profile_combo.setEnabled(self._profile_combo.count() > 0)
        self._delete_profile_button.setEnabled(self._profile_combo.currentIndex() >= 0)

    def _select_matching_profile(self, roi: Roi) -> None:
        names = [name for name, saved in self._settings.roi_profiles().items() if saved == roi]
        self._profile_combo.setCurrentIndex(self._profile_combo.findText(names[0]) if names else -1)
        self._update_profile_buttons()

    def _on_profile_activated(self, index: int) -> None:
        name = self._profile_combo.itemText(index)
        roi = self._settings.roi_profiles().get(name)
        if roi is None:
            return
        if not roi_on_screen(roi):
            QMessageBox.warning(
                self,
                "Area non disponibile",
                f"L'area del profilo «{name}» ({roi}) è fuori dagli schermi collegati.\n"
                "Seleziona di nuovo l'area e salva il profilo.",
            )
            current = self._worker.roi()
            if current is not None:
                self._select_matching_profile(current)
            else:
                self._refresh_profiles()
            return
        self._apply_roi(roi, f"  (profilo «{name}»)")

    def _save_profile(self) -> None:
        roi = self._worker.roi()
        if roi is None:
            QMessageBox.information(self, "Nessuna area", "Seleziona prima un'area da salvare come profilo.")
            return
        current = self._profile_combo.currentText() if self._profile_combo.currentIndex() >= 0 else ""
        name, ok = QInputDialog.getText(self, "Salva profilo", f"Nome del profilo per l'area {roi}:", text=current)
        name = name.strip()
        if not ok or not name:
            return
        if name in self._settings.roi_profiles() and name != current:
            answer = QMessageBox.question(
                self,
                "Profilo esistente",
                f"Il profilo «{name}» esiste già. Sovrascriverlo?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._settings.save_roi_profile(name, roi)
        self._refresh_profiles(select=name)
        self.statusBar().showMessage(f"Profilo «{name}» salvato", 3000)

    def _delete_profile(self) -> None:
        if self._profile_combo.currentIndex() < 0:
            return
        name = self._profile_combo.currentText()
        answer = QMessageBox.question(
            self,
            "Elimina profilo",
            f"Eliminare il profilo «{name}»? L'area attuale resta selezionata.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._settings.delete_roi_profile(name)
            self._refresh_profiles()

    # ----- Scorciatoie globali -----

    def _setup_hotkeys(self) -> None:
        self._hotkeys = GlobalHotkeys(self)
        self._hotkeys.activated.connect(self._on_hotkey)
        if not HOTKEYS_SUPPORTED:
            self._hotkey_label.setText("Scorciatoie globali disponibili solo su Windows.")
            return
        parts = []
        for name, keys, action in (
            ("capture", HOTKEY_CAPTURE, "scatto"),
            ("toggle", HOTKEY_TOGGLE, "avvia/pausa"),
        ):
            registered = self._hotkeys.register(name, keys)
            parts.append(f"{keys} {action}" + ("" if registered else " (già in uso da un'altra app)"))
        self._hotkey_label.setText("Scorciatoie globali: " + " · ".join(parts))

    def _on_hotkey(self, name: str) -> None:
        if self._selector is not None or self._gallery_open:
            return  # durante la selezione dell'area o l'esportazione non si cattura
        if self._worker.roi() is None:
            # Niente finestre di dialogo: l'utente sta guardando la lezione.
            self.statusBar().showMessage("Scorciatoia ignorata: seleziona prima un'area (ROI).", 5000)
            return
        if name == "capture":
            self._worker.request_manual_capture()
        elif name == "toggle":
            if self._worker.is_monitoring():
                self._worker.pause_monitoring()
            else:
                self._worker.start_monitoring()

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
        dialog = GalleryDialog(self._storage, self._settings, self)
        self._gallery_open = True
        try:
            dialog.exec()
        finally:
            self._gallery_open = False
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
        self._settings.threshold = percent

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
        self._settings.save_geometry("main", self)
        self._hotkeys.unregister_all()
        self._worker.stop()
        self._worker.wait(3000)
        self._overlay.close()
        super().closeEvent(event)
        QApplication.quit()
