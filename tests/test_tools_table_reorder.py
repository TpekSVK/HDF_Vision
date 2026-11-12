"""Custom recipe reordering must not request Qt's second row removal."""
import unittest

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtCore import QMimeData
from PySide6.QtWidgets import QApplication, QAbstractItemView, QTableWidgetItem

from app.ui.golden_wizard.tools_table import ToolsTableWidget


class InternalDrop(QDropEvent):
    def __init__(self, table, position):
        super().__init__(position, Qt.MoveAction, QMimeData(), Qt.LeftButton, Qt.NoModifier)
        self.table = table

    def source(self):
        return self.table


class ToolsTableReorderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def check_drop(self, selected, destination, expected, reject=False):
        table = ToolsTableWidget(3, 1)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setDragDropMode(QAbstractItemView.InternalMove)
        table.setDragDropOverwriteMode(False)
        names = ['A', 'B', 'C']
        orders = []

        def populate(order):
            table.setRowCount(3)
            for row, index in enumerate(order):
                item = QTableWidgetItem(names[index])
                item.setData(Qt.UserRole, row)
                table.setItem(row, 0, item)
            table.selectRow(order.index(selected))

        def reordered(order):
            orders.append(order)
            populate([0, 1, 2] if reject else order)

        populate([0, 1, 2])
        table.rowsReordered.connect(reordered)
        table.show()
        self.app.processEvents()
        y = table.rowViewportPosition(destination) + 1 if destination < 3 else table.viewport().height() - 1
        event = InternalDrop(table, QPointF(5, y))
        table.dropEvent(event)
        # startDrag removes the selected row if the result is MoveAction.
        self.assertEqual(event.dropAction(), Qt.IgnoreAction)
        self.assertTrue(event.isAccepted())
        self.assertEqual(table.rowCount(), 3)
        self.assertEqual([table.item(row, 0).text() for row in range(3)], expected)
        table.close()
        return orders

    def test_move_down(self):
        self.assertEqual(self.check_drop(0, 3, ['B', 'C', 'A']), [[1, 2, 0]])

    def test_move_up(self):
        self.assertEqual(self.check_drop(2, 0, ['C', 'A', 'B']), [[2, 0, 1]])

    def test_same_position(self):
        self.assertEqual(self.check_drop(1, 1, ['A', 'B', 'C']), [])

    def test_rejected_reorder_preserves_rows(self):
        self.check_drop(0, 3, ['A', 'B', 'C'], reject=True)
