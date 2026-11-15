"""Regression checks for the Tk card list's recycled Canvas item pool."""
import sys
import unittest
from collections import OrderedDict
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Minecraft迁移工具"))
from ui.card_list import ModCardList


class FakeCanvas:
    def __init__(self, width=900, height=640):
        self.width = width
        self.height = height
        self.next_id = 1
        self.items = {}

    def winfo_width(self):
        return self.width

    def winfo_height(self):
        return self.height

    def _create(self, kind, coords, options):
        item_id = self.next_id
        self.next_id += 1
        self.items[item_id] = {"kind": kind, "coords": coords, "options": options}
        return item_id

    def create_image(self, *coords, **options):
        return self._create("image", coords, options)

    def create_text(self, *coords, **options):
        return self._create("text", coords, options)

    def create_rectangle(self, *coords, **options):
        return self._create("rectangle", coords, options)

    def coords(self, item_id, *coords):
        if coords:
            self.items[item_id]["coords"] = coords
        return self.items[item_id]["coords"]

    def itemconfigure(self, item_id, **options):
        self.items[item_id]["options"].update(options)

    def tag_raise(self, _item_id):
        pass

    def move(self, _tag, _dx, _dy):
        pass


class FakeScrollbar:
    def set(self, *_args):
        pass


class CardListVirtualizationTests(unittest.TestCase):
    def make_list(self):
        widget = object.__new__(ModCardList)
        widget.canvas = FakeCanvas()
        widget.vsb = FakeScrollbar()
        widget.rows = [{"index": i} for i in range(5000)]
        widget._top = 0.0
        widget._drawn_top = None
        widget._drawn_first = None
        widget._slots = []
        widget._row_to_slot = {}
        widget._empty_item = None
        widget._hover = -1
        widget._sel = -1
        widget._hover_action = None
        widget._action_rects = {}
        widget._sel_anim = {}
        widget.ROW_H = 64
        widget._rendered_rows = []

        def draw_row(index):
            slot_index = widget._row_to_slot[index]
            surface = widget._slots[slot_index]["surface"]
            surface.begin()
            surface.create_rectangle(index, 0, index + 1, 1, fill="#fff")
            surface.create_text(index, 0, text=str(index))
            surface.finish()
            widget._rendered_rows.append(index)

        widget._draw_row = draw_row
        return widget

    def test_item_pool_scales_with_viewport_not_dataset(self):
        widget = self.make_list()
        ModCardList._render(widget)
        visible_slots = len(range(0, widget._rows_per_page() + 2))
        self.assertEqual(len(widget._slots), visible_slots)
        initial_item_count = len(widget.canvas.items)
        self.assertEqual(initial_item_count, visible_slots * 2)

        for first in (1, 25, 100, 900, 2500, 4990):
            widget._top = float(first * widget.ROW_H)
            ModCardList._render(widget)
            expected_rows = set(range(first, min(len(widget.rows),
                                                 first + widget._rows_per_page() + 2)))
            expected_slots = len(expected_rows)
            self.assertLessEqual(len(widget._slots), visible_slots)
            self.assertEqual(len(widget.canvas.items), initial_item_count)
            self.assertEqual(set(widget._row_to_slot), expected_rows)
            self.assertEqual(len(set(widget._row_to_slot.values())), expected_slots)

    def test_reused_slots_update_items_without_creating_more(self):
        widget = self.make_list()
        ModCardList._render(widget)
        item_count = len(widget.canvas.items)
        slot_index = widget._row_to_slot[0]
        old_ids = tuple(item_id for items in
                        widget._slots[slot_index]["surface"].items.values()
                        for item_id in items)

        widget._top = float(1 * widget.ROW_H)
        ModCardList._render(widget)
        new_slot_index = widget._row_to_slot[widget._rows_per_page() + 2]
        self.assertEqual(new_slot_index, slot_index)
        self.assertEqual(len(widget.canvas.items), item_count)
        new_ids = tuple(item_id for items in
                        widget._slots[new_slot_index]["surface"].items.values()
                        for item_id in items)
        self.assertEqual(new_ids, old_ids)

    def test_photo_caches_are_lru_bounded(self):
        widget = object.__new__(ModCardList)
        widget._photos = OrderedDict()
        widget._misc_photos = OrderedDict()
        widget.fallback_icon = None
        widget.icon_provider = lambda row: row["image"]
        widget.ICON_PX = 44

        class FakeImage:
            LANCZOS = object()

            def __init__(self, cache_key=None):
                self.info = {} if cache_key is None else {
                    "_mctool_photo_key": cache_key}

            def copy(self):
                return self

            def thumbnail(self, *_args):
                pass

        class FakePhoto:
            def __init__(self, image):
                self.image = image

        with mock.patch("PIL.ImageTk.PhotoImage", side_effect=FakePhoto):
            # 图标改成了后台解码：_icon_photo 先给兜底图、把解码排进队列。这里不起线程
            # （_ensure_icon_worker 打桩），手动把"解码结果"喂回去，再走一次真实的
            # _icon_poll 回填路径，最后检查两个缓存都被 LRU 限住。
            widget._ensure_icon_worker = lambda: None
            jobs, done, _queued = widget._icon_state()
            for index in range(520):
                ModCardList._icon_photo(widget, {
                    "icon_key": f"icon-{index}", "image": FakeImage()})
            # 第一次见到图标时先画兜底图（不能返回 None，否则文字起点会跳）
            self.assertIn(("fallback", id(None)), widget._photos)
            while True:
                try:
                    cache_key, row = jobs.get_nowait()
                except Exception:
                    break
                done.put((cache_key, row["image"]))
            ModCardList._icon_poll(widget)
            self.assertEqual(len(widget._photos), 512)
            self.assertNotIn(("icon", "icon-0"), widget._photos)
            self.assertIn(("icon", "icon-519"), widget._photos)

            for index in range(520):
                ModCardList._photo_for(widget, FakeImage((20, 17, 8, f"#{index:06x}")))
            self.assertEqual(len(widget._misc_photos), 512)
            self.assertNotIn((20, 17, 8, "#000000"), widget._misc_photos)
            self.assertIn((20, 17, 8, "#000207"), widget._misc_photos)

    def test_icon_is_decoded_off_the_main_thread(self):
        """滚动路径上不能同步跑 icon_provider：真实图标 3.5ms/张，一屏 12 张就是 41ms。

        这里让 provider 故意慢 50ms，量 `_icon_photo` 的返回耗时 —— 它只该排队 + 给兜底图。
        """
        import time
        widget = object.__new__(ModCardList)
        widget._photos = OrderedDict()
        widget._misc_photos = OrderedDict()
        widget.fallback_icon = None
        widget.ICON_PX = 44
        widget.icon_provider = lambda row: time.sleep(0.05) or None

        class FakeImage:
            LANCZOS = object()

            def copy(self):
                return self

            def thumbnail(self, *_a):
                pass

        class FakePhoto:
            def __init__(self, image):
                self.image = image

        with mock.patch("PIL.ImageTk.PhotoImage", side_effect=FakePhoto):
            widget._ensure_icon_worker = lambda: None
            t0 = time.perf_counter()
            ModCardList._icon_photo(widget, {"icon_key": "slow", "image": FakeImage()})
            spent = (time.perf_counter() - t0) * 1000
        self.assertLess(spent, 20, "取图标不该等解码：%.1fms" % spent)
        self.assertNotIn(("icon", "slow"), widget._photos)     # 还没解码完
        self.assertIn(("fallback", id(None)), widget._photos)  # 先给兜底


if __name__ == "__main__":
    unittest.main(verbosity=2)
