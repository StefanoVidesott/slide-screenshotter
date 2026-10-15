"""Galleria pre-esportazione: selezione delle slide da includere nel PDF."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QKeySequence, QMouseEvent, QPixmap, QShortcut, QWheelEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..pdf_export import ExportError, export_pdf
from ..storage import SlideStorage, StorageError

_THUMB_WIDTH = 240
_THUMB_MIN = 100
_THUMB_MAX = 900
_ZOOM_STEP = 40
_GRID_SPACING = 6
# margini interni di SlideThumbnail (6 + 6)
_THUMB_PADDING = 12


class _ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class SlideThumbnail(QWidget):
    """Miniatura con checkbox. Clic sull'immagine = attiva/disattiva."""

    toggled = Signal()

    def __init__(
        self, index: int, path: Path, width: int = _THUMB_WIDTH, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.path = path

        modified = datetime.fromtimestamp(path.stat().st_mtime).strftime("%H:%M:%S")
        self.checkbox = QCheckBox(f"#{index}  ·  {modified}")
        self.checkbox.setChecked(True)
        self.checkbox.toggled.connect(self._on_toggled)

        self.image = _ClickableLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setCursor(Qt.CursorShape.PointingHandCursor)
        # Copia ridotta alla dimensione massima di zoom: ridimensionare da qui
        # e' molto piu' rapido che ripartire ogni volta dall'immagine originale.
        self._source = QPixmap(str(path))
        if self._source.isNull():
            self.image.setText("(immagine non leggibile)")
        elif self._source.width() > _THUMB_MAX:
            self._source = self._source.scaledToWidth(
                _THUMB_MAX, Qt.TransformationMode.SmoothTransformation
            )
        self.set_thumb_width(width)
        self.image.setToolTip(path.name)
        self.image.clicked.connect(self.checkbox.toggle)

        self._opacity = QGraphicsOpacityEffect(self.image)
        self._opacity.setOpacity(1.0)
        self.image.setGraphicsEffect(self._opacity)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.checkbox)
        layout.addWidget(self.image)

    def set_thumb_width(self, width: int) -> None:
        if self._source.isNull():
            self.image.setFixedSize(width, width * 9 // 16)
            return
        self.image.setPixmap(
            self._source.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
        )

    def is_selected(self) -> bool:
        return self.checkbox.isChecked()

    def set_selected(self, selected: bool) -> None:
        self.checkbox.setChecked(selected)

    def _on_toggled(self, checked: bool) -> None:
        self._opacity.setOpacity(1.0 if checked else 0.3)
        self.toggled.emit()


class GalleryDialog(QDialog):
    """Mostra tutte le slide catturate e genera il PDF con quelle selezionate."""

    def __init__(self, storage: SlideStorage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._storage = storage
        self.setWindowTitle("Galleria slide · Esporta PDF")
        self.resize(4 * (_THUMB_WIDTH + 30) + 40, 720)

        self._thumb_width = _THUMB_WIDTH
        self._columns = 0
        self._thumbnails: list[SlideThumbnail] = []
        grid_host = QWidget()
        self._grid = QGridLayout(grid_host)
        self._grid.setSpacing(_GRID_SPACING)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        for i, path in enumerate(storage.list_slides()):
            thumb = SlideThumbnail(i + 1, path, self._thumb_width)
            thumb.toggled.connect(self._update_summary)
            self._thumbnails.append(thumb)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(grid_host)
        # Ctrl+rotellina per lo zoom, resize per ridistribuire le colonne
        self._viewport = self._scroll.viewport()
        self._viewport.installEventFilter(self)

        zoom_out = QPushButton("−")
        zoom_out.setFixedWidth(28)
        zoom_out.setToolTip("Rimpicciolisci (Ctrl+-)")
        zoom_out.clicked.connect(lambda: self._zoom_by(-_ZOOM_STEP))
        zoom_in = QPushButton("+")
        zoom_in.setFixedWidth(28)
        zoom_in.setToolTip("Ingrandisci (Ctrl++)")
        zoom_in.clicked.connect(lambda: self._zoom_by(_ZOOM_STEP))
        self._zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self._zoom_slider.setRange(_THUMB_MIN, _THUMB_MAX)
        self._zoom_slider.setSingleStep(_ZOOM_STEP // 2)
        self._zoom_slider.setPageStep(_ZOOM_STEP)
        self._zoom_slider.setValue(self._thumb_width)
        self._zoom_slider.setFixedWidth(160)
        self._zoom_slider.setToolTip("Dimensione miniature (anche Ctrl+rotellina)")
        self._zoom_slider.valueChanged.connect(self._set_thumb_width)

        for keys in (QKeySequence.StandardKey.ZoomIn, "Ctrl+="):
            QShortcut(QKeySequence(keys), self, activated=lambda: self._zoom_by(_ZOOM_STEP))
        QShortcut(
            QKeySequence(QKeySequence.StandardKey.ZoomOut),
            self,
            activated=lambda: self._zoom_by(-_ZOOM_STEP),
        )

        select_all = QPushButton("Seleziona tutte")
        select_all.clicked.connect(lambda: self._set_all(True))
        select_none = QPushButton("Deseleziona tutte")
        select_none.clicked.connect(lambda: self._set_all(False))
        self._summary = QLabel()
        self._generate = QPushButton("Genera PDF")
        self._generate.setDefault(True)
        self._generate.clicked.connect(self._generate_pdf)
        close = QPushButton("Chiudi")
        close.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addWidget(select_all)
        buttons.addWidget(select_none)
        buttons.addSpacing(12)
        buttons.addWidget(self._summary)
        buttons.addStretch()
        buttons.addWidget(QLabel("Zoom:"))
        buttons.addWidget(zoom_out)
        buttons.addWidget(self._zoom_slider)
        buttons.addWidget(zoom_in)
        buttons.addSpacing(12)
        buttons.addWidget(close)
        buttons.addWidget(self._generate)

        layout = QVBoxLayout(self)
        layout.addWidget(self._scroll)
        layout.addLayout(buttons)
        self._update_summary()
        self._relayout()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._viewport:
            if event.type() == QEvent.Type.Resize:
                self._relayout()
            elif (
                event.type() == QEvent.Type.Wheel
                and isinstance(event, QWheelEvent)
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier
            ):
                delta = event.angleDelta().y()
                if delta:
                    self._zoom_by(_ZOOM_STEP if delta > 0 else -_ZOOM_STEP)
                return True
        return super().eventFilter(watched, event)

    def _zoom_by(self, step: int) -> None:
        # passa dallo slider: clamp ai limiti e aggiornamento coerente della UI
        self._zoom_slider.setValue(self._thumb_width + step)

    def _set_thumb_width(self, width: int) -> None:
        if width == self._thumb_width:
            return
        self._thumb_width = width
        for thumb in self._thumbnails:
            thumb.set_thumb_width(width)
        self._relayout(force=True)

    def _relayout(self, force: bool = False) -> None:
        """Distribuisce le miniature su quante colonne entrano nella finestra."""
        margins = self._grid.contentsMargins()
        available = self._viewport.width() - margins.left() - margins.right()
        cell = self._thumb_width + _THUMB_PADDING + _GRID_SPACING
        columns = max(1, (available + _GRID_SPACING) // cell)
        if columns == self._columns and not force:
            return
        self._columns = columns
        for thumb in self._thumbnails:
            self._grid.removeWidget(thumb)
        for i, thumb in enumerate(self._thumbnails):
            self._grid.addWidget(thumb, i // columns, i % columns)

    def _selected_paths(self) -> list[Path]:
        return [t.path for t in self._thumbnails if t.is_selected()]

    def _set_all(self, selected: bool) -> None:
        for thumb in self._thumbnails:
            thumb.set_selected(selected)

    def _update_summary(self) -> None:
        selected = len(self._selected_paths())
        self._summary.setText(f"{selected} / {len(self._thumbnails)} selezionate")
        self._generate.setEnabled(selected > 0)

    def _generate_pdf(self) -> None:
        paths = self._selected_paths()
        if not paths:
            QMessageBox.warning(self, "Nessuna slide", "Seleziona almeno una slide da esportare.")
            return

        default_name = Path.home() / f"slides_{datetime.now():%Y-%m-%d_%H%M}.pdf"
        filename, _ = QFileDialog.getSaveFileName(self, "Salva PDF", str(default_name), "PDF (*.pdf)")
        if not filename:
            return  # annullato dall'utente
        output = Path(filename)
        if output.suffix.lower() != ".pdf":
            output = output.with_suffix(".pdf")

        try:
            export_pdf(paths, output)
        except ExportError as exc:
            QMessageBox.critical(self, "Esportazione fallita", str(exc))
            return

        answer = QMessageBox.question(
            self,
            "PDF salvato",
            f"PDF con {len(paths)} slide salvato in:\n{output}\n\n"
            "Vuoi svuotare la cartella temporanea delle slide?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            try:
                self._storage.clear()
            except StorageError as exc:
                QMessageBox.critical(self, "Pulizia fallita", str(exc))
        self.accept()
