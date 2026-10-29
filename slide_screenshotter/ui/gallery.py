"""Galleria pre-esportazione: scelta, riordino ed eliminazione delle slide, poi PDF."""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QEventLoop, QModelIndex, QPoint, QRect, QSize, Qt, QThread, Signal
from PySide6.QtGui import (
    QColor,
    QDrag,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QImageReader,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPalette,
    QPen,
    QPixmap,
    QShortcut,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSlider,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionButton,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from ..duplicates import Superseded, find_superseded
from ..pdf_export import ExportCancelled, ExportError, OcrConfig, default_ocr_config, export_pdf
from ..settings import AppSettings
from ..storage import SlideStorage, StorageError

_THUMB_MIN = 100
_THUMB_MAX = 900
_ZOOM_STEP = 40

_PathRole = Qt.ItemDataRole.UserRole
_AspectRole = Qt.ItemDataRole.UserRole + 1  # altezza / larghezza dell'immagine
_TimeRole = Qt.ItemDataRole.UserRole + 2
_SupersededRole = Qt.ItemDataRole.UserRole + 3  # (tipo, elemento che la supera) o None

_BADGE_COLOR = QColor("#e67e22")


def _is_checked(index: QModelIndex) -> bool:
    value = index.data(Qt.ItemDataRole.CheckStateRole)
    return value in (Qt.CheckState.Checked, Qt.CheckState.Checked.value)


class _SlideDelegate(QStyledItemDelegate):
    """Disegna una slide: checkbox, numero e ora, miniatura, eventuale avviso duplicato.

    Le immagini vengono caricate solo quando diventano visibili e tenute in cache
    già ridotte alla dimensione massima di zoom; la copia alla dimensione attuale
    viene rigenerata solo quando lo zoom cambia.
    """

    PAD = 6
    HEADER = 22

    def __init__(self, view: QListWidget) -> None:
        super().__init__(view)
        self._view = view
        self.width = 240
        self._sources: dict[Path, QPixmap] = {}
        self._scaled: dict[Path, QPixmap] = {}

    def set_width(self, width: int) -> None:
        self.width = width
        self._scaled.clear()

    def rename(self, mapping: dict[Path, Path]) -> None:
        """Aggiorna la cache dopo che i file sono stati rinominati."""
        self._sources = {mapping.get(p, p): pix for p, pix in self._sources.items()}
        self._scaled = {mapping.get(p, p): pix for p, pix in self._scaled.items()}

    def forget(self, path: Path) -> None:
        self._sources.pop(path, None)
        self._scaled.pop(path, None)

    # ----- Geometria -----

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        return QSize(self.width + 2 * self.PAD, self.HEADER + self._image_height(index) + 2 * self.PAD)

    def _image_height(self, index: QModelIndex) -> int:
        aspect = index.data(_AspectRole) or 9 / 16
        return round(self.width * aspect)

    def _checkbox_rect(self, rect: QRect) -> QRect:
        size = 16
        return QRect(rect.left() + self.PAD, rect.top() + self.PAD + (self.HEADER - size) // 2, size, size)

    def _image_rect(self, rect: QRect, index: QModelIndex) -> QRect:
        top = rect.top() + self.PAD + self.HEADER
        return QRect(rect.left() + self.PAD, top, self.width, self._image_height(index))

    # ----- Disegno -----

    def _pixmap(self, path: Path) -> QPixmap:
        scaled = self._scaled.get(path)
        if scaled is not None:
            return scaled
        source = self._sources.get(path)
        if source is None:
            source = QPixmap(str(path))
            if not source.isNull() and source.width() > _THUMB_MAX:
                source = source.scaledToWidth(_THUMB_MAX, Qt.TransformationMode.SmoothTransformation)
            self._sources[path] = source
        if source.isNull():
            return source
        scaled = source.scaledToWidth(self.width, Qt.TransformationMode.SmoothTransformation)
        self._scaled[path] = scaled
        return scaled

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect
        palette = option.palette
        included = _is_checked(index)

        if option.state & QStyle.StateFlag.State_Selected:
            highlight = palette.color(QPalette.ColorRole.Highlight)
            fill = QColor(highlight)
            fill.setAlpha(60)
            painter.setPen(QPen(highlight, 2))
            painter.setBrush(fill)
            painter.drawRoundedRect(rect.adjusted(1, 1, -2, -2), 6, 6)

        # Checkbox "inclusa nel PDF"
        box = QStyleOptionButton()
        box.rect = self._checkbox_rect(rect)
        box.state = QStyle.StateFlag.State_Enabled | (
            QStyle.StateFlag.State_On if included else QStyle.StateFlag.State_Off
        )
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorCheckBox, box, painter, option.widget)

        # Numero e ora di cattura
        text_left = box.rect.right() + 6
        text_rect = QRect(text_left, rect.top() + self.PAD, rect.right() - self.PAD - text_left, self.HEADER)
        label = f"#{index.row() + 1}  ·  {index.data(_TimeRole)}"
        painter.setPen(palette.color(QPalette.ColorRole.Text))
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            option.fontMetrics.elidedText(label, Qt.TextElideMode.ElideRight, text_rect.width()),
        )

        # Miniatura, attenuata se esclusa dal PDF
        image_rect = self._image_rect(rect, index)
        pixmap = self._pixmap(Path(index.data(_PathRole)))
        painter.setOpacity(1.0 if included else 0.3)
        if pixmap.isNull():
            painter.drawText(image_rect, Qt.AlignmentFlag.AlignCenter, "(immagine non leggibile)")
        else:
            painter.drawPixmap(image_rect, pixmap)
        painter.setOpacity(1.0)

        superseded = index.data(_SupersededRole)
        if superseded:
            self._draw_badge(painter, option, image_rect, superseded)
        painter.restore()

    def _draw_badge(self, painter: QPainter, option: QStyleOptionViewItem, image_rect: QRect, superseded) -> None:
        kind, target = superseded
        row = self._view.row(target)
        if row < 0:
            return  # la slide di riferimento è stata eliminata
        text = f"duplicato di #{row + 1}" if kind == "duplicate" else f"incompleta → #{row + 1}"
        metrics = option.fontMetrics
        width = min(metrics.horizontalAdvance(text) + 12, image_rect.width() - 8)
        height = metrics.height() + 6
        # In basso a sinistra: in alto coprirebbe il titolo della slide.
        badge = QRect(image_rect.left() + 4, image_rect.bottom() - height - 3, width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_BADGE_COLOR)
        painter.drawRoundedRect(badge, 4, 4)
        painter.setPen(QColor("white"))
        painter.drawText(
            badge,
            Qt.AlignmentFlag.AlignCenter,
            metrics.elidedText(text, Qt.TextElideMode.ElideRight, badge.width() - 8),
        )

    # ----- Interazione -----

    def editorEvent(self, event: QEvent, model, option: QStyleOptionViewItem, index: QModelIndex) -> bool:  # noqa: N802
        """Clic sulla checkbox: include/esclude senza cambiare la selezione."""
        if (
            isinstance(event, QMouseEvent)
            and event.button() == Qt.MouseButton.LeftButton
            and self._checkbox_rect(option.rect).adjusted(-4, -4, 4, 4).contains(event.position().toPoint())
        ):
            if event.type() == QEvent.Type.MouseButtonRelease:
                state = Qt.CheckState.Unchecked if _is_checked(index) else Qt.CheckState.Checked
                model.setData(index, state, Qt.ItemDataRole.CheckStateRole)
            return True
        return super().editorEvent(event, model, option, index)


class _SlideList(QListWidget):
    """Griglia di slide con selezione multipla, trascinamento per riordinare e zoom."""

    order_changed = Signal()
    delete_requested = Signal()
    toggle_requested = Signal()
    zoom_requested = Signal(int)  # +1 ingrandisci, -1 rimpicciolisci
    files_dropped = Signal(list)  # list[Path]: file trascinati da fuori (es. Esplora risorse)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # ListMode + flusso orizzontale con a capo: le colonne si adattano alla
        # larghezza e il trascinamento riordina davvero gli elementi (IconMode no).
        self.setViewMode(QListView.ViewMode.ListMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setSpacing(4)
        self.setUniformItemSizes(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        # L'indicatore di Qt accetta il rilascio solo nei 2 pixel tra un elemento e
        # l'altro: posizione e linea di inserimento sono gestite qui sotto.
        self.setDropIndicatorShown(False)
        self._drop_row: int | None = None
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def startDrag(self, supported_actions: Qt.DropAction) -> None:  # noqa: N802
        """Avvia il trascinamento senza lasciare alla vista la rimozione degli elementi.

        Il comportamento di Qt rimuove gli elementi selezionati dalla lista se il
        trascinamento termina con MoveAction: qui lo spostamento lo fa move_selected,
        quindi il risultato di exec() viene ignorato.
        """
        indexes = self.selectedIndexes()
        if not indexes:
            return
        mime = self.model().mimeData(indexes)
        if mime is None:
            return
        drag = QDrag(self)
        drag.setMimeData(mime)
        rect = self.visualRect(self.currentIndex() if self.currentIndex() in indexes else indexes[0])
        pixmap = self.viewport().grab(rect).scaledToWidth(120, Qt.TransformationMode.SmoothTransformation)
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
        drag.exec(Qt.DropAction.MoveAction, Qt.DropAction.MoveAction)

    @staticmethod
    def _local_files(event: QDragMoveEvent | QDragEnterEvent | QDropEvent) -> list[Path]:
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        return [Path(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.source() is not self and self._local_files(event):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def move_selected(self, target: int) -> bool:
        """Sposta le slide selezionate prima della riga `target`. True se l'ordine cambia."""
        rows = sorted(self.row(item) for item in self.selectedItems())
        if not rows:
            return False
        contiguous = rows == list(range(rows[0], rows[-1] + 1))
        if contiguous and rows[0] <= target <= rows[-1] + 1:
            return False
        items = [self.takeItem(row) for row in reversed(rows)][::-1]
        target -= sum(1 for row in rows if row < target)
        for offset, item in enumerate(items):
            self.insertItem(target + offset, item)
        self.clearSelection()
        for item in items:
            item.setSelected(True)
        self.setCurrentItem(items[0], self.selectionModel().SelectionFlag.NoUpdate)
        self.order_changed.emit()
        return True

    def drop_row_at(self, pos: QPoint) -> int:
        """Riga prima della quale inserire, per un rilascio nel punto `pos` del viewport.

        Si segue l'ordine di lettura: il punto cade prima del primo elemento che sta
        su una riga più in basso, o sulla stessa riga ma con il centro più a destra.
        """
        for row in range(self.count()):
            rect = self.visualRect(self.indexFromItem(self.item(row)))
            if pos.y() < rect.top():
                return row
            if pos.y() <= rect.bottom() and pos.x() < rect.center().x():
                return row
        return self.count()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        if event.source() is not self:
            if self._local_files(event):
                event.acceptProposedAction()
            else:
                event.ignore()
            return
        super().dragMoveEvent(event)  # scorrimento automatico vicino ai bordi
        self._drop_row = self.drop_row_at(event.position().toPoint())
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        self.viewport().update()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:  # noqa: N802
        self._drop_row = None
        self.viewport().update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self._drop_row = None
        if event.source() is not self:
            files = self._local_files(event)
            if files:
                event.acceptProposedAction()
                self.files_dropped.emit(files)
            else:
                event.ignore()
            return
        self.move_selected(self.drop_row_at(event.position().toPoint()))
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        self.stopAutoScroll()
        self.setState(QAbstractItemView.State.NoState)
        self.viewport().update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)
        if self._drop_row is None or self.count() == 0:
            return
        # Linea verticale nel punto di inserimento.
        if self._drop_row < self.count():
            rect = self.visualRect(self.indexFromItem(self.item(self._drop_row)))
            x = rect.left() - 2
        else:
            rect = self.visualRect(self.indexFromItem(self.item(self.count() - 1)))
            x = rect.right() + 2
        painter = QPainter(self.viewport())
        painter.setPen(QPen(self.palette().color(QPalette.ColorRole.Highlight), 3))
        painter.drawLine(x, rect.top() + 4, x, rect.bottom() - 4)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Space and not event.modifiers():
            self.toggle_requested.emit()
        elif event.matches(QKeySequence.StandardKey.Delete) or event.key() == Qt.Key.Key_Backspace:
            self.delete_requested.emit()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                self.zoom_requested.emit(1 if delta > 0 else -1)
            event.accept()
        else:
            super().wheelEvent(event)


class _ExportThread(QThread):
    """Crea il PDF fuori dal thread della GUI (con l'OCR può richiedere minuti)."""

    progressed = Signal(int)  # pagine completate

    def __init__(self, paths: list[Path], output: Path, ocr: OcrConfig | None) -> None:
        super().__init__()
        self._paths = paths
        self._output = output
        self._ocr = ocr
        self._cancel = threading.Event()
        self.error: str | None = None
        self.cancelled = False

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            export_pdf(self._paths, self._output, self._ocr, self._progress)
        except ExportCancelled:
            self.cancelled = True
        except ExportError as exc:
            self.error = str(exc)

    def _progress(self, done: int, total: int) -> bool:
        self.progressed.emit(done)
        return not self._cancel.is_set()


class GalleryDialog(QDialog):
    """Mostra tutte le slide catturate e genera il PDF con quelle incluse."""

    def __init__(self, storage: SlideStorage, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._storage = storage
        self._settings = settings
        self.setWindowTitle("Galleria slide · Esporta PDF")
        self.resize(4 * 270 + 40, 720)
        settings.restore_geometry("gallery", self)

        self._thumb_width = min(max(settings.gallery_zoom, _THUMB_MIN), _THUMB_MAX)
        self._list = _SlideList()
        self._delegate = _SlideDelegate(self._list)
        self._delegate.set_width(self._thumb_width)
        self._list.setItemDelegate(self._delegate)
        for path in storage.list_slides():
            self._list.addItem(self._make_item(path))

        self._list.itemChanged.connect(self._update_summary)
        self._list.itemSelectionChanged.connect(self._update_summary)
        self._list.itemDoubleClicked.connect(self._toggle_item)
        self._list.order_changed.connect(self._persist_order)
        self._list.delete_requested.connect(self._delete_selected)
        self._list.toggle_requested.connect(self._toggle_selected)
        self._list.zoom_requested.connect(lambda sign: self._zoom_by(sign * _ZOOM_STEP))
        self._list.files_dropped.connect(self._import_paths)
        self._list.customContextMenuRequested.connect(self._show_context_menu)

        hint = QLabel(
            "Clic: seleziona · Shift/Ctrl+clic: selezione multipla · Ctrl+A: tutte · "
            "Spazio o doppio clic: includi/escludi · Canc: elimina · "
            "trascina per riordinare · Ctrl+rotellina: zoom · "
            "trascina file immagine qui per importarli"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text);")

        include_all = QPushButton("Includi tutte")
        include_all.clicked.connect(lambda: self._set_included(self._all_items(), True))
        exclude_all = QPushButton("Escludi tutte")
        exclude_all.clicked.connect(lambda: self._set_included(self._all_items(), False))
        find_duplicates = QPushButton("Trova duplicati")
        find_duplicates.setToolTip(
            "Cerca slide consecutive ripetute o incomplete (es. elenchi che compaiono\n"
            "una voce alla volta) ed esclude tutte tranne l'ultima di ogni serie."
        )
        find_duplicates.clicked.connect(self._find_duplicates)
        import_button = QPushButton("Importa immagini…")
        import_button.setToolTip(
            "Aggiunge in fondo screenshot o altre immagini (PNG, JPG, BMP, WebP…).\n"
            "Si possono anche trascinare nella galleria dalla cartella dei file."
        )
        import_button.clicked.connect(self._import_dialog)
        self._delete_button = QPushButton("Elimina selezionate")
        self._delete_button.clicked.connect(self._delete_selected)
        self._summary = QLabel()

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

        self._ocr_config = default_ocr_config()
        self._ocr_check = QCheckBox("Testo ricercabile (OCR)")
        if self._ocr_config is None:
            self._ocr_check.setEnabled(False)
            self._ocr_check.setToolTip(
                "Richiede Tesseract OCR con le lingue italiano/inglese.\n"
                "Windows: installalo da github.com/UB-Mannheim/tesseract/wiki\n"
                "e riapri la galleria."
            )
        else:
            self._ocr_check.setChecked(settings.ocr_enabled)
            self._ocr_check.setToolTip(
                f"Aggiunge al PDF un livello di testo invisibile (lingue: {self._ocr_config.languages}):\n"
                "il PDF diventa ricercabile e il testo si può copiare. Rallenta l'esportazione."
            )
            self._ocr_check.toggled.connect(self._on_ocr_toggled)

        self._generate = QPushButton("Genera PDF")
        self._generate.setDefault(True)
        self._generate.clicked.connect(self._generate_pdf)
        close = QPushButton("Chiudi")
        close.clicked.connect(self.reject)

        selection_row = QHBoxLayout()
        selection_row.addWidget(include_all)
        selection_row.addWidget(exclude_all)
        selection_row.addWidget(find_duplicates)
        selection_row.addWidget(import_button)
        selection_row.addWidget(self._delete_button)
        selection_row.addSpacing(12)
        selection_row.addWidget(self._summary)
        selection_row.addStretch()
        selection_row.addWidget(QLabel("Zoom:"))
        selection_row.addWidget(zoom_out)
        selection_row.addWidget(self._zoom_slider)
        selection_row.addWidget(zoom_in)

        export_row = QHBoxLayout()
        export_row.addWidget(self._ocr_check)
        export_row.addStretch()
        export_row.addWidget(close)
        export_row.addWidget(self._generate)

        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addWidget(self._list)
        layout.addLayout(selection_row)
        layout.addLayout(export_row)
        self._update_summary()

    # ----- Elementi -----

    @staticmethod
    def _make_item(path: Path) -> QListWidgetItem:
        item = QListWidgetItem()
        item.setData(_PathRole, path)
        size = QImageReader(str(path)).size()  # legge solo l'intestazione del file
        item.setData(_AspectRole, size.height() / size.width() if size.isValid() and size.width() else None)
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime).strftime("%H:%M:%S")
        except OSError:
            modified = "--:--:--"
        item.setData(_TimeRole, modified)
        item.setToolTip(path.name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        return item

    def _all_items(self) -> list[QListWidgetItem]:
        return [self._list.item(row) for row in range(self._list.count())]

    def _included_paths(self) -> list[Path]:
        return [
            item.data(_PathRole)
            for item in self._all_items()
            if item.checkState() == Qt.CheckState.Checked
        ]

    def _set_included(self, items: list[QListWidgetItem], included: bool) -> None:
        state = Qt.CheckState.Checked if included else Qt.CheckState.Unchecked
        for item in items:
            item.setCheckState(state)

    def _toggle_item(self, item: QListWidgetItem) -> None:
        self._set_included([item], item.checkState() != Qt.CheckState.Checked)

    def _toggle_selected(self) -> None:
        items = self._list.selectedItems()
        if not items:
            return
        # Se qualcuna è esclusa le include tutte, altrimenti le esclude tutte.
        include = any(item.checkState() != Qt.CheckState.Checked for item in items)
        self._set_included(items, include)

    def _show_context_menu(self, pos: QPoint) -> None:
        item = self._list.itemAt(pos)
        if item is None:
            return
        if not item.isSelected():
            self._list.setCurrentItem(item)  # seleziona solo quella sotto il cursore
        items = self._list.selectedItems()
        menu = QMenu(self)
        menu.addAction("Includi nel PDF", lambda: self._set_included(items, True))
        menu.addAction("Escludi dal PDF", lambda: self._set_included(items, False))
        menu.addSeparator()
        menu.addAction(f"Elimina ({len(items)})…", self._delete_selected)
        menu.exec(self._list.viewport().mapToGlobal(pos))

    def _update_summary(self, *_args) -> None:
        included = len(self._included_paths())
        selected = len(self._list.selectedItems())
        text = f"{included} / {self._list.count()} incluse"
        if selected:
            text += f" · {selected} selezionate"
        self._summary.setText(text)
        self._generate.setEnabled(included > 0)
        self._delete_button.setEnabled(selected > 0)

    # ----- Riordino ed eliminazione -----

    def _persist_order(self) -> None:
        """Rinomina i file su disco perché l'ordine resti anche alla prossima apertura."""
        items = self._all_items()
        old_paths = [item.data(_PathRole) for item in items]
        try:
            new_paths = self._storage.reorder(old_paths)
        except StorageError as exc:
            QMessageBox.critical(self, "Riordino non riuscito", str(exc))
            self._reload()
            return
        for item, path in zip(items, new_paths):
            item.setData(_PathRole, path)
            item.setToolTip(path.name)
        self._delegate.rename(dict(zip(old_paths, new_paths)))
        self._list.viewport().update()  # i numeri #n sono cambiati

    def _delete_selected(self) -> None:
        items = self._list.selectedItems()
        if not items:
            return
        answer = QMessageBox.question(
            self,
            "Elimina slide",
            f"Eliminare definitivamente {len(items)} slide dal disco?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        paths = [item.data(_PathRole) for item in items]
        try:
            self._storage.delete(paths)
        except StorageError as exc:
            QMessageBox.critical(self, "Eliminazione non riuscita", str(exc))
            self._reload()
            return
        for item, path in zip(items, paths):
            self._delegate.forget(path)
            self._list.takeItem(self._list.row(item))
        self._update_summary()
        self._list.viewport().update()

    def _reload(self) -> None:
        """Ricarica l'elenco dal disco dopo un errore, per non mostrare uno stato falso."""
        self._list.clear()
        for path in self._storage.list_slides():
            self._list.addItem(self._make_item(path))
        self._update_summary()

    # ----- Importazione -----

    def _import_dialog(self) -> None:
        names, _ = QFileDialog.getOpenFileNames(
            self,
            "Importa immagini",
            "",
            "Immagini (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff);;Tutti i file (*)",
        )
        self._import_paths([Path(n) for n in names])

    def _import_paths(self, paths: list[Path]) -> None:
        """Copia le immagini nella cartella delle slide e le aggiunge in fondo alla lista."""
        paths = [p for p in paths if p.is_file()]
        if not paths:
            return
        try:
            added, failed = self._storage.import_images(paths)
        except StorageError as exc:
            QMessageBox.critical(self, "Importazione non riuscita", str(exc))
            self._reload()
            return
        for path in added:
            self._list.addItem(self._make_item(path))
        if added:
            self._list.scrollToBottom()
        self._update_summary()
        if failed:
            QMessageBox.warning(
                self,
                "Importazione parziale",
                "Impossibile leggere come immagine:\n" + "\n".join(failed[:10]),
            )

    # ----- Duplicati -----

    def _find_duplicates(self) -> None:
        items = self._all_items()
        if len(items) < 2:
            return
        dialog = QProgressDialog("Analisi delle slide…", "Annulla", 0, len(items), self)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(300)

        def progress(done: int, total: int) -> bool:
            dialog.setValue(done)
            QApplication.processEvents()
            return not dialog.wasCanceled()

        results = find_superseded([item.data(_PathRole) for item in items], progress)
        dialog.close()

        marked = 0
        for item, result in zip(items, results):
            if isinstance(result, Superseded):
                item.setData(_SupersededRole, (result.kind, items[result.by]))
                item.setCheckState(Qt.CheckState.Unchecked)
                marked += 1
            else:
                item.setData(_SupersededRole, None)
        if marked:
            message = (
                f"{marked} slide duplicate o incomplete sono state escluse dal PDF "
                "(segnate in arancione).\nControllale e reincludi quelle da tenere."
            )
        else:
            message = "Nessuna slide duplicata o incompleta trovata."
        QMessageBox.information(self, "Trova duplicati", message)

    # ----- Zoom -----

    def _zoom_by(self, step: int) -> None:
        # passa dallo slider: clamp ai limiti e aggiornamento coerente della UI
        self._zoom_slider.setValue(self._thumb_width + step)

    def _set_thumb_width(self, width: int) -> None:
        if width == self._thumb_width:
            return
        self._thumb_width = width
        self._settings.gallery_zoom = width
        self._delegate.set_width(width)
        self._list.doItemsLayout()  # le dimensioni degli elementi sono cambiate

    # ----- Esportazione -----

    def _on_ocr_toggled(self, checked: bool) -> None:
        self._settings.ocr_enabled = checked

    def _generate_pdf(self) -> None:
        paths = self._included_paths()
        if not paths:
            QMessageBox.warning(self, "Nessuna slide", "Includi almeno una slide da esportare.")
            return

        directory = Path(self._settings.export_dir or Path.home())
        if not directory.is_dir():
            directory = Path.home()
        default_name = directory / f"slides_{datetime.now():%Y-%m-%d_%H%M}.pdf"
        filename, _ = QFileDialog.getSaveFileName(self, "Salva PDF", str(default_name), "PDF (*.pdf)")
        if not filename:
            return  # annullato dall'utente
        output = Path(filename)
        if output.suffix.lower() != ".pdf":
            output = output.with_suffix(".pdf")
        self._settings.export_dir = str(output.parent)

        ocr = self._ocr_config if self._ocr_check.isChecked() else None
        thread = _ExportThread(paths, output, ocr)
        progress = QProgressDialog(
            "Riconoscimento del testo…" if ocr else "Creazione del PDF…", "Annulla", 0, len(paths), self
        )
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(500)
        # Metodo di un QObject (non una lambda): la chiamata arriva nel thread della GUI.
        thread.progressed.connect(progress.setValue)
        progress.canceled.connect(thread.cancel)
        loop = QEventLoop()
        thread.finished.connect(loop.quit)
        thread.start()
        loop.exec()
        progress.close()

        if thread.cancelled:
            return
        if thread.error:
            QMessageBox.critical(self, "Esportazione fallita", thread.error)
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

    def done(self, result: int) -> None:
        self._settings.save_geometry("gallery", self)
        super().done(result)
