"""Mini-finestra flottante sempre in primo piano."""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QWidget

from .win32 import exclude_from_capture


class OverlayState(Enum):
    IDLE = "#8a8a8a"  # monitoraggio in pausa
    READY = "#2ecc71"  # pronto a catturare
    COOLDOWN = "#e74c3c"  # cooldown in corso


class StatusOverlay(QWidget):
    """Pillola semitrasparente con un puntino di stato e il numero di slide.

    Trascinabile con il mouse; non ruba il focus alla finestra della lezione.
    """

    _MARGIN = 16

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(84, 30)
        self.setToolTip("Slide catturate · verde: pronto, rosso: cooldown, grigio: in pausa")

        self._count = 0
        self._monitoring = False
        self._cooldown = False
        self._drag_offset: QPoint | None = None
        self._move_to_corner()
        # Su Windows l'overlay non compare negli screenshot anche se finisce dentro la ROI.
        exclude_from_capture(self)

    def set_count(self, count: int) -> None:
        self._count = count
        self.update()

    def set_monitoring(self, active: bool) -> None:
        self._monitoring = active
        self.update()

    def set_cooldown(self, active: bool) -> None:
        self._cooldown = active
        self.update()

    def _state(self) -> OverlayState:
        if self._cooldown:
            return OverlayState.COOLDOWN
        return OverlayState.READY if self._monitoring else OverlayState.IDLE

    def _move_to_corner(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.right() - self.width() - self._MARGIN, area.top() + self._MARGIN)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 20, 20, 170))
        painter.drawRoundedRect(rect, radius, radius)

        dot = 10
        painter.setBrush(QColor(self._state().value))
        painter.drawEllipse(QRectF(12, (rect.height() - dot) / 2, dot, dot))

        font = QFont(self.font())
        font.setBold(True)
        font.setPointSizeF(10.5)
        painter.setFont(font)
        painter.setPen(QColor("white"))
        text_rect = QRectF(28, 0, rect.width() - 36, rect.height())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, str(self._count))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None
