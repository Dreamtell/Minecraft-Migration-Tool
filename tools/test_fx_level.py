# tools/test_fx_level.py
"""「特效强度」4 档（utils/fx.py）的测试。

覆盖：档位归一化、预设表、派生键、配置读写与老配置兼容、
毫秒缩放、Qt 设置窗的滑块↔档位映射、Tk 设置窗的落地逻辑、
吐司/浮标在「关闭」档的行为、闪屏在「关闭」档的 0 粒子。
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "Minecraft迁移工具"
sys.path.insert(0, str(PRODUCT))

from PySide6 import QtCore, QtWidgets  # noqa: E402

from utils import fx  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class FxPresetTests(unittest.TestCase):
    def tearDown(self):
        fx.set_level(fx.DEFAULT)

    def test_normalize_accepts_levels_numbers_and_garbage(self):
        self.assertEqual(fx.normalize("off"), "off")
        self.assertEqual(fx.normalize("LIGHT"), "light")
        self.assertEqual(fx.normalize(" full "), "full")
        for i, lv in enumerate(fx.LEVELS):
            self.assertEqual(fx.normalize(i), lv)
            self.assertEqual(fx.normalize(str(i)), lv)
        for bad in (None, "", "turbo", 99, object()):
            self.assertEqual(fx.normalize(bad), fx.DEFAULT)

    def test_presets_get_monotonically_stronger(self):
        anims = [fx.PRESETS[lv]["anim"] for lv in fx.LEVELS]
        parts = [fx.PRESETS[lv]["particle"] for lv in fx.LEVELS]
        self.assertEqual(anims, sorted(anims))
        self.assertEqual(parts, sorted(parts))
        self.assertEqual(anims[0], 0.0)
        self.assertEqual(parts[0], 0.0)
        self.assertLess(anims[0], anims[-1])
        self.assertLess(parts[0], parts[-1])

    def test_off_disables_every_effect(self):
        p = fx.preset("off")
        self.assertFalse(p["splash"])
        self.assertFalse(p["edge_fx"])
        self.assertEqual(p["feel"], "crisp")
        self.assertEqual(p["toast"], 0)
        self.assertEqual(fx.PRESETS["off"]["particle"], 0.0)

    def test_derived_keys_match_preset(self):
        for lv in fx.LEVELS:
            d = fx.derived(lv)
            p = fx.preset(lv)
            self.assertEqual(d["fx_level"], lv)
            self.assertEqual(d["splash"], p["splash"])
            self.assertEqual(d["float_edge_fx"], p["edge_fx"])
            self.assertEqual(d["start_btn_feel"], p["feel"])

    def test_apply_to_writes_into_config(self):
        cfg = {"theme": "dark"}
        fx.apply_to(cfg, "full")
        self.assertEqual(cfg["theme"], "dark")
        self.assertEqual(cfg["fx_level"], "full")
        self.assertTrue(cfg["splash"])
        self.assertEqual(cfg["start_btn_feel"], "heavy")

    def test_legacy_config_without_level_is_untouched(self):
        cfg = {"splash": False, "start_btn_feel": "crisp", "float_edge_fx": False}
        out = fx.from_config(cfg)
        self.assertNotIn("fx_level", out)
        self.assertFalse(out["splash"])
        self.assertEqual(out["start_btn_feel"], "crisp")

    def test_from_config_rederives_when_level_present(self):
        out = fx.from_config({"fx_level": "off", "splash": True})
        self.assertFalse(out["splash"])
        self.assertFalse(out["float_edge_fx"])

    def test_ms_scaling(self):
        fx.set_level("off")
        self.assertEqual(fx.ms(160), 1)          # 关闭档 = 立刻
        fx.set_level("standard")
        self.assertEqual(fx.ms(160), 160)
        fx.set_level("full")
        self.assertGreater(fx.ms(160), 160)
        fx.set_level("light")
        self.assertLess(fx.ms(160), 160)

    def test_bootstrap_reads_config_and_defaults(self):
        self.assertEqual(fx.bootstrap({"fx_level": "light"}), "light")
        self.assertEqual(fx.current(), "light")
        self.assertEqual(fx.bootstrap({}), fx.DEFAULT)
        self.assertEqual(fx.bootstrap(None), fx.DEFAULT)


class QtSettingsFxTests(unittest.TestCase):
    """设置窗：星空滑块 4 档 ↔ 派生键。"""

    def setUp(self):
        self._old = fx.current()
        from ui.qt_main.settings import SettingsDialog
        self.Dialog = SettingsDialog

    def tearDown(self):
        fx.set_level(self._old)

    def _dialog(self, **cfg):
        dlg = self.Dialog(None, dict({"theme": "dark"}, **cfg))
        dlg.show()
        APP.processEvents()
        return dlg

    def test_dialog_has_four_stop_slider_starting_at_level(self):
        dlg = self._dialog(fx_level="light")
        stops = dlg.fx_card.stops()
        self.assertEqual(len(stops), len(fx.LEVELS))
        self.assertAlmostEqual(dlg.fx_card.value(), 1.0 / 3.0, places=3)
        dlg.close()

    def test_values_map_each_level_to_derived_keys(self):
        dlg = self._dialog(fx_level="standard")
        for idx, lv in enumerate(fx.LEVELS):
            dlg.fx_card.setValue(idx / 3.0)
            APP.processEvents()
            vals = dlg.values()
            p = fx.preset(lv)
            self.assertEqual(vals["fx_level"], lv)
            self.assertEqual(vals["splash"], p["splash"])
            self.assertEqual(vals["float_edge_fx"], p["edge_fx"])
            self.assertEqual(vals["start_btn_feel"], p["feel"])
        dlg.close()

    def test_slider_snaps_between_levels(self):
        dlg = self._dialog(fx_level="full")
        dlg.fx_card.setValue(0.55)
        APP.processEvents()
        self.assertEqual(dlg.values()["fx_level"], "standard")
        dlg.fx_card.setValue(0.05)
        APP.processEvents()
        self.assertEqual(dlg.values()["fx_level"], "off")
        dlg.close()

    def test_dialog_does_not_touch_cache_until_applied(self):
        fx.set_level("standard")
        dlg = self._dialog(fx_level="off")
        self.assertEqual(fx.current(), "standard")
        dlg.close()


class TkSettingsFxTests(unittest.TestCase):
    """Tk 设置窗：选档位后派生值落到实例上并立即存盘。"""

    def setUp(self):
        self._old = fx.current()
        from ui.mw_settings import SettingsMixin

        class _Var:
            def __init__(self, v=""):
                self.v = v

            def set(self, v):
                self.v = v

            def get(self):
                return self.v

        class _Stub(SettingsMixin):
            def __init__(self):
                self.settings_fx_var = _Var()
                self.start_btn_feel = _Var("standard")
                self.splash_enabled = True
                self.float_edge_fx = True
                self.fx_level = fx.DEFAULT
                self.saved = 0
                self.toasts = []

            def save_config(self):
                self.saved += 1

            def toast(self, text, level="info", **kw):
                self.toasts.append(text)

            def set_float_edge_fx(self, on):
                self.float_edge_fx = bool(on)

        self._Stub = _Stub

    def tearDown(self):
        fx.set_level(self._old)

    def test_each_level_updates_instance_and_saves(self):
        for lv in fx.LEVELS:
            stub = self._Stub()
            stub.start_btn_feel.set("standard")
            stub._set_fx_level(lv)
            p = fx.preset(lv)
            self.assertEqual(stub.fx_level, lv)
            self.assertEqual(stub.splash_enabled, p["splash"])
            self.assertEqual(stub.float_edge_fx, p["edge_fx"])
            self.assertEqual(stub.start_btn_feel.get(), p["feel"])
            self.assertEqual(stub.settings_fx_var.get(), lv)
            self.assertEqual(stub.saved, 1)
            self.assertEqual(fx.current(), lv)

    def test_bad_level_falls_back_to_default(self):
        stub = self._Stub()
        stub._set_fx_level("nonsense")
        self.assertEqual(stub.fx_level, fx.DEFAULT)


class EffectBehaviourTests(unittest.TestCase):
    """关闭档：吐司直接出现、浮标光带不画、闪屏 0 粒子。"""

    def setUp(self):
        self._old = fx.current()

    def tearDown(self):
        fx.set_level(self._old)

    def test_toast_appears_without_animation_when_off(self):
        from ui.qt_main.toast import ToastHost

        host_parent = QtWidgets.QWidget()
        host_parent.resize(500, 400)
        host_parent.show()
        host = ToastHost(host_parent)
        fx.set_level("off")
        card = host.show("直接出现", level="ok")
        APP.processEvents()
        self.assertIsNotNone(card)
        self.assertEqual(card.opacity, 1.0)
        self.assertFalse(host._timer.isActive())
        host.clear()
        host_parent.close()

    def test_toast_animates_when_standard(self):
        from ui.qt_main.toast import ToastHost

        host_parent = QtWidgets.QWidget()
        host_parent.resize(500, 400)
        host_parent.show()
        host = ToastHost(host_parent)
        fx.set_level("standard")
        card = host.show("淡入", level="ok")
        APP.processEvents()
        self.assertLess(card.opacity, 1.0)
        host.clear()
        host_parent.close()

    def test_float_glow_skipped_when_off(self):
        from ui.qt_main.net_float import _EdgeGlow

        host = QtWidgets.QWidget()
        host.resize(400, 300)
        host.show()
        glow = _EdgeGlow(host)
        fx.set_level("off")
        glow.show_on("right", QtCore.QPoint(200, 150))
        self.assertFalse(glow.isVisible())
        fx.set_level("standard")
        glow.show_on("right", QtCore.QPoint(200, 150))
        self.assertTrue(glow.isVisible())
        self.assertGreater(len(glow._parts), 0)
        glow.hide()
        host.close()

    def test_splash_has_no_particles_and_short_pop_when_off(self):
        cfg = Path(__file__).resolve().parents[1] / "_dctest" / "fx_off_config.json"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text('{"fx_level": "off"}', encoding="utf-8")
        code = (
            "import sys; sys.path.insert(0, r'%s');"
            "import ui.splash as s;"
            "print(s._PARTICLES, round(s._POP_SEC, 3), round(s._OUT_SEC, 3))"
            % str(PRODUCT)
        )
        env = dict(os.environ, MCTOOL_CONFIG=str(cfg), PYTHONIOENCODING="utf-8")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, env=env, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        parts, pop, out_sec = out.stdout.split()
        self.assertEqual(int(parts), 0)
        self.assertLessEqual(float(pop), 0.1)
        self.assertLessEqual(float(out_sec), 0.1)

    def test_splash_full_is_slower_than_standard(self):
        cfg = Path(__file__).resolve().parents[1] / "_dctest" / "fx_full_config.json"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text('{"fx_level": "full"}', encoding="utf-8")
        code = (
            "import sys; sys.path.insert(0, r'%s');"
            "import ui.splash as s;"
            "print(s._PARTICLES, round(s._POP_SEC, 3))"
            % str(PRODUCT)
        )
        env = dict(os.environ, MCTOOL_CONFIG=str(cfg), PYTHONIOENCODING="utf-8")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, env=env, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        parts, pop = out.stdout.split()
        self.assertGreater(int(parts), 22)      # 全开档粒子更多
        self.assertGreater(float(pop), 0.30)    # 弹出更慢


if __name__ == "__main__":
    unittest.main(verbosity=2)
