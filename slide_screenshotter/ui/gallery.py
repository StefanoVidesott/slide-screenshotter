"""Galleria pre-esportazione: selezione delle slide da includere nel PDF."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent, QPixmap
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
    QVBoxLayout,
    QWidget,
)

from ..pdf_export import ExportError, export_pdf
from ..storage import SlideStorage, StorageError

_THUMB_WIDTH = 240
_COLUMNS = 4


class _ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class SlideThumbnail(QWidget):
    """Miniatura con checkbox. Clic sull'immagine = attiva/disattiva."""

    toggled = Signal()

    def __init__(self, index: int, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = path

        modified = datetime.fromtimestamp(path.stat().st_mtime).strftime("%H:%M:%S")
        self.checkbox = QCheckBox(f"#{index}  ·  {modified}")
        self.checkbox.setChecked(True)
        self.checkbox.toggled.connect(self._on_toggled)

        self.image = _ClickableLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setCursor(Qt.CursorShape.PointingHandCursor)
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.image.setText("(immagine non leggibile)")
            self.image.setFixedSize(_THUMB_WIDTH, _THUMB_WIDTH * 9 // 16)
        else:
            self.image.setPixmap(
                pixmap.scaledToWidth(_THUMB_WIDTH, Qt.TransformationMode.SmoothTransformation)
            )
        self.image.setToolTip(path.name)
        self.image.clicked.connect(self.checkbox.toggle)

        self._opacity = QGraphicsOpacityEffect(self.image)
        self._opacity.setOpacity(1.0)
        self.image.setGraphicsEffect(self._opacity)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.checkbox)
        layout.addWidget(self.image)

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

        self._thumbnails: list[SlideThumbnail] = []
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        for i, path in enumerate(storage.list_slides()):
            thumb = SlideThumbnail(i + 1, path)
            thumb.toggled.connect(self._update_summary)
            self._thumbnails.append(thumb)
            grid.addWidget(thumb, i // _COLUMNS, i % _COLUMNS)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(grid_host)

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
        buttons.addWidget(close)
        buttons.addWidget(self._generate)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll)
        layout.addLayout(buttons)
        self._update_summary()

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
