"""Selezione a schermo intero della Region of Interest."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from ..monitor import Roi

_MIN_SIZE = 20  # pixel logici


class RoiSelector(QWidget):
    """Oscura lo schermo sotto il cursore e permette di trascinare un rettangolo.

    Emette `roi_selected` con la ROI in pixel *fisici* (quelli usati da mss),
    oppure `cancelled` se l'utente preme Esc o fa un clic senza trascinare.
    """

    roi_selected = Signal(object)  # Roi
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)

        self._screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self._origin: QPoint | None = None
        self._current: QPoint | None = None
        self._finished = False

    def start(self) -> None:
        self.setScreen(self._screen)
        self.setGeometry(self._screen.geometry())
        self.showFullScreen()
        self.activateWindow()
        self.raise_()

    def _selection(self) -> QRect:
        if self._origin is None or self._current is None:
            return QRect()
        return QRect(self._origin, self._current).normalized()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 110))

        selection = self._selection()
        if selection.isValid():
            # "Buca" lo strato scuro per mostrare l'area selezionata a piena luminosità.
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.fillRect(selection, Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setPen(QPen(QColor("#3daee9"), 2))
            painter.drawRect(selection)
            label = f"{selection.width()} × {selection.height()}"
            painter.setPen(QColor("white"))
            painter.drawText(selection.topLeft() + QPoint(4, -6), label)
        else:
            painter.setPen(QColor("white"))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Trascina per selezionare l'area delle slide · Esc per annullare",
            )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()
            self._current = self._origin
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            self._cancel()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._origin is not None:
            self._current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._origin is None:
            return
        self._current = event.position().toPoint()
        selection = self._selection()
        if selection.width() < _MIN_SIZE or selection.height() < _MIN_SIZE:
            self._cancel()
            return
        self._finished = True
        roi = self._to_physical(selection)
        self.close()
        self.roi_selected.emit(roi)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()

    def closeEvent(self, event) -> None:
        if not self._finished:
            self._finished = True
            self.cancelled.emit()
        super().closeEvent(event)

    def _cancel(self) -> None:
        self.close()  # closeEvent emette `cancelled`

    def _to_physical(self, selection: QRect) -> Roi:
        """Converte da pixel logici di Qt a pixel fisici del desktop (usati da mss).

        Con lo scaling HiDPI (es. 150%) Qt lavora in coordinate logiche, mentre mss
        cattura in pixel reali: moltiplichiamo per il devicePixelRatio dello schermo.

        Qt6 mantiene l'origine di ogni schermo in pixel nativi (su Windows con monitor
        a scaling diverso è l'unico modo per avere un desktop virtuale coerente), quindi
        va scalato solo l'offset *all'interno* dello schermo, non l'origine.
        """
        ratio = self._screen.devicePixelRatio()
        screen_origin = self._screen.geometry().topLeft()
        return Roi(
            left=screen_origin.x() + round(selection.x() * ratio),
            top=screen_origin.y() + round(selection.y() * ratio),
            width=round(selection.width() * ratio),
            height=round(selection.height() * ratio),
        )
