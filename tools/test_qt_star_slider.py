# tools/test_qt_star_slider.py
"""星空滑块（ui/qt_main/star_slider.py）的离屏渲染测试。

覆盖：值映射/信号、渐变方向、星点被填充区裁掉、白色拇指、键盘/滚轮、
隐藏时停表、卡片标题渐变、深浅主题、预览窗口的档位联动。
"""
import math
import os
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Minecraft迁移工具"))

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from ui.qt_main import star_slider as ss  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def pump(ms):
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        APP.processEvents(QtCore.QEventLoop.AllEvents, 5)
        time.sleep(0.002)


def grab(widget):
    return widget.grab().toImage().convertToFormat(QtGui.QImage.Format_ARGB32)


def rgb(img, x, y):
    c = QtGui.QColor(img.pixel(int(x), int(y)))
    return c.red(), c.green(), c.blue()


class StarSliderTests(unittest.TestCase):
    def setUp(self):
        self.host = QtWidgets.QWidget()
        self.host.setStyleSheet("background: #0f0f11;")
        self.host.resize(400, 80)
        self.s = ss.StarSlider(self.host, value=1.0, dark=True, twinkle=False)
        self.s.setGeometry(20, 20, 300, 38)
        self.host.show()
        pump(30)

    def tearDown(self):
        self.host.close()
        self.host.deleteLater()

    def test_value_mapping_and_signal(self):
        tr = self.s._track_rect()
        self.assertAlmostEqual(self.s._value_at(tr.left()), 0.0, places=6)
        self.assertAlmostEqual(self.s._value_at(tr.right()), 1.0, places=6)
        self.assertAlmostEqual(self.s._value_at(tr.center().x()), 0.5, places=2)
        seen = []
        self.s.valueChanged.connect(seen.append)
        self.s.setValue(0.25)
        self.assertEqual(len(seen), 1)
        self.assertAlmostEqual(seen[0], 0.25, places=6)
        self.s.setValue(0.25)              # 没变就不重复发
        self.assertEqual(len(seen), 1)
        self.s.setValue(5)                 # 越界夹到 1
        self.assertEqual(self.s.value(), 1.0)

    def test_gradient_runs_blue_to_violet_along_full_track(self):
        # 渐变按整条轨道铺：值拉满，但右端取样点避开白色拇指和它的白光
        self.s.setValue(1.0)
        pump(30)
        img = grab(self.s)
        tr = self.s._track_rect()
        y = tr.center().y()
        r0, g0, b0 = rgb(img, tr.left() + 0.08 * tr.width(), y)
        r1, g1, b1 = rgb(img, tr.left() + 0.75 * tr.width(), y)
        self.assertGreater(b0, r0)                 # 左端偏蓝
        self.assertGreater(r1, r0 + 30)            # 右端更红（紫）
        self.assertGreater(b0, g0)
        self.assertGreaterEqual(b1, 240)

    def test_stars_are_clipped_to_filled_part(self):
        # 用两个确定位置的星点：0.2 在填充里，0.8 应被裁掉
        # phase=pi/2 且速度 0 → 闪烁系数恒为 1，星点画成纯白
        self.s._stars = [
            (0.2, 0.5, 1.8, 255, math.pi / 2, 0.0, False),
            (0.8, 0.5, 1.8, 255, math.pi / 2, 0.0, False),
        ]
        self.s._t0 = time.perf_counter()
        self.s.setValue(0.35)
        img = grab(self.s)
        tr = self.s._track_rect()
        y = tr.top() + 0.5 * tr.height()
        lit = rgb(img, tr.left() + 0.2 * tr.width(), y)
        dark = rgb(img, tr.left() + 0.8 * tr.width(), y)
        self.assertTrue(all(v > 200 for v in lit), lit)      # 白点
        self.assertLess(max(dark), 90, dark)                 # 未填充区只是底槽
        # 拉到 1.0 之后第二个点就亮了
        self.s.setValue(1.0)
        img = grab(self.s)
        lit2 = rgb(img, tr.left() + 0.8 * tr.width(), y)
        self.assertTrue(all(v > 200 for v in lit2), lit2)

    def test_thumb_is_white_circle_at_value(self):
        self.s._stars = []                      # 只看拇指，排除星点干扰
        self.s.setValue(0.5)
        img = grab(self.s)
        tr = self.s._track_rect()
        cx = tr.left() + tr.width() * 0.5
        r, g, b = rgb(img, cx, tr.center().y())
        self.assertGreater(min(r, g, b), 240)
        # 拇指之外紧邻的一圈保持轨道本色：没有光晕、没有落影
        r2, g2, b2 = rgb(img, cx - ss._u(ss._THUMB_R) * 1.5, tr.center().y())
        self.assertLess(g2, 150, (r2, g2, b2))
        # 更远处的渐变也不该是纯白
        r3, g3, b3 = rgb(img, tr.left() + tr.width() * 0.25, tr.center().y())
        self.assertLess(min(r3, g3, b3), 250)

    def test_stops_snap_to_nodes_and_step_by_key(self):
        s = ss.StarSlider(self.host, value=1.0, twinkle=False, stops=4)
        s.setGeometry(20, 20, 300, 38)
        s.show()
        pump(30)
        try:
            stops = s.stops()
            self.assertEqual(len(stops), 4)
            self.assertAlmostEqual(stops[0], 0.0)
            self.assertAlmostEqual(stops[-1], 1.0)
            s.setValue(0.5)                      # 落在两档中间 → 吸到最近档
            self.assertIn(round(s.value(), 3), (0.333, 0.667))
            s.setValue(0.99)                     # 差一点也吸到 1.0
            self.assertAlmostEqual(s.value(), 1.0, places=6)
            s.setValue(0.0)
            s.setFocus()
            for expect, key in ((1 / 3.0, QtCore.Qt.Key_Right),
                                (2 / 3.0, QtCore.Qt.Key_Right),
                                (1.0, QtCore.Qt.Key_Right),
                                (1.0, QtCore.Qt.Key_Right)):     # 到头不动
                s.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key,
                                                QtCore.Qt.NoModifier))
                pump(300)
                self.assertAlmostEqual(s.value(), expect, places=3)
            s.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Left,
                                            QtCore.Qt.NoModifier))
            pump(300)
            self.assertAlmostEqual(s.value(), 2 / 3.0, places=3)
        finally:
            s.close()
            s.deleteLater()

    def test_drag_follows_then_snaps_on_release(self):
        s = ss.StarSlider(self.host, value=1.0, twinkle=False, stops=4)
        s.setGeometry(20, 20, 300, 38)
        s.show()
        pump(30)
        try:
            tr = s._track_rect()
            pos = QtCore.QPointF(tr.left() + tr.width() * 0.62, tr.center().y())
            s.mousePressEvent(QtGui.QMouseEvent(
                QtCore.QEvent.MouseButtonPress, pos,
                QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
            # 按住时跟手，不吸附
            self.assertAlmostEqual(s.value(), 0.62, places=2)
            s.mouseMoveEvent(QtGui.QMouseEvent(
                QtCore.QEvent.MouseMove,
                QtCore.QPointF(tr.left() + tr.width() * 0.05, tr.center().y()),
                QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
            self.assertAlmostEqual(s.value(), 0.05, places=2)
            # 松手才吸附，并且是滑过去的（有动画）
            s.mouseReleaseEvent(QtGui.QMouseEvent(
                QtCore.QEvent.MouseButtonRelease,
                QtCore.QPointF(tr.left() + tr.width() * 0.05, tr.center().y()),
                QtCore.Qt.LeftButton, QtCore.Qt.NoButton, QtCore.Qt.NoModifier))
            self.assertLess(s.value(), 0.05)          # 立刻开始往 0 滑
            pump(400)
            self.assertAlmostEqual(s.value(), 0.0, places=3)
        finally:
            s.close()
            s.deleteLater()

    def test_snap_uses_distance_plus_drag_direction(self):
        s = ss.StarSlider(self.host, value=0.0, twinkle=False, stops=4)
        try:
            self.assertAlmostEqual(s._snap_with_direction(0.47, 1), 2 / 3.0, places=6)
            self.assertAlmostEqual(s._snap_with_direction(0.47, -1), 1 / 3.0, places=6)
            self.assertAlmostEqual(s._snap_with_direction(0.47, 0), 1 / 3.0, places=6)
            # 离得够远时方向说了不算，还是就近
            self.assertAlmostEqual(s._snap_with_direction(0.60, -1), 2 / 3.0, places=6)
            self.assertAlmostEqual(s._snap_with_direction(0.60, 1), 2 / 3.0, places=6)
            self.assertAlmostEqual(s._snap_with_direction(0.90, -1), 1.0, places=6)
            # 离中线太远时，方向拉不动它
            self.assertAlmostEqual(s._snap_with_direction(0.40, 1), 1 / 3.0, places=6)
        finally:
            s.close()
            s.deleteLater()

    def test_timer_interval_follows_screen_refresh_rate(self):
        hz = self.s._refresh_hz()
        self.assertGreaterEqual(hz, 24.0)
        self.assertLessEqual(hz, 240.0)
        self.assertEqual(self.s._interval, max(3, int(round(1000.0 / hz))))
        # 60Hz 约 17ms、144Hz 约 7ms、240Hz 约 4ms
        if abs(hz - 60.0) < 1.0:
            self.assertIn(self.s._interval, (16, 17))

    def test_direction_threshold_in_a_real_drag(self):
        s = ss.StarSlider(self.host, value=0.0, twinkle=False, stops=4)
        s.setGeometry(20, 20, 300, 38)
        s.show()
        pump(30)
        try:
            tr = s._track_rect()
            def drag(from_v, to_v):
                s.mousePressEvent(QtGui.QMouseEvent(
                    QtCore.QEvent.MouseButtonPress,
                    QtCore.QPointF(tr.left() + tr.width() * from_v, tr.center().y()),
                    QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
                s.mouseMoveEvent(QtGui.QMouseEvent(
                    QtCore.QEvent.MouseMove,
                    QtCore.QPointF(tr.left() + tr.width() * to_v, tr.center().y()),
                    QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
                s.mouseReleaseEvent(QtGui.QMouseEvent(
                    QtCore.QEvent.MouseButtonRelease,
                    QtCore.QPointF(tr.left() + tr.width() * to_v, tr.center().y()),
                    QtCore.Qt.LeftButton, QtCore.Qt.NoButton, QtCore.Qt.NoModifier))
                pump(360)
            drag(0.0, 0.46)                       # 往右拖，过了 35% 分界 → 进到 2/3
            self.assertAlmostEqual(s.value(), 2 / 3.0, places=3)
            drag(2 / 3.0, 0.46)                   # 往左拖，得退到 65% 以下才回 1/3
            self.assertAlmostEqual(s.value(), 1 / 3.0, places=3)
        finally:
            s.close()
            s.deleteLater()

    def test_stops_are_visible_on_the_track(self):
        s = ss.StarSlider(self.host, value=0.0, dark=True, twinkle=False, stops=4)
        s.setGeometry(20, 20, 300, 38)
        s.show()
        pump(40)
        try:
            tr = s._track_rect()
            y = tr.center().y()
            tick_x = tr.left() + tr.width() / 3.0
            img = grab(s)
            tick = rgb(img, tick_x, y)
            plain = rgb(img, tick_x + 22, y)
            self.assertGreater(sum(tick), sum(plain) + 60)   # 刻度比槽底亮
            # 两端不画刻度：只画中间档
            self.assertEqual(s._node_stops(), [1 / 3.0, 2 / 3.0])
            s.set_stops_visible(False)
            pump(20)
            img2 = grab(s)
            self.assertLess(sum(rgb(img2, tick_x, y)), sum(plain) + 40)
        finally:
            s.close()
            s.deleteLater()

    def test_continuous_slider_has_no_ticks(self):
        s = ss.StarSlider(self.host, value=0.0, dark=True, twinkle=False)
        s.setGeometry(20, 20, 300, 38)
        s.show()
        pump(40)
        try:
            tr = s._track_rect()
            img = grab(s)
            y = tr.center().y()
            for frac in (1 / 3.0, 2 / 3.0):
                x = tr.left() + tr.width() * frac
                self.assertLess(sum(rgb(img, x, y)), sum(rgb(img, x + 22, y)) + 40)
        finally:
            s.close()
            s.deleteLater()


    def test_below_ultra_fill_is_a_single_color(self):
        s = ss.StarSlider(self.host, value=0.0, dark=True, twinkle=False)
        s.setGeometry(20, 20, 300, 38)
        s.show()
        try:
            s.setValue(0.6)          # 没拉满：只有单色，不要渐变和星星
            pump(60)
            tr = s._track_rect()
            y = tr.center().y()
            img = grab(s)
            a = rgb(img, tr.left() + tr.width() * 0.10, y)
            b = rgb(img, tr.left() + tr.width() * 0.45, y)
            for ca, cb in zip(a, b):
                self.assertLess(abs(ca - cb), 14, (a, b))
            self.assertAlmostEqual(s._fx, 0.0, places=3)
            self.assertAlmostEqual(s._node_alpha(), 1.0, places=3)
        finally:
            s.close()
            s.deleteLater()

    def test_ultra_has_gradient_and_hides_nodes(self):
        s = ss.StarSlider(self.host, value=0.0, dark=True, twinkle=False, stops=4)
        s.setGeometry(20, 20, 300, 38)
        s.show()
        try:
            s.setValue(1.0)
            pump(600)                # 等 Ultra 特效淡入
            self.assertGreater(s._fx, 0.95)
            self.assertLess(s._node_alpha(), 0.1)      # 拉满时节点隐藏
            tr = s._track_rect()
            y = tr.center().y()
            img = grab(s)
            left = rgb(img, tr.left() + tr.width() * 0.06, y)
            right = rgb(img, tr.left() + tr.width() * 0.80, y)
            self.assertGreater(right[0], left[0] + 40, (left, right))   # 右端更紫
            self.assertGreater(min(left[2], right[2]), 200)
        finally:
            s.close()
            s.deleteLater()

    def test_ultra_colors_keep_moving(self):
        s = ss.StarSlider(self.host, value=1.0, dark=True, twinkle=False)
        s.setGeometry(20, 20, 300, 38)
        s.show()
        pump(120)
        try:
            tr = s._track_rect()
            y = tr.center().y()
            x = tr.left() + tr.width() * 0.55
            a = rgb(grab(s), x, y)
            pump(320)
            b = rgb(grab(s), x, y)
            self.assertGreater(sum(abs(i - j) for i, j in zip(a, b)), 8, (a, b))
        finally:
            s.close()
            s.deleteLater()

    def test_thumb_is_not_clipped_at_track_ends(self):
        self.s._stars = []
        for v in (0.0, 1.0):
            self.s.setValue(v)
            img = grab(self.s)
            tr = self.s._track_rect()
            r = self.s._thumb_radius()
            cx = tr.left() + tr.width() * v
            self.assertGreaterEqual(cx - r, 0.0, "左端被裁")
            self.assertLessEqual(cx + r, self.s.width(), "右端被裁")
            # 最外侧一列像素不该是拇指的白（否则就是被切平了）
            _, g, _ = rgb(img, self.s.width() - 1, tr.center().y())
            self.assertLess(g, 240, "右边缘被切平")

    def test_stars_animate_over_time(self):
        s = ss.StarSlider(self.host, value=1.0, twinkle=True)
        s.setGeometry(20, 20, 300, 38)
        s._stars = [
            (0.30, 0.5, 1.6, 235, 1.0, 3.0, False),
            (0.60, 0.5, 1.6, 235, 2.0, 3.0, False),
        ]
        s.show()
        pump(60)
        try:
            img_a = grab(s)
            pump(260)                          # 闪烁速度 3 rad/s，260ms 足够变一轮
            img_b = grab(s)
            tr = s._track_rect()
            changed = 0
            for xn in (0.30, 0.60):
                x = int(tr.left() + xn * tr.width())
                y = int(tr.top() + 0.5 * tr.height())
                if rgb(img_a, x, y) != rgb(img_b, x, y):
                    changed += 1
            self.assertGreaterEqual(changed, 1, "星点没有随时间变化")
            self.assertTrue(s._timer.isActive())
        finally:
            s.close()
            s.deleteLater()

    def test_keyboard_and_wheel_change_value(self):
        self.s.setFocus()
        before = self.s.value()
        ev = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Left,
                             QtCore.Qt.NoModifier)
        self.s.keyPressEvent(ev)
        pump(260)                                   # 让动画收敛
        self.assertLess(self.s.value(), before)
        ev = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_End,
                             QtCore.Qt.NoModifier)
        self.s.keyPressEvent(ev)
        pump(260)
        self.assertAlmostEqual(self.s.value(), 1.0, places=3)
        we = QtGui.QWheelEvent(QtCore.QPointF(150, 19), QtCore.QPointF(150, 19),
                               QtCore.QPoint(0, 0), QtCore.QPoint(0, -120),
                               QtCore.Qt.NoButton, QtCore.Qt.NoModifier,
                               QtCore.Qt.NoScrollPhase, False)
        self.s.wheelEvent(we)
        pump(260)
        self.assertLess(self.s.value(), 1.0)

    def test_timer_stops_when_hidden_and_when_idle(self):
        self.s.set_twinkle(True)
        pump(50)
        self.assertTrue(self.s._timer.isActive())   # 闪烁要表
        self.s.set_twinkle(False)
        pump(120)
        self.assertFalse(self.s._timer.isActive())  # 不闪又没动画 → 停表
        self.s.set_twinkle(True)
        pump(50)
        self.s.hide()
        pump(20)
        self.assertFalse(self.s._timer.isActive())

    def test_light_theme_uses_light_card_and_darker_title(self):
        self.s.set_dark(False)
        self.s.setValue(0.0)               # 值归零，样品落在未填充的底槽上
        pump(40)
        img = grab(self.s)
        tr = self.s._track_rect()
        r, g, b = rgb(img, tr.center().x(), tr.center().y())
        self.assertGreater(min(r, g, b), 200)       # 底槽在浅色下是浅灰白


class CardAndDemoTests(unittest.TestCase):
    def setUp(self):
        self.card = ss.StarSliderCard("Ultra", "6 Astra ›", dark=True, value=1.0)
        self.card.resize(320, 120)
        self.card.show()
        pump(40)

    def tearDown(self):
        self.card.close()
        self.card.deleteLater()

    def test_title_is_gradient_text(self):
        img = grab(self.card)
        self.card.set_title("Ultra")
        pump(20)
        img = grab(self.card)
        # 标题行找「紫」像素：红蓝明显高于绿
        found = 0
        for y in range(4, 34):
            for x in range(4, self.card.width() - 4):
                r, g, b = rgb(img, x, y)
                if r > 110 and b > 150 and g < 120 and b > g + 60:
                    found += 1
        self.assertGreater(found, 30, "标题没有渐变字（可能是豆腐块）")

    def test_card_forwards_value_and_keeps_slider_inside(self):
        seen = []
        self.card.valueChanged.connect(seen.append)
        self.card.setValue(0.4)                 # 卡片默认四档 → 吸到 1/3
        self.assertAlmostEqual(seen[-1], 1 / 3.0, places=3)
        s = self.card.slider()
        self.assertGreaterEqual(s.geometry().top(), 0)
        self.assertLessEqual(s.geometry().bottom() + 1, self.card.height())

    def test_demo_maps_value_to_preset_names(self):
        demo = ss.StarSliderDemo()
        demo.resize(360, 232)
        demo.show()
        pump(40)
        try:
            demo.card.setValue(0.9)            # 吸到 1.0 → Ultra
            self.assertAlmostEqual(demo.card.value(), 1.0, places=3)
            self.assertEqual(demo.card.title(), "Ultra")
            demo.card.setValue(0.6)            # 吸到 2/3 → High
            self.assertAlmostEqual(demo.card.value(), 2 / 3.0, places=3)
            self.assertEqual(demo.card.title(), "High")
            demo.card.setValue(0.1)            # 吸到 0.0 → Low
            self.assertAlmostEqual(demo.card.value(), 0.0, places=3)
            self.assertEqual(demo.card.title(), "Low")
            demo._cycle()                      # 已是最低档 → 回到最高档
            pump(300)
            self.assertAlmostEqual(demo.card.value(), 1.0, places=3)
            self.assertEqual(demo.card.title(), "Ultra")
            demo._cycle()                      # 降一档
            pump(300)
            self.assertAlmostEqual(demo.card.value(), 2 / 3.0, places=3)
            self.assertEqual(demo.card.title(), "High")
        finally:
            demo.close()
            demo.deleteLater()


if __name__ == "__main__":
    unittest.main()
