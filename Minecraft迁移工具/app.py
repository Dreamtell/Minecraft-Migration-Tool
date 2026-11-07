# app.py
import os
import queue
import sys
import ctypes
import tempfile
from pathlib import Path

# ⚠ 必须**在导入其它项目模块之前**就把临时目录定好：只要有任何一个模块在 import 期
#   调用了 tempfile.mkdtemp()，Python 就会把"解析出来的临时目录"缓存下来，之后再改
#   tempfile.tempdir 也救不回来（用户 m15432 的 tmpXXXX 目录就是这么掉进程序目录的）。
_启动临时目录 = None  # 由 _准备干净临时目录() 填充，见下
import time
import tkinter as tk
from tkinter import messagebox


# ---- 高 DPI ----
# 必须赶在建任何窗口之前（--splash-child / --qt-host 子进程也算）：
# 导入 utils.dpi 时就会声明 DPI 感知并算出 UI_SCALE，详见该模块顶部说明。
from utils.dpi import UI_SCALE, window_geometry  # noqa: E402


# ---- 独立进程闪屏的子进程入口 ----
# 必须放在所有重活之前：子进程只负责画闪屏，绝不碰 Tk/单实例锁/托盘。
if "--splash-child" in sys.argv:
    try:
        from ui.splash_qt import run_child
    except Exception:
        sys.exit(2)
    sys.exit(run_child())

# ---- Qt 窗口的子进程入口 ----
# 关键：这条分支要**在 import tkinter 之外**先跑掉，子进程里只有 Qt、没有 Tk
# （见 ui/qt_host.py 顶部：两个 GUI 库共享主线程会出致命的 GIL 错误）。
if "--qt-host" in sys.argv:
    try:
        from ui.qt_host import run_host
    except Exception:
        sys.exit(2)
    sys.exit(run_host(sys.argv))

from tendo import singleton
from utils.helpers import (get_icon_path, warm_up_emoji_font, clear_layered_style,
                           focus_window, force_foreground, trace_exc)
from utils.config import load_raw_config, save_raw_config
from winotify import Notification, audio

# 界面语言：**必须在任何窗口建起来之前**装上（见 utils/i18n.py 顶部说明）。
# 中文（默认）时 install() 直接返回、什么都不做，行为与以前逐字节一致。
from utils import i18n
# 文案模板：trp 按位置填值（中文模式下与原拼接结果逐字一致）
from utils.i18n import trp
# ⚠ `i18n.install()` 与界面模块的 import 都在 main() 里、按"先装语言再导入界面"的顺序做：
#   界面模块的模块级文案（表头、标签）是在 **import 那一刻**求值的，早一步导入就永远
#   翻不到。以前 install() 写在 import 之后，真实的启动顺序下那些文案其实都没翻。
#   另外首次启动要先问一次语言，那也必须发生在 install() 之前。

# 闪屏最短显示时长（秒）。主界面构建只要 ~0.7s，不兜底的话立方体刚起转就淡出了；
# 超过这个时间就立刻淡出，不会平白拖慢启动。
SPLASH_MIN_SEC = 1.5

# 是否用独立进程的 Qt 闪屏（PySide6 可用时默认开）。它不受主线程构建影响，
# 实测主线程连续忙 1.95s 期间仍是 59~62fps；进程内的 Tk 闪屏在那段时间只有
# ~25fps 且最大冻结 325ms（因为只有 on_stage 时才泵得到一帧）。
# 出问题时可以用环境变量 DSH_NO_QT_SPLASH=1 关掉，退回进程内闪屏。
SPLASH_QT = os.environ.get("DSH_NO_QT_SPLASH") != "1"

# 单实例锁的持有者：必须活到进程结束（被回收就释放锁，程序会变成可多开）
_LOCK_HOLDER = None


def _enable_fatal_log():
    """把致命错误（段错误 / GIL 致命错误 / 崩溃）的栈落一份到文件里。

    这些东西默认只写 stderr；GUI 启动方式下常常一闪而过、抓不到。写文件之后
    用户直接把文件发过来就能看到**所有线程**的栈和当时的调用链。
    """
    try:
        import faulthandler
        from pathlib import Path
        log = Path.home() / ".minecraft_migrate_fatal.log"
        fh = open(log, "a", encoding="utf-8", buffering=1)
        faulthandler.enable(fh, all_threads=True)
        try:
            fh.write(trp("\n===== 启动 {0} =====\n", time.strftime("%Y-%m-%d %H:%M:%S")))
        except Exception:
            pass
        return fh
    except Exception:
        return None


_FATAL_LOG = _enable_fatal_log()


def send_toast(title, msg):
    """弹一条 Windows 系统通知（winotify，兼容 Win10/11）。"""
    try:
        toast = Notification(app_id="迁移工坊", title=title, msg=msg,
                             duration="short")
        toast.set_audio(audio.Default, loop=False)
        toast.show()
    except Exception:
        pass


def _准备干净临时目录():
    """把临时目录钉死在一个**我们自己建、确定可写**的地方。

    默认走 `%TEMP%` 就够了；但如果 `%TEMP%` 不可写（公司电脑、被清理工具锁住、
    便携盘上跑），`tempfile` 会**悄悄退回当前目录** —— 于是单实例锁文件
    （`tendo` 用 `tempfile.gettempdir()` 定位）和 `tempfile.mkdtemp()` 的目录
    就撒在程序目录里，用户看到的就是"跑一次多出一堆奇怪文件"。

    这里先试 `%TEMP%`，写不进去就换到 `%LOCALAPPDATA%/Minecraft迁移工具/tmp`，
    并把 `TMP`/`TEMP`/`tempfile.tempdir` 一起改过去（子进程、matplotlib 之类也跟着走）。
    """
    备选 = []
    try:
        备选.append(Path(tempfile.gettempdir()))
    except Exception:
        pass
    try:
        根 = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        备选.append(Path(根) / "Minecraft迁移工具" / "tmp")
    except Exception:
        pass
    try:
        备选.append(Path.home() / "Minecraft迁移工具" / "tmp")
    except Exception:
        pass

    # ⚠ 关键：`tempfile.gettempdir()` 在 TMP/TEMP 没设、系统临时目录也不可写时，
    #   会**返回当前目录** —— 那正是"文件撒在程序目录里"的元凶。这种答案要直接否决，
    #   否则第一项永远"可写"，永远轮不到后面的兜底目录。
    禁区 = set()
    for 处 in (Path.cwd(), Path(sys.argv[0]).resolve().parent):
        try:
            禁区.add(处.resolve())
        except Exception:
            pass

    for 目录 in 备选:
        try:
            if 目录.resolve() in 禁区:
                continue
        except Exception:
            continue
        try:
            目录.mkdir(parents=True, exist_ok=True)
            探针 = 目录 / (".w%d" % os.getpid())
            探针.write_text("1", encoding="utf-8")
            探针.unlink()
        except Exception:
            continue
        try:
            tempfile.tempdir = str(目录)
            os.environ["TMP"] = str(目录)
            os.environ["TEMP"] = str(目录)
        except Exception:
            pass
        return 目录
    return None


# 导入期就执行（见上面的注释）：失败也不抛，留 None 走 Python 默认行为
try:
    _启动临时目录 = _准备干净临时目录()
except Exception:
    _启动临时目录 = None


def _清理程序目录里的空临时目录():
    """删掉 exe 旁边 / 当前工作目录里**空的** `tmp*` 目录。

    起因（用户 m15432）：`tempfile.mkdtemp()` 在 `tempfile.tempdir` 还没被我们
    钉死之前（PyInstaller 启动早期、或某个依赖在 import 期就建了一次）会落进程序
    目录，留下一个个空目录。这里做两件事的兜底清理：启动时扫一遍、退出时再扫一遍。

    只删「`tmp` 开头 + 完全不空不碰 + 是目录」的，用户自己的东西一律不动。
    """
    处们 = set()
    for 处 in (Path(sys.argv[0]).resolve().parent, Path(sys.executable).resolve().parent, Path.cwd()):
        try:
            处们.add(处.resolve())
        except Exception:
            pass
    删 = 0
    for 处 in 处们:
        try:
            if not 处.is_dir():
                continue
            项们 = list(处.iterdir())
        except Exception:
            continue
        for 项 in 项们:
            try:
                if not 项.is_dir() or not 项.name.startswith("tmp"):
                    continue
                if any(项.iterdir()):      # 非空 → 不是我们建的那种空壳，别碰
                    continue
                项.rmdir()
                删 += 1
            except Exception:
                continue
    return 删


try:
    _清理程序目录里的空临时目录()
    import atexit as _atexit

    _atexit.register(_清理程序目录里的空临时目录)
except Exception:
    pass


def _想用Qt主界面():
    """配置要求用 Qt 主界面，且没有被「纯 Tk 模式」总开关关掉。

    环境变量 MCTOOL_MAIN=tk / qt 可以临时覆盖配置（排查问题、验证脚本用）。
    """
    强制 = (os.environ.get("MCTOOL_MAIN") or "").strip().lower()
    if 强制 in ("tk", "qt"):
        return 强制 == "qt"
    try:
        cfg = load_raw_config()
    except Exception:
        return False
    if not bool(cfg.get("qt_enabled", True)):
        return False
    return str(cfg.get("main_window_backend", "tk") or "tk") == "qt"


def main():
    # 单实例检查
    global _LOCK_HOLDER
    # 先把临时目录钉在确定可写的地方，锁文件才不会掉到程序目录里（见函数注释）
    临时 = _启动临时目录 or _准备干净临时目录()
    # 再清一遍：有些依赖是在我们导入期之后才第一次建临时目录的
    try:
        _清理程序目录里的空临时目录()
    except Exception:
        pass
    try:
        # 必须留着这个对象：它被回收就会释放单实例锁，程序就变成"可多开"了。
        # 放在模块级变量里而不是局部变量，就是为了让它活到进程结束
        # （顺带也不会被 IDE 当成"赋值了没用"的死变量）。
        #
        # ⚠ 显式给 lockfile：tendo 默认把锁放在 `tempfile.gettempdir()` 里，
        #   虽然上面已经把它指好了，但显式传一次能从根上保证"锁永远不会落在 exe 旁边"。
        锁名 = os.path.splitext(os.path.abspath(sys.argv[0]))[0].replace(
            "/", "-").replace(":", "").replace("\\", "-") + "-.lock"
        _锁路径 = str((临时 or Path(tempfile.gettempdir())) / 锁名)
        _LOCK_HOLDER = singleton.SingleInstance(lockfile=_锁路径)
    except singleton.SingleInstanceException:
        # 已经在跑了：优先请那个实例把主界面叫出来（窗口可能正挂在托盘里，
        # 这种状态下靠标题 FindWindow + ShowWindow 是叫不动的）
        try:
            # 前台锁：别的进程替我们调 SetForegroundWindow 默认会被忽略，
            # 这里先"授权"给那个实例，它才有资格把窗口抢到前台
            ctypes.windll.user32.AllowSetForegroundWindow(-1)     # ASFW_ANY
        except Exception:
            pass
        # 已经在跑的可能是 Qt 主界面：先走命名管道叫它（失败不要紧，下面还有 Tk 那条路）
        if _想用Qt主界面():
            try:
                # tray.py 自己会把 _qt 加进 sys.path 再 import PySide6
                from ui.qt_main.tray import request_show_existing as _qt_show
                if _qt_show():
                    sys.exit(0)
            except SystemExit:
                raise
            except Exception:
                pass
        from ui import tray as _tray_mod
        if not _tray_mod.request_show_existing():
            try:
                hwnd = ctypes.windll.user32.FindWindowW(
                    None, "Minecraft 整合包迁移工具 - 增强版 v5")
                if hwnd:
                    force_foreground(hwnd)      # 提到最前（不只是 SetForegroundWindow）
            except Exception:
                pass
        sys.exit(0)

    # ---- Qt 主界面分支（配置 main_window_backend == "qt"）----
    # 必须在建 Tk() 之前分流：一旦建了 Tk 根窗口，这个进程就不能再跑 Qt 主循环
    # （两个 GUI 库共享主线程 = HANDOFF 里那个 GIL 致命错误）。
    # Qt 起不来（没装 PySide6 / 导入失败 / 建窗口炸了）就回落到下面的 Tk 版。
    if _想用Qt主界面():
        try:
            from ui.qt_main.app_qt import run as _run_qt, qt_available as _qt_ok
            if _qt_ok():
                sys.exit(_run_qt(icon_path=get_icon_path()))
        except SystemExit:
            raise
        except Exception as e:
            trace_exc("app/Qt主界面启动失败，回落Tk", e)

    # 创建主窗口（先隐藏，避免瞬移）；使用 TkinterDnD 以支持文件拖拽
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    except Exception:
        root = tk.Tk()
    root.withdraw()
    root.title("Minecraft 整合包迁移工具 - 增强版 v5")
    # 字号跟着 UI_SCALE 走（Tk 的 scaling 是"每磅多少像素"，96dpi 基准 = 4/3）
    try:
        root.tk.call("tk", "scaling", 4.0 / 3.0 * UI_SCALE)
    except Exception:
        pass
    root.geometry(f"{int(1000 * UI_SCALE)}x{int(1080 * UI_SCALE)}")

    # 设置图标
    icon_path = get_icon_path()
    if icon_path:
        try:
            root.iconbitmap(icon_path)
        except:
            pass

    # ---- 首次启动先问语言 ----
    # 判断依据：配置里**没有 `language` 键**（老用户只要在设置里改过语言就会写进去）。
    # 这一步必须赶在 install() 和界面模块 import 之前 —— 界面文案是按语言渲染的。
    if "language" not in load_raw_config():
        try:
            from ui.lang_picker import ask_language
            语言 = ask_language(root, icon_path)
        except Exception:
            语言 = "zh"                       # 出任何问题都退回默认中文，别挡住启动
        try:
            i18n.set_language(语言)
            save_raw_config({"language": 语言})
        except Exception:
            pass

    # 装语言层（中文时什么都不做），然后**才**导入界面模块
    i18n.install()
    from ui.main_window import MigrationGUI
    from ui.dialogs import ask_close_action

    # 先把 emoji 那边的一次性开销（字体回退枚举 ~270ms + 首个带 emoji 的 Label
    # 排版 ~40ms）做掉，再弹闪屏。放这儿是为了"闪屏一出现就是流畅的"——挪到闪屏
    # 出来之后做的话，用户会看到立方体先愣住 300ms 才开始转。
    # 代价是双击之后要多等这 300ms 才看到卡片，但总时长不变（闪屏最短时长照算）。
    # 设置里关掉启动动画时这笔预热照样做：界面里的 emoji 该卡还是会卡。
    # 闪屏子进程先起来（它自己启动要 ~0.4s），再去做 emoji 预热 —— 两件事并行，
    # 等主界面开始构建时闪屏正好已经画出来了。放在预热之后的话，用户会先愣一下
    # 才看到卡片。
    qt_splash = None
    _raw_cfg = load_raw_config()
    splash_on = bool(_raw_cfg.get("splash", True))
    # 设置里关掉 PySide6（纯 Tk 模式）时，连闪屏都别去碰 Qt：
    # `from ui import splash_qt` 会顺带把 PySide6 载进主进程，那就不是"纯 Tk"了
    qt_allowed = bool(_raw_cfg.get("qt_enabled", True))
    splash_t0 = time.perf_counter()
    splash_qt = None
    qt_handle = None
    if splash_on and SPLASH_QT and qt_allowed:
        try:
            from ui import splash_qt as splash_qt_mod
            # 只把子进程拉起来、不等它就绪：它自己启动要 ~0.4s，正好和下面的
            # emoji 预热（~0.3s）重叠，启动总时长省下这一截。
            qt_handle = splash_qt_mod.launch()
            splash_qt = splash_qt_mod
        except Exception:
            qt_handle = None
            splash_qt = None

    # emoji 那边的一次性开销（字体回退枚举 ~270ms + 首个带 emoji 的 Label 排版
    # ~40ms）先做掉，再弹闪屏，闪屏一出现就是流畅的。这笔无论用哪个闪屏都要做。
    warm_up_emoji_font()

    # 预热这段时间里子进程多半已经 READY 了，这里一般只是确认一下
    if qt_handle is not None:
        try:
            qt_splash = splash_qt.wait_ready(qt_handle)
        except Exception:
            qt_splash = None

    # 弹出启动闪屏，盖住主界面构建期间的空窗（构建实测约 0.5~2s）
    # 优先用**独立进程**的 Qt 闪屏：构建期间主线程被占满，进程内的闪屏只有
    # on_stage 那几下能泵到帧（实测最大 325ms 完全静止）；独立进程有自己的事件
    # 循环，主线程再忙也照样 59~62fps。拿不到 PySide6 / 起不来就回落进程内 Tk 闪屏。
    splash = None
    if splash_on and qt_splash is None:
        try:
            from ui.splash import SplashScreen
            splash = SplashScreen(root, icon_path=icon_path)
            root.update()           # 让它真的画出来，而不是停在未渲染状态
        except Exception:
            splash = None

    # 主窗口的位置先算好，但**不显示**——构建期间窗口保持隐藏，等闪屏收走了
    # 再让 Tk/Windows 原生显示出来。不提前映射、不预画、不做显形动画。
    root.update_idletasks()
    # 尺寸/位置统一走 utils/dpi（含高 DPI 缩放 + 不超出屏幕），别再写死 1000x1080
    root.geometry(window_geometry(root, 1000, 1080))

    # 创建主界面实例（此时窗口仍隐藏）
    # on_stage：构建途中被回调，用来让出一帧，否则主线程被占满，闪屏动画会停住。
    def on_stage(text=""):
        if splash is not None and text:
            splash.set_status(text)
        try:
            root.update()
        except Exception:
            pass

    app = MigrationGUI(root, on_stage=on_stage)

    if splash is not None:
        try:
            splash.lift()                   # 保证闪屏仍在最上层
        except Exception:
            pass

    def _把窗口类底色改成主题色():
        """把 Tk 顶层窗口类的背景刷子换成主题底色。

        这是"启动白屏"的**根**：`deiconify()` 时 Windows 要先用窗口类的背景刷子
        擦一遍客户区，Tk 注册的类用的是系统底色（白），而我们的第一帧要等 Tk
        自己重绘 —— 中间那段白就是这么来的。给 root 设 `bg` 只影响 Tk 自己的
        绘制，改不了那个刷子。

        改成主题色之后：窗口一显示就是对的底色，且**窗口保持普通窗口**（不分层），
        Windows 的原生"打开动画"照常播放 —— 用 -alpha 淡入虽然也能挡住白，
        但分层窗口会让系统跳过打开动画（用户 m14710 要的就是那个动画）。
        """
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            user32.GetParent.restype = ctypes.c_void_p
            user32.GetParent.argtypes = [ctypes.c_void_p]
            hwnd = user32.GetParent(ctypes.c_void_p(root.winfo_id())) or root.winfo_id()
            色 = str((getattr(app, "theme", None) or {}).get("bg", "") or "")
            if not 色.startswith("#") or len(色) < 7:
                return
            r = int(色[1:3], 16); g = int(色[3:5], 16); b = int(色[5:7], 16)
            刷 = gdi32.CreateSolidBrush((b << 16) | (g << 8) | r)     # COLORREF 是 BGR
            if not 刷:
                return
            # GCLP_HBRBACKGROUND = -10；系统不复制刷子，所以要一直拿住这个句柄
            if user32.SetClassLongPtrW(ctypes.c_void_p(hwnd), -10, ctypes.c_void_p(刷)) == 0:
                gdi32.DeleteObject(ctypes.c_void_p(刷))
                return
            _窗口类刷子[0] = 刷
            # 让这块区域按新刷子重画一次
            user32.InvalidateRect(ctypes.c_void_p(hwnd), None, True)
        except Exception:
            pass

    _窗口类刷子 = [None]

    def _make_sure_visible():
        """收尾：保证窗口显示出来，并且是普通窗口（不带 layered）。

        layered 是"Windows 会跳过关闭动画"的那个样式，这里兜底清一下。
        """
        try:
            root.deiconify()
        except Exception:
            pass
        clear_layered_style(root)
        focus_window(root)

    def reveal_main_window():
        """主界面显形：先把窗口类底色改成主题色，再让系统照常播"打开动画"。"""
        _把窗口类底色改成主题色()
        try:
            root.deiconify()
        except Exception:
            pass
        # 排好版、把该画的画掉；窗口此时可能已经可见，但底色已经是主题色，
        # 所以即便有一两帧没画完也是深色，不会闪白
        try:
            root.update_idletasks()
        except Exception:
            pass

    # 构建如果太快（约 0.4s），立方体刚起转就收起了，所以给闪屏一个最短显示时长；
    # 等待期间继续 update，动画照常跑。
    if qt_splash is not None:
        # 独立进程闪屏：它自己按 60fps 播，这里只需要凑够最短显示时长，
        # 然后发个 close 让它播"收起"动画（不阻塞），主界面立刻显形 —— 闪屏在最上层
        # 缩没了自然露出主界面。
        remain = SPLASH_MIN_SEC - (time.perf_counter() - splash_t0)
        while remain > 0 and time.time() < splash_t0 + SPLASH_MIN_SEC:
            time.sleep(0.01)
            remain = SPLASH_MIN_SEC - (time.perf_counter() - splash_t0)
        reveal_main_window()
        qt_splash.close()
        _make_sure_visible()
    elif splash is not None:
        remain = SPLASH_MIN_SEC - (time.perf_counter() - splash_t0)
        deadline = time.time() + remain
        while remain > 0 and time.time() < deadline:
            try:
                root.update()
            except Exception:
                break
            time.sleep(0.002)       # 让出一点点 CPU 即可，sleep 太久会把帧率拖下去
        try:
            # 主界面等卡片缩到只剩一小点（on_shrunk）才显形：显形会让主窗口做
            # 第一次整绘（实测 150ms 上下），趁卡片已经很小的时候做，看不出来。
            splash.close(on_shrunk=reveal_main_window, on_done=_make_sure_visible)
        except Exception:
            reveal_main_window()
            _make_sure_visible()
    else:
        reveal_main_window()
        _make_sure_visible()

    # ------------------------------------------------------- 系统托盘 / 后台
    # 关窗口不再退出，只是 withdraw 收进托盘，任务在后台继续跑；托盘菜单里才有
    # 真正的"退出"。没装 pywin32 时 tray 为 None，一切退回"关窗口就退出"。
    from ui import tray as tray_mod

    tray = None
    if tray_mod.available():
        try:
            tray = tray_mod.TrayIcon("Minecraft 整合包迁移工具", icon_path)
            if not tray.start():
                tray = None
        except Exception:
            tray = None

    hidden = {"v": False}          # 主界面是不是收在托盘里
    tray_tip_shown = {"v": False}
    poll_id = {"v": None}          # tray_poll 的 after id（退出时要撤掉）
    quitting = {"v": False}

    def notify_task_done(name, detail="", force=False):
        """任务跑完时弹个系统通知。

        force=True 是"后台静默执行"用的：那种模式下没有任何窗口反馈，
        所以不管界面是不是挂在托盘里都得通知一声。
        """
        if hidden["v"] or force:
            send_toast(trp("✅ {0}完成", name), detail or trp("点托盘图标打开主界面查看"))

    app._task_done_cb = notify_task_done
    app._in_tray_cb = lambda: hidden["v"]
    if tray is not None:
        # 托盘右键菜单里的勾选状态跟配置对齐（ask 视为"默认收进托盘"）
        tray.close_to_tray = (getattr(app, "close_action", "ask") != "exit")

    def apply_close_action(action):
        """按用户选的关闭方式收尾。"""
        if action == "tray":
            hide_to_tray()
        elif action == "exit":
            request_quit()

    def ask_and_close():
        """没在跑任务时点 ❌：问一次挂后台还是直接退出（可记住）。"""
        action = getattr(app, "close_action", "ask")
        if action == "ask":
            action, remember = ask_close_action(root, app.theme)
            if action is None:
                return                      # 取消：什么都不做
            if remember:
                app.set_close_action(action)
                if tray is not None:
                    tray.close_to_tray = (action == "tray")
        apply_close_action(action)

    def show_main_window():
        """从托盘把主界面叫回来——同样走原生显示，不做淡入/预画。"""
        if not hidden["v"]:
            try:
                root.deiconify()
                focus_window(root)
            except Exception:
                pass
            return
        hidden["v"] = False
        if tray is not None:
            tray.set_tooltip("Minecraft 整合包迁移工具")
        _make_sure_visible()
        try:
            app._flush_pending_diff()       # 挂托盘期间扫描出的窗口，这时候补开
        except Exception:
            pass

    def hide_to_tray():
        hidden["v"] = True
        if tray is not None:
            tray.set_tooltip("Minecraft 整合包迁移工具（后台运行中，点这里打开）")
        try:
            root.withdraw()                 # withdraw 会触发系统原生的隐藏动画
        except Exception:
            pass
        if not tray_tip_shown["v"]:
            tray_tip_shown["v"] = True
            send_toast(trp("已收进系统托盘"),
                       trp("程序仍在后台运行。点托盘图标可重新打开，右键可退出。"))

    def restart_app():
        """重启自己（切语言后用它，比"当场换文案"稳）。

        顺序很重要：先存盘 → 撤托盘/关 Qt 子进程 → **放掉单实例锁** → 起新进程 →
        结束自己。锁不放的话新进程一启动就被 tendo 判成"已经在跑"，会自己退出。
        """
        global _LOCK_HOLDER
        try:
            app.save_config()
        except Exception:
            pass
        if tray is not None:
            try:
                tray.stop()
            except Exception:
                pass
        try:
            app.stop_qt_host()
        except Exception:
            pass
        _LOCK_HOLDER = None                 # 放锁：让新进程能拿到
        try:
            import gc
            gc.collect()
        except Exception:
            pass
        try:
            import subprocess
            参数 = [a for a in sys.argv[1:] if not a.startswith("--qt-host")]
            if getattr(sys, "frozen", False):
                # 打包后 sys.executable 就是 exe 本身，别再拼 app.py
                命令 = [sys.executable] + 参数
            else:
                命令 = [sys.executable, os.path.abspath(sys.argv[0])] + 参数
            subprocess.Popen(命令, close_fds=True)
        except Exception as e:
            trace_exc("app/重启", e)
        try:
            root.destroy()
        except Exception:
            pass
        os._exit(0)

    # 设置页切语言时要能调到它；探针直接建 MigrationGUI 时没有这个属性，
    # 所以验证脚本仍然走"当场翻"，不会把测试进程重启掉
    try:
        app.重启程序 = restart_app
    except Exception as e:
        trace_exc("app/挂重启入口", e)

    def relaunch_as_admin():
        """以管理员身份重新启动（目标的目录要管理员权限时才用得上）。

        走 ShellExecuteW 的 runas：会弹一次 UAC。锁要先放掉，道理同 restart_app。
        """
        global _LOCK_HOLDER
        try:
            app.save_config()
        except Exception:
            pass
        if tray is not None:
            try:
                tray.stop()
            except Exception:
                pass
        try:
            app.stop_qt_host()
        except Exception:
            pass
        _LOCK_HOLDER = None
        try:
            import gc
            gc.collect()
        except Exception:
            pass
        ok = False
        try:
            import ctypes
            参数 = " ".join([a for a in sys.argv[1:] if not a.startswith("--qt-host")])
            if getattr(sys, "frozen", False):
                程序 = sys.executable
                参数字符串 = 参数
            else:
                程序 = sys.executable
                参数字符串 = '"' + os.path.abspath(sys.argv[0]) + '" ' + 参数
            r = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", 程序, 参数字符串, None, 1)
            ok = int(r) > 32          # ShellExecuteW 返回 >32 才算成功
        except Exception as e:
            trace_exc("app/提权重启", e)
        if not ok:
            # 用户在 UAC 上点了"否"：把锁拿回来，程序继续用普通权限跑
            try:
                app.log("以管理员身份重启被取消，继续用普通权限运行。", level="WARNING")
            except Exception:
                pass
            return
        try:
            root.destroy()
        except Exception:
            pass
        os._exit(0)

    try:
        app.重启管理员 = relaunch_as_admin
    except Exception as e:
        trace_exc("app/挂提权入口", e)

    def request_quit():
        """请求退出：先跳出主循环，存盘和销毁统一放到 mainloop 之后做。

        顺手把托盘轮询的定时器撤掉——不然它会带着一个已经被销毁的 Tcl 命令
        在解释器收尾时炸出一行 "invalid command name"。
        """
        quitting["v"] = True
        if poll_id["v"] is not None:
            try:
                root.after_cancel(poll_id["v"])
            except Exception:
                pass
            poll_id["v"] = None
        try:
            root.quit()
        except Exception:
            pass

    def tray_poll():
        """Tk 主线程这边轮询：抽托盘窗口的消息 + 取托盘塞进来的命令。

        托盘的消息循环现在就在这个线程里（`tray.pump()`），不再另起后台线程 ——
        那条线程和 Qt 的事件泵会撞出致命的 GIL 错误（见 ui/tray.py 顶部说明）。
        """
        if quitting["v"]:
            return
        if tray is not None:
            try:
                tray.pump()
            except Exception:
                pass
            try:
                while True:
                    cmd = tray.commands.get_nowait()
                    if cmd == "open":
                        show_main_window()
                    elif cmd == "toggle_close_to_tray":
                        # 右键菜单里那项是勾选式的：勾上=关闭时收进托盘，取消=直接退出
                        new = "exit" if getattr(app, "close_action", "ask") == "tray" else "tray"
                        app.set_close_action(new)
                        tray.close_to_tray = (new == "tray")
                    elif cmd == "exit":
                        busy = app.busy_task_name()
                        if busy and not messagebox.askyesno(
                                trp("⚠️ {0}进行中", busy),
                                trp("{0}任务还在执行，现在退出会中断它，可能导致数据损坏或程序状态异常。\n\n确定要退出吗？（也可以选“否”，把窗口收进托盘让它跑完）", busy),
                                default="no", icon="warning", parent=root):
                            continue        # 不退出，继续处理后面的命令
                        request_quit()
                        return
            except queue.Empty:
                pass
        try:
            poll_id["v"] = root.after(80, tray_poll)
        except Exception:
            pass

    # 关闭事件处理：任务进行中不允许直接关掉，只能挂到托盘或取消这次关闭
    def on_closing():
        busy = app.busy_task_name()

        if busy:
            if tray is None:
                # 没有托盘就没地方"挂着跑"，只能劝住
                messagebox.showwarning(
                    trp("⚠️ {0}进行中", busy),
                    trp("{0}任务正在执行，现在关闭会中断操作，可能导致数据损坏或程序状态异常。\n\n请等任务结束后再关闭窗口。", busy),
                    parent=root)
                return
            if messagebox.askyesno(
                    trp("⚠️ {0}进行中", busy),
                    trp("{0}任务正在执行，窗口不能直接关闭。\n\n点击「是」 → 收进系统托盘，任务在后台继续跑，跑完会弹通知\n点击「否」 → 返回程序，等任务结束", busy),
                    default="no", icon="warning", parent=root):
                hide_to_tray()
            return

        # 没在跑任务：有托盘就让用户选（挂后台 / 直接退出），没托盘就只能直接退出
        if tray is not None:
            ask_and_close()
        else:
            request_quit()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    if tray is not None:
        root.after(300, tray_poll)
    # 主线程看门狗：界面被卡住超过 1 秒，就把**当时各线程的调用栈**写进
    # ~/.minecraft_migrate_hang.log。用户报"点几下鼠标就未响应"这类问题时，让 ta 复现
    # 一次、把这个文件发过来，就能直接看到卡在哪一行 —— 比事后猜靠谱得多。
    # 平时开销只是每 100ms 一次 time.monotonic()，不卡就不写文件。
    try:
        from utils import hangwatch
        hangwatch.start(root)
    except Exception:
        pass
    root.mainloop()

    # 主循环结束（不管从哪条路退出）：统一收尾
    # ⚠ 第一件事就是把浮标关掉：它是**独立的分层顶层窗口**，下面那段 exit 流程会
    # 先 root.withdraw() + 280ms 隐藏动画再 destroy，而浮标的可见性判定是 1 秒轮询 ——
    # 于是这中间（stop_qt_host 还可能更久）就出现"主界面没了、浮标还留在桌面上"。
    # 显式关掉最稳，不依赖任何轮询时序。
    try:
        浮 = getattr(app, "_net_float", None)
        if 浮 is not None:
            浮.close()
    except Exception:
        pass
    try:
        app.stop_qt_host()          # Qt 窗口子进程不能留在后台
    except Exception:
        pass
    if tray is not None:
        try:
            tray.stop()
        except Exception:
            pass
    try:
        app.save_config()
    except Exception:
        pass
    # 窗口还开着（托盘菜单"退出"、或没托盘时关窗口）：同样先 withdraw，让系统把
    # 它自己那段隐藏动画播完再销毁，别"啪"地消失。
    try:
        if root.winfo_exists() and root.winfo_viewable():
            root.withdraw()
            deadline = time.time() + 0.28
            while time.time() < deadline:
                root.update()
                time.sleep(0.005)
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass


if __name__ == "__main__":
    main()
