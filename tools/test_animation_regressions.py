"""Animation lifecycle, frame-rate independence and real Tk scrolling regressions."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Minecraft迁移工具'))
_temp = tempfile.TemporaryDirectory(prefix='mctool-animation-tests-')
os.environ['MCTOOL_CONFIG'] = str(Path(_temp.name) / 'config.json')

from _testcfg import use_config as _use_config   # noqa: E402
_CFG_PATH = os.environ["MCTOOL_CONFIG"]


def setUpModule():                     # 全量跑时各模块各用各的临时配置
    _use_config(_CFG_PATH)
os.environ['MCTOOL_LANG'] = 'zh'
Path(os.environ['MCTOOL_CONFIG']).write_text('{"language":"zh"}', encoding='utf-8')
import tkinter as tk
from utils import anim
from utils.helpers import LiquidProgress, SegmentedControl, SmoothScroller, SwitchRow, _PxAcc
from utils.theme import LIGHT_THEME
from ui.mw_migration import _SmoothText
from ui.rounded_tabs import RoundedTabs


class AnimationTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry('600x320+100+100')
        self.errors = []
        self.root.report_callback_exception = lambda *e: self.errors.append(repr(e))
        self.initial_holds = anim._hr_holders
        self.root.update_idletasks()

    def tearDown(self):
        self.root.destroy()
        self.assertEqual(anim._hr_holders, self.initial_holds, 'timer precision leaked')
        self.assertFalse(self.errors, self.errors)

    def run_for(self, ms):
        self.root.after(ms, self.root.quit)
        self.root.mainloop()

    def test_loop_stop_inside_step_does_not_restart(self):
        events = []
        def step():
            events.append(loop.running())
            loop.stop()
            return True
        loop = anim.FrameLoop(self.root, step).start()
        self.run_for(50)
        self.assertEqual(events, [True])
        self.assertFalse(loop.running())

    def test_loop_start_inside_step_does_not_duplicate(self):
        events = []
        def step():
            events.append(1)
            loop.start()
            return len(events) < 3
        loop = anim.FrameLoop(self.root, step).start()
        self.run_for(160)
        self.assertEqual(len(events), 3)
        self.assertFalse(loop.running())

    def test_loop_restart_inside_step_keeps_only_new_chain(self):
        events = []
        def step():
            events.append(1)
            if len(events) == 1:
                loop.stop()
                loop.start()
                return False
            return False
        loop = anim.FrameLoop(self.root, step).start()
        self.run_for(90)
        self.assertEqual(len(events), 2)
        self.assertFalse(loop.running())

    def test_loop_exception_and_destroy_release_timer(self):
        child = tk.Frame(self.root)
        loop = anim.FrameLoop(child, lambda: True).start()
        child.destroy()
        self.assertFalse(loop.running())
        def broken():
            raise RuntimeError('expected test failure')
        loop = anim.FrameLoop(self.root, broken).start()
        self.run_for(45)
        self.assertFalse(loop.running())
        self.assertEqual(anim._hr_holders, self.initial_holds)

    def test_frame_budget_deducts_render_time(self):
        widget = mock.Mock()
        now = [10.0]
        def step():
            now[0] += 0.003
            return True
        with mock.patch.object(anim.time, 'perf_counter', side_effect=lambda: now[0]), \
             mock.patch.object(anim, 'delay', return_value=8):
            loop = anim.FrameLoop(widget, step).start()
            loop._走()
            self.assertEqual(widget.after.call_args.args[0], 5)
            loop.stop()

    def test_repeated_loops_do_not_accumulate_destroy_bindings(self):
        before = self.root.bind('<Destroy>')
        for _ in range(30):
            loop = anim.FrameLoop(self.root, lambda: False).start()
            loop.stop()
        self.assertEqual(self.root.bind('<Destroy>'), before)

    def test_progress_motion_matches_across_refresh_rates(self):
        results = []
        bar = LiquidProgress(self.root, LIGHT_THEME)
        for dt in (0.004, 0.008, 0.016):
            bar._fraction, bar._shown, bar._flow = 0.8, 0.0, 0.0
            for _ in range(round(.096 / dt)):
                bar._advance(dt)
            results.append((bar._shown, bar._flow))
        for shown, phase in results[1:]:
            self.assertAlmostEqual(shown, results[0][0], places=9)
            self.assertAlmostEqual(phase, results[0][1], places=9)

    def test_progress_zero_stops_and_map_resumes_partial(self):
        bar = LiquidProgress(self.root, LIGHT_THEME)
        bar.pack(fill='x')
        self.run_for(40)
        bar.set_fraction(.5)
        self.run_for(160)
        self.assertTrue(bar._loop.running())
        bar.pack_forget()
        self.run_for(40)
        self.assertFalse(bar._loop.running())
        bar.pack(fill='x')
        self.run_for(40)
        self.assertTrue(bar._loop.running())
        bar.set_fraction(0)
        self.run_for(60)
        self.assertFalse(bar._loop.running())
        self.assertEqual(bar._shown, 0)

    def test_progress_completion_stops(self):
        bar = LiquidProgress(self.root, LIGHT_THEME)
        bar.pack(fill='x')
        bar.set_fraction(1)
        self.run_for(600)
        self.assertFalse(bar._loop.running())
        self.assertEqual(bar._shown, 1)

    def test_switch_immediate_set_cancels_previous_animation(self):
        widget = SwitchRow(self.root, LIGHT_THEME, 'test')
        widget.pack(fill='x')
        self.run_for(20)
        widget.set(True, animate=True)
        self.run_for(40)
        widget.set(False, animate=False)
        self.run_for(190)
        self.assertEqual(widget._t, 0)
        self.assertFalse(widget._loop.running())

    def test_segment_retarget_stops_at_latest_value(self):
        widget = SegmentedControl(self.root, LIGHT_THEME, [('a','a'),('b','b'),('c','c')])
        widget.pack()
        self.run_for(20)
        widget.set('c')
        self.run_for(35)
        widget.set('b')
        self.run_for(210)
        self.assertEqual(widget._t, 1)
        self.assertFalse(widget._loop.running())

    def test_tabs_notify_once_and_latest_page_stays_selected(self):
        widget = RoundedTabs(self.root, LIGHT_THEME)
        widget.pack(fill='both', expand=True)
        pages = [widget.page(str(i)) for i in range(3)]
        events=[]
        widget.on_change=events.append
        self.run_for(20)
        widget.select(1)
        self.run_for(30)
        widget.select(2, animate=False)
        self.run_for(200)
        self.assertEqual(events, [1,2])
        self.assertFalse(pages[1].winfo_ismapped())
        self.assertTrue(pages[2].winfo_ismapped())
        self.assertEqual(widget.bar.coords(widget._sel_item)[0], widget._tabs[2]['x'])

    def test_tabs_destroy_does_not_release_other_loops(self):
        other = anim.FrameLoop(self.root, lambda: True).start()
        widget = RoundedTabs(self.root, LIGHT_THEME)
        widget.page('a')
        widget.page('b')
        widget.select(1)
        widget.destroy()
        self.assertEqual(anim._hr_holders, self.initial_holds + 1)
        other.stop()

    def make_log(self):
        log=_SmoothText(self.root, width=30, height=8, wrap='word')
        log.pack(fill='both', expand=True)
        log.insert('end','\n'.join(('very long wrapped line ' * 20) for _ in range(100)))
        self.run_for(50)
        return log

    def test_history_wrapped_lines_scroll_pixels_and_settle(self):
        log = self.make_log()
        log.event_generate('<MouseWheel>',delta=-120)
        self.run_for(500)
        self.assertGreater(log.yview()[0], 0)
        self.assertFalse(log._scroller.scrolling())
        # A notch is three display lines, not three long logical paragraphs.
        self.assertEqual(log.index('@0,0').split('.')[0], '1')

    def test_history_bottom_and_zero_delta_do_not_loop(self):
        log = self.make_log()
        log.yview_moveto(1)
        log.event_generate('<MouseWheel>',delta=-120)
        self.run_for(120)
        self.assertFalse(log._scroller.scrolling())
        with mock.patch.object(log._scroller, '_mover') as mover:
            log._scroller.wheel(SimpleNamespace(delta=0))
            self.run_for(50)
            mover.assert_not_called()
        self.assertFalse(log._scroller.scrolling())

    def test_history_small_delta_and_manual_scrollbar_priority(self):
        log = self.make_log()
        log._scroller.wheel(SimpleNamespace(delta=-15))
        self.assertAlmostEqual(log._scroller._left, log._scroller.px_per_notch/8)
        log.yview('moveto', .5)
        self.assertFalse(log._scroller.scrolling())

    def test_reverse_scroll_does_not_drop_gesture(self):
        positions=[]
        sc=SmoothScroller(self.root, lambda px: (positions.append(px) or px,False))
        sc.scroll_px(100)
        self.run_for(30)
        sc.scroll_px(-300)
        before=len(positions)
        self.run_for(500)
        self.assertTrue(positions[before:])
        self.assertTrue(all(p < 0 for p in positions[before:]))
        self.assertFalse(sc.scrolling())
        self.assertAlmostEqual(sum(positions), -200, delta=1.5)


class ScrollTailTests(unittest.TestCase):
    """Use scheduled frames and integer pixel consumption, as the real widgets do."""

    def simulate(self, events, frame_ms, level=4, notch_px=48):
        clock = [100.0]
        widget = mock.Mock()
        widget.winfo_exists.return_value = True
        scheduled = []
        moves = []
        acc = _PxAcc()
        holds = anim._hr_holders

        def after(ms, callback):
            scheduled[:] = [(clock[0] + ms / 1000.0, callback)]
            return "scroll-frame"

        def move(px):
            consumed = acc.take(px)
            moves.append((clock[0] - 100.0, consumed))
            return consumed, False

        widget.after.side_effect = after
        widget.after_cancel.side_effect = lambda *_: scheduled.clear()
        inputs = [(100.0 + ms / 1000.0, px) for ms, px in events]
        with mock.patch('utils.helpers.time.perf_counter', side_effect=lambda: clock[0]), \
             mock.patch.object(anim, 'delay', return_value=frame_ms), \
             mock.patch.object(SmoothScroller, 'GLIDE_SCALE', SmoothScroller._SPEED_SCALES[level - 1]):
            sc = SmoothScroller(widget, move, px_per_notch=notch_px)
            try:
                while inputs or scheduled:
                    input_at = inputs[0][0] if inputs else float("inf")
                    frame_at = scheduled[0][0] if scheduled else float("inf")
                    clock[0] = min(input_at, frame_at)
                    self.assertLess(clock[0], 102.0, "scroll never settled")
                    if input_at <= frame_at:
                        _, px = inputs.pop(0)
                        sc.scroll_px(px)
                    else:
                        _, callback = scheduled.pop(0)
                        callback()
                self.assertFalse(sc.scrolling())
                self.assertEqual(anim._hr_holders, holds)
                return (clock[0] - 100.0) * 1000.0 - events[-1][0], moves
            finally:
                sc.stop()

    def test_large_burst_settles_promptly_without_losing_distance(self):
        for level in range(1, 6):
            for frame in (4, 8, 16, 33):
                for direction in (-1, 1):
                    with self.subTest(level=level, frame=frame, direction=direction):
                        tail, moves = self.simulate([(0, 480 * direction)], frame, level)
                        budget = 160 * SmoothScroller._SPEED_SCALES[level - 1] + 2 * frame
                        self.assertLessEqual(tail, budget)
                        self.assertAlmostEqual(sum(px for _, px in moves), 480 * direction, delta=1)
                        self.assertTrue(all(px * direction >= 0 for _, px in moves))

    def test_small_and_continuous_inputs_keep_their_distance(self):
        for events in ([(0, 48)], [(n * 8, 6) for n in range(40)],
                       [(n * 8, 48) for n in range(10)]):
            for frame in (4, 8, 16):
                with self.subTest(events=events, frame=frame):
                    tail, moves = self.simulate(events, frame)
                    self.assertLessEqual(tail, 140 + frame)
                    self.assertAlmostEqual(sum(px for _, px in moves),
                                           sum(px for _, px in events), delta=1)

    def test_reverse_burst_has_no_forward_frames_after_reversal(self):
        for frame in (4, 8, 16):
            tail, moves = self.simulate([(0, 480), (32, -720)], frame)
            self.assertLessEqual(tail, 180)
            self.assertAlmostEqual(sum(px for _, px in moves), -240, delta=1)
            reverse_moves = [px for at, px in moves if at >= .032]
            self.assertTrue(reverse_moves)
            self.assertTrue(all(px <= 0 for px in reverse_moves))


if __name__ == '__main__':
    try:
        unittest.main(verbosity=2)
    finally:
        _temp.cleanup()
