# -*- coding: utf-8 -*-
"""整合包更新提醒（红色警示条）真程序探针：两形态 / 标签行药丸 / 弹窗 / 主题 / 落盘。"""
import json, os, sys, tempfile, time, traceback

PROJ = r"C:\Users\chenping\PycharmProjects\Minecraft-Migration-Tool\Minecraft迁移工具"
出图 = os.path.join(os.path.dirname(PROJ), "_probe_shots")
os.makedirs(出图, exist_ok=True)
cfg = os.path.join(tempfile.gettempdir(), "mctool_pw_probe.json")
json.dump({"language": "zh"}, open(cfg, "w", encoding="utf-8"))
os.environ["MCTOOL_CONFIG"] = cfg
sys.path.insert(0, PROJ)
os.chdir(PROJ)

import tkinter as tk
import ui.main_window as MW

结果 = []
def 记(名, 好, 详情=""):
    结果.append((名, bool(好), 详情))
    print(("PASS " if 好 else "FAIL ") + 名 + (" | " + str(详情) if 详情 else ""))

实例 = {}
原init = MW.MigrationGUI.__init__
def 包装init(self, *a, **k):
    原init(self, *a, **k)
    实例["gui"] = self
MW.MigrationGUI.__init__ = 包装init

def 文本们(w, 出=None):
    出 = [] if 出 is None else 出
    try:
        文 = w.cget("text")
        if 文:
            出.append(str(文))
    except Exception:
        pass
    for 子 in w.winfo_children():
        文本们(子, 出)
    return 出

def 读配置():
    try:
        return json.load(open(cfg, encoding="utf-8"))
    except Exception:
        return {}

def 截图(w, 名):
    try:
        from PIL import ImageGrab
        w.update()
        time.sleep(0.35)
        w.update()
        x, y = w.winfo_rootx(), w.winfo_rooty()
        ww, hh = max(1, w.winfo_width()), max(1, w.winfo_height())
        p = os.path.join(出图, 名 + ".png")
        ImageGrab.grab(bbox=(x - 3, y - 3, x + ww + 3, y + hh + 3)).save(p)
        print("图:", p, "%dx%d" % (ww, hh))
    except Exception as e:
        print("截图失败", e)

def 检查(root):
    try:
        gui = 实例["gui"]
        gui.root.update_idletasks()
        bar = getattr(gui, "pack_warning_bar", None)
        tabs = getattr(gui, "list_tabs", None)
        记("警示条与标签行都在", bar is not None and tabs is not None)
        if bar is None or tabs is None:
            return
        记("挂在页外共用区（三页都可见）", bar.master is getattr(gui, "list_area", None))
        记("避开通用主题迁移", getattr(bar, "_skip_theme_walk", False) is True)
        文 = 文本们(bar)
        记("横幅里有复制链接", any("复制 AI 提示词" in t for t in 文))
        记("横幅里有收起入口", any("收起" in t for t in 文))
        记("没有「不再提示」", not any("不再提示" in t for t in 文))
        记("横幅占满整条", bar.winfo_width() > 900, "宽=%d" % bar.winfo_width())
        记("展开时标签行没有尾按钮", tabs._tail is None)
        记("横幅在标签行上面", bar.winfo_rooty() < tabs.winfo_rooty())
        截图(bar, "pw_1_横幅")

        行高0 = tabs.bar.winfo_height()
        行宽0 = tabs.bar.winfo_width()
        bar._设收起(True)
        gui.root.update_idletasks()
        记("收起后横幅整条摘掉", bar.winfo_ismapped() == 0)
        记("收起后标签行右端出现药丸", tabs._tail is not None,
           (tabs._tail or {}).get("text"))
        记("药丸文字是 ⚠ 更新提醒", (tabs._tail or {}).get("text") == "⚠ 更新提醒")
        记("药丸和三个标签同一行", tabs._tail["x"] > tabs._tabs[-1]["x"],
           "尾 x=%d 末页 x=%d" % (tabs._tail["x"], tabs._tabs[-1]["x"]))
        记("药丸高度与标签一致", tabs._tail is not None and tabs._tail["w"] > 60,
           "宽=%d" % (tabs._tail or {}).get("w", 0))
        记("标签条高度没变", tabs.bar.winfo_height() == 行高0,
           "%s -> %s" % (行高0, tabs.bar.winfo_height()))
        记("药丸是警示红", tabs._tail_color("normal").lower() in ("#fde5e1", "#4d1517"),
           tabs._tail_color("normal"))
        记("收起状态落盘", 读配置().get("pack_warning_collapsed") is True)
        记("主窗口记了形态", getattr(gui, "pack_warning_collapsed", None) is True)
        截图(tabs.bar, "pw_2_标签行药丸")

        # 真点一下药丸中心（走 canvas 的点击命中）
        cx = tabs._tail["x"] + tabs._tail["w"] // 2
        tabs.bar.event_generate("<Button-1>", x=cx, y=tabs.__dict__.get("PILL_Y", 22))
        gui.root.update_idletasks()
        记("点药丸弹出完整说明", bar._弹窗 is not None)
        if bar._弹窗 is not None:
            窗文 = 文本们(bar._弹窗)
            记("弹窗标题", "整合包更新提醒" in 窗文)
            记("弹窗有完整说明", any("扫出来是「没有差异」" in t for t in 窗文))
            记("弹窗说不能关闭", any("不能关闭" in t for t in 窗文))
            截图(bar._弹窗, "pw_3_弹窗")

        bar._展开()
        gui.root.update_idletasks()
        记("展开后弹窗关闭", bar._弹窗 is None)
        记("展开后横幅回来", bar.winfo_ismapped() == 1 and bar.winfo_width() > 900)
        记("展开后药丸摘掉", tabs._tail is None)
        记("横幅回到标签行上面", bar.winfo_rooty() < tabs.winfo_rooty())
        记("展开状态落盘", 读配置().get("pack_warning_collapsed") is False)

        try:
            gui.apply_theme()
            gui.root.update_idletasks()
            边 = bar._配色()["边"]
            实 = ""
            for c in bar.winfo_children():
                try:
                    实 = c.cget("highlightbackground")
                except Exception:
                    pass
            记("切主题后横幅仍是红的", str(实).lower() == 边.lower(), "实=%s" % 实)
            bar._设收起(True)
            gui.root.update_idletasks()
            记("切主题后收起，药丸仍挂得上", tabs._tail is not None)
            截图(tabs.bar, "pw_4_切主题后药丸")
        except Exception as e:
            记("主题切换不炸", False, repr(e))
    except Exception:
        记("探针异常", False, traceback.format_exc().splitlines()[-1])
    finally:
        print("---- 汇总 ----")
        坏 = [r for r in 结果 if not r[1]]
        print("PASS %d / FAIL %d" % (len(结果) - len(坏), len(坏)))
        try:
            root.destroy()
        except Exception:
            pass

原loop = tk.Misc.mainloop
def 探针loop(self, n=0):
    self.after(400, lambda: 检查(self))
    self.after(30000, lambda: (print("TIMEOUT"), self.destroy()))
    return 原loop(self, n)
tk.Misc.mainloop = 探针loop

import app
try:
    app.main()
except SystemExit:
    pass
