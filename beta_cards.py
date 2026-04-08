# Copyright (C) 2026 Triple Alfa
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This file is part of Beta Cards.
#
# Beta Cards is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# Beta Cards is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# Beta Cards. If not, see <https://www.gnu.org/licenses/>.

import html
import json
import random
import re
import struct
import sys
import time
import ctypes
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

try:
    import winsound
except ImportError:
    winsound = None

from odt_rules_parser import load_rules_as_html
from PySide6.QtCore import QEvent, QIODevice, QRect, QSize, QTimer, Qt, QUrl, Signal, QItemSelectionModel
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QIcon, QKeyEvent, QKeySequence, QPainter, QPen, QPixmap, QScreen
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices, QSoundEffect
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


APP_DISPLAY_NAME = "Beta Cards"
APP_STORAGE_NAME = "BetaCards"
APP_VERSION = "0.3.0"
APP_WINDOWS_APP_ID = "TripleAlfa.BetaCards"
APP_RELEASE_NOTES = """
The first alpha release of Beta Cards.
This release is for testing purposes only.
""".strip()
WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1
LR_LOADFROMFILE = 0x0010
MIN_DECK_SIZE = 30
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


class CardGridListWidget(QListWidget):
    cardActivated = Signal(QListWidgetItem)
    cardRightClicked = Signal(QListWidgetItem)

    def reset_ctrl_shift_selection_state(self) -> None:
        self._ctrl_shift_base_rows = None
        self._ctrl_shift_pivot_row = None

    def apply_row_selection(self, selected_rows: set[int], current_row: int) -> None:
        self.clearSelection()
        for row in sorted(selected_rows):
            item = self.item(row)
            if item is not None:
                item.setSelected(True)
        current_item = self.item(current_row)
        if current_item is not None:
            self.setCurrentItem(current_item, QItemSelectionModel.NoUpdate)
            current_item.setSelected(True)

    def handle_ctrl_shift_arrow(self, event: QKeyEvent) -> bool:
        if event.key() not in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            return False
        if self.count() <= 0:
            event.accept()
            return True

        current_row = self.currentRow()
        if current_row < 0:
            current_row = 0
            self.setCurrentRow(current_row)

        base_rows = getattr(self, "_ctrl_shift_base_rows", None)
        pivot_row = getattr(self, "_ctrl_shift_pivot_row", None)
        if base_rows is None or pivot_row is None:
            base_rows = {self.row(item) for item in self.selectedItems()}
            if not base_rows:
                base_rows = {current_row}
            pivot_row = current_row
            self._ctrl_shift_base_rows = set(base_rows)
            self._ctrl_shift_pivot_row = pivot_row

        translated_event = QKeyEvent(
            event.type(),
            event.key(),
            Qt.ControlModifier,
            event.text(),
            event.isAutoRepeat(),
            event.count(),
        )
        super().keyPressEvent(translated_event)
        new_row = self.currentRow()
        if new_row < 0:
            new_row = current_row

        range_rows = set(range(min(pivot_row, new_row), max(pivot_row, new_row) + 1))
        self.apply_row_selection(set(base_rows) | range_rows, new_row)
        event.accept()
        return True

    def mousePressEvent(self, event) -> None:
        self.reset_ctrl_shift_selection_state()
        self._pending_drag_focus_row = None
        self._pending_drag_start_pos = None
        self._pending_drag_started_on_item = False
        item = self.itemAt(event.position().toPoint())
        modifiers = event.modifiers()
        has_selection_modifiers = bool(modifiers & (Qt.ControlModifier | Qt.ShiftModifier))
        if event.button() == Qt.LeftButton and not has_selection_modifiers:
            self._pending_drag_focus_row = self.row(item) if item is not None else self.currentRow()
            self._pending_drag_start_pos = event.position().toPoint()
            self._pending_drag_started_on_item = item is not None
        if not item:
            if event.button() == Qt.LeftButton:
                super().mousePressEvent(event)
                return
            super().mousePressEvent(event)
            return

        if event.button() == Qt.RightButton:
            if item.isSelected():
                self.setCurrentItem(item, QItemSelectionModel.NoUpdate)
            else:
                self.clearSelection()
                item.setSelected(True)
                self.setCurrentItem(item, QItemSelectionModel.NoUpdate)
            self.cardRightClicked.emit(item)
            event.accept()
            return

        if event.button() == Qt.LeftButton:
            if has_selection_modifiers:
                super().mousePressEvent(event)
                return
            if item.isSelected() and len(self.selectedItems()) > 1:
                self.setCurrentItem(item, QItemSelectionModel.NoUpdate)
                event.accept()
                return
            super().mousePressEvent(event)
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        start_pos = getattr(self, "_pending_drag_start_pos", None)
        if (
            start_pos is not None
            and event.buttons() & Qt.LeftButton
            and (event.position().toPoint() - start_pos).manhattanLength() >= QApplication.startDragDistance()
        ):
            if getattr(self, "_rubberband_preserve_current_row", None) is None:
                self._rubberband_preserve_current_row = getattr(self, "_pending_drag_focus_row", self.currentRow())
                self._suppress_current_highlight = False
                self.viewport().update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self._pending_drag_focus_row = None
        self._pending_drag_start_pos = None
        self._pending_drag_started_on_item = False
        preserved_row = getattr(self, "_rubberband_preserve_current_row", None)
        self._suppress_current_highlight = False
        if preserved_row is not None:
            self._rubberband_preserve_current_row = None
            if 0 <= preserved_row < self.count():
                vertical_value = self.verticalScrollBar().value()
                horizontal_value = self.horizontalScrollBar().value()
                current_item = self.item(preserved_row)
                if current_item is not None:
                    self.setCurrentItem(current_item, QItemSelectionModel.NoUpdate)
                    self.verticalScrollBar().setValue(vertical_value)
                    self.horizontalScrollBar().setValue(horizontal_value)
        self.viewport().update()

    def mouseDoubleClickEvent(self, event) -> None:
        item = self.itemAt(event.position().toPoint())
        if item and event.button() == Qt.LeftButton:
            if item != self.currentItem():
                self.setCurrentItem(item)
            self.cardActivated.emit(item)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:
        """Handle mouse wheel scrolling to scroll by exactly one row at a time."""
        angle = event.angleDelta().y()
        if angle == 0:
            event.accept()
            return
        
        # Scroll by one row (grid height) per wheel tick
        row_height = self.gridSize().height()
        if row_height <= 0:
            super().wheelEvent(event)
            return
        
        # Determine scroll direction and amount (negative when scrolling down)
        scroll_amount = row_height if angle > 0 else -row_height
        
        # Update scroll bar position
        scroll_bar = self.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.value() - scroll_amount)
        event.accept()

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.SelectAll):
            self.reset_ctrl_shift_selection_state()
            self.selectAll()
            event.accept()
            return
        if event.modifiers() == (Qt.ControlModifier | Qt.ShiftModifier):
            if self.handle_ctrl_shift_arrow(event):
                return
        else:
            self.reset_ctrl_shift_selection_state()
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        focus_row = getattr(self, "_rubberband_preserve_current_row", None)
        if focus_row is not None and 0 <= focus_row < self.count():
            focus_item = self.item(focus_row)
        else:
            focus_item = self.currentItem()
        if focus_item is None:
            return
        rect = self.visualItemRect(focus_item)
        if not rect.isValid() or rect.isEmpty():
            return
        painter = QPainter(self.viewport())
        pen = QPen(QColor("#ffd24d"))
        pen.setWidth(3)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect.adjusted(1, 1, -2, -2))


class InfiniteSilenceIODevice(QIODevice):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._chunk = b"\x00" * 16384

    def set_chunk(self, chunk: bytes) -> None:
        self._chunk = chunk or (b"\x00" * 16384)

    def readData(self, maxlen: int) -> bytes:
        if maxlen <= 0:
            return b""
        if maxlen <= len(self._chunk):
            return self._chunk[:maxlen]
        repeats = (maxlen // len(self._chunk)) + 1
        return (self._chunk * repeats)[:maxlen]

    def writeData(self, data) -> int:
        return 0

    def bytesAvailable(self) -> int:
        return len(self._chunk) + super().bytesAvailable()


class DeckListWidget(QListWidget):
    cardDoubleClicked = Signal(QListWidgetItem)
    cardRightClicked = Signal(QListWidgetItem)
    deletePressed = Signal()

    def reset_ctrl_shift_selection_state(self) -> None:
        self._ctrl_shift_base_rows = None
        self._ctrl_shift_pivot_row = None

    def apply_row_selection(self, selected_rows: set[int], current_row: int) -> None:
        self.clearSelection()
        for row in sorted(selected_rows):
            item = self.item(row)
            if item is not None:
                item.setSelected(True)
        current_item = self.item(current_row)
        if current_item is not None:
            self.setCurrentItem(current_item, QItemSelectionModel.NoUpdate)
            current_item.setSelected(True)

    def handle_ctrl_shift_arrow(self, event: QKeyEvent) -> bool:
        if event.key() not in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            return False
        if self.count() <= 0:
            event.accept()
            return True

        current_row = self.currentRow()
        if current_row < 0:
            current_row = 0
            self.setCurrentRow(current_row)

        base_rows = getattr(self, "_ctrl_shift_base_rows", None)
        pivot_row = getattr(self, "_ctrl_shift_pivot_row", None)
        if base_rows is None or pivot_row is None:
            base_rows = {self.row(item) for item in self.selectedItems()}
            if not base_rows:
                base_rows = {current_row}
            pivot_row = current_row
            self._ctrl_shift_base_rows = set(base_rows)
            self._ctrl_shift_pivot_row = pivot_row

        translated_event = QKeyEvent(
            event.type(),
            event.key(),
            Qt.ControlModifier,
            event.text(),
            event.isAutoRepeat(),
            event.count(),
        )
        super().keyPressEvent(translated_event)
        new_row = self.currentRow()
        if new_row < 0:
            new_row = current_row

        range_rows = set(range(min(pivot_row, new_row), max(pivot_row, new_row) + 1))
        self.apply_row_selection(set(base_rows) | range_rows, new_row)
        event.accept()
        return True

    def mouseDoubleClickEvent(self, event) -> None:
        item = self.itemAt(event.position().toPoint())
        if item and event.button() == Qt.LeftButton:
            self.cardDoubleClicked.emit(item)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:
        self.reset_ctrl_shift_selection_state()
        self._pending_drag_focus_row = None
        self._pending_drag_start_pos = None
        self._pending_drag_started_on_item = False
        item = self.itemAt(event.position().toPoint())
        modifiers = event.modifiers()
        has_selection_modifiers = bool(modifiers & (Qt.ControlModifier | Qt.ShiftModifier))
        if event.button() == Qt.LeftButton and not has_selection_modifiers:
            self._pending_drag_focus_row = self.row(item) if item is not None else self.currentRow()
            self._pending_drag_start_pos = event.position().toPoint()
            self._pending_drag_started_on_item = item is not None
        if not item:
            # Deselect on empty space click
            if event.button() == Qt.LeftButton:
                super().mousePressEvent(event)
                return
            super().mousePressEvent(event)
            return
        
        if event.button() == Qt.RightButton:
            if item.isSelected():
                self.setCurrentItem(item, QItemSelectionModel.NoUpdate)
            else:
                self.clearSelection()
                item.setSelected(True)
                self.setCurrentItem(item, QItemSelectionModel.NoUpdate)
            self.cardRightClicked.emit(item)
            event.accept()
            return

        if event.button() == Qt.LeftButton:
            if has_selection_modifiers:
                super().mousePressEvent(event)
                return
            if item.isSelected() and len(self.selectedItems()) > 1:
                self.setCurrentItem(item, QItemSelectionModel.NoUpdate)
                event.accept()
                return
        
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        start_pos = getattr(self, "_pending_drag_start_pos", None)
        if (
            start_pos is not None
            and event.buttons() & Qt.LeftButton
            and (event.position().toPoint() - start_pos).manhattanLength() >= QApplication.startDragDistance()
        ):
            if getattr(self, "_rubberband_preserve_current_row", None) is None:
                self._rubberband_preserve_current_row = getattr(self, "_pending_drag_focus_row", self.currentRow())
                self._suppress_current_highlight = False
                self.viewport().update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self._pending_drag_focus_row = None
        self._pending_drag_start_pos = None
        self._pending_drag_started_on_item = False
        preserved_row = getattr(self, "_rubberband_preserve_current_row", None)
        self._suppress_current_highlight = False
        if preserved_row is not None:
            self._rubberband_preserve_current_row = None
            if 0 <= preserved_row < self.count():
                vertical_value = self.verticalScrollBar().value()
                horizontal_value = self.horizontalScrollBar().value()
                current_item = self.item(preserved_row)
                if current_item is not None:
                    self.setCurrentItem(current_item, QItemSelectionModel.NoUpdate)
                    self.verticalScrollBar().setValue(vertical_value)
                    self.horizontalScrollBar().setValue(horizontal_value)
        self.viewport().update()

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.SelectAll):
            self.reset_ctrl_shift_selection_state()
            self.selectAll()
            event.accept()
            return
        if event.key() == Qt.Key_Delete:
            self.reset_ctrl_shift_selection_state()
            self.deletePressed.emit()
            event.accept()
            return
        if event.modifiers() == (Qt.ControlModifier | Qt.ShiftModifier):
            if self.handle_ctrl_shift_arrow(event):
                return
        else:
            self.reset_ctrl_shift_selection_state()
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        focus_row = getattr(self, "_rubberband_preserve_current_row", None)
        if focus_row is not None and 0 <= focus_row < self.count():
            focus_item = self.item(focus_row)
        else:
            focus_item = self.currentItem()
        if focus_item is None:
            return
        rect = self.visualItemRect(focus_item)
        if not rect.isValid() or rect.isEmpty():
            return
        painter = QPainter(self.viewport())
        pen = QPen(QColor("#ffd24d"))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect.adjusted(1, 1, -2, -2))


class ZoomableCardView(QGraphicsView):
    cardPreviewRequested = Signal()
    cardCloseRequested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.pixmap_item = QGraphicsPixmapItem()
        self.pixmap_item.setTransformationMode(Qt.SmoothTransformation)
        self.scene.addItem(self.pixmap_item)
        self.placeholder_text = "No Active Game"
        self.original_pixmap = QPixmap()
        self.user_zoom = 1.0
        self.max_user_zoom = 6.0
        self.manual_zoom_enabled = True
        self.manual_pan_enabled = True
        self._is_dragging = False
        self._last_drag_pos = None

        self.setRenderHints(
            QPainter.Antialiasing
            | QPainter.TextAntialiasing
            | QPainter.SmoothPixmapTransform
        )
        if hasattr(QPainter, "LosslessImageRendering"):
            self.setRenderHint(QPainter.LosslessImageRendering, True)
        self.setAlignment(Qt.AlignCenter)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("border: 1px solid #666; background: #111;")
        self.setMinimumSize(120, 160)

    def has_image(self) -> bool:
        return not self.original_pixmap.isNull()

    def show_placeholder(self, text: str) -> None:
        self.placeholder_text = text
        self.original_pixmap = QPixmap()
        self.pixmap_item.setPixmap(QPixmap())
        self.scene.setSceneRect(0, 0, 1, 1)
        self.user_zoom = 1.0
        self.resetTransform()
        self.viewport().update()

    def set_image_path(self, image_path: str) -> None:
        if not image_path or not Path(image_path).exists():
            self.show_placeholder("No image available")
            return
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            self.show_placeholder("No image available")
            return
        self.original_pixmap = pixmap
        self.user_zoom = 1.0
        self._update_scaled_pixmap(center_on_scene=True)

    def zoom_to_default(self) -> None:
        if not self.has_image():
            return
        self.user_zoom = 1.0
        self._update_scaled_pixmap(center_on_scene=True)

    def _fit_scale(self) -> float:
        if self.original_pixmap.isNull():
            return 1.0
        viewport_size = self.viewport().size()
        if viewport_size.width() <= 0 or viewport_size.height() <= 0:
            return 1.0
        pixmap_size = self.original_pixmap.size()
        return min(
            viewport_size.width() / max(1, pixmap_size.width()),
            viewport_size.height() / max(1, pixmap_size.height()),
        )

    def _clamp_relative_position(self, value: float) -> float:
        return max(0.0, min(1.0, value))

    def _relative_scene_position(self, viewport_pos) -> Optional[tuple[float, float]]:
        if not self.has_image():
            return None
        current_pixmap = self.pixmap_item.pixmap()
        if current_pixmap.isNull():
            return None
        scene_pos = self.mapToScene(viewport_pos)
        width = max(1.0, float(current_pixmap.width()))
        height = max(1.0, float(current_pixmap.height()))
        return (
            self._clamp_relative_position(scene_pos.x() / width),
            self._clamp_relative_position(scene_pos.y() / height),
        )

    def _update_scaled_pixmap(
        self,
        center_on_scene: bool = False,
        anchor_viewport_pos=None,
        anchor_relative_pos: Optional[tuple[float, float]] = None,
    ) -> None:
        if self.original_pixmap.isNull():
            self.pixmap_item.setPixmap(QPixmap())
            self.scene.setSceneRect(0, 0, 1, 1)
            return

        scale = self._fit_scale() * self.user_zoom
        target_width = max(1, int(round(self.original_pixmap.width() * scale)))
        target_height = max(1, int(round(self.original_pixmap.height() * scale)))
        scaled_pixmap = self.original_pixmap.scaled(
            target_width,
            target_height,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.pixmap_item.setPixmap(scaled_pixmap)
        self.scene.setSceneRect(scaled_pixmap.rect())

        if center_on_scene:
            self.centerOn(self.pixmap_item.boundingRect().center())
            return

        if anchor_viewport_pos is not None and anchor_relative_pos is not None:
            anchor_x = anchor_relative_pos[0] * scaled_pixmap.width()
            anchor_y = anchor_relative_pos[1] * scaled_pixmap.height()
            self.horizontalScrollBar().setValue(int(round(anchor_x - anchor_viewport_pos.x())))
            self.verticalScrollBar().setValue(int(round(anchor_y - anchor_viewport_pos.y())))

    def wheelEvent(self, event) -> None:
        if not self.has_image():
            event.accept()
            return
        if not self.manual_zoom_enabled:
            event.accept()
            return
        angle = event.angleDelta().y()
        if angle == 0:
            event.accept()
            return
        previous_zoom = self.user_zoom
        if angle > 0:
            self.user_zoom = min(self.max_user_zoom, self.user_zoom * 1.15)
        else:
            self.user_zoom = max(1.0, self.user_zoom / 1.15)
        if abs(self.user_zoom - previous_zoom) < 0.001:
            event.accept()
            return
        anchor_viewport_pos = event.position().toPoint()
        anchor_relative_pos = self._relative_scene_position(anchor_viewport_pos)
        self._update_scaled_pixmap(
            anchor_viewport_pos=anchor_viewport_pos,
            anchor_relative_pos=anchor_relative_pos,
        )
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.RightButton and self.has_image():
            if self.manual_zoom_enabled or self.manual_pan_enabled:
                self.cardCloseRequested.emit()
            else:
                self.cardPreviewRequested.emit()
            event.accept()
            return
        if event.button() == Qt.LeftButton and self.has_image() and self.manual_pan_enabled:
            self._is_dragging = True
            self._last_drag_pos = event.position().toPoint()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._is_dragging and self._last_drag_pos is not None:
            delta = event.position().toPoint() - self._last_drag_pos
            self._last_drag_pos = event.position().toPoint()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._is_dragging:
            self._is_dragging = False
            self._last_drag_pos = None
            self.viewport().unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        if not self._is_dragging:
            self.viewport().unsetCursor()
        super().leaveEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.has_image():
            center_pos = self.viewport().rect().center()
            anchor_relative_pos = self._relative_scene_position(center_pos)
            self._update_scaled_pixmap(
                anchor_viewport_pos=center_pos,
                anchor_relative_pos=anchor_relative_pos,
            )

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.has_image():
            return
        painter = QPainter(self.viewport())
        painter.save()
        painter.setPen(Qt.white)
        painter.setFont(self.font())
        painter.drawText(self.viewport().rect(), Qt.AlignCenter | Qt.TextWordWrap, self.placeholder_text)
        painter.restore()


@dataclass
class Card:
    id: str
    name: str
    value: str
    faction: str
    effect: str
    set_name: str
    card_number: str
    artist_name: str
    card_author: str
    image_path: str
    source: str


@dataclass
class Deck:
    id: str
    name: str
    entries: Dict[str, int]
    card_snapshots: Dict[str, Dict[str, str]]
    updated_at: float


def slugify(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or f"card-{int(time.time() * 1000)}"


def title_from_stem(stem: str) -> str:
    text = stem.replace("_", " ").replace("-", " ").strip()
    return " ".join(part.capitalize() for part in text.split()) or stem


def parse_card_filename(stem: str) -> dict | None:
    """Parse filename following pattern: SetName CardNumber - CardName
    
    Example: 'Dark Portal 001 - Demon Gate'
    Returns: {'set_name': 'Dark Portal', 'card_number': '001', 'name': 'Demon Gate'}
    
    Returns None if filename doesn't match the expected pattern.
    """
    import re
    # Pattern: word(s) + optional spaces, then digits, then space-dash-space, then rest
    match = re.match(r'^(.+?)\s+(\d+)\s*-\s*(.+)$', stem.strip())
    if match:
        set_name = match.group(1).strip()
        card_number = match.group(2).strip()
        card_name = match.group(3).strip()
        return {
            'set_name': set_name,
            'card_number': card_number,
            'name': card_name
        }
    return None


def stable_card_id_for_path(path: Path) -> str:
    return slugify(path.stem)


class Storage:
    def __init__(self) -> None:
        self.base_dir = self._base_dir()
        self.decks_dir = self.base_dir / "decks"
        self.decks_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.base_dir / "config.json"

    def _base_dir(self) -> Path:
        if sys.platform == "win32":
            root = Path.home() / "AppData" / "Roaming"
        elif sys.platform == "darwin":
            root = Path.home() / "Library" / "Application Support"
        else:
            root = Path.home() / ".local" / "share"
        path = root / APP_STORAGE_NAME
        path.mkdir(parents=True, exist_ok=True)
        return path

    def load_config(self) -> dict:
        if not self.config_path.exists():
            return self._default_config()
        try:
            config = json.loads(self.config_path.read_text(encoding="utf-8"))
            return {**self._default_config(), **config}
        except Exception:
            return self._default_config()

    def _default_config(self) -> dict:
        return {
            "window_size": [1450, 900],
            "window_position": [0, 0],
            "window_maximized": False,
            "restore_window_state": True,
            "play_timer_game_start_enabled": True,
            "play_timer_game_start_seconds": 120,
            "play_timer_draw_enabled": True,
            "play_timer_draw_seconds": 300,
        }

    def save_config(self, config: dict) -> None:
        self.config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    def load_decks(self) -> List[Deck]:
        decks: List[Deck] = []
        for deck_file in sorted(self.decks_dir.glob("*.json")):
            try:
                raw = json.loads(deck_file.read_text(encoding="utf-8"))
                decks.append(
                    Deck(
                        id=raw["id"],
                        name=raw["name"],
                        entries=raw.get("entries", {}),
                        card_snapshots=raw.get("card_snapshots", {}),
                        updated_at=raw.get("updated_at", 0),
                    )
                )
            except Exception:
                continue
        return decks

    def save_deck(self, deck: Deck) -> None:
        path = self.decks_dir / f"{deck.id}.json"
        path.write_text(json.dumps(asdict(deck), indent=2), encoding="utf-8")

    def delete_deck(self, deck_id: str) -> None:
        path = self.decks_dir / f"{deck_id}.json"
        if path.exists():
            path.unlink()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.storage = Storage()
        self.config = self.storage.load_config()
        self.cards_folder: Optional[Path] = None
        self.card_maker_image_path: Optional[Path] = None
        self.library: List[Card] = []
        self.library_by_id: Dict[str, Card] = {}
        self.card_icon_cache: Dict[str, tuple[QIcon, Optional[tuple[int, int]]]] = {}
        self.decks: List[Deck] = self.storage.load_decks()
        self.active_preview_dialog: Optional[QDialog] = None
        self.active_preview_view: Optional[ZoomableCardView] = None
        self.active_preview_prev_button: Optional[QPushButton] = None
        self.active_preview_next_button: Optional[QPushButton] = None
        self.active_preview_source: str = ""
        self.active_preview_card_id: str = ""
        self.consume_preview_close_release = False
        self.current_deck_id: Optional[str] = None
        self.builder_entries: Dict[str, int] = {}
        self.play_draw_pile: List[str] = []
        self.play_discard_pile: List[str] = []
        self.play_current_card_id: Optional[str] = None
        self.play_game_active = False
        self.play_draw_log: List[str] = []
        self.timer_mode = "countdown"
        self.timer_started_at: Optional[float] = None
        self.timer_elapsed: float = 0.0
        self.countdown_target_seconds = 300
        self.countdown_remaining_seconds = 300.0
        self.countdown_flash_until: float = 0.0
        self.metronome_bpm = self.default_metronome_bpm()
        self.metronome_beats_per_bar = self.default_metronome_beats()
        self.metronome_current_beat = 0
        self.metronome_last_beat_at = 0.0
        self._restoring_window_geometry = False
        self.last_windowed_geometry: Optional[QRect] = None
        self._restore_pre_snap_after_maximize = False
        self._pre_snap_restore_geometry: Optional[QRect] = None

        self.setWindowTitle(APP_DISPLAY_NAME)
        self.apply_app_icon()
        self.resize(1450, 900)
        self.restore_window_state()
        self.stopwatch_timer = QTimer(self)
        self.stopwatch_timer.timeout.connect(self.refresh_stopwatch)
        self.metronome_timer = QTimer(self)
        self.metronome_timer.timeout.connect(self.advance_metronome_beat)
        self.metronome_bar_timer = QTimer(self)
        self.metronome_bar_timer.timeout.connect(self.update_metronome_bar)
        self.metronome_tick_sound = QSoundEffect(self)
        self.metronome_tick_alt_sound = QSoundEffect(self)
        self.countdown_end_sound = QSoundEffect(self)
        self.countdown_end_sound_playing = False
        self.metronome_keepalive_stream = InfiniteSilenceIODevice(self)
        self.metronome_keepalive_sink: Optional[QAudioSink] = None
        self.metronome_use_alt_tick_sound = False
        self.metronome_tick_sound.setVolume(0.48)
        self.metronome_tick_alt_sound.setVolume(0.48)
        self.countdown_end_sound.setVolume(0.5)
        QApplication.instance().installEventFilter(self)

        self.build_ui()
        self.load_metronome_sounds()
        self.load_countdown_sounds()
        self.setup_metronome_keepalive_audio()
        self.load_saved_folder()
        self.refresh_deck_selects()
        self.render_builder()
        self.render_play_state()
        self.refresh_play_timer_option_state()
        QTimer.singleShot(0, self.prewarm_rules_browser)
        self.timer_mode_combo.blockSignals(True)
        self.timer_mode_combo.setCurrentText("Countdown")
        self.timer_mode_combo.blockSignals(False)
        self.on_timer_mode_changed("Countdown")

    def default_cards_folder(self) -> Path:
        """Return the user cards folder path for config/UI purposes."""
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent / "cards"
        return Path(__file__).resolve().parent / "cards"

    def get_card_source_folder(self) -> Path:
        """Return the active cards folder used for library loading."""
        return self.default_cards_folder()

    def default_sounds_folder(self) -> Path:
        if getattr(sys, "frozen", False):
            bundle_dir = getattr(sys, "_MEIPASS", None)
            if bundle_dir:
                return Path(bundle_dir) / "sounds"
            return Path(sys.executable).resolve().parent / "sounds"
        return Path(__file__).resolve().parent / "sounds"

    def app_icon_path(self) -> Path:
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            bundle_dir = getattr(sys, "_MEIPASS", exe_dir)
            icon_path = exe_dir / "_internal" / "icons" / "main_icon.png"
            if not icon_path.exists() and bundle_dir != exe_dir:
                icon_path = Path(bundle_dir) / "_internal" / "icons" / "main_icon.png"
            return icon_path
        return Path(__file__).resolve().parent / "_internal" / "icons" / "main_icon.png"

    def app_icon_ico_path(self) -> Path:
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            bundle_dir = getattr(sys, "_MEIPASS", exe_dir)
            icon_path = exe_dir / "_internal" / "icons" / "main_icon.ico"
            if not icon_path.exists() and bundle_dir != exe_dir:
                icon_path = Path(bundle_dir) / "_internal" / "icons" / "main_icon.ico"
            return icon_path
        return Path(__file__).resolve().parent / "_internal" / "icons" / "main_icon.ico"

    def apply_app_icon(self) -> None:
        icon_path = self.app_icon_path()
        if not icon_path.exists():
            return
        icon = QIcon(str(icon_path))
        if icon.isNull():
            return
        self.setWindowIcon(icon)
        app = QApplication.instance()
        if app is not None:
            app.setWindowIcon(icon)

    def apply_native_window_icon(self) -> None:
        if sys.platform != "win32":
            return

        icon_path = self.app_icon_ico_path()
        if not icon_path.exists():
            return

        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            hicon = user32.LoadImageW(
                None,
                str(icon_path),
                1,
                0,
                0,
                LR_LOADFROMFILE,
            )
            if not hicon:
                return
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)
        except Exception:
            pass

    def load_metronome_sounds(self) -> None:
        sounds_dir = self.default_sounds_folder()
        tick_wav_path = sounds_dir / "metronome_tick.wav"
        if tick_wav_path.exists():
            tick_source = QUrl.fromLocalFile(str(tick_wav_path.resolve()))
            self.metronome_tick_sound.setSource(tick_source)
            self.metronome_tick_alt_sound.setSource(tick_source)

    def load_countdown_sounds(self) -> None:
        sounds_dir = self.default_sounds_folder()
        countdown_alert_path = sounds_dir / "countdown_alert.wav"
        if countdown_alert_path.exists():
            alert_source = QUrl.fromLocalFile(str(countdown_alert_path.resolve()))
            self.countdown_end_sound.setSource(alert_source)

    def setup_metronome_keepalive_audio(self) -> None:
        try:
            audio_device = QMediaDevices.defaultAudioOutput()
            audio_format = audio_device.preferredFormat() if not audio_device.isNull() else QAudioFormat()
            if audio_format.sampleRate() <= 0:
                audio_format.setSampleRate(44100)
                audio_format.setChannelCount(1)
                audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            self.metronome_keepalive_stream.set_chunk(self.build_metronome_keepalive_chunk(audio_format))
            self.metronome_keepalive_sink = QAudioSink(audio_device, audio_format, self)
            self.metronome_keepalive_stream.open(QIODevice.OpenModeFlag.ReadOnly)
        except Exception:
            self.metronome_keepalive_sink = None

    def build_metronome_keepalive_chunk(self, audio_format: QAudioFormat) -> bytes:
        channels = max(1, audio_format.channelCount())
        frame_count = max(4096, audio_format.sampleRate() // 5)
        sample_format = audio_format.sampleFormat()

        if sample_format == QAudioFormat.SampleFormat.UInt8:
            low = 127
            high = 129
            data = bytearray()
            for index in range(frame_count):
                value = high if (index % 2) else low
                data.extend(bytes([value]) * channels)
            return bytes(data)

        if sample_format == QAudioFormat.SampleFormat.Int32:
            amplitude = 1024
            pack = "<" + ("i" * channels)
            frames = []
            for index in range(frame_count):
                value = amplitude if (index % 2) else -amplitude
                frames.append(struct.pack(pack, *([value] * channels)))
            return b"".join(frames)

        if sample_format == QAudioFormat.SampleFormat.Float:
            amplitude = 0.00012
            pack = "<" + ("f" * channels)
            frames = []
            for index in range(frame_count):
                value = amplitude if (index % 2) else -amplitude
                frames.append(struct.pack(pack, *([value] * channels)))
            return b"".join(frames)

        amplitude = 8
        pack = "<" + ("h" * channels)
        frames = []
        for index in range(frame_count):
            value = amplitude if (index % 2) else -amplitude
            frames.append(struct.pack(pack, *([value] * channels)))
        return b"".join(frames)

    def min_deck_size(self) -> int:
        value = self.config.get("min_deck_size", MIN_DECK_SIZE)
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return MIN_DECK_SIZE

    def save_app_config(self) -> None:
        self.storage.save_config(self.config)

    def save_window_state(self) -> None:
        """Save current window geometry and state to config."""
        if self.isMaximized():
            normal_geom = self.normalGeometry()
            if normal_geom.isValid() and normal_geom.width() >= 400 and normal_geom.height() >= 300:
                geom = QRect(normal_geom)
            else:
                geom = self.last_windowed_geometry or self.geometry()
        else:
            geom = self.geometry()
            self.last_windowed_geometry = QRect(geom)
        self.config["window_size"] = [geom.width(), geom.height()]
        self.config["window_position"] = [geom.x(), geom.y()]
        self.config["window_maximized"] = self.isMaximized()
        self.save_app_config()

    def default_window_geometry_for_screen(self, available: QRect) -> QRect:
        default_width = min(1450, max(900, int(available.width() * 0.85)))
        default_height = min(900, max(650, int(available.height() * 0.85)))
        default_width = min(default_width, available.width())
        default_height = min(default_height, available.height())
        x = available.center().x() - default_width // 2
        y = available.center().y() - default_height // 2
        return QRect(x, y, default_width, default_height)

    def looks_like_maximized_window_size(self, width: int, height: int, available: QRect) -> bool:
        width_close = width >= int(available.width() * 0.97)
        height_close = height >= int(available.height() * 0.97)
        return width_close and height_close

    def restore_window_state(self) -> None:
        """Restore window geometry and state from config, with validation."""
        if not self.restore_window_state_enabled():
            return

        width, height = self.window_size()
        x, y = self.window_position()
        is_maximized = self.window_maximized()

        # Check if position is valid (on screen)
        app = QApplication.instance()
        if app is None:
            return

        screens = app.screens()
        if not screens:
            return

        # If position is at default (0, 0), center on primary screen for first startup
        if x == 0 and y == 0:
            primary_screen = screens[0]
            geom = primary_screen.availableGeometry()
            if is_maximized and self.looks_like_maximized_window_size(width, height, geom):
                safe_geom = self.default_window_geometry_for_screen(geom)
                width = safe_geom.width()
                height = safe_geom.height()
            x = geom.center().x() - width // 2
            y = geom.center().y() - height // 2
            position_valid = True
        else:
            # Check if saved position intersects with any screen
            position_valid = False
            for screen in screens:
                available_geometry = screen.availableGeometry()
                if available_geometry.contains(x + 100, y + 100):
                    if is_maximized and self.looks_like_maximized_window_size(width, height, available_geometry):
                        safe_geom = self.default_window_geometry_for_screen(available_geometry)
                        width = safe_geom.width()
                        height = safe_geom.height()
                        x = safe_geom.x()
                        y = safe_geom.y()
                    position_valid = True
                    break

        if position_valid:
            self.resize(width, height)
            self.move(x, y)
            self.last_windowed_geometry = QRect(x, y, width, height)
        else:
            # Position is invalid, just use the size
            self.resize(width, height)
            self.last_windowed_geometry = QRect(self.x(), self.y(), width, height)

        if is_maximized:
            self.showMaximized()

    def is_snapped_geometry(self, geom: QRect) -> bool:
        """Best-effort detection for Windows snap layouts so restore prefers true windowed geometry."""
        app = QApplication.instance()
        if app is None:
            return False

        tolerance = 24
        for screen in app.screens():
            available = screen.availableGeometry()
            near_full_height = (
                abs(geom.y() - available.y()) <= tolerance
                and abs(geom.height() - available.height()) <= tolerance
            )
            if not near_full_height:
                continue

            touches_left = abs(geom.x() - available.x()) <= tolerance
            touches_right = abs(geom.right() - available.right()) <= tolerance
            width_ratio = geom.width() / max(1, available.width())

            if (touches_left or touches_right) and width_ratio <= 0.7:
                return True

        return False

    def remember_windowed_geometry(self) -> None:
        """Track the last non-maximized, non-snapped window geometry for restore behavior."""
        if self._restoring_window_geometry or self.isMaximized() or self.isFullScreen():
            return

        geom = self.geometry()
        if geom.width() < 400 or geom.height() < 300:
            return
        if self.is_snapped_geometry(geom):
            return

        self.last_windowed_geometry = QRect(geom)

    def default_card_author(self) -> str:
        return str(self.config.get("default_card_author", "")).strip()

    def builder_pool_sort_mode(self) -> str:
        return str(self.config.get("builder_pool_sort_mode", "Sort: Name A-Z")).strip() or "Sort: Name A-Z"

    def system_sounds_enabled(self) -> bool:
        return bool(self.config.get("audio_system_sounds", True))

    def restore_window_state_enabled(self) -> bool:
        return bool(self.config.get("restore_window_state", True))

    def window_size(self) -> tuple[int, int]:
        size = self.config.get("window_size", [1450, 900])
        try:
            return (max(400, int(size[0])), max(300, int(size[1])))
        except (TypeError, ValueError, IndexError):
            return (1450, 900)

    def window_position(self) -> tuple[int, int]:
        pos = self.config.get("window_position", [0, 0])
        try:
            return (int(pos[0]), int(pos[1]))
        except (TypeError, ValueError, IndexError):
            return (0, 0)

    def window_maximized(self) -> bool:
        return bool(self.config.get("window_maximized", False))

    def metronome_audio_warmup_enabled(self) -> bool:
        return bool(self.config.get("audio_metronome_warmup_enabled", True))

    def metronome_audio_warmup_ms(self) -> int:
        value = self.config.get("audio_metronome_warmup_ms", 700)
        try:
            return max(0, min(5000, int(value)))
        except (TypeError, ValueError):
            return 700

    def max_metronome_bpm(self) -> int:
        value = self.config.get("audio_max_metronome_bpm", 360)
        try:
            return max(20, min(2000, int(value)))
        except (TypeError, ValueError):
            return 360

    def default_metronome_bpm(self) -> int:
        value = self.config.get("audio_default_metronome_bpm", 60)
        try:
            return max(20, min(self.max_metronome_bpm(), int(value)))
        except (TypeError, ValueError):
            return 60

    def default_metronome_beats(self) -> int:
        value = self.config.get("audio_default_metronome_beats", 4)
        try:
            return max(1, min(12, int(value)))
        except (TypeError, ValueError):
            return 4

    def play_timer_game_start_enabled(self) -> bool:
        return bool(self.config.get("play_timer_game_start_enabled", True))

    def play_timer_game_start_seconds(self) -> int:
        value = self.config.get("play_timer_game_start_seconds", 120)
        try:
            return max(0, min(35999, int(value)))
        except (TypeError, ValueError):
            return 120

    def play_timer_draw_enabled(self) -> bool:
        return bool(self.config.get("play_timer_draw_enabled", True))

    def play_timer_draw_seconds(self) -> int:
        value = self.config.get("play_timer_draw_seconds", 300)
        try:
            return max(0, min(35999, int(value)))
        except (TypeError, ValueError):
            return 300

    def known_factions(self) -> List[str]:
        return sorted({card.faction.strip() for card in self.library if card.faction.strip()}, key=str.lower)

    def known_set_names(self) -> List[str]:
        return sorted({card.set_name.strip() for card in self.library if card.set_name.strip()}, key=str.lower)

    def refresh_card_maker_factions(self, selected_faction: str = "") -> None:
        current_data = selected_faction.strip()
        if not current_data:
            current_data = ""

        self.card_maker_faction_combo.blockSignals(True)
        self.card_maker_faction_combo.clear()
        self.card_maker_faction_combo.addItem("No Faction", "")
        self.card_maker_faction_combo.addItem("Add New Faction...", "__new__")
        for faction in self.known_factions():
            self.card_maker_faction_combo.addItem(faction, faction)

        if current_data and self.card_maker_faction_combo.findData(current_data) >= 0:
            self.card_maker_faction_combo.setCurrentIndex(self.card_maker_faction_combo.findData(current_data))
            self.card_maker_custom_faction_input.hide()
            self.card_maker_custom_faction_input.clear()
        elif current_data:
            self.card_maker_faction_combo.setCurrentIndex(self.card_maker_faction_combo.findData("__new__"))
            self.card_maker_custom_faction_input.show()
            self.card_maker_custom_faction_input.setText(current_data)
        else:
            self.card_maker_faction_combo.setCurrentIndex(0)
            self.card_maker_custom_faction_input.hide()
            self.card_maker_custom_faction_input.clear()
        self.card_maker_faction_combo.blockSignals(False)

    def on_card_maker_faction_changed(self) -> None:
        is_new = self.card_maker_faction_combo.currentData() == "__new__"
        self.card_maker_custom_faction_input.setVisible(is_new)
        if is_new:
            self.card_maker_custom_faction_input.setFocus()
        else:
            self.card_maker_custom_faction_input.clear()

    def current_card_maker_faction(self) -> str:
        if self.card_maker_faction_combo.currentData() == "__new__":
            return self.card_maker_custom_faction_input.text().strip()
        return str(self.card_maker_faction_combo.currentData() or "").strip()

    def refresh_card_maker_set_names(self, selected_set_name: str = "") -> None:
        current_data = selected_set_name.strip()
        if not current_data:
            current_data = ""

        self.card_maker_set_name_combo.blockSignals(True)
        self.card_maker_set_name_combo.clear()
        self.card_maker_set_name_combo.addItem("No Set Name", "")
        self.card_maker_set_name_combo.addItem("Add New Set Name...", "__new__")
        for set_name in self.known_set_names():
            self.card_maker_set_name_combo.addItem(set_name, set_name)

        if current_data and self.card_maker_set_name_combo.findData(current_data) >= 0:
            self.card_maker_set_name_combo.setCurrentIndex(self.card_maker_set_name_combo.findData(current_data))
            self.card_maker_custom_set_name_input.hide()
            self.card_maker_custom_set_name_input.clear()
        elif current_data:
            self.card_maker_set_name_combo.setCurrentIndex(self.card_maker_set_name_combo.findData("__new__"))
            self.card_maker_custom_set_name_input.show()
            self.card_maker_custom_set_name_input.setText(current_data)
        else:
            self.card_maker_set_name_combo.setCurrentIndex(0)
            self.card_maker_custom_set_name_input.hide()
            self.card_maker_custom_set_name_input.clear()
        self.card_maker_set_name_combo.blockSignals(False)

    def on_card_maker_set_name_changed(self) -> None:
        is_new = self.card_maker_set_name_combo.currentData() == "__new__"
        self.card_maker_custom_set_name_input.setVisible(is_new)
        if is_new:
            self.card_maker_custom_set_name_input.setFocus()
        else:
            self.card_maker_custom_set_name_input.clear()

    def current_card_maker_set_name(self) -> str:
        if self.card_maker_set_name_combo.currentData() == "__new__":
            return self.card_maker_custom_set_name_input.text().strip()
        return str(self.card_maker_set_name_combo.currentData() or "").strip()

    def effect_box_height_for_lines(self, lines: int) -> int:
        metrics = QFontMetrics(self.font())
        return (metrics.lineSpacing() * lines) + 16

    def meta_box_min_height(self, lines: int = 2) -> int:
        metrics = QFontMetrics(self.font())
        return (metrics.lineSpacing() * lines) + 8

    def show_message_box(
        self,
        icon,
        title: str,
        text: str,
        buttons=QMessageBox.Ok,
        default_button=QMessageBox.NoButton,
    ):
        box = QMessageBox(self)
        box.setIcon(icon)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(buttons)
        if default_button != QMessageBox.NoButton:
            box.setDefaultButton(default_button)
        if not self.system_sounds_enabled():
            box.setOption(QMessageBox.DontUseNativeDialog, True)
        elif winsound is not None and sys.platform == "win32":
            beep_type = winsound.MB_OK
            if icon == QMessageBox.Warning:
                beep_type = winsound.MB_ICONWARNING
            elif icon == QMessageBox.Critical:
                beep_type = winsound.MB_ICONHAND
            elif icon == QMessageBox.Question:
                beep_type = winsound.MB_ICONWARNING
            elif icon == QMessageBox.Information:
                beep_type = winsound.MB_ICONASTERISK
            winsound.MessageBeep(beep_type)
        return box.exec()

    def info_box(self, title: str, text: str) -> None:
        self.show_message_box(QMessageBox.Information, title, text)

    def warning_box(self, title: str, text: str) -> None:
        self.show_message_box(QMessageBox.Warning, title, text)

    def critical_box(self, title: str, text: str) -> None:
        self.show_message_box(QMessageBox.Critical, title, text)

    def question_box(self, title: str, text: str, default_button=QMessageBox.No):
        return self.show_message_box(
            QMessageBox.Question,
            title,
            text,
            QMessageBox.Yes | QMessageBox.No,
            default_button,
        )

    def format_effect_html(self, effect_text: str) -> str:
        text = effect_text or "No effect text."
        if not text.strip():
            return "No effect text."
        
        # Find all bracketed sections (including multi-line)
        result = []
        last_end = 0
        
        for match in re.finditer(r'\[([^\[\]]*(?:\n[^\[\]]*)*)\]', text):
            # Add non-bracketed text before this match
            before_text = text[last_end:match.start()]
            if before_text:
                result.append(html.escape(before_text))
            
            # Determine color based on content
            bracketed_content = match.group(0)  # Include the brackets
            color = "#d14cff"
            if "deck" in bracketed_content.lower():
                color = "#ff4d4d"
            
            escaped_bracketed = html.escape(bracketed_content)
            result.append(f'<span style="color: {color}; font-weight: 600;">{escaped_bracketed}</span>')
            last_end = match.end()
        
        # Add remaining text after last match
        remaining = text[last_end:]
        if remaining:
            result.append(html.escape(remaining))
        
        # Join and replace newlines with <br>
        html_text = "".join(result)
        html_text = html_text.replace("\n", "<br>")
        
        return html_text if html_text.strip() else "No effect text."

    def build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)

        tabs = QTabWidget()
        self.tabs = tabs
        outer.addWidget(tabs, 1)

        self.builder_tab = QWidget()
        self.play_tab = QWidget()
        self.card_maker_tab = QWidget()
        self.options_tab = QWidget()
        self.rules_tab = QWidget()
        self.about_tab = QWidget()
        self.card_maker_tab_scroll = self.wrap_tab_in_scroll_area(self.card_maker_tab)
        self.options_tab_scroll = self.wrap_tab_in_scroll_area(self.options_tab)
        self.rules_tab_scroll = self.wrap_tab_in_scroll_area(self.rules_tab)
        self.about_tab_scroll = self.wrap_tab_in_scroll_area(self.about_tab)
        tabs.addTab(self.play_tab, "Play")
        tabs.addTab(self.builder_tab, "Deck Builder")
        tabs.addTab(self.card_maker_tab_scroll, "Card Maker")
        tabs.addTab(self.options_tab_scroll, "Options")
        tabs.addTab(self.rules_tab_scroll, "Rules")
        tabs.addTab(self.about_tab_scroll, "About")

        self.build_builder_tab()
        self.build_play_tab()
        self.build_card_maker_tab()
        self.build_options_tab()
        self.build_rules_tab()
        self.build_about_tab()
        tabs.setCurrentIndex(0)

    def wrap_tab_in_scroll_area(self, content: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setWidget(content)
        return scroll

    def build_options_tab(self) -> None:
        layout = QVBoxLayout(self.options_tab)

        cards_group = QGroupBox("Cards Folder")
        cards_layout = QVBoxLayout(cards_group)
        cards_layout.addWidget(
            QLabel(
                "Cards are loaded from a folder on disk. By default the app uses the "
                "`cards` folder next to the app files."
            )
        )
        self.cards_folder_label = QLabel("No cards folder selected")
        self.cards_folder_label.setWordWrap(True)
        cards_layout.addWidget(self.cards_folder_label)

        button_row = QHBoxLayout()
        choose_folder_button = QPushButton("Choose Cards Folder")
        choose_folder_button.clicked.connect(self.choose_cards_folder)
        button_row.addWidget(choose_folder_button)

        use_default_button = QPushButton("Use Default Folder")
        use_default_button.clicked.connect(self.use_default_cards_folder)
        button_row.addWidget(use_default_button)
        button_row.addStretch(1)
        cards_layout.addLayout(button_row)

        self.default_cards_folder_label = QLabel(f"Default: {self.default_cards_folder()}")
        self.default_cards_folder_label.setWordWrap(True)
        cards_layout.addWidget(self.default_cards_folder_label)

        layout.addWidget(cards_group)

        gameplay_group = QGroupBox("Gameplay")
        gameplay_form = QFormLayout(gameplay_group)
        self.min_deck_size_spin = QSpinBox()
        self.min_deck_size_spin.setMinimum(1)
        self.min_deck_size_spin.setMaximum(999)
        self.min_deck_size_spin.setValue(self.min_deck_size())
        self.min_deck_size_spin.valueChanged.connect(self.on_min_deck_size_changed)
        gameplay_form.addRow("Minimum Deck Size", self.min_deck_size_spin)
        gameplay_hint = QLabel("Default is 30. Saved decks and valid play decks use this value.")
        gameplay_hint.setWordWrap(True)
        gameplay_form.addRow(gameplay_hint)
        layout.addWidget(gameplay_group)

        play_timer_group = QGroupBox("Play Timer")
        play_timer_form = QFormLayout(play_timer_group)
        self.play_timer_game_start_checkbox = QCheckBox("Apply warmup countdown on game start")
        self.play_timer_game_start_checkbox.setChecked(self.play_timer_game_start_enabled())
        self.play_timer_game_start_checkbox.toggled.connect(self.on_play_timer_game_start_toggled)
        play_timer_form.addRow(self.play_timer_game_start_checkbox)
        warmup_row = QHBoxLayout()
        self.play_timer_game_start_minutes_spin = QSpinBox()
        self.play_timer_game_start_minutes_spin.setMinimum(0)
        self.play_timer_game_start_minutes_spin.setMaximum(599)
        self.play_timer_game_start_minutes_spin.setValue(self.play_timer_game_start_seconds() // 60)
        self.play_timer_game_start_minutes_spin.valueChanged.connect(self.on_play_timer_game_start_duration_changed)
        self.play_timer_game_start_seconds_spin = QSpinBox()
        self.play_timer_game_start_seconds_spin.setMinimum(0)
        self.play_timer_game_start_seconds_spin.setMaximum(59)
        self.play_timer_game_start_seconds_spin.setValue(self.play_timer_game_start_seconds() % 60)
        self.play_timer_game_start_seconds_spin.valueChanged.connect(self.on_play_timer_game_start_duration_changed)
        warmup_row.addWidget(QLabel("Min"))
        warmup_row.addWidget(self.play_timer_game_start_minutes_spin)
        warmup_row.addWidget(QLabel("Sec"))
        warmup_row.addWidget(self.play_timer_game_start_seconds_spin)
        warmup_row.addStretch(1)
        play_timer_form.addRow("Warmup Countdown", warmup_row)

        self.play_timer_draw_checkbox = QCheckBox("Apply draw countdown on card draw")
        self.play_timer_draw_checkbox.setChecked(self.play_timer_draw_enabled())
        self.play_timer_draw_checkbox.toggled.connect(self.on_play_timer_draw_toggled)
        play_timer_form.addRow(self.play_timer_draw_checkbox)
        draw_row = QHBoxLayout()
        self.play_timer_draw_minutes_spin = QSpinBox()
        self.play_timer_draw_minutes_spin.setMinimum(0)
        self.play_timer_draw_minutes_spin.setMaximum(599)
        self.play_timer_draw_minutes_spin.setValue(self.play_timer_draw_seconds() // 60)
        self.play_timer_draw_minutes_spin.valueChanged.connect(self.on_play_timer_draw_duration_changed)
        self.play_timer_draw_seconds_spin = QSpinBox()
        self.play_timer_draw_seconds_spin.setMinimum(0)
        self.play_timer_draw_seconds_spin.setMaximum(59)
        self.play_timer_draw_seconds_spin.setValue(self.play_timer_draw_seconds() % 60)
        self.play_timer_draw_seconds_spin.valueChanged.connect(self.on_play_timer_draw_duration_changed)
        draw_row.addWidget(QLabel("Min"))
        draw_row.addWidget(self.play_timer_draw_minutes_spin)
        draw_row.addWidget(QLabel("Sec"))
        draw_row.addWidget(self.play_timer_draw_seconds_spin)
        draw_row.addStretch(1)
        play_timer_form.addRow("Draw Countdown", draw_row)
        play_timer_hint = QLabel(
            "Game start and card draw can automatically switch the timer to Countdown and load your chosen duration."
        )
        play_timer_hint.setWordWrap(True)
        play_timer_form.addRow(play_timer_hint)
        layout.addWidget(play_timer_group)

        deck_builder_group = QGroupBox("Deck Builder")
        deck_builder_form = QFormLayout(deck_builder_group)
        self.default_card_author_input = QLineEdit()
        self.default_card_author_input.setPlaceholderText("Optional default author name")
        self.default_card_author_input.setText(self.default_card_author())
        self.default_card_author_input.textChanged.connect(self.on_default_card_author_changed)
        deck_builder_form.addRow("Default Card Author", self.default_card_author_input)
        author_hint = QLabel(
            "Used only when a new card image is loaded in Card Maker and no matching JSON file is found."
        )
        author_hint.setWordWrap(True)
        deck_builder_form.addRow(author_hint)
        layout.addWidget(deck_builder_group)

        audio_group = QGroupBox("Audio")
        audio_form = QFormLayout(audio_group)
        self.audio_system_sounds_checkbox = QCheckBox("Enable system popup sounds")
        self.audio_system_sounds_checkbox.setChecked(self.system_sounds_enabled())
        self.audio_system_sounds_checkbox.toggled.connect(self.on_audio_system_sounds_toggled)
        audio_form.addRow(self.audio_system_sounds_checkbox)
        self.metronome_audio_warmup_checkbox = QCheckBox("Warm up metronome audio")
        self.metronome_audio_warmup_checkbox.setChecked(self.metronome_audio_warmup_enabled())
        self.metronome_audio_warmup_checkbox.toggled.connect(self.on_metronome_audio_warmup_toggled)
        audio_form.addRow(self.metronome_audio_warmup_checkbox)
        self.metronome_audio_warmup_spin = QSpinBox()
        self.metronome_audio_warmup_spin.setMinimum(0)
        self.metronome_audio_warmup_spin.setMaximum(5000)
        self.metronome_audio_warmup_spin.setSuffix(" ms")
        self.metronome_audio_warmup_spin.setSingleStep(100)
        self.metronome_audio_warmup_spin.setValue(self.metronome_audio_warmup_ms())
        self.metronome_audio_warmup_spin.setEnabled(self.metronome_audio_warmup_enabled())
        self.metronome_audio_warmup_spin.valueChanged.connect(self.on_metronome_audio_warmup_ms_changed)
        audio_form.addRow("Metronome Warmup Delay", self.metronome_audio_warmup_spin)
        self.max_metronome_bpm_spin = QSpinBox()
        self.max_metronome_bpm_spin.setMinimum(20)
        self.max_metronome_bpm_spin.setMaximum(2000)
        self.max_metronome_bpm_spin.setSingleStep(10)
        self.max_metronome_bpm_spin.setValue(self.max_metronome_bpm())
        self.max_metronome_bpm_spin.valueChanged.connect(self.on_max_metronome_bpm_changed)
        audio_form.addRow("Maximum Metronome BPM", self.max_metronome_bpm_spin)
        self.default_metronome_bpm_spin = QSpinBox()
        self.default_metronome_bpm_spin.setMinimum(20)
        self.default_metronome_bpm_spin.setMaximum(self.max_metronome_bpm())
        self.default_metronome_bpm_spin.setValue(self.default_metronome_bpm())
        self.default_metronome_bpm_spin.valueChanged.connect(self.on_default_metronome_bpm_changed)
        audio_form.addRow("Default Metronome BPM", self.default_metronome_bpm_spin)
        self.default_metronome_beats_spin = QSpinBox()
        self.default_metronome_beats_spin.setMinimum(1)
        self.default_metronome_beats_spin.setMaximum(12)
        self.default_metronome_beats_spin.setValue(self.default_metronome_beats())
        self.default_metronome_beats_spin.valueChanged.connect(self.on_default_metronome_beats_changed)
        audio_form.addRow("Default Metronome Beats", self.default_metronome_beats_spin)
        audio_hint = QLabel(
            "Popup sounds use native system alerts. Metronome warmup can help optical receivers and external decoders lock on before the first beat."
        )
        audio_hint.setWordWrap(True)
        audio_form.addRow(audio_hint)
        layout.addWidget(audio_group)

        window_group = QGroupBox("Window")
        window_form = QFormLayout(window_group)
        self.restore_window_state_checkbox = QCheckBox("Remember window size and position on exit")
        self.restore_window_state_checkbox.setChecked(self.restore_window_state_enabled())
        self.restore_window_state_checkbox.toggled.connect(self.on_restore_window_state_toggled)
        window_form.addRow(self.restore_window_state_checkbox)
        window_hint = QLabel("When enabled, the app will restore the window to the same size and position from the previous session.")
        window_hint.setWordWrap(True)
        window_form.addRow(window_hint)
        layout.addWidget(window_group)

        layout.addStretch(1)

    def build_about_tab(self) -> None:
        layout = QVBoxLayout(self.about_tab)

        summary_group = QGroupBox("Beta Cards")
        summary_layout = QFormLayout(summary_group)
        summary_layout.addRow("Version", QLabel(APP_VERSION))
        summary_layout.addRow("Creators", QLabel("Triple Alfa & GPT-5.4"))
        summary_layout.addRow("Platform", QLabel(sys.platform))
        layout.addWidget(summary_group)

        info_group = QGroupBox("Application Info")
        info_layout = QVBoxLayout(info_group)
        info_text = QLabel(
            "\n".join(
                [
                    "Beta Cards is a companion app for the similarly named digital card game.",
                    f"Default cards folder: {self.default_cards_folder()}",
                    f"App data folder: {self.storage.base_dir}",
                ]
            )
        )
        info_text.setWordWrap(True)
        info_layout.addWidget(info_text)
        layout.addWidget(info_group)

        notes_group = QGroupBox("Release Notes")
        notes_layout = QVBoxLayout(notes_group)
        notes_text = QLabel(APP_RELEASE_NOTES)
        notes_text.setWordWrap(True)
        notes_layout.addWidget(notes_text)
        layout.addWidget(notes_group)

        layout.addStretch(1)

    def build_rules_tab(self) -> None:
        layout = QVBoxLayout(self.rules_tab)

        content_row = QHBoxLayout()
        content_row.addStretch(1)
        content_container = QWidget()
        content_container.setMaximumWidth(1400)
        content_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        content_layout = QVBoxLayout(content_container)
        content_layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("Game Rules")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 52px; font-weight: bold; padding: 4px;")
        content_layout.addWidget(title)

        # Create QTextBrowser to display HTML-formatted rules
        self.rules_browser = QTextBrowser()
        self.rules_browser.setReadOnly(True)
        self.rules_browser.setOpenExternalLinks(False)
        self.rules_browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.rules_browser.setStyleSheet(
            "QTextBrowser { border: 1px solid #ccc; padding: 8px; }"
        )
        
        # Load and display rules
        rules_html = self.load_rules_content()
        self.rules_browser.setHtml(rules_html)

        content_layout.addWidget(self.rules_browser)
        content_row.addWidget(content_container, 6)
        content_row.addStretch(1)
        layout.addLayout(content_row, 1)
    
    def load_rules_content(self) -> str:
        """Load the rules from the ODT file and return as HTML."""
        rules_path = self.get_rules_path()
        
        if not rules_path.exists():
            return "<p><i>Rules file not found at: " + str(rules_path) + "</i></p>"
        
        return load_rules_as_html(rules_path)

    def prewarm_rules_browser(self) -> None:
        """Force the Rules document to perform its initial text layout before first use."""
        if not hasattr(self, "rules_browser") or self.rules_browser is None:
            return

        document = self.rules_browser.document()
        viewport = self.rules_browser.viewport()
        text_width = max(800, viewport.width() - 18)
        document.setTextWidth(text_width)
        document.adjustSize()
    
    def get_rules_path(self) -> Path:
        """Return the path to the rules file."""
        if getattr(sys, "frozen", False):
            # Running as compiled executable
            internal_dir = Path(sys.executable).parent / "_internal"
        else:
            # Running as script
            internal_dir = Path(__file__).resolve().parent / "_internal"
        
        return internal_dir / "rules" / "Rules.odt"

    def build_card_maker_tab(self) -> None:
        layout = QVBoxLayout(self.card_maker_tab)

        choose_row = QHBoxLayout()
        choose_button = QPushButton("Choose Card Image")
        choose_button.clicked.connect(self.choose_card_maker_image)
        choose_row.addWidget(choose_button)
        self.card_maker_image_path_label = QLabel("No image selected")
        choose_row.addWidget(self.card_maker_image_path_label, 1)
        layout.addLayout(choose_row)

        preview_and_form = QGridLayout()
        self.card_maker_preview = QLabel("Select an image to preview the card")
        self.card_maker_preview.setAlignment(Qt.AlignCenter)
        self.card_maker_preview.setMinimumHeight(320)
        self.card_maker_preview.setStyleSheet("border: 1px solid #666;")
        preview_and_form.addWidget(self.card_maker_preview, 0, 0)

        form_box = QGroupBox("Card Details")
        form = QFormLayout(form_box)
        self.card_maker_name_input = QLineEdit()
        self.card_maker_name_input.setPlaceholderText("Card name")
        self.card_maker_value_input = QLineEdit()
        self.card_maker_value_input.setPlaceholderText("Value")
        self.card_maker_faction_combo = QComboBox()
        self.card_maker_faction_combo.currentIndexChanged.connect(self.on_card_maker_faction_changed)
        self.card_maker_custom_faction_input = QLineEdit()
        self.card_maker_custom_faction_input.setPlaceholderText("Enter a new faction")
        self.card_maker_custom_faction_input.hide()
        self.card_maker_set_name_combo = QComboBox()
        self.card_maker_set_name_combo.currentIndexChanged.connect(self.on_card_maker_set_name_changed)
        self.card_maker_custom_set_name_input = QLineEdit()
        self.card_maker_custom_set_name_input.setPlaceholderText("Enter a new set name")
        self.card_maker_custom_set_name_input.hide()
        self.card_maker_card_number_input = QLineEdit()
        self.card_maker_card_number_input.setPlaceholderText("Card number")
        self.card_maker_artist_name_input = QLineEdit()
        self.card_maker_artist_name_input.setPlaceholderText("Artist name")
        self.card_maker_card_author_input = QLineEdit()
        self.card_maker_card_author_input.setPlaceholderText("Card author")
        self.card_maker_effect_input = QTextEdit()
        self.card_maker_effect_input.setPlaceholderText("Effect text")
        form.addRow("Name", self.card_maker_name_input)
        form.addRow("Value", self.card_maker_value_input)
        form.addRow("Faction", self.card_maker_faction_combo)
        form.addRow("", self.card_maker_custom_faction_input)
        form.addRow("Set Name", self.card_maker_set_name_combo)
        form.addRow("", self.card_maker_custom_set_name_input)
        form.addRow("Card Number", self.card_maker_card_number_input)
        form.addRow("Artist Name", self.card_maker_artist_name_input)
        form.addRow("Card Author", self.card_maker_card_author_input)
        form.addRow("Effect", self.card_maker_effect_input)

        create_button = QPushButton("Create Matching JSON")
        create_button.clicked.connect(self.create_card_json)
        form.addRow(create_button)
        self.card_maker_status_label = QLabel("The JSON file will be saved next to the selected image.")
        self.card_maker_status_label.setWordWrap(True)
        form.addRow(self.card_maker_status_label)

        preview_and_form.addWidget(form_box, 0, 1)
        preview_and_form.setColumnStretch(0, 1)
        preview_and_form.setColumnStretch(1, 1)
        layout.addLayout(preview_and_form, 1)
        self.refresh_card_maker_factions()
        self.card_maker_faction_combo.setCurrentIndex(0)
        self.refresh_card_maker_set_names()
        self.card_maker_set_name_combo.setCurrentIndex(0)

    def build_builder_tab(self) -> None:
        layout = QVBoxLayout(self.builder_tab)
        top = QHBoxLayout()
        self.deck_name_input = QLineEdit()
        self.deck_name_input.setPlaceholderText("Deck name")
        top.addWidget(self.deck_name_input, 1)
        self.saved_decks_combo = QComboBox()
        self.saved_decks_combo.currentIndexChanged.connect(self.load_selected_saved_deck)
        top.addWidget(self.saved_decks_combo)
        new_button = QPushButton("New Deck")
        new_button.clicked.connect(self.reset_builder)
        duplicate_button = QPushButton("Duplicate Deck")
        duplicate_button.clicked.connect(self.duplicate_deck)
        save_button = QPushButton("Save Deck")
        save_button.clicked.connect(self.save_deck)
        delete_button = QPushButton("Delete Deck")
        delete_button.clicked.connect(self.delete_deck)
        top.addWidget(new_button)
        top.addWidget(duplicate_button)
        top.addWidget(save_button)
        top.addWidget(delete_button)
        layout.addLayout(top)
        self.builder_save_status_label = QLabel("")
        self.builder_save_status_label.setStyleSheet("color: #8fd694; font-size: 12px;")
        layout.addWidget(self.builder_save_status_label)

        pool_group = QGroupBox("Card Pool")
        pool_layout = QVBoxLayout(pool_group)
        pool_search_row = QHBoxLayout()
        self.builder_pool_search = QLineEdit()
        self.builder_pool_search.setPlaceholderText("Search cards to add")
        self.builder_pool_search.textChanged.connect(self.render_builder)
        pool_search_row.addWidget(self.builder_pool_search, 1)
        self.builder_pool_faction_filter = QComboBox()
        self.builder_pool_faction_filter.currentIndexChanged.connect(self.render_builder)
        pool_search_row.addWidget(self.builder_pool_faction_filter)
        self.builder_pool_min_value = QLineEdit()
        self.builder_pool_min_value.setPlaceholderText("Min Value")
        self.builder_pool_min_value.textChanged.connect(self.render_builder)
        self.builder_pool_min_value.setFixedWidth(90)
        pool_search_row.addWidget(self.builder_pool_min_value)
        self.builder_pool_max_value = QLineEdit()
        self.builder_pool_max_value.setPlaceholderText("Max Value")
        self.builder_pool_max_value.textChanged.connect(self.render_builder)
        self.builder_pool_max_value.setFixedWidth(90)
        pool_search_row.addWidget(self.builder_pool_max_value)
        self.builder_pool_deck_filter = QComboBox()
        self.builder_pool_deck_filter.addItems(["All Cards", "In Deck", "Not In Deck"])
        self.builder_pool_deck_filter.currentIndexChanged.connect(self.render_builder)
        pool_search_row.addWidget(self.builder_pool_deck_filter)
        self.builder_pool_sort_combo = QComboBox()
        self.builder_pool_sort_combo.addItems(
            [
                "Sort: Name A-Z",
                "Sort: Name Z-A",
                "Sort: Value Low-High",
                "Sort: Value High-Low",
                "Sort: Faction A-Z",
                "Sort: Set / Number",
            ]
        )
        sort_index = self.builder_pool_sort_combo.findText(self.builder_pool_sort_mode())
        if sort_index >= 0:
            self.builder_pool_sort_combo.setCurrentIndex(sort_index)
        self.builder_pool_sort_combo.currentIndexChanged.connect(self.on_builder_pool_sort_changed)
        pool_search_row.addWidget(self.builder_pool_sort_combo)
        refresh_pool_button = QPushButton("Refresh (F5)")
        refresh_pool_button.setFixedWidth(100)
        refresh_pool_button.clicked.connect(self.refresh_cards_pool)
        pool_search_row.addWidget(refresh_pool_button)
        self.builder_pool_count_label = QLabel("Cards shown: 0")
        pool_search_row.addWidget(self.builder_pool_count_label)
        pool_layout.addLayout(pool_search_row)

        self.builder_pool_list = CardGridListWidget()
        self.builder_pool_list.setViewMode(QListView.IconMode)
        self.builder_pool_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.builder_pool_list.setIconSize(QSize(286, 400))
        self.builder_pool_list.setResizeMode(QListView.Adjust)
        self.builder_pool_list.setMovement(QListView.Static)
        self.builder_pool_list.setSpacing(12)
        self.builder_pool_list.setGridSize(QSize(306, 452))
        self.builder_pool_list.setWordWrap(False)
        self.builder_pool_list.setTextElideMode(Qt.ElideRight)
        grid_font = QFont(self.builder_pool_list.font())
        grid_font.setPointSize(max(7, grid_font.pointSize() - 1))
        self.builder_pool_list.setFont(grid_font)
        self.builder_pool_list.cardActivated.connect(self.add_pool_card_from_click)
        self.builder_pool_list.currentItemChanged.connect(self.update_builder_pool_detail)
        self.builder_pool_list.cardRightClicked.connect(self.show_builder_pool_context_menu)

        pool_instructions = QLabel(
            "Left-click once to select a card, double-click to add one copy, double-click in the deck to remove one copy, and right-click to open a larger preview."
        )
        pool_instructions.setWordWrap(True)
        pool_layout.addWidget(pool_instructions)

        pool_detail_group = QGroupBox("Card Details")
        pool_detail_form = QFormLayout(pool_detail_group)
        self.builder_pool_name = QLabel("-")
        self.builder_pool_value = QLabel("-")
        self.builder_pool_value.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.builder_pool_faction = QLabel("-")
        self.builder_pool_faction.setStyleSheet("font-size: 14px; font-weight: 600;")
        self.builder_pool_meta = QTextEdit()
        self.builder_pool_meta.setReadOnly(True)
        self.builder_pool_meta.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.builder_pool_meta.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.builder_pool_meta.setLineWrapMode(QTextEdit.WidgetWidth)
        self.builder_pool_meta.setMinimumHeight(self.meta_box_min_height())
        self.builder_pool_meta.setMaximumHeight(self.effect_box_height_for_lines(3))
        self.builder_pool_meta.setPlainText("-")
        self.builder_pool_effect = QTextEdit()
        self.builder_pool_effect.setReadOnly(True)
        self.builder_pool_effect.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.builder_pool_effect.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.builder_pool_effect.setLineWrapMode(QTextEdit.WidgetWidth)
        self.builder_pool_effect.setMinimumHeight(self.effect_box_height_for_lines(6))
        self.builder_pool_effect.setPlainText("-")
        pool_detail_form.addRow("Name", self.builder_pool_name)
        pool_detail_form.addRow("Value", self.builder_pool_value)
        pool_detail_form.addRow("Faction", self.builder_pool_faction)
        pool_detail_form.addRow("Meta", self.builder_pool_meta)
        pool_detail_form.addRow("Effect", self.builder_pool_effect)

        pool_splitter = QSplitter(Qt.Vertical)
        pool_splitter.addWidget(self.builder_pool_list)
        pool_splitter.addWidget(pool_detail_group)
        pool_splitter.setChildrenCollapsible(False)
        pool_splitter.setStretchFactor(0, 3)
        pool_splitter.setStretchFactor(1, 1)
        pool_splitter.setSizes([720, 240])
        pool_layout.addWidget(pool_splitter, 1)

        self.deck_group = QGroupBox(f"Deck (0/{self.min_deck_size()})")
        deck_layout = QVBoxLayout(self.deck_group)
        deck_stats_row = QHBoxLayout()
        self.deck_total_value_label = QLabel("Total Value: 0")
        self.deck_average_value_label = QLabel("Average Value: -")
        deck_stats_row.addWidget(self.deck_total_value_label)
        deck_stats_row.addStretch(1)
        deck_stats_row.addWidget(self.deck_average_value_label)
        deck_layout.addLayout(deck_stats_row)

        self.deck_entries_list = DeckListWidget()
        self.deck_entries_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.deck_entries_list.currentItemChanged.connect(self.update_deck_entry_detail)
        self.deck_entries_list.cardDoubleClicked.connect(self.remove_one_copy_from_deck_item)
        self.deck_entries_list.cardRightClicked.connect(self.show_deck_card_preview)
        self.deck_entries_list.deletePressed.connect(self.remove_selected_deck_entries_completely)

        self.deck_stats_toggle = QPushButton("Show Deck Stats")
        self.deck_stats_toggle.setCheckable(True)
        self.deck_stats_toggle.toggled.connect(self.on_deck_stats_toggled)
        self.deck_stats_tabs = QTabWidget()
        self.deck_faction_stats_table = self.create_deck_stats_table("Faction")
        self.deck_value_stats_table = self.create_deck_stats_table("Value")
        self.deck_stats_tabs.addTab(self.deck_faction_stats_table, "Faction (0)")
        self.deck_stats_tabs.addTab(self.deck_value_stats_table, "Value (0)")
        self.deck_stats_tabs.setVisible(False)

        deck_list_panel = QWidget()
        deck_list_layout = QVBoxLayout(deck_list_panel)
        deck_list_layout.setContentsMargins(0, 0, 0, 0)
        deck_list_layout.addWidget(self.deck_entries_list, 1)
        deck_list_layout.addWidget(self.deck_stats_toggle)
        deck_list_layout.addWidget(self.deck_stats_tabs, 1)

        detail_group = QGroupBox("Card Details")
        form = QFormLayout(detail_group)
        self.deck_entry_name = QLabel("-")
        self.deck_entry_value = QLabel("-")
        self.deck_entry_value.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.deck_entry_faction = QLabel("-")
        self.deck_entry_faction.setStyleSheet("font-size: 14px; font-weight: 600;")
        self.deck_entry_effect = QTextEdit()
        self.deck_entry_effect.setReadOnly(True)
        self.deck_entry_effect.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.deck_entry_effect.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.deck_entry_effect.setLineWrapMode(QTextEdit.WidgetWidth)
        self.deck_entry_effect.setMinimumHeight(self.effect_box_height_for_lines(6))
        self.deck_entry_effect.setPlainText("-")
        self.deck_entry_meta = QTextEdit()
        self.deck_entry_meta.setReadOnly(True)
        self.deck_entry_meta.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.deck_entry_meta.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.deck_entry_meta.setLineWrapMode(QTextEdit.WidgetWidth)
        self.deck_entry_meta.setMinimumHeight(self.meta_box_min_height())
        self.deck_entry_meta.setMaximumHeight(self.effect_box_height_for_lines(3))
        self.deck_entry_meta.setPlainText("-")
        self.deck_entry_quantity = QSpinBox()
        self.deck_entry_quantity.setMinimum(0)
        self.deck_entry_quantity.setMaximum(99)
        self.deck_entry_quantity.valueChanged.connect(self.update_selected_entry_quantity)
        form.addRow("Name", self.deck_entry_name)
        form.addRow("Value", self.deck_entry_value)
        form.addRow("Faction", self.deck_entry_faction)
        form.addRow("Effect", self.deck_entry_effect)
        form.addRow("Meta", self.deck_entry_meta)
        form.addRow("Quantity", self.deck_entry_quantity)

        deck_splitter = QSplitter(Qt.Vertical)
        deck_splitter.addWidget(deck_list_panel)
        deck_splitter.addWidget(detail_group)
        deck_splitter.setChildrenCollapsible(False)
        deck_splitter.setStretchFactor(0, 7)
        deck_splitter.setStretchFactor(1, 3)
        deck_splitter.setSizes([700, 300])
        deck_layout.addWidget(deck_splitter, 1)

        horizontal_splitter = QSplitter(Qt.Horizontal)
        horizontal_splitter.addWidget(pool_group)
        horizontal_splitter.addWidget(self.deck_group)
        horizontal_splitter.setChildrenCollapsible(False)
        horizontal_splitter.setStretchFactor(0, 5)
        horizontal_splitter.setStretchFactor(1, 1)
        horizontal_splitter.setSizes([1080, 280])
        layout.addWidget(horizontal_splitter, 1)

    def build_play_tab(self) -> None:
        layout = QVBoxLayout(self.play_tab)
        controls = QHBoxLayout()
        self.play_deck_combo = QComboBox()
        self.play_deck_combo.currentIndexChanged.connect(self.on_play_deck_selection_changed)
        controls.addWidget(self.play_deck_combo, 1)
        self.start_game_button = QPushButton("Start Game")
        self.start_game_button.clicked.connect(self.start_game)
        self.reset_game_button = QPushButton("Reset Game")
        self.reset_game_button.clicked.connect(self.confirm_reset_game)
        controls.addWidget(self.start_game_button)
        controls.addWidget(self.reset_game_button)
        layout.addLayout(controls)

        grid = QGridLayout()
        left_panel = QGroupBox("Deck")
        left_layout = QVBoxLayout(left_panel)
        self.remaining_label = QLabel("Remaining: 0")
        left_layout.addWidget(self.remaining_label)
        self.play_deck_list = DeckListWidget()
        self.play_deck_list.cardRightClicked.connect(self.show_play_list_card_preview)
        left_layout.addWidget(self.play_deck_list, 1)
        self.play_history_toggle = QPushButton("Show History")
        self.play_history_toggle.setCheckable(True)
        self.play_history_toggle.toggled.connect(self.on_play_history_toggled)
        left_layout.addWidget(self.play_history_toggle)
        self.play_history_tabs = QTabWidget()
        self.discard_list = DeckListWidget()
        self.discard_list.cardRightClicked.connect(self.show_play_list_card_preview)
        self.draw_log_list = DeckListWidget()
        self.draw_log_list.cardRightClicked.connect(self.show_play_list_card_preview)
        self.play_history_tabs.addTab(self.discard_list, "Discard (0)")
        self.play_history_tabs.addTab(self.draw_log_list, "Draw Log (0)")
        self.play_history_tabs.setVisible(False)
        left_layout.addWidget(self.play_history_tabs, 1)
        left_scroll = self.wrap_tab_in_scroll_area(left_panel)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        grid.addWidget(left_scroll, 0, 0)

        self.play_image_view = ZoomableCardView()
        self.play_image_view.manual_zoom_enabled = False
        self.play_image_view.manual_pan_enabled = False
        self.play_image_view.cardPreviewRequested.connect(self.show_current_play_card_preview)
        center_layout = QVBoxLayout()
        center_layout.addWidget(self.play_image_view, 1)
        self.play_image_hint_label = QLabel("Right-click the card image to open the larger zoomable preview.")
        self.play_image_hint_label.setAlignment(Qt.AlignCenter)
        self.play_image_hint_label.setWordWrap(True)
        center_layout.addWidget(self.play_image_hint_label)
        grid.addLayout(center_layout, 0, 1)

        right = QVBoxLayout()
        timer_group = QGroupBox("Timer")
        timer_layout = QVBoxLayout(timer_group)
        timer_mode_row = QHBoxLayout()
        self.timer_mode_combo = QComboBox()
        self.timer_mode_combo.addItems(["Stopwatch", "Countdown"])
        self.timer_mode_combo.currentTextChanged.connect(self.on_timer_mode_changed)
        timer_mode_row.addWidget(self.timer_mode_combo, 1)
        timer_layout.addLayout(timer_mode_row)

        self.stopwatch_label = QLabel("00:00.0")
        self.stopwatch_label.setAlignment(Qt.AlignCenter)
        self.stopwatch_label.setStyleSheet("font-size: 28px; font-weight: bold;")
        timer_layout.addWidget(self.stopwatch_label)

        countdown_row = QHBoxLayout()
        countdown_row.setSpacing(6)
        self.countdown_minutes_spin = QSpinBox()
        self.countdown_minutes_spin.setMinimum(0)
        self.countdown_minutes_spin.setMaximum(999)
        self.countdown_minutes_spin.setValue(5)
        self.countdown_minutes_spin.valueChanged.connect(self.on_countdown_inputs_changed)
        self.countdown_minutes_spin.setFixedWidth(84)
        self.countdown_seconds_spin = QSpinBox()
        self.countdown_seconds_spin.setMinimum(0)
        self.countdown_seconds_spin.setMaximum(59)
        self.countdown_seconds_spin.setValue(0)
        self.countdown_seconds_spin.valueChanged.connect(self.on_countdown_inputs_changed)
        self.countdown_seconds_spin.setFixedWidth(84)
        countdown_row.addStretch(1)
        countdown_row.addWidget(QLabel("Min"))
        countdown_row.addWidget(self.countdown_minutes_spin)
        countdown_row.addWidget(QLabel("Sec"))
        countdown_row.addWidget(self.countdown_seconds_spin)
        countdown_row.addStretch(1)
        timer_layout.addLayout(countdown_row)

        timer_buttons = QHBoxLayout()
        timer_start_button = QPushButton("Start")
        timer_start_button.clicked.connect(self.start_stopwatch)
        timer_pause_button = QPushButton("Pause")
        timer_pause_button.clicked.connect(self.pause_stopwatch)
        timer_reset_button = QPushButton("Reset")
        timer_reset_button.clicked.connect(self.reset_stopwatch)
        timer_buttons.addWidget(timer_start_button)
        timer_buttons.addWidget(timer_pause_button)
        timer_buttons.addWidget(timer_reset_button)
        timer_layout.addLayout(timer_buttons)

        self.countdown_alert_sound_checkbox = QCheckBox("Countdown alert sound")
        self.countdown_alert_sound_checkbox.setChecked(True)
        timer_layout.addWidget(self.countdown_alert_sound_checkbox)

        timer_group.setLayout(timer_layout)
        right.addWidget(timer_group)

        metronome_group = QGroupBox("Metronome")
        metronome_layout = QVBoxLayout(metronome_group)
        self.metronome_bar = QProgressBar()
        self.metronome_bar.setOrientation(Qt.Vertical)
        self.metronome_bar.setRange(0, 100)
        self.metronome_bar.setValue(0)
        self.metronome_bar.setFormat("1")
        self.metronome_bar.setAlignment(Qt.AlignCenter)
        self.metronome_bar.setTextVisible(True)
        self.metronome_bar.setFixedHeight(120)
        self.metronome_bar.setFixedWidth(132)
        self.metronome_bar.setStyleSheet(
            "QProgressBar { border: 1px solid #666; border-radius: 10px; background: #161616; "
            "color: #f5f5f5; font-size: 28px; font-weight: bold; text-align: center; }"
            "QProgressBar::chunk { background: #ffb347; border-radius: 8px; }"
        )
        metronome_meter_row = QHBoxLayout()
        metronome_meter_row.addStretch(1)
        metronome_meter_row.addWidget(self.metronome_bar)
        metronome_meter_row.addStretch(1)
        metronome_layout.addLayout(metronome_meter_row)

        metronome_settings = QHBoxLayout()
        self.metronome_bpm_spin = QSpinBox()
        self.metronome_bpm_spin.setMinimum(20)
        self.metronome_bpm_spin.setMaximum(self.max_metronome_bpm())
        self.metronome_bpm_spin.setValue(self.metronome_bpm)
        self.metronome_bpm_spin.valueChanged.connect(self.on_metronome_settings_changed)
        self.metronome_bpm_spin.setFixedWidth(84)
        self.metronome_beats_spin = QSpinBox()
        self.metronome_beats_spin.setMinimum(1)
        self.metronome_beats_spin.setMaximum(12)
        self.metronome_beats_spin.setValue(self.metronome_beats_per_bar)
        self.metronome_beats_spin.valueChanged.connect(self.on_metronome_settings_changed)
        self.metronome_beats_spin.setFixedWidth(84)
        metronome_settings.addStretch(1)
        metronome_settings.addWidget(QLabel("BPM"))
        metronome_settings.addWidget(self.metronome_bpm_spin)
        metronome_settings.addWidget(QLabel("Beats"))
        metronome_settings.addWidget(self.metronome_beats_spin)
        self.metronome_sound_checkbox = QCheckBox("Click sound")
        self.metronome_sound_checkbox.setChecked(True)
        self.metronome_sound_checkbox.toggled.connect(self.on_metronome_sound_toggled)
        metronome_layout.addWidget(self.metronome_sound_checkbox)
        self.metronome_visual_checkbox = QCheckBox("Visual beat meter")
        self.metronome_visual_checkbox.setChecked(True)
        self.metronome_visual_checkbox.toggled.connect(self.on_metronome_visual_toggled)
        metronome_layout.addWidget(self.metronome_visual_checkbox)

        metronome_settings.addStretch(1)
        metronome_layout.addLayout(metronome_settings)

        metronome_buttons = QHBoxLayout()
        metronome_start_button = QPushButton("Start")
        metronome_start_button.clicked.connect(self.start_metronome)
        metronome_pause_button = QPushButton("Pause")
        metronome_pause_button.clicked.connect(self.pause_metronome)
        metronome_reset_button = QPushButton("Reset")
        metronome_reset_button.clicked.connect(self.reset_metronome)
        metronome_buttons.addWidget(metronome_start_button)
        metronome_buttons.addWidget(metronome_pause_button)
        metronome_buttons.addWidget(metronome_reset_button)
        metronome_layout.addLayout(metronome_buttons)
        right.addWidget(metronome_group)

        randomizer_group = QGroupBox("Randomizer")
        randomizer_layout = QVBoxLayout(randomizer_group)

        dice_group = QGroupBox("Dice Roller")
        dice_layout = QVBoxLayout(dice_group)
        dice_controls = QHBoxLayout()
        self.dice_count_spin = QSpinBox()
        self.dice_count_spin.setMinimum(1)
        self.dice_count_spin.setMaximum(99)
        self.dice_count_spin.setValue(1)
        self.dice_count_spin.setFixedWidth(84)
        self.dice_sides_spin = QSpinBox()
        self.dice_sides_spin.setMinimum(2)
        self.dice_sides_spin.setMaximum(999)
        self.dice_sides_spin.setValue(6)
        self.dice_sides_spin.setFixedWidth(84)
        dice_controls.addStretch(1)
        dice_controls.addWidget(QLabel("Dice"))
        dice_controls.addWidget(self.dice_count_spin)
        dice_controls.addWidget(QLabel("Sides"))
        dice_controls.addWidget(self.dice_sides_spin)
        dice_controls.addStretch(1)
        dice_layout.addLayout(dice_controls)
        roll_dice_button = QPushButton("Roll Dice")
        roll_dice_button.clicked.connect(self.roll_dice)
        dice_layout.addWidget(roll_dice_button)
        self.dice_result_label = QLabel("Results: -")
        self.dice_result_label.setWordWrap(True)
        self.dice_total_label = QLabel("Total: -")
        self.dice_total_label.setWordWrap(True)
        dice_layout.addWidget(self.dice_result_label)
        dice_layout.addWidget(self.dice_total_label)
        randomizer_layout.addWidget(dice_group)

        range_group = QGroupBox("Range Generator")
        range_layout = QVBoxLayout(range_group)
        range_controls = QHBoxLayout()
        self.random_min_spin = QSpinBox()
        self.random_min_spin.setMinimum(-999999)
        self.random_min_spin.setMaximum(999999)
        self.random_min_spin.setValue(1)
        self.random_min_spin.setFixedWidth(96)
        self.random_max_spin = QSpinBox()
        self.random_max_spin.setMinimum(-999999)
        self.random_max_spin.setMaximum(999999)
        self.random_max_spin.setValue(10)
        self.random_max_spin.setFixedWidth(96)
        range_controls.addStretch(1)
        range_controls.addWidget(QLabel("From"))
        range_controls.addWidget(self.random_min_spin)
        range_controls.addWidget(QLabel("To"))
        range_controls.addWidget(self.random_max_spin)
        range_controls.addStretch(1)
        range_layout.addLayout(range_controls)
        generate_random_button = QPushButton("Generate Number")
        generate_random_button.clicked.connect(self.generate_random_number)
        range_layout.addWidget(generate_random_button)
        self.random_range_result_label = QLabel("Result: -")
        self.random_range_result_label.setWordWrap(True)
        range_layout.addWidget(self.random_range_result_label)
        randomizer_layout.addWidget(range_group)
        right.addWidget(randomizer_group)

        # Notes section for game notes
        notes_group = QGroupBox("Notes")
        notes_layout = QVBoxLayout(notes_group)
        self.game_notes_text = QTextEdit()
        self.game_notes_text.setPlaceholderText("Type your game notes here...")
        self.game_notes_text.setMaximumHeight(120)
        notes_layout.addWidget(self.game_notes_text)
        right.addWidget(notes_group)

        right.addStretch(1)
        self.draw_card_button = QPushButton("Start Game")
        self.draw_card_button.clicked.connect(self.handle_primary_play_action)
        self.draw_card_button.setMinimumHeight(72)
        self.draw_card_button.setStyleSheet(
            "font-size: 22px; font-weight: bold; padding: 12px 24px;"
        )
        right.addWidget(self.draw_card_button)
        right_panel = QWidget()
        right_panel.setLayout(right)
        right_scroll = self.wrap_tab_in_scroll_area(right_panel)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        grid.addWidget(right_scroll, 0, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(2, 1)
        layout.addLayout(grid, 1)

    def roll_dice(self) -> None:
        dice_count = self.dice_count_spin.value()
        dice_sides = self.dice_sides_spin.value()
        rolls = [random.randint(1, dice_sides) for _ in range(dice_count)]
        self.dice_result_label.setText(
            "Results: " + ", ".join(str(result) for result in rolls)
        )
        self.dice_total_label.setText(f"Total: {sum(rolls)}")

    def generate_random_number(self) -> None:
        minimum = self.random_min_spin.value()
        maximum = self.random_max_spin.value()
        if minimum > maximum:
            minimum, maximum = maximum, minimum
            self.random_min_spin.blockSignals(True)
            self.random_max_spin.blockSignals(True)
            self.random_min_spin.setValue(minimum)
            self.random_max_spin.setValue(maximum)
            self.random_min_spin.blockSignals(False)
            self.random_max_spin.blockSignals(False)

        result = random.randint(minimum, maximum)
        self.random_range_result_label.setText(f"Result: {result}")

    def load_saved_folder(self) -> None:
        """Load cards from the configured cards folder."""
        folder = self.config.get("cards_folder")
        cards_path = Path(folder) if folder else self.get_card_source_folder()

        try:
            if cards_path.exists():
                self.load_cards_from_sources([cards_path], cards_path)
            else:
                self.cards_folder = cards_path
                self.cards_folder_label.setText("No cards folder found")
        except Exception as error:
            self.critical_box("Card loading failed", str(error))

    def choose_card_maker_image(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose card image",
            str(self.cards_folder) if self.cards_folder else "",
            "Images (*.png *.jpg *.jpeg *.webp *.gif *.bmp)",
        )
        if not file_path:
            return
        image_path = Path(file_path)
        self.card_maker_image_path = image_path
        self.card_maker_image_path_label.setText(str(image_path))
        self.show_card_preview_from_path(str(image_path), self.card_maker_preview)
        self.card_maker_status_label.setText(
            "The JSON file will be saved next to the selected image."
        )
        self.load_card_maker_fields_for_image(image_path)

    def load_card_maker_fields_for_image(self, image_path: Path) -> None:
        json_path = image_path.with_suffix(".json")
        if json_path.exists():
            try:
                payload = json.loads(json_path.read_text(encoding="utf-8"))
                if isinstance(payload, list):
                    payload = payload[0] if payload else {}
                self.card_maker_name_input.setText(str(payload.get("name", title_from_stem(image_path.stem))))
                self.card_maker_value_input.setText(
                    str(payload.get("value", payload.get("cost", payload.get("mana", ""))))
                )
                self.refresh_card_maker_factions(
                    str(payload.get("faction", payload.get("Faction", ""))).strip()
                )
                self.refresh_card_maker_set_names(
                    str(payload.get("set_name", payload.get("setName", ""))).strip()
                )
                self.card_maker_card_number_input.setText(
                    str(payload.get("card_number", payload.get("cardNumber", "")))
                )
                self.card_maker_artist_name_input.setText(
                    str(payload.get("artist_name", payload.get("artistName", "")))
                )
                self.card_maker_card_author_input.setText(
                    str(payload.get("card_author", payload.get("cardAuthor", "")))
                )
                self.card_maker_effect_input.setPlainText(
                    str(payload.get("effect", payload.get("text", payload.get("description", ""))))
                )
                self.card_maker_status_label.setText(f"Loaded existing data from {json_path.name}.")
                return
            except Exception as error:
                self.card_maker_status_label.setText(
                    f"Could not read {json_path.name}. Starting with blank fields."
                )
                print(error)

        # No JSON file found - try to parse filename pattern: SetName CardNumber - CardName
        parsed = parse_card_filename(image_path.stem)
        if parsed:
            self.card_maker_name_input.setText(parsed['name'])
            self.card_maker_value_input.setText("")
            self.refresh_card_maker_factions("")
            self.refresh_card_maker_set_names(parsed['set_name'])
            self.card_maker_card_number_input.setText(parsed['card_number'])
            self.card_maker_artist_name_input.setText("")
            self.card_maker_card_author_input.setText(self.default_card_author())
            self.card_maker_effect_input.setPlainText("")
            self.card_maker_status_label.setText(
                f"Parsed filename: Set '{parsed['set_name']}', Card #{parsed['card_number']}, Name '{parsed['name']}'"
            )
        else:
            # Fallback to current behavior
            self.card_maker_name_input.setText(title_from_stem(image_path.stem))
            self.card_maker_value_input.setText("")
            self.refresh_card_maker_factions("")
            self.refresh_card_maker_set_names("")
            self.card_maker_card_number_input.setText("")
            self.card_maker_artist_name_input.setText("")
            self.card_maker_card_author_input.setText(self.default_card_author())
            self.card_maker_effect_input.setPlainText("")
            self.card_maker_status_label.setText(
                "Filename doesn't match expected pattern. Using default naming."
            )

    def create_card_json(self) -> None:
        if not self.card_maker_image_path:
            self.info_box("No image selected", "Choose a card image first.")
            return

        image_path = self.card_maker_image_path
        json_path = image_path.with_suffix(".json")
        previous_ids = self.card_ids_for_image(image_path)
        existing_json_id = ""
        if json_path.exists():
            try:
                existing_payload = json.loads(json_path.read_text(encoding="utf-8"))
                if isinstance(existing_payload, list):
                    existing_payload = existing_payload[0] if existing_payload else {}
                existing_json_id = str(existing_payload.get("id", "")).strip()
            except Exception:
                existing_json_id = ""

        name = self.card_maker_name_input.text().strip() or title_from_stem(image_path.stem)
        value = self.card_maker_value_input.text().strip()
        faction = self.current_card_maker_faction()
        set_name = self.current_card_maker_set_name()
        card_number = self.card_maker_card_number_input.text().strip()
        artist_name = self.card_maker_artist_name_input.text().strip()
        card_author = self.card_maker_card_author_input.text().strip()
        effect = self.card_maker_effect_input.toPlainText().strip()
        card_id = existing_json_id or stable_card_id_for_path(image_path)
        payload = {
            "id": card_id,
            "name": name,
            "value": value,
            "faction": faction,
            "set_name": set_name,
            "card_number": card_number,
            "artist_name": artist_name,
            "card_author": card_author,
            "effect": effect,
            "image": image_path.name,
        }

        try:
            json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            self.card_maker_status_label.setText(f"Saved {json_path.name} next to the image.")
            # Reload only the card library so save stays responsive.
            self.reload_library_from_sources_fast()
            self.reconcile_updated_card(image_path, previous_ids)
            self.refresh_card_maker_factions(faction)
            self.refresh_card_maker_set_names(set_name)
        except Exception as error:
            self.critical_box("Save failed", str(error))

    def refresh_cards_pool(self) -> None:
        """Reload cards from all sources and update the deck builder pool (optimized)."""
        self.refresh_cards_pool_fast()

    def reload_library_from_sources_fast(self) -> bool:
        """Reload the card library without clearing icon cache or re-rendering unrelated tabs."""
        folder = self.config.get("cards_folder")
        cards_path = Path(folder) if folder else self.get_card_source_folder()

        self.cards_folder = cards_path
        self.cards_folder_label.setText(str(self.cards_folder))

        if not cards_path.exists():
            self.library = []
            self.library_by_id = {}
            return False

        self.library = self.scan_cards([cards_path])
        self.library.sort(key=lambda card: card.name.lower())
        self.library_by_id = {card.id: card for card in self.library}
        return True

    def refresh_cards_pool_fast(self) -> None:
        """Optimized card pool refresh that preserves icon cache and only updates pool view.
        
        This is faster than full reload because it:
        - Preserves the icon cache (no need to re-render and re-cache all icons)
        - Skips unnecessary UI updates (card maker, play tab)
        - Only re-renders the builder pool view
        """
        try:
            if not self.reload_library_from_sources_fast():
                return

            # Update only what's necessary for the pool view
            self.refresh_builder_pool_filters()
            self.render_builder_pool()
            self.render_builder_deck_contents()
        except Exception as error:
            self.critical_box("Card refresh failed", str(error))

    def choose_cards_folder(self) -> None:
        start_dir = str(self.cards_folder or self.default_cards_folder())
        folder = QFileDialog.getExistingDirectory(self, "Choose cards folder", start_dir)
        if not folder:
            return
        self.config["cards_folder"] = folder
        self.save_app_config()
        self.load_saved_folder()

    def use_default_cards_folder(self) -> None:
        folder = self.default_cards_folder()
        if not folder.exists():
            self.warning_box("Default folder not found", f"The default cards folder does not exist:\n{folder}")
            self.cards_folder_label.setText(f"Folder not found: {folder}")
            return
        self.config.pop("cards_folder", None)
        self.save_app_config()
        self.load_saved_folder()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle keyboard shortcuts."""
        if event.key() == Qt.Key_F5:
            self.refresh_cards_pool()
        else:
            super().keyPressEvent(event)

    def on_min_deck_size_changed(self, value: int) -> None:
        self.config["min_deck_size"] = int(value)
        self.save_app_config()
        self.render_builder()
        self.refresh_deck_selects()

    def on_default_card_author_changed(self, text: str) -> None:
        cleaned = text.strip()
        if cleaned:
            self.config["default_card_author"] = cleaned
        else:
            self.config.pop("default_card_author", None)
        self.save_app_config()

    def on_builder_pool_sort_changed(self) -> None:
        self.config["builder_pool_sort_mode"] = self.builder_pool_sort_combo.currentText()
        self.save_app_config()
        self.render_builder()

    def on_audio_system_sounds_toggled(self, checked: bool) -> None:
        self.config["audio_system_sounds"] = bool(checked)
        self.save_app_config()

    def on_metronome_audio_warmup_toggled(self, checked: bool) -> None:
        self.config["audio_metronome_warmup_enabled"] = bool(checked)
        self.metronome_audio_warmup_spin.setEnabled(bool(checked))
        self.save_app_config()

    def on_restore_window_state_toggled(self, checked: bool) -> None:
        self.config["restore_window_state"] = bool(checked)
        self.save_app_config()

    def on_metronome_audio_warmup_ms_changed(self, value: int) -> None:
        self.config["audio_metronome_warmup_ms"] = int(value)
        self.save_app_config()

    def on_max_metronome_bpm_changed(self, value: int) -> None:
        capped = max(20, min(2000, int(value)))
        self.config["audio_max_metronome_bpm"] = capped
        self.max_metronome_bpm_spin.blockSignals(True)
        self.max_metronome_bpm_spin.setValue(capped)
        self.max_metronome_bpm_spin.blockSignals(False)
        self.default_metronome_bpm_spin.setMaximum(capped)
        if self.default_metronome_bpm_spin.value() > capped:
            self.default_metronome_bpm_spin.setValue(capped)
        self.metronome_bpm_spin.setMaximum(capped)
        if self.metronome_bpm_spin.value() > capped:
            self.metronome_bpm_spin.setValue(capped)
        self.save_app_config()

    def on_default_metronome_bpm_changed(self, value: int) -> None:
        capped = max(20, min(self.max_metronome_bpm(), int(value)))
        self.config["audio_default_metronome_bpm"] = capped
        self.default_metronome_bpm_spin.blockSignals(True)
        self.default_metronome_bpm_spin.setValue(capped)
        self.default_metronome_bpm_spin.blockSignals(False)
        self.save_app_config()

    def on_default_metronome_beats_changed(self, value: int) -> None:
        capped = max(1, min(12, int(value)))
        self.config["audio_default_metronome_beats"] = capped
        self.default_metronome_beats_spin.blockSignals(True)
        self.default_metronome_beats_spin.setValue(capped)
        self.default_metronome_beats_spin.blockSignals(False)
        self.save_app_config()

    def refresh_play_timer_option_state(self) -> None:
        warmup_enabled = self.play_timer_game_start_checkbox.isChecked()
        self.play_timer_game_start_minutes_spin.setEnabled(warmup_enabled)
        self.play_timer_game_start_seconds_spin.setEnabled(warmup_enabled)
        draw_enabled = self.play_timer_draw_checkbox.isChecked()
        self.play_timer_draw_minutes_spin.setEnabled(draw_enabled)
        self.play_timer_draw_seconds_spin.setEnabled(draw_enabled)

    def on_play_timer_game_start_toggled(self, checked: bool) -> None:
        self.config["play_timer_game_start_enabled"] = bool(checked)
        self.refresh_play_timer_option_state()
        self.save_app_config()

    def on_play_timer_game_start_duration_changed(self, _value: int) -> None:
        total_seconds = (
            self.play_timer_game_start_minutes_spin.value() * 60
            + self.play_timer_game_start_seconds_spin.value()
        )
        self.config["play_timer_game_start_seconds"] = int(total_seconds)
        self.save_app_config()

    def on_play_timer_draw_toggled(self, checked: bool) -> None:
        self.config["play_timer_draw_enabled"] = bool(checked)
        self.refresh_play_timer_option_state()
        self.save_app_config()

    def on_play_timer_draw_duration_changed(self, _value: int) -> None:
        total_seconds = (
            self.play_timer_draw_minutes_spin.value() * 60
            + self.play_timer_draw_seconds_spin.value()
        )
        self.config["play_timer_draw_seconds"] = int(total_seconds)
        self.save_app_config()

    def card_ids_for_image(self, image_path: Path) -> List[str]:
        resolved = str(image_path.resolve())
        matches = []
        for card in self.library:
            try:
                if card.image_path and str(Path(card.image_path).resolve()) == resolved:
                    matches.append(card.id)
            except Exception:
                continue
        stable_id = stable_card_id_for_path(image_path)
        if stable_id not in matches:
            matches.append(stable_id)
        return matches

    def remap_entry_ids(
        self,
        entries: Dict[str, int],
        old_ids: List[str],
        new_id: str,
    ) -> Dict[str, int]:
        if not old_ids:
            return dict(entries)
        remapped = dict(entries)
        total_quantity = 0
        for old_id in old_ids:
            if old_id == new_id:
                continue
            total_quantity += remapped.pop(old_id, 0)
        if total_quantity:
            remapped[new_id] = remapped.get(new_id, 0) + total_quantity
        return remapped

    def reconcile_updated_card(self, image_path: Path, previous_ids: List[str]) -> None:
        canonical_card = next(
            (
                card for card in self.library
                if card.image_path and Path(card.image_path).resolve() == image_path.resolve()
            ),
            None,
        )
        if canonical_card is None:
            return

        new_id = canonical_card.id
        old_ids = list(dict.fromkeys(previous_ids + [stable_card_id_for_path(image_path)]))

        updated_builder_entries = self.remap_entry_ids(self.builder_entries, old_ids, new_id)
        if updated_builder_entries != self.builder_entries:
            self.builder_entries = updated_builder_entries

        updated_any_decks = False
        for index, deck in enumerate(self.decks):
            updated_entries = self.remap_entry_ids(deck.entries, old_ids, new_id)
            if updated_entries == deck.entries and not any(old_id in deck.card_snapshots for old_id in old_ids if old_id != new_id):
                continue
            deck.entries = updated_entries
            deck.card_snapshots = self.build_card_snapshots(deck.entries)
            deck.updated_at = time.time()
            self.decks[index] = deck
            self.storage.save_deck(deck)
            updated_any_decks = True

        if updated_any_decks:
            self.refresh_deck_selects()
        self.render_builder()

    def load_cards_folder(self, folder: Path) -> None:
        """Legacy method for loading cards from a single folder."""
        try:
            self.cards_folder = folder
            self.library = self.scan_cards([folder])
            self.library.sort(key=lambda card: card.name.lower())
            self.library_by_id = {card.id: card for card in self.library}
            self.card_icon_cache = {}
            self.cards_folder_label.setText(str(folder))
            self.refresh_card_maker_factions(self.current_card_maker_faction())
            self.refresh_card_maker_set_names(self.current_card_maker_set_name())
            self.render_builder()
            self.render_play_state()
        except Exception as error:
            self.critical_box("Import failed", str(error))

    def load_cards_from_sources(self, folders: list[Path], user_folder: Path | None = None) -> None:
        """Load cards from multiple sources with proper priority handling.
        
        Args:
            folders: List of card source folders in priority order (user cards first).
            user_folder: The primary user folder path for display and card maker reference.
        """
        try:
            self.cards_folder = user_folder or (folders[0] if folders else self.default_cards_folder())
            self.library = self.scan_cards(folders)
            self.library.sort(key=lambda card: card.name.lower())
            self.library_by_id = {card.id: card for card in self.library}
            self.card_icon_cache = {}
            self.cards_folder_label.setText(str(self.cards_folder))
            self.refresh_card_maker_factions(self.current_card_maker_faction())
            self.refresh_card_maker_set_names(self.current_card_maker_set_name())
            self.render_builder()
            self.render_play_state()
        except Exception as error:
            self.critical_box("Import failed", str(error))

    def scan_cards(self, folders: list[Path] | Path) -> List[Card]:
        """Scan one or more card folders for card files.
        
        Args:
            folders: Single Path or list of Paths to scan for cards.
                    If list, folders are scanned in order with first occurrence taking priority.
        
        Returns:
            List of Card objects loaded from all folders, deduplicated by ID (first source wins).
        """
        # Normalize input to list
        if isinstance(folders, Path):
            folders = [folders]
        
        # Scan all folders for images first
        image_paths = {}
        for folder in folders:
            for path in folder.rglob("*"):
                if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
                    stem_lower = path.stem.lower()
                    if stem_lower not in image_paths:
                        image_paths[stem_lower] = path
        
        cards: List[Card] = []
        used_image_stems = set()
        folder_resolved = [f.resolve() for f in folders]

        # Scan all folders for JSON files
        for folder in folders:
            for json_file in folder.rglob("*.json"):
                # Limit file size to prevent DoS (10MB)
                try:
                    content = json_file.read_text(encoding="utf-8")
                    if len(content) > 10 * 1024 * 1024:
                        continue
                except (OSError, ValueError):
                    continue
                try:
                    raw = json.loads(content)
                except json.JSONDecodeError:
                    continue
                items = raw if isinstance(raw, list) else [raw]
                for index, item in enumerate(items):
                    fallback_stem = json_file.stem.lower()
                    image_path = ""
                    image_value = str(item.get("image", "")).strip()
                    if image_value:
                        candidate = (json_file.parent / image_value).resolve()
                        # Path traversal protection: ensure candidate is within one of the source folders
                        is_valid = False
                        for folder_res in folder_resolved:
                            try:
                                candidate.relative_to(folder_res)
                                is_valid = True
                                break
                            except ValueError:
                                pass
                        
                        if is_valid and candidate.exists():
                            image_path = str(candidate)
                        
                        if not image_path:
                            linked = image_paths.get(Path(image_value).stem.lower())
                            if linked:
                                image_path = str(linked)
                    elif fallback_stem in image_paths:
                        image_path = str(image_paths[fallback_stem])

                    if image_path:
                        used_image_stems.add(Path(image_path).stem.lower())
                    stable_fallback_path = Path(image_path) if image_path else json_file
                    stable_fallback_id = stable_card_id_for_path(stable_fallback_path)
                    if len(items) > 1 and not str(item.get("id", "")).strip():
                        stable_fallback_id = f"{stable_fallback_id}-{index + 1}"

                    try:
                        cards.append(
                            Card(
                                id=slugify(str(item.get("id") or stable_fallback_id))[:100],
                                name=str(item.get("name") or title_from_stem(json_file.stem))[:200],
                                value=str(item.get("value", item.get("cost", item.get("mana", ""))))[:50],
                                faction=str(item.get("faction", item.get("Faction", "")))[:100],
                                effect=str(item.get("effect", item.get("text", item.get("description", ""))))[:5000],
                                set_name=str(item.get("set_name", item.get("setName", "")))[:100],
                                card_number=str(item.get("card_number", item.get("cardNumber", "")))[:50],
                                artist_name=str(item.get("artist_name", item.get("artistName", "")))[:200],
                                card_author=str(item.get("card_author", item.get("cardAuthor", "")))[:200],
                                image_path=image_path,
                                source=str(json_file),
                            )
                        )
                    except (TypeError, ValueError, KeyError):
                        continue

        # Add orphaned images (images without JSON files)
        for stem, image_path in image_paths.items():
            if stem in used_image_stems:
                continue
            cards.append(
                Card(
                    id=stable_card_id_for_path(image_path),
                    name=title_from_stem(stem),
                    value="",
                    faction="",
                    effect="",
                    set_name="",
                    card_number="",
                    artist_name="",
                    card_author="",
                    image_path=str(image_path),
                    source=str(image_path),
                )
            )

        # Deduplicate by ID - first occurrence (user cards) wins
        seen_ids = set()
        deduped: List[Card] = []
        for card in cards:
            if card.id not in seen_ids:
                deduped.append(card)
                seen_ids.add(card.id)
            # Duplicate IDs are silently skipped (first source takes priority)
        
        return deduped

    def add_pool_card_from_click(self, item: QListWidgetItem) -> None:
        if not item:
            return
        selected_items = self.builder_pool_list.selectedItems()
        target_items = selected_items if item.isSelected() and len(selected_items) > 1 else [item]
        seen_card_ids = set()
        for target_item in target_items:
            card_id = target_item.data(Qt.UserRole)
            if card_id in seen_card_ids:
                continue
            seen_card_ids.add(card_id)
            self.builder_entries[card_id] = self.builder_entries.get(card_id, 0) + 1
        self.render_builder_deck_contents()
        self.refresh_builder_pool_counts_only()

    def show_builder_pool_context_menu(self, item: QListWidgetItem) -> None:
        if not item:
            return
        card = self.library_by_id.get(item.data(Qt.UserRole))
        if not card:
            return
        self.open_card_preview_dialog(card, source="builder_pool")

    def render_builder(self) -> None:
        self.refresh_builder_pool_filters()
        self.render_builder_pool()
        self.render_builder_deck_contents()

    def refresh_builder_pool_filters(self) -> None:
        current_faction = (
            self.builder_pool_faction_filter.currentData()
            if self.builder_pool_faction_filter.count()
            else "__all__"
        )
        factions = sorted({card.faction.strip() for card in self.library if card.faction.strip()}, key=str.lower)
        self.builder_pool_faction_filter.blockSignals(True)
        self.builder_pool_faction_filter.clear()
        self.builder_pool_faction_filter.addItem("All Factions", "__all__")
        for faction in factions:
            self.builder_pool_faction_filter.addItem(faction, faction)
        index = self.builder_pool_faction_filter.findData(current_faction)
        self.builder_pool_faction_filter.setCurrentIndex(index if index >= 0 else 0)
        self.builder_pool_faction_filter.blockSignals(False)

    def card_pool_sort_key(self, card: Card):
        mode = self.builder_pool_sort_combo.currentText()
        value_text = card.value.strip()
        try:
            numeric_value = float(value_text)
            has_numeric_value = 0
        except ValueError:
            numeric_value = float("inf")
            has_numeric_value = 1
        set_number = card.card_number.strip() or "zzzz"
        if mode == "Sort: Name Z-A":
            return card.name.lower()
        if mode == "Sort: Value Low-High":
            return (has_numeric_value, numeric_value, card.name.lower())
        if mode == "Sort: Value High-Low":
            return (has_numeric_value, -numeric_value if has_numeric_value == 0 else float("inf"), card.name.lower())
        if mode == "Sort: Faction A-Z":
            return ((card.faction or "zzz").lower(), card.name.lower())
        if mode == "Sort: Set / Number":
            return ((card.set_name or "zzz").lower(), set_number.lower(), card.name.lower())
        return card.name.lower()

    def parse_pool_value_filter(self, text: str) -> Optional[float]:
        cleaned = text.strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None

    def card_numeric_value(self, card: Card) -> Optional[float]:
        try:
            return float(card.value.strip())
        except ValueError:
            return None

    def current_builder_value_stats(self) -> tuple[float, Optional[float]]:
        total_cards = sum(self.builder_entries.values())
        active_deck = self.get_current_saved_deck()
        total_value = 0.0
        for card_id, quantity in self.builder_entries.items():
            card = self.get_card_for_deck_entry(card_id, active_deck)
            try:
                numeric_value = float(str(card.get("value", "")).strip())
            except (TypeError, ValueError):
                numeric_value = 0.0
            total_value += numeric_value * quantity
        average_value = (total_value / total_cards) if total_cards else None
        return total_value, average_value

    def format_stat_number(self, value: Optional[float]) -> str:
        if value is None:
            return "-"
        rounded = round(value)
        if abs(value - rounded) < 0.0001:
            return str(int(rounded))
        return f"{value:.2f}"

    def show_builder_save_status(self, text: str) -> None:
        self.builder_save_status_label.setText(text)
        QTimer.singleShot(2200, lambda message=text: self.clear_builder_save_status(message))

    def clear_builder_save_status(self, expected_text: str = "") -> None:
        if expected_text and self.builder_save_status_label.text() != expected_text:
            return
        self.builder_save_status_label.clear()

    def render_builder_pool(self) -> None:
        total = sum(self.builder_entries.values())
        self.deck_group.setTitle(f"Deck ({total}/{self.min_deck_size()})")

        pool_query = self.builder_pool_search.text().strip().lower()
        selected_faction = self.builder_pool_faction_filter.currentData()
        deck_filter = self.builder_pool_deck_filter.currentText()
        min_value = self.parse_pool_value_filter(self.builder_pool_min_value.text())
        max_value = self.parse_pool_value_filter(self.builder_pool_max_value.text())
        filtered_pool = []
        for card in self.library:
            if pool_query not in (
                f"{card.name} {card.effect} {card.value} {card.faction} {card.set_name} {card.card_number} {card.artist_name} {card.card_author}".lower()
            ):
                continue
            if selected_faction not in (None, "__all__") and card.faction != selected_faction:
                continue
            if min_value is not None or max_value is not None:
                card_value = self.card_numeric_value(card)
                if card_value is None:
                    continue
                if min_value is not None and card_value < min_value:
                    continue
                if max_value is not None and card_value > max_value:
                    continue
            quantity_in_deck = self.builder_entries.get(card.id, 0)
            if deck_filter == "In Deck" and quantity_in_deck <= 0:
                continue
            if deck_filter == "Not In Deck" and quantity_in_deck > 0:
                continue
            filtered_pool.append(card)
        sort_mode = self.builder_pool_sort_combo.currentText()
        filtered_pool.sort(key=self.card_pool_sort_key, reverse=(sort_mode == "Sort: Name Z-A"))
        selected_pool_card_ids = {
            item.data(Qt.UserRole)
            for item in self.builder_pool_list.selectedItems()
        }
        selected_pool_card_id = (
            self.builder_pool_list.currentItem().data(Qt.UserRole)
            if self.builder_pool_list.currentItem()
            else None
        )
        pool_scrollbar = self.builder_pool_list.verticalScrollBar()
        previous_scroll_value = pool_scrollbar.value()
        self.builder_pool_list.clear()
        self.builder_pool_count_label.setText(f"Cards shown: {len(filtered_pool)}")
        for card in filtered_pool:
            quantity_in_deck = self.builder_entries.get(card.id, 0)
            item = QListWidgetItem(
                self.get_card_icon(card.image_path),
                self.format_builder_pool_item_text(card.name, quantity_in_deck),
            )
            item.setData(Qt.UserRole, card.id)
            item.setTextAlignment(Qt.AlignCenter)
            item.setSizeHint(QSize(306, 452))
            item.setToolTip(
                f"{card.name}\nValue: {card.value or '-'}\nFaction: {card.faction or '-'}\n{self.format_card_meta(asdict(card))}"
            )
            self.builder_pool_list.addItem(item)
        if self.builder_pool_list.count():
            current_item_to_restore = None
            fallback_selected_item = None
            for index in range(self.builder_pool_list.count()):
                pool_item = self.builder_pool_list.item(index)
                card_id = pool_item.data(Qt.UserRole)
                if card_id in selected_pool_card_ids:
                    pool_item.setSelected(True)
                    if fallback_selected_item is None:
                        fallback_selected_item = pool_item
                if card_id == selected_pool_card_id:
                    current_item_to_restore = pool_item

            if current_item_to_restore is not None:
                self.builder_pool_list.setCurrentItem(current_item_to_restore, QItemSelectionModel.NoUpdate)
            elif fallback_selected_item is not None:
                self.builder_pool_list.setCurrentItem(fallback_selected_item, QItemSelectionModel.NoUpdate)
            else:
                self.builder_pool_list.setCurrentRow(0)
        else:
            self.builder_pool_name.setText("-")
            self.builder_pool_value.setText("-")
            self.builder_pool_faction.setText("-")
            self.builder_pool_meta.setPlainText("-")
            self.builder_pool_effect.setHtml(self.format_effect_html("-"))
        pool_scrollbar.setValue(min(previous_scroll_value, pool_scrollbar.maximum()))

    def render_builder_deck_contents(self) -> None:
        total = sum(self.builder_entries.values())
        self.deck_group.setTitle(f"Deck ({total}/{self.min_deck_size()})")
        total_value, average_value = self.current_builder_value_stats()
        self.deck_total_value_label.setText(f"Total Value: {self.format_stat_number(total_value)}")
        self.deck_average_value_label.setText(f"Average Value: {self.format_stat_number(average_value)}")
        faction_stats, value_stats = self.current_builder_distribution_stats()
        self.populate_deck_stats_table(self.deck_faction_stats_table, faction_stats, "Faction")
        self.populate_deck_stats_table(self.deck_value_stats_table, value_stats, "Value")
        self.deck_stats_tabs.setTabText(0, f"Faction ({len(faction_stats)})")
        self.deck_stats_tabs.setTabText(1, f"Value ({len(value_stats)})")

        selected_card_ids = {
            item.data(Qt.UserRole)
            for item in self.deck_entries_list.selectedItems()
        }
        selected_card_id = (
            self.deck_entries_list.currentItem().data(Qt.UserRole)
            if self.deck_entries_list.currentItem()
            else None
        )
        selected_row = self.deck_entries_list.currentRow()
        self.deck_entries_list.clear()
        active_deck = self.get_current_saved_deck()
        for card_id, quantity in sorted(
            self.builder_entries.items(),
            key=lambda item: self.get_card_for_deck_entry(item[0], active_deck)["name"].lower(),
        ):
            card = self.get_card_for_deck_entry(card_id, active_deck)
            item = QListWidgetItem(f"{card['name']}    x{quantity}")
            item.setData(Qt.UserRole, card_id)
            self.deck_entries_list.addItem(item)
        if self.deck_entries_list.count():
            current_item_to_restore = None
            fallback_selected_item = None
            for index in range(self.deck_entries_list.count()):
                deck_item = self.deck_entries_list.item(index)
                card_id = deck_item.data(Qt.UserRole)
                if card_id in selected_card_ids:
                    deck_item.setSelected(True)
                    if fallback_selected_item is None:
                        fallback_selected_item = deck_item
                if card_id == selected_card_id:
                    current_item_to_restore = deck_item

            if current_item_to_restore is not None:
                self.deck_entries_list.setCurrentItem(current_item_to_restore, QItemSelectionModel.NoUpdate)
            elif fallback_selected_item is not None:
                self.deck_entries_list.setCurrentItem(fallback_selected_item, QItemSelectionModel.NoUpdate)
            else:
                fallback_row = min(selected_row, self.deck_entries_list.count() - 1)
                self.deck_entries_list.setCurrentRow(max(fallback_row, 0))
        else:
            self.deck_entry_name.setText("-")
            self.deck_entry_value.setText("-")
            self.deck_entry_faction.setText("-")
            self.deck_entry_effect.setHtml(self.format_effect_html("-"))
            self.deck_entry_meta.setPlainText("-")
            self.deck_entry_quantity.blockSignals(True)
            self.deck_entry_quantity.setValue(0)
            self.deck_entry_quantity.blockSignals(False)

    def current_builder_distribution_stats(self) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
        active_deck = self.get_current_saved_deck()
        faction_counts: Dict[str, int] = {}
        value_counts: Dict[str, int] = {}

        for card_id, quantity in self.builder_entries.items():
            if quantity <= 0:
                continue
            card = self.get_card_for_deck_entry(card_id, active_deck)

            faction = str(card.get("faction", "")).strip() or "No Faction"
            faction_counts[faction] = faction_counts.get(faction, 0) + quantity

            value = str(card.get("value", "")).strip() or "No Value"
            value_counts[value] = value_counts.get(value, 0) + quantity

        def value_sort_key(item: tuple[str, int]) -> tuple[int, float | str]:
            value = item[0]
            try:
                return (0, float(value))
            except (TypeError, ValueError):
                return (1, value.lower())

        faction_items = sorted(faction_counts.items(), key=lambda item: item[0].lower())
        value_items = sorted(value_counts.items(), key=value_sort_key)
        return faction_items, value_items

    def on_deck_stats_toggled(self, checked: bool) -> None:
        self.deck_stats_toggle.setText("Hide Deck Stats" if checked else "Show Deck Stats")
        self.deck_stats_tabs.setVisible(checked)

    def create_deck_stats_table(self, first_column_label: str) -> QTableWidget:
        table = QTableWidget(0, 2)
        table.setHorizontalHeaderLabels([first_column_label, "Count"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionMode(QTableWidget.NoSelection)
        table.setFocusPolicy(Qt.NoFocus)
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        table.setWordWrap(False)
        table.horizontalHeader().setStretchLastSection(False)
        table.horizontalHeader().setSectionsClickable(False)
        table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        table.setColumnWidth(1, 64)
        table.setStyleSheet(
            "QTableWidget { border: none; background: transparent; alternate-background-color: rgba(255,255,255,0.04); }"
            "QHeaderView::section { background: #2d2d2d; padding: 4px 6px; border: none; font-weight: 600; }"
        )
        count_header = table.horizontalHeaderItem(1)
        if count_header is not None:
            count_header.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return table

    def populate_deck_stats_table(
        self,
        table: QTableWidget,
        rows: list[tuple[str, int]],
        first_column_label: str,
    ) -> None:
        header_item = table.horizontalHeaderItem(0)
        if header_item is not None:
            header_item.setText(first_column_label)

        display_rows = rows or [("-", 0)]
        table.setRowCount(len(display_rows))

        bold_font = QFont(table.font())
        bold_font.setBold(True)

        for row_index, (label, count) in enumerate(display_rows):
            label_item = QTableWidgetItem(str(label))
            label_item.setFlags(Qt.ItemIsEnabled)
            label_item.setFont(bold_font if label != "-" else table.font())
            label_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            table.setItem(row_index, 0, label_item)

            count_text = "-" if label == "-" else str(count)
            count_item = QTableWidgetItem(count_text)
            count_item.setFlags(Qt.ItemIsEnabled)
            count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row_index, 1, count_item)

        table.resizeColumnToContents(0)
        table.setColumnWidth(1, max(64, table.columnWidth(1)))

    def refresh_builder_pool_counts_only(self) -> None:
        for index in range(self.builder_pool_list.count()):
            item = self.builder_pool_list.item(index)
            card_id = item.data(Qt.UserRole)
            card = self.library_by_id.get(card_id)
            if not card:
                continue
            quantity_in_deck = self.builder_entries.get(card.id, 0)
            item.setText(self.format_builder_pool_item_text(card.name, quantity_in_deck))

    def update_builder_pool_detail(
        self,
        current: Optional[QListWidgetItem],
        _previous: Optional[QListWidgetItem],
    ) -> None:
        if not current:
            # Clear details when deselected
            self.builder_pool_name.setText("-")
            self.builder_pool_value.setText("-")
            self.builder_pool_faction.setText("-")
            self.builder_pool_meta.setPlainText("-")
            self.builder_pool_effect.setPlainText("-")
            return
        card = self.library_by_id.get(current.data(Qt.UserRole))
        if not card:
            return
        self.builder_pool_name.setText(card.name)
        self.builder_pool_value.setText(card.value or "-")
        self.builder_pool_faction.setText(card.faction or "-")
        self.builder_pool_meta.setPlainText(self.format_card_meta(asdict(card)))
        self.builder_pool_effect.setHtml(self.format_effect_html(card.effect))

    def update_deck_entry_detail(self, current: Optional[QListWidgetItem], _previous: Optional[QListWidgetItem]) -> None:
        if not current:
            # Clear details when deselected
            self.deck_entry_name.setText("-")
            self.deck_entry_value.setText("-")
            self.deck_entry_faction.setText("-")
            self.deck_entry_effect.setPlainText("-")
            self.deck_entry_meta.setPlainText("-")
            self.deck_entry_quantity.blockSignals(True)
            self.deck_entry_quantity.setValue(0)
            self.deck_entry_quantity.blockSignals(False)
            return
        card_id = current.data(Qt.UserRole)
        card = self.get_card_for_deck_entry(card_id, self.get_current_saved_deck())
        self.deck_entry_name.setText(card["name"])
        self.deck_entry_value.setText(card.get("value", "-") or "-")
        self.deck_entry_faction.setText(card.get("faction", "-") or "-")
        self.deck_entry_effect.setHtml(self.format_effect_html(card["effect"]))
        self.deck_entry_meta.setPlainText(self.format_card_meta(card))
        self.deck_entry_quantity.blockSignals(True)
        self.deck_entry_quantity.setValue(self.builder_entries.get(card_id, 0))
        self.deck_entry_quantity.blockSignals(False)

    def remove_one_copy_from_deck_item(self, item: QListWidgetItem) -> None:
        if not item:
            return
        selected_items = self.deck_entries_list.selectedItems()
        target_items = selected_items if item.isSelected() and len(selected_items) > 1 else [item]
        seen_card_ids = set()
        for target_item in target_items:
            card_id = target_item.data(Qt.UserRole)
            if card_id in seen_card_ids:
                continue
            seen_card_ids.add(card_id)
            current_quantity = self.builder_entries.get(card_id, 0)
            if current_quantity <= 1:
                self.builder_entries.pop(card_id, None)
            else:
                self.builder_entries[card_id] = current_quantity - 1
        self.render_builder_deck_contents()
        self.refresh_builder_pool_counts_only()

    def remove_selected_deck_entries_completely(self) -> None:
        selected_items = self.deck_entries_list.selectedItems()
        if not selected_items:
            return
        selected_card_ids = {
            item.data(Qt.UserRole)
            for item in selected_items
        }
        for card_id in selected_card_ids:
            self.builder_entries.pop(card_id, None)
        self.render_builder_deck_contents()
        self.refresh_builder_pool_counts_only()

    def show_deck_card_preview(self, item: QListWidgetItem) -> None:
        if not item:
            return
        card_id = item.data(Qt.UserRole)
        card = self.library_by_id.get(card_id)
        if card is None:
            card_data = self.get_card_for_deck_entry(card_id, self.get_current_saved_deck())
            card = Card(
                id=card_data.get("id", card_id),
                name=card_data.get("name", card_id),
                value=card_data.get("value", ""),
                faction=card_data.get("faction", ""),
                effect=card_data.get("effect", ""),
                set_name=card_data.get("set_name", ""),
                card_number=card_data.get("card_number", ""),
                artist_name=card_data.get("artist_name", ""),
                card_author=card_data.get("card_author", ""),
                image_path=card_data.get("image_path", ""),
                source=card_data.get("source", ""),
            )
        self.open_card_preview_dialog(card, source="deck_entries")

    def update_selected_entry_quantity(self, value: int) -> None:
        current = self.deck_entries_list.currentItem()
        if not current:
            return
        card_id = current.data(Qt.UserRole)
        if value <= 0:
            self.builder_entries.pop(card_id, None)
        else:
            self.builder_entries[card_id] = value
        self.render_builder_deck_contents()
        self.refresh_builder_pool_counts_only()

    def refresh_deck_selects(self) -> None:
        self.saved_decks_combo.blockSignals(True)
        self.saved_decks_combo.clear()
        self.saved_decks_combo.addItem("New unsaved deck", None)
        for deck in sorted(self.decks, key=lambda item: item.name.lower()):
            self.saved_decks_combo.addItem(deck.name, deck.id)
        if self.current_deck_id:
            index = self.saved_decks_combo.findData(self.current_deck_id)
            if index >= 0:
                self.saved_decks_combo.setCurrentIndex(index)
        self.saved_decks_combo.blockSignals(False)

        self.play_deck_combo.blockSignals(True)
        self.play_deck_combo.clear()
        self.play_deck_combo.addItem("Select a valid deck", None)
        for deck in sorted(self.decks, key=lambda item: item.name.lower()):
            if sum(deck.entries.values()) >= self.min_deck_size():
                self.play_deck_combo.addItem(deck.name, deck.id)
        saved_play_deck_id = self.config.get("play_selected_deck_id")
        if saved_play_deck_id:
            play_index = self.play_deck_combo.findData(saved_play_deck_id)
            self.play_deck_combo.setCurrentIndex(play_index if play_index >= 0 else 0)
        else:
            self.play_deck_combo.setCurrentIndex(0)
        self.play_deck_combo.blockSignals(False)

    def builder_has_unsaved_changes(self) -> bool:
        if self.current_deck_id:
            current_deck = self.get_current_saved_deck()
            if current_deck is None:
                return bool(self.deck_name_input.text().strip()) or bool(self.builder_entries)
            return (
                self.deck_name_input.text().strip() != current_deck.name
                or dict(self.builder_entries) != dict(current_deck.entries)
            )
        return bool(self.deck_name_input.text().strip()) or bool(self.builder_entries)

    def restore_saved_deck_selection(self) -> None:
        self.saved_decks_combo.blockSignals(True)
        if self.current_deck_id:
            index = self.saved_decks_combo.findData(self.current_deck_id)
            self.saved_decks_combo.setCurrentIndex(index if index >= 0 else 0)
        else:
            self.saved_decks_combo.setCurrentIndex(0)
        self.saved_decks_combo.blockSignals(False)

    def save_deck(self) -> None:
        name = self.deck_name_input.text().strip()
        total = sum(self.builder_entries.values())
        if not name:
            self.warning_box("Missing name", "Please enter a deck name before saving.")
            return
        required_size = self.min_deck_size()
        if total < required_size:
            confirmation = self.question_box(
                "Save invalid deck?",
                (
                    f"This deck only has {total} cards, but the minimum size is {required_size}.\n\n"
                    "It can be saved as a work in progress, but it will not appear as a valid deck in Play yet.\n\n"
                    "Do you want to save it anyway?"
                ),
                QMessageBox.No,
            )
            if confirmation != QMessageBox.Yes:
                return

        deck_id = self.current_deck_id or f"deck-{int(time.time() * 1000)}"
        deck = Deck(
            id=deck_id,
            name=name,
            entries=dict(self.builder_entries),
            card_snapshots=self.build_card_snapshots(self.builder_entries),
            updated_at=time.time(),
        )
        existing_index = next((index for index, item in enumerate(self.decks) if item.id == deck_id), -1)
        if existing_index >= 0:
            self.decks[existing_index] = deck
        else:
            self.decks.append(deck)
        self.current_deck_id = deck_id
        self.storage.save_deck(deck)
        self.refresh_deck_selects()
        self.render_builder()
        self.show_builder_save_status(f'Saved "{name}"')

    def duplicate_deck(self) -> None:
        source_deck = self.get_current_saved_deck()
        if source_deck is None:
            self.info_box("No deck selected", "Select a saved deck to duplicate first.")
            return

        duplicate_name = self.make_duplicate_deck_name(source_deck.name)
        duplicate_deck = Deck(
            id=f"deck-{int(time.time() * 1000)}",
            name=duplicate_name,
            entries=dict(source_deck.entries),
            card_snapshots=dict(source_deck.card_snapshots),
            updated_at=time.time(),
        )
        self.decks.append(duplicate_deck)
        self.storage.save_deck(duplicate_deck)
        self.current_deck_id = duplicate_deck.id
        self.deck_name_input.setText(duplicate_deck.name)
        self.builder_entries = dict(duplicate_deck.entries)
        self.refresh_deck_selects()
        self.render_builder()

    def make_duplicate_deck_name(self, base_name: str) -> str:
        existing_names = {deck.name for deck in self.decks}
        if f"{base_name} Copy" not in existing_names:
            return f"{base_name} Copy"

        copy_number = 2
        while f"{base_name} Copy {copy_number}" in existing_names:
            copy_number += 1
        return f"{base_name} Copy {copy_number}"

    def delete_deck(self) -> None:
        if not self.current_deck_id:
            self.info_box("No deck selected", "Select a saved deck first.")
            return
        deck_id = self.current_deck_id
        deck = next((item for item in self.decks if item.id == deck_id), None)
        deck_name = deck.name if deck else "this deck"
        confirmation = self.question_box(
            "Delete deck?",
            f"Are you sure you want to delete \"{deck_name}\"?\n\nThis cannot be undone.",
            QMessageBox.No,
        )
        if confirmation != QMessageBox.Yes:
            return
        self.decks = [deck for deck in self.decks if deck.id != deck_id]
        self.storage.delete_deck(deck_id)
        self.reset_builder()
        self.refresh_deck_selects()
        if self.play_deck_combo.currentData() == deck_id:
            self.reset_game()

    def reset_builder(self) -> None:
        self.current_deck_id = None
        self.builder_entries = {}
        self.deck_name_input.clear()
        self.refresh_deck_selects()
        self.render_builder()

    def load_selected_saved_deck(self) -> None:
        deck_id = self.saved_decks_combo.currentData()
        if deck_id == self.current_deck_id:
            return
        if self.builder_has_unsaved_changes():
            confirmation = self.question_box(
                "Discard unsaved changes?",
                "You have unsaved deck changes. Switch decks and discard those changes?",
                QMessageBox.No,
            )
            if confirmation != QMessageBox.Yes:
                self.restore_saved_deck_selection()
                return
        if not deck_id:
            self.current_deck_id = None
            self.builder_entries = {}
            self.deck_name_input.clear()
            self.render_builder()
            return
        deck = next((item for item in self.decks if item.id == deck_id), None)
        if not deck:
            return
        self.current_deck_id = deck.id
        self.deck_name_input.setText(deck.name)
        self.builder_entries = dict(deck.entries)
        self.render_builder()

    def build_card_snapshots(self, entries: Dict[str, int]) -> Dict[str, Dict[str, str]]:
        snapshots: Dict[str, Dict[str, str]] = {}
        for card_id in entries:
            card = self.library_by_id.get(card_id)
            snapshots[card_id] = {
                "id": card.id if card else card_id,
                "name": card.name if card else card_id,
                "value": card.value if card else "",
                "faction": card.faction if card else "",
                "effect": card.effect if card else "",
                "set_name": card.set_name if card else "",
                "card_number": card.card_number if card else "",
                "artist_name": card.artist_name if card else "",
                "card_author": card.card_author if card else "",
                "image_path": card.image_path if card else "",
                "source": card.source if card else "",
            }
        return snapshots

    def get_current_saved_deck(self) -> Optional[Deck]:
        if not self.current_deck_id:
            return None
        return next((deck for deck in self.decks if deck.id == self.current_deck_id), None)

    def get_card_for_deck_entry(self, card_id: str, deck: Optional[Deck]) -> Dict[str, str]:
        if card_id in self.library_by_id:
            return asdict(self.library_by_id[card_id])
        if deck and card_id in deck.card_snapshots:
            snapshot = dict(deck.card_snapshots[card_id])
            if "value" not in snapshot and "cost" in snapshot:
                snapshot["value"] = snapshot.get("cost", "")
            snapshot.setdefault("faction", "")
            snapshot.setdefault("set_name", "")
            snapshot.setdefault("card_number", "")
            snapshot.setdefault("artist_name", "")
            snapshot.setdefault("card_author", "")
            return snapshot
        return {
            "id": card_id,
            "name": card_id,
            "value": "",
            "faction": "",
            "effect": "",
            "set_name": "",
            "card_number": "",
            "artist_name": "",
            "card_author": "",
            "image_path": "",
            "source": "",
        }

    def format_card_meta(self, card: Dict[str, str]) -> str:
        lines = []
        if card.get("set_name"):
            lines.append(f"Set: {card['set_name']}")
        if card.get("card_number"):
            lines.append(f"Card Number: {card['card_number']}")
        if card.get("artist_name"):
            lines.append(f"Artist: {card['artist_name']}")
        if card.get("card_author"):
            lines.append(f"Author: {card['card_author']}")
        return " | ".join(lines) if lines else "No extra metadata."

    def format_builder_pool_item_text(self, name: str, quantity_in_deck: int) -> str:
        return f"In deck: {quantity_in_deck}"

    def on_play_history_toggled(self, checked: bool) -> None:
        self.play_history_tabs.setVisible(checked)
        self.play_history_toggle.setText("Hide History" if checked else "Show History")

    def update_primary_play_action_button(self) -> None:
        self.draw_card_button.setText("Draw Card" if self.play_game_active else "Start Game")

    def handle_primary_play_action(self) -> None:
        if self.play_game_active:
            self.draw_card()
        else:
            self.start_game()

    def on_play_deck_selection_changed(self) -> None:
        deck_id = self.play_deck_combo.currentData()
        if deck_id:
            self.config["play_selected_deck_id"] = deck_id
        else:
            self.config.pop("play_selected_deck_id", None)
        self.save_app_config()

    def set_timer_countdown_preset(self, total_seconds: int) -> None:
        total_seconds = max(0, int(total_seconds))
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        self.pause_stopwatch()
        self.timer_mode_combo.blockSignals(True)
        self.timer_mode_combo.setCurrentText("Countdown")
        self.timer_mode_combo.blockSignals(False)
        self.timer_mode = "countdown"
        self.countdown_minutes_spin.setEnabled(True)
        self.countdown_seconds_spin.setEnabled(True)
        self.countdown_minutes_spin.blockSignals(True)
        self.countdown_seconds_spin.blockSignals(True)
        self.countdown_minutes_spin.setValue(minutes)
        self.countdown_seconds_spin.setValue(seconds)
        self.countdown_minutes_spin.blockSignals(False)
        self.countdown_seconds_spin.blockSignals(False)
        self.countdown_target_seconds = total_seconds
        self.reset_stopwatch()

    def apply_play_timer_preset(self, trigger: str) -> None:
        if trigger == "game_start":
            if not self.play_timer_game_start_enabled():
                return
            seconds = self.play_timer_game_start_seconds()
        elif trigger == "draw":
            if not self.play_timer_draw_enabled():
                return
            seconds = self.play_timer_draw_seconds()
        else:
            return
        self.set_timer_countdown_preset(seconds)

    def start_game(self) -> None:
        if (
            self.play_game_active
            or self.play_draw_pile
            or self.play_discard_pile
            or self.play_current_card_id is not None
        ):
            confirmation = self.question_box(
                "Start new game?",
                "A game is already in progress.\n\nStart a new game and discard the current one?",
                QMessageBox.No,
            )
            if confirmation != QMessageBox.Yes:
                return
        deck_id = self.play_deck_combo.currentData()
        if not deck_id:
            self.info_box("No deck selected", "Choose a valid deck first.")
            return
        deck = next((item for item in self.decks if item.id == deck_id), None)
        if not deck:
            return
        self.play_draw_pile = []
        for card_id, quantity in deck.entries.items():
            self.play_draw_pile.extend([card_id] * quantity)
        self.play_discard_pile = []
        self.play_current_card_id = None
        self.play_draw_log = []
        self.play_game_active = True
        self.render_play_state()
        self.apply_play_timer_preset("game_start")

    def draw_card(self) -> None:
        if not self.play_draw_pile:
            return
        if self.play_current_card_id is not None:
            self.play_discard_pile.insert(0, self.play_current_card_id)
        index = random.randrange(len(self.play_draw_pile))
        card_id = self.play_draw_pile.pop(index)
        self.play_current_card_id = card_id
        self.play_draw_log.insert(0, card_id)
        self.render_play_state()
        self.apply_play_timer_preset("draw")

    def reset_game(self) -> None:
        self.play_draw_pile = []
        self.play_discard_pile = []
        self.play_current_card_id = None
        self.play_draw_log = []
        self.play_game_active = False
        self.play_image_view.zoom_to_default()
        self.reset_metronome()
        self.game_notes_text.clear()
        self.render_play_state()

    def confirm_reset_game(self) -> None:
        if (
            not self.play_game_active
            and not self.play_draw_pile
            and not self.play_discard_pile
            and self.play_current_card_id is None
        ):
            self.reset_game()
            return
        confirmation = self.question_box(
            "Reset game?",
            "Are you sure you want to reset the current game?\n\nThis cannot be undone.",
            QMessageBox.No,
        )
        if confirmation != QMessageBox.Yes:
            return
        self.reset_game()

    def render_play_state(self) -> None:
        self.remaining_label.setText(f"Remaining: {len(self.play_draw_pile)}")
        self.play_deck_list.clear()
        self.discard_list.clear()
        self.draw_log_list.clear()
        deck_counts: Dict[str, int] = {}
        for card_id in self.play_draw_pile:
            deck_counts[card_id] = deck_counts.get(card_id, 0) + 1
        counts: Dict[str, int] = {}
        for card_id in self.play_discard_pile:
            counts[card_id] = counts.get(card_id, 0) + 1
        play_deck = self.get_selected_play_deck()
        for card_id, quantity in sorted(
            deck_counts.items(),
            key=lambda item: self.get_card_for_deck_entry(item[0], play_deck)["name"].lower(),
        ):
            card = self.get_card_for_deck_entry(card_id, play_deck)
            item = QListWidgetItem(f"{card['name']}    x{quantity}")
            item.setData(Qt.UserRole, card_id)
            self.play_deck_list.addItem(item)
        for card_id, quantity in counts.items():
            card = self.get_card_for_deck_entry(card_id, play_deck)
            item = QListWidgetItem(f"{card['name']}    x{quantity}")
            item.setData(Qt.UserRole, card_id)
            self.discard_list.addItem(item)
        for draw_number, card_id in enumerate(self.play_draw_log, start=1):
            card = self.get_card_for_deck_entry(card_id, play_deck)
            item = QListWidgetItem(f"#{len(self.play_draw_log) - draw_number + 1}  {card['name']}")
            item.setData(Qt.UserRole, card_id)
            self.draw_log_list.addItem(item)
        self.play_history_tabs.setTabText(0, f"Discard ({len(self.play_discard_pile)})")
        self.play_history_tabs.setTabText(1, f"Draw Log ({len(self.play_draw_log)})")

        self.update_primary_play_action_button()
        self.refresh_play_image()

    def show_play_list_card_preview(self, item: QListWidgetItem) -> None:
        if not item:
            return
        card_id = item.data(Qt.UserRole)
        play_deck = self.get_selected_play_deck()
        card_data = self.get_card_for_deck_entry(card_id, play_deck)
        card = Card(
            id=card_data.get("id", card_id),
            name=card_data.get("name", card_id),
            value=card_data.get("value", ""),
            faction=card_data.get("faction", ""),
            effect=card_data.get("effect", ""),
            set_name=card_data.get("set_name", ""),
            card_number=card_data.get("card_number", ""),
            artist_name=card_data.get("artist_name", ""),
            card_author=card_data.get("card_author", ""),
            image_path=card_data.get("image_path", ""),
            source=card_data.get("source", ""),
        )
        self.open_card_preview_dialog(card)

    def show_current_play_card_preview(self) -> None:
        if not self.play_current_card_id:
            return
        play_deck = self.get_selected_play_deck()
        card_data = self.get_card_for_deck_entry(self.play_current_card_id, play_deck)
        card = Card(
            id=card_data.get("id", self.play_current_card_id),
            name=card_data.get("name", self.play_current_card_id),
            value=card_data.get("value", ""),
            faction=card_data.get("faction", ""),
            effect=card_data.get("effect", ""),
            set_name=card_data.get("set_name", ""),
            card_number=card_data.get("card_number", ""),
            artist_name=card_data.get("artist_name", ""),
            card_author=card_data.get("card_author", ""),
            image_path=card_data.get("image_path", ""),
            source=card_data.get("source", ""),
        )
        self.open_card_preview_dialog(card)

    def get_selected_play_deck(self) -> Optional[Deck]:
        deck_id = self.play_deck_combo.currentData()
        return next((deck for deck in self.decks if deck.id == deck_id), None)

    def refresh_play_image(self) -> None:
        if not self.play_current_card_id:
            self.play_image_view.show_placeholder("Warmup Phase" if self.play_game_active else "No Active Game")
            return
        play_deck = self.get_selected_play_deck()
        card = self.get_card_for_deck_entry(self.play_current_card_id, play_deck)
        self.play_image_view.set_image_path(card.get("image_path", ""))

    def get_card_icon_cache_signature(self, image_path: str) -> Optional[tuple[int, int]]:
        if not image_path:
            return None
        try:
            stat = Path(image_path).stat()
        except OSError:
            return None
        return (int(stat.st_mtime_ns), int(stat.st_size))

    def get_card_icon(self, image_path: str) -> QIcon:
        current_signature = self.get_card_icon_cache_signature(image_path)
        cached = self.card_icon_cache.get(image_path)
        if cached is not None:
            cached_icon, cached_signature = cached
            if cached_signature == current_signature:
                return cached_icon

        if image_path and Path(image_path).exists():
            pixmap = QPixmap(image_path)
            if not pixmap.isNull():
                icon = QIcon(
                    pixmap.scaled(
                        286,
                        400,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                )
                self.card_icon_cache[image_path] = (icon, current_signature)
                return icon

        fallback = QPixmap(286, 400)
        fallback.fill(Qt.darkGray)
        icon = QIcon(fallback)
        self.card_icon_cache[image_path] = (icon, current_signature)
        return icon

    def changeEvent(self, event) -> None:
        """Preserve normal native maximize/restore while fixing maximize-from-snap restore."""
        super().changeEvent(event)
        
        if event.type() == QEvent.WindowStateChange:
            entering_maximized = bool(self.windowState() & Qt.WindowMaximized) and not bool(
                event.oldState() & Qt.WindowMaximized
            )
            if entering_maximized:
                current = self.geometry()
                if self.is_snapped_geometry(current) and self.last_windowed_geometry is not None:
                    self._restore_pre_snap_after_maximize = True
                    self._pre_snap_restore_geometry = QRect(self.last_windowed_geometry)
                else:
                    self._restore_pre_snap_after_maximize = False
                    self._pre_snap_restore_geometry = None

            was_maximized = bool(event.oldState() & Qt.WindowMaximized)
            if was_maximized and not self.isMaximized():
                if self._restore_pre_snap_after_maximize and self._pre_snap_restore_geometry is not None:
                    QTimer.singleShot(80, self.apply_pre_snap_restore_geometry)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self.apply_native_window_icon)

    def apply_pre_snap_restore_geometry(self) -> None:
        """After maximize->restore from a snapped state, return to the earlier true windowed geometry."""
        target = self._pre_snap_restore_geometry
        if (
            target is None
            or self.isMaximized()
            or self.isFullScreen()
            or self.is_snapped_geometry(self.geometry())
        ):
            return

        self._restore_pre_snap_after_maximize = False
        self._pre_snap_restore_geometry = None
        self._restoring_window_geometry = True
        self.resize(target.size())
        self.move(target.topLeft())
        self._restoring_window_geometry = False
        self.last_windowed_geometry = QRect(target)


    def closeEvent(self, event) -> None:
        """Save window state before closing if enabled."""
        if self.restore_window_state_enabled():
            self.save_window_state()
        super().closeEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.MouseButtonPress and hasattr(self, "game_notes_text"):
            if self.game_notes_text.hasFocus():
                global_pos = event.globalPosition().toPoint()
                clicked_widget = QApplication.widgetAt(global_pos)
                inside_notes = False
                current = clicked_widget
                while current is not None:
                    if current is self.game_notes_text:
                        inside_notes = True
                        break
                    current = current.parentWidget()

                if not inside_notes:
                    self.game_notes_text.clearFocus()

        if self.active_preview_dialog is None:
            return super().eventFilter(watched, event)

        if watched == self.active_preview_dialog and event.type() == QEvent.MouseButtonPress:
            if event.button() == Qt.RightButton:
                self.active_preview_dialog.close()
                return True

        if event.type() == QEvent.KeyPress and self.active_preview_dialog is not None:
            if self.handle_active_preview_keypress(event):
                return True

        if event.type() == QEvent.MouseButtonPress:
            global_pos = event.globalPosition().toPoint()
            if not self.active_preview_dialog.frameGeometry().contains(global_pos):
                self.consume_preview_close_release = True
                self.active_preview_dialog.close()
                return True

        if event.type() == QEvent.MouseButtonRelease and self.consume_preview_close_release:
            self.consume_preview_close_release = False
            return True

        return super().eventFilter(watched, event)

    def handle_active_preview_keypress(self, event: QKeyEvent) -> bool:
        key = event.key()
        if key not in (Qt.Key_Left, Qt.Key_Right):
            return False

        if self.active_preview_source == "builder_pool":
            direction = -1 if key == Qt.Key_Left else 1
            self.navigate_builder_pool_preview(direction)
            return True

        if self.active_preview_source == "deck_entries":
            direction = -1 if key == Qt.Key_Left else 1
            self.navigate_deck_entry_preview(direction)
            return True

        return False

    def navigate_deck_entry_preview(self, direction: int) -> None:
        if self.active_preview_source != "deck_entries":
            return
        if self.deck_entries_list.count() <= 0:
            return

        current_row = self.deck_entries_list.currentRow()
        if current_row < 0:
            current_row = 0
        new_row = max(0, min(self.deck_entries_list.count() - 1, current_row + direction))
        if new_row == current_row:
            self.update_active_preview_navigation_buttons()
            return

        self.deck_entries_list.setCurrentRow(new_row)
        item = self.deck_entries_list.item(new_row)
        if item is None:
            self.update_active_preview_navigation_buttons()
            return
        card_id = item.data(Qt.UserRole)
        card = self.library_by_id.get(card_id)
        if card is None:
            card_data = self.get_card_for_deck_entry(card_id, self.get_current_saved_deck())
            card = Card(
                id=card_data.get("id", card_id),
                name=card_data.get("name", card_id),
                value=card_data.get("value", ""),
                faction=card_data.get("faction", ""),
                effect=card_data.get("effect", ""),
                set_name=card_data.get("set_name", ""),
                card_number=card_data.get("card_number", ""),
                artist_name=card_data.get("artist_name", ""),
                card_author=card_data.get("card_author", ""),
                image_path=card_data.get("image_path", ""),
                source=card_data.get("source", ""),
            )
        self.update_active_preview_card(card)

    def update_active_preview_navigation_buttons(self) -> None:
        if self.active_preview_prev_button is None or self.active_preview_next_button is None:
            return

        if self.active_preview_source == "builder_pool":
            if self.builder_pool_list.count() <= 0:
                self.active_preview_prev_button.setEnabled(False)
                self.active_preview_next_button.setEnabled(False)
                return
            current_row = self.builder_pool_list.currentRow()
            if current_row < 0:
                current_row = 0
            self.active_preview_prev_button.setEnabled(current_row > 0)
            self.active_preview_next_button.setEnabled(current_row < self.builder_pool_list.count() - 1)
            return

        if self.active_preview_source == "deck_entries":
            if self.deck_entries_list.count() <= 0:
                self.active_preview_prev_button.setEnabled(False)
                self.active_preview_next_button.setEnabled(False)
                return
            current_row = self.deck_entries_list.currentRow()
            if current_row < 0:
                current_row = 0
            self.active_preview_prev_button.setEnabled(current_row > 0)
            self.active_preview_next_button.setEnabled(current_row < self.deck_entries_list.count() - 1)
            return

    def navigate_builder_pool_preview(self, direction: int) -> None:
        if self.active_preview_source != "builder_pool":
            return
        if self.builder_pool_list.count() <= 0:
            return

        current_row = self.builder_pool_list.currentRow()
        if current_row < 0:
            current_row = 0
        new_row = max(0, min(self.builder_pool_list.count() - 1, current_row + direction))
        if new_row == current_row:
            self.update_active_preview_navigation_buttons()
            return

        self.builder_pool_list.setCurrentRow(new_row)
        item = self.builder_pool_list.item(new_row)
        if item is None:
            self.update_active_preview_navigation_buttons()
            return
        card = self.library_by_id.get(item.data(Qt.UserRole))
        if card is None:
            self.update_active_preview_navigation_buttons()
            return
        self.update_active_preview_card(card)

    def update_active_preview_card(self, card: Card) -> None:
        if self.active_preview_dialog is None or self.active_preview_view is None:
            return
        self.active_preview_card_id = card.id
        self.active_preview_dialog.setWindowTitle(card.name)
        self.active_preview_view.set_image_path(card.image_path)
        self.update_active_preview_navigation_buttons()

    def open_card_preview_dialog(self, card: Card, source: str = "") -> None:
        if self.active_preview_dialog is not None:
            self.active_preview_dialog.close()
        dialog = QDialog(self, Qt.Popup | Qt.FramelessWindowHint)
        dialog.setWindowTitle(card.name)
        screen = self.screen() or QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen else self.geometry()
        max_width = max(480, int(available.width() * 0.92))
        max_height = max(640, int(available.height() * 0.92))

        pixmap = QPixmap(card.image_path) if card.image_path and Path(card.image_path).exists() else QPixmap()
        if pixmap.isNull():
            preview_width = min(700, max_width)
            preview_height = min(900, max_height)
        else:
            preview_width = min(max_width, max(480, pixmap.width()))
            preview_height = min(max_height, max(640, pixmap.height()))

        dialog.resize(preview_width, preview_height)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)

        content_row = QHBoxLayout()
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(0)

        preview_view = ZoomableCardView()
        preview_view.setStyleSheet("border: 1px solid #666; background: #111;")
        preview_view.setMinimumSize(preview_width, preview_height)
        preview_view.set_image_path(card.image_path)
        preview_view.cardCloseRequested.connect(dialog.close)
        if source in ("builder_pool", "deck_entries"):
            prev_button = QPushButton("<")
            prev_button.setFixedWidth(42)
            prev_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
            prev_button.setFocusPolicy(Qt.NoFocus)
            prev_button.setStyleSheet("font-size: 22px; font-weight: bold;")
            if source == "builder_pool":
                prev_button.clicked.connect(lambda: self.navigate_builder_pool_preview(-1))
            else:
                prev_button.clicked.connect(lambda: self.navigate_deck_entry_preview(-1))
            content_row.addWidget(prev_button)
            self.active_preview_prev_button = prev_button
        else:
            self.active_preview_prev_button = None

        content_row.addWidget(preview_view, 1)

        if source in ("builder_pool", "deck_entries"):
            next_button = QPushButton(">")
            next_button.setFixedWidth(42)
            next_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
            next_button.setFocusPolicy(Qt.NoFocus)
            next_button.setStyleSheet("font-size: 22px; font-weight: bold;")
            if source == "builder_pool":
                next_button.clicked.connect(lambda: self.navigate_builder_pool_preview(1))
            else:
                next_button.clicked.connect(lambda: self.navigate_deck_entry_preview(1))
            content_row.addWidget(next_button)
            self.active_preview_next_button = next_button
        else:
            self.active_preview_next_button = None

        layout.addLayout(content_row)
        self.active_preview_dialog = dialog
        self.active_preview_view = preview_view
        self.active_preview_source = source
        self.active_preview_card_id = card.id
        self.update_active_preview_navigation_buttons()
        dialog.finished.connect(self.on_preview_dialog_closed)
        dialog.show()
        dialog.move(
            available.center().x() - dialog.width() // 2,
            available.center().y() - dialog.height() // 2,
        )

    def on_preview_dialog_closed(self, _result: int) -> None:
        self.active_preview_dialog = None
        self.active_preview_view = None
        self.active_preview_prev_button = None
        self.active_preview_next_button = None
        self.active_preview_source = ""
        self.active_preview_card_id = ""

    def show_card_preview_from_path(self, image_path: str, label: QLabel, zoom: float = 1.0) -> None:
        if not image_path or not Path(image_path).exists():
            label.setPixmap(QPixmap())
            label.setText("No image available")
            return
        pixmap = QPixmap(image_path)
        target_width = max(1, int(label.width() * zoom))
        target_height = max(1, int(label.height() * zoom))
        scaled = pixmap.scaled(target_width, target_height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        label.setPixmap(scaled)
        label.setText("")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.remember_windowed_geometry()
        if self.card_maker_image_path:
            self.show_card_preview_from_path(str(self.card_maker_image_path), self.card_maker_preview)
        if self.play_current_card_id:
            self.refresh_play_image()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self.remember_windowed_geometry()

    def start_stopwatch(self) -> None:
        if self.timer_started_at is not None:
            return
        if self.timer_mode == "countdown" and self.countdown_remaining_seconds <= 0:
            self.on_countdown_inputs_changed()
        self.countdown_flash_until = 0.0
        self.stopwatch_label.setStyleSheet("font-size: 28px; font-weight: bold;")
        self.timer_started_at = time.time()
        self.stopwatch_timer.start(100)

    def pause_stopwatch(self) -> None:
        if self.timer_started_at is None:
            return
        self.apply_running_timer_delta()
        self.timer_started_at = None
        self.stopwatch_timer.stop()
        self.refresh_stopwatch()

    def reset_stopwatch(self) -> None:
        self.timer_started_at = None
        self.timer_elapsed = 0.0
        self.countdown_remaining_seconds = float(self.countdown_target_seconds)
        self.countdown_flash_until = 0.0
        self.countdown_end_sound_playing = False
        self.countdown_end_sound.stop()
        self.stopwatch_timer.stop()
        self.stopwatch_label.setStyleSheet("font-size: 28px; font-weight: bold;")
        self.refresh_stopwatch()

    def refresh_stopwatch(self) -> None:
        self.apply_running_timer_delta()
        if self.timer_mode == "countdown":
            total_tenths = max(0, int(self.countdown_remaining_seconds * 10))
            minutes = total_tenths // 600
            seconds = (total_tenths % 600) // 10
            fraction = total_tenths % 10
            self.stopwatch_label.setText(f"{minutes:02d}:{seconds:02d}.{fraction}")
            if self.countdown_remaining_seconds <= 0 and self.timer_started_at is not None:
                self.timer_started_at = None
                self.countdown_flash_until = time.time() + 3.0
                self.countdown_end_sound_playing = False
        else:
            tenths = int(self.timer_elapsed * 10)
            minutes = tenths // 600
            seconds = (tenths % 600) // 10
            fraction = tenths % 10
            self.stopwatch_label.setText(f"{minutes:02d}:{seconds:02d}.{fraction}")

        self.update_timer_flash_state()
        if self.timer_started_at is None and time.time() >= self.countdown_flash_until:
            self.stopwatch_timer.stop()

    def apply_running_timer_delta(self) -> None:
        if self.timer_started_at is None:
            return
        now = time.time()
        delta = now - self.timer_started_at
        self.timer_started_at = now
        if self.timer_mode == "countdown":
            self.countdown_remaining_seconds = max(0.0, self.countdown_remaining_seconds - delta)
        else:
            self.timer_elapsed += delta

    def on_timer_mode_changed(self, text: str) -> None:
        was_running = self.timer_started_at is not None
        if was_running:
            self.pause_stopwatch()
        self.timer_mode = "countdown" if text.lower() == "countdown" else "stopwatch"
        self.countdown_minutes_spin.setEnabled(self.timer_mode == "countdown")
        self.countdown_seconds_spin.setEnabled(self.timer_mode == "countdown")
        self.reset_stopwatch()

    def on_countdown_inputs_changed(self) -> None:
        total_seconds = (self.countdown_minutes_spin.value() * 60) + self.countdown_seconds_spin.value()
        self.countdown_target_seconds = total_seconds
        if self.timer_mode == "countdown" and self.timer_started_at is None:
            self.countdown_remaining_seconds = float(total_seconds)
            self.refresh_stopwatch()

    def update_timer_flash_state(self) -> None:
        if time.time() < self.countdown_flash_until:
            phase = int(time.time() * 5) % 2
            color = "#ff4d4d" if phase == 0 else "#ffffff"
            self.stopwatch_label.setStyleSheet(
                f"font-size: 28px; font-weight: bold; color: {color};"
            )
            # Play countdown end sound while flashing (if enabled and sound is available)
            if (
                self.countdown_alert_sound_checkbox.isChecked()
                and self.countdown_end_sound.source().isValid()
                and not self.countdown_end_sound_playing
            ):
                self.countdown_end_sound.play()
                self.countdown_end_sound_playing = True
        else:
            self.stopwatch_label.setStyleSheet("font-size: 28px; font-weight: bold;")
            # Stop countdown sound when flashing ends
            if self.countdown_end_sound_playing:
                self.countdown_end_sound.stop()
                self.countdown_end_sound_playing = False

    def metronome_interval_ms(self) -> int:
        bpm = max(1, self.metronome_bpm)
        return max(60, int(round(60000 / bpm)))

    def update_metronome_display(self) -> None:
        if self.metronome_visual_checkbox.isChecked():
            beat_number = self.metronome_current_beat or 1
            self.metronome_bar.setFormat(str(beat_number))
        else:
            self.metronome_bar.setFormat("")

    def clear_metronome_pulse(self) -> None:
        self.update_metronome_display()
        self.metronome_bar.setValue(0)

    def start_metronome_audio_keepalive(self) -> None:
        self.stop_metronome_audio_keepalive()
        if not self.metronome_sound_checkbox.isChecked():
            return
        if not self.metronome_keepalive_sink:
            return
        self.metronome_keepalive_sink.start(self.metronome_keepalive_stream)

    def stop_metronome_audio_keepalive(self) -> None:
        if self.metronome_keepalive_sink:
            self.metronome_keepalive_sink.stop()

    def play_metronome_click(self, accented: bool) -> None:
        if not self.metronome_sound_checkbox.isChecked():
            return
        if self.metronome_tick_sound.source().isValid():
            sound = self.metronome_tick_alt_sound if self.metronome_use_alt_tick_sound else self.metronome_tick_sound
            self.metronome_use_alt_tick_sound = not self.metronome_use_alt_tick_sound
            sound.setVolume(0.62 if accented else 0.48)
            sound.play()
            return
        QApplication.beep()

    def update_metronome_bar(self) -> None:
        if not self.metronome_visual_checkbox.isChecked() or self.metronome_last_beat_at <= 0:
            self.metronome_bar.setValue(0)
            return
        interval_seconds = self.metronome_interval_ms() / 1000.0
        if interval_seconds <= 0:
            self.metronome_bar.setValue(0)
            return
        phase = ((time.monotonic() - self.metronome_last_beat_at) % interval_seconds) / interval_seconds
        progress = phase * 2 if phase <= 0.5 else (1.0 - phase) * 2
        self.metronome_bar.setValue(max(0, min(100, int(round(progress * 100)))))

    def advance_metronome_beat(self) -> None:
        beats_per_bar = max(1, self.metronome_beats_per_bar)
        if self.metronome_current_beat <= 0 or self.metronome_current_beat >= beats_per_bar:
            self.metronome_current_beat = 1
        else:
            self.metronome_current_beat += 1
        accented = self.metronome_current_beat == 1 and beats_per_bar > 1
        self.metronome_last_beat_at = time.monotonic()
        self.update_metronome_display()
        chunk_color = "#ffb347" if accented else "#6ecbff"
        if not self.metronome_visual_checkbox.isChecked():
            chunk_color = "#161616"
        self.metronome_bar.setStyleSheet(
            "QProgressBar { border: 1px solid #666; border-radius: 10px; background: #161616; "
            "color: #f5f5f5; font-size: 28px; font-weight: bold; text-align: center; }"
            f"QProgressBar::chunk {{ background: {chunk_color}; border-radius: 8px; }}"
        )
        self.update_metronome_bar()
        self.play_metronome_click(accented)

    def begin_metronome_playback(self) -> None:
        self.advance_metronome_beat()
        self.metronome_timer.start(self.metronome_interval_ms())
        self.metronome_bar_timer.start(16)

    def start_metronome(self) -> None:
        self.metronome_bpm = self.metronome_bpm_spin.value()
        self.metronome_beats_per_bar = self.metronome_beats_spin.value()
        if self.metronome_timer.isActive():
            self.metronome_timer.start(self.metronome_interval_ms())
            self.start_metronome_audio_keepalive()
            return
        if self.metronome_sound_checkbox.isChecked() and self.metronome_keepalive_sink:
            self.start_metronome_audio_keepalive()
            delay_ms = self.metronome_audio_warmup_ms() if self.metronome_audio_warmup_enabled() else 0
            QTimer.singleShot(delay_ms, self.begin_metronome_playback)
            return
        self.begin_metronome_playback()

    def pause_metronome(self) -> None:
        self.metronome_timer.stop()
        self.metronome_bar_timer.stop()
        self.stop_metronome_audio_keepalive()
        self.clear_metronome_pulse()

    def reset_metronome(self) -> None:
        self.pause_metronome()
        self.metronome_current_beat = 0
        self.metronome_last_beat_at = 0.0
        self.update_metronome_display()

    def on_metronome_settings_changed(self) -> None:
        self.metronome_bpm = self.metronome_bpm_spin.value()
        self.metronome_beats_per_bar = self.metronome_beats_spin.value()
        if self.metronome_current_beat > self.metronome_beats_per_bar:
            self.metronome_current_beat = 0
            self.update_metronome_display()
        if self.metronome_timer.isActive():
            self.metronome_timer.start(self.metronome_interval_ms())
            self.start_metronome_audio_keepalive()

    def on_metronome_visual_toggled(self, checked: bool) -> None:
        self.metronome_bar.setStyleSheet(
            "QProgressBar { border: 1px solid #666; border-radius: 10px; background: #161616; "
            "color: #f5f5f5; font-size: 28px; font-weight: bold; text-align: center; }"
            f"QProgressBar::chunk {{ background: {'#161616' if not checked else '#6ecbff'}; border-radius: 8px; }}"
        )
        self.update_metronome_display()
        if not checked:
            self.metronome_bar.setValue(0)

    def on_metronome_sound_toggled(self, checked: bool) -> None:
        if checked and self.metronome_timer.isActive():
            self.start_metronome_audio_keepalive()
        else:
            self.stop_metronome_audio_keepalive()


def main() -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_WINDOWS_APP_ID)
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_DISPLAY_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_STORAGE_NAME)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
