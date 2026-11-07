"""Run separately: Qt must never be imported into the Tk application process."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Minecraft迁移工具'))
_temp=tempfile.TemporaryDirectory(prefix='mctool-qt-scroll-')
os.environ['MCTOOL_CONFIG']=str(Path(_temp.name)/'config.json')
os.environ['MCTOOL_LANG']='zh'
os.environ['QT_QPA_PLATFORM']='offscreen'
Path(os.environ['MCTOOL_CONFIG']).write_text('{"language":"zh"}',encoding='utf-8')
from ui.qt_big_view import SmoothTextEdit, QtCore, QtGui, QtWidgets
from utils import anim


class QtScrollTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.holds=anim._hr_holders
        self.view=SmoothTextEdit()
        self.view.resize(500,300)
        self.view.setPlainText('\n'.join(str(n) for n in range(1000)))
        self.view.show()
        self.app.processEvents()
        self.view._sw_setup()
        self.view.verticalScrollBar().setValue(500)

    def tearDown(self):
        self.view.close()
        self.view.deleteLater()
        self.app.processEvents()
        self.assertEqual(anim._hr_holders,self.holds)

    def event(self,pixels=0,angle=0):
        return SimpleNamespace(pixelDelta=lambda:QtCore.QPoint(0,pixels),
                               angleDelta=lambda:QtCore.QPoint(0,angle),
                               accept=lambda:None,ignore=lambda:None)

    def test_touchpad_direction_matches_wheel(self):
        self.view.wheelEvent(self.event(pixels=20))
        self.assertEqual(self.view.verticalScrollBar().value(),480)
        self.view.wheelEvent(self.event(angle=120))
        self.assertLess(self.view._sw_target,480)

    def test_frequent_wheel_events_do_not_restart_clock_or_timer(self):
        self.view.wheelEvent(self.event(angle=-120))
        old=self.view._sw_last
        timer_id=self.view._sw_timer.timerId()
        self.view.wheelEvent(self.event(angle=-15))
        self.assertEqual(self.view._sw_last,old)
        self.assertEqual(self.view._sw_timer.timerId(),timer_id)

    def test_same_timestamp_does_not_snap_to_target(self):
        self.view.wheelEvent(self.event(angle=-120))
        cur=self.view._sw_cur
        self.assertTrue(self.view.tick_scroll(self.view._sw_last))
        self.assertEqual(self.view._sw_cur,cur)
        self.assertTrue(self.view._sw_active)

    def test_dense_events_advance_in_real_event_loop(self):
        events=[]
        self.view.verticalScrollBar().valueChanged.connect(events.append)
        feed=QtCore.QTimer()
        feed.setTimerType(QtCore.Qt.PreciseTimer)
        feed.setInterval(1)
        feed.timeout.connect(lambda:self.view.wheelEvent(self.event(angle=-5)))
        loop=QtCore.QEventLoop()
        feed.start()
        QtCore.QTimer.singleShot(120,loop.quit)
        loop.exec()
        feed.stop()
        self.assertGreater(len(events),3)
        self.assertGreater(self.view.verticalScrollBar().value(),500)
        self.view.hide()
        self.assertFalse(self.view._sw_timer.isActive())
        self.assertFalse(self.view._sw_busy())

    def test_scroll_trajectory_matches_across_frame_intervals(self):
        positions=[]
        for dt in (.004,.008,.016):
            self.view._sw_cancel()
            self.view._sw_active=True
            self.view._sw_cur=0.0
            self.view._sw_target=500.0
            self.view._sw_last=0.0
            for n in range(1,round(.096/dt)+1):
                self.view.tick_scroll(n*dt)
            positions.append(self.view._sw_cur)
        for position in positions[1:]:
            self.assertAlmostEqual(position,positions[0],places=8)

    def test_wheel_burst_settles_quickly_at_all_frame_intervals(self):
        for frame in (4, 8, 16):
            for notches in (-10, -1, 1, 10):
                with self.subTest(frame=frame, notches=notches):
                    self.view._sw_cancel()
                    self.view.verticalScrollBar().setValue(2000)
                    with mock.patch('ui.qt_big_view.time.perf_counter', return_value=100.0):
                        self.view.wheelEvent(self.event(angle=-120 * notches))
                    target = 2000 + notches * self.view.wheel_px()
                    positions = [2000]
                    for elapsed in range(frame, 401, frame):
                        active = self.view.tick_scroll(100.0 + elapsed / 1000.0)
                        positions.append(self.view.verticalScrollBar().value())
                        if not active:
                            break
                    self.assertFalse(active)
                    self.assertLessEqual(elapsed, 150 + frame)
                    self.assertEqual(positions[-1], round(target))
                    self.assertTrue(all((b-a) * notches >= 0
                                        for a, b in zip(positions, positions[1:])))
                    self.view._sw_step()
                    self.assertFalse(self.view._sw_timer.isActive())
                    self.assertEqual(anim._hr_holders, self.holds)

    def test_reverse_wheel_reaches_signed_total(self):
        self.view.wheelEvent(self.event(angle=-1200))
        self.view.tick_scroll(self.view._sw_last + .032)
        before = self.view.verticalScrollBar().value()
        self.view.wheelEvent(self.event(angle=1440))
        now = self.view._sw_last
        positions = [before]
        for n in range(1, 51):
            active = self.view.tick_scroll(now + n * .004)
            positions.append(self.view.verticalScrollBar().value())
            if not active:
                break
        self.assertFalse(active)
        self.assertEqual(positions[-1], round(500 - 2 * self.view.wheel_px()))
        self.assertTrue(all(b <= a for a, b in zip(positions, positions[1:])))

    def test_continuous_wheel_stops_and_releases_timer_in_real_loop(self):
        loop = QtCore.QEventLoop()
        pending = list(range(10))
        last_input = [None]
        settled = []
        feed = QtCore.QTimer()
        feed.setTimerType(QtCore.Qt.PreciseTimer)
        feed.setInterval(8)

        def send():
            pending.pop()
            self.view.wheelEvent(self.event(angle=-120))
            last_input[0] = time.perf_counter()
            if not pending:
                feed.stop()

        def check():
            if not pending and not self.view._sw_busy():
                settled.append(time.perf_counter() - last_input[0])
                loop.quit()

        poll = QtCore.QTimer()
        poll.setInterval(2)
        poll.timeout.connect(check)
        watchdog = QtCore.QTimer()
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(loop.quit)
        feed.timeout.connect(send)
        feed.start()
        poll.start()
        watchdog.start(1000)
        try:
            loop.exec()
        finally:
            feed.stop()
            poll.stop()
            watchdog.stop()
        self.assertTrue(settled, "scroll did not settle in the real event loop")
        self.assertLess(settled[0], .30)
        self.assertEqual(self.view.verticalScrollBar().value(),
                         round(500 + 10 * self.view.wheel_px()))
        self.assertFalse(self.view._sw_timer.isActive())
        self.assertEqual(anim._hr_holders, self.holds)


if __name__=='__main__':
    try:
        unittest.main(verbosity=2)
    finally:
        _temp.cleanup()
