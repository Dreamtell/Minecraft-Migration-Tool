"""Regression checks for the Tk virtual table hot scroll path.

These tests use a small fake Canvas so they do not create a Tk window.  The
real widget is still exercised by the application's existing UI probes.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Minecraft迁移工具"))
from ui.virtual_table import VirtualTable


class VirtualTableScrollTests(unittest.TestCase):
    def make_table(self):
        body = SimpleNamespace(position=0.0)
        body.canvasy = lambda _y: body.position
        table = object.__new__(VirtualTable)
        table.body = body
        table.row_height = 26
        table._first = 0
        table.render_count = 0
        table.scroll_notifications = 0
        def render():
            table.render_count += 1
            table._first = max(0, int(body.position // table.row_height))
        table._render = render
        table.on_scroll = lambda: setattr(
            table, "scroll_notifications", table.scroll_notifications + 1)
        return table, body

    def test_sub_row_pixel_scroll_does_not_repaint_rows(self):
        table, body = self.make_table()
        body.position = 13
        VirtualTable._after_scroll(table)
        self.assertEqual(table.render_count, 0)
        self.assertEqual(table.scroll_notifications, 1)

    def test_crossing_row_boundary_repaints_once(self):
        table, body = self.make_table()
        body.position = 26
        VirtualTable._after_scroll(table)
        self.assertEqual(table.render_count, 1)
        self.assertEqual(table.scroll_notifications, 1)
        self.assertEqual(table._first, 1)

    def test_fractional_position_is_clamped_to_the_first_row(self):
        table, body = self.make_table()
        body.position = -4
        VirtualTable._after_scroll(table)
        self.assertEqual(table.render_count, 0)
        self.assertEqual(table.scroll_notifications, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
