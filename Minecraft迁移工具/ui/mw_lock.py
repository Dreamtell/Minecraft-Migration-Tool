# ui/mw_lock.py
"""锁屏遮罩：流动边框、迁移期间的锁与解锁。

拆自 ui/main_window.py（2026-10 拆分），方法原样搬移、未改逻辑；
状态仍在 MigrationGUI 实例上，这个类只提供方法。
"""
import time
import tkinter as tk
from utils.helpers import trace_exc


class LockMixin:
    """锁屏遮罩：流动边框、迁移期间的锁与解锁。"""

    # ---------- 锁屏遮罩：流动边框 + 内嵌执行日志 ----------
    _BORDER_W = 4          # 边框厚度
    _TILE = 240            # 渐变瓦片长度（定长 → 窗口缩放只要增减瓦片，不用重渲图）
    # 流动边框的两套配色：跑任务时是红（警示），跑完变绿（完成）
    _FLOW_RED = ((0x8e, 0x00, 0x00), (0xff, 0x17, 0x44))
    _FLOW_GREEN = ((0x1b, 0x5e, 0x20), (0x4c, 0xaf, 0x50))
    _FLOW_WARN = ((0x8d, 0x4a, 0x00), (0xff, 0xa0, 0x00))

    def _flow_tile(self, vertical=False, colors=None):
        """一段“暗→亮→暗”的渐变瓦片（做流动边框用）。

        用定长瓦片首尾相接 + 每帧整体平移，而不是逐帧改每个格子的颜色：
        前者每帧只有十几次 canvas.move，后者要上百次 itemconfigure。

        `colors=(暗, 亮)`：跑任务时红、跑完绿（见 _FLOW_RED / _FLOW_GREEN）。
        """
        cache = getattr(self, "_flow_tiles", None)
        if cache is None:
            cache = self._flow_tiles = {}
        colors = tuple(colors or self._FLOW_RED)
        key = (bool(vertical), colors)
        if key in cache:
            return cache[key]
        try:
            from PIL import Image as _I, ImageDraw as _D, ImageTk as _IT
            import math as _m
            W, H = self._BORDER_W, self._TILE
            im = _I.new("RGB", (W if vertical else H, H if vertical else W))
            d = _D.Draw(im)
            dark, bright = colors
            for i in range(H):
                t = 0.5 - 0.5 * _m.cos(2 * _m.pi * i / H)      # 0→1→0 平滑
                c = tuple(int(dark[k] + (bright[k] - dark[k]) * t) for k in range(3))
                if vertical:
                    d.line([(0, i), (self._BORDER_W - 1, i)], fill=c)
                else:
                    d.line([(i, 0), (i, self._BORDER_W - 1)], fill=c)
            photo = _IT.PhotoImage(im)
        except Exception:
            photo = None
        cache[key] = photo
        return photo

    def _build_flow_border(self, cv, w, h, colors=None):
        """围着窗口铺一圈流动边框（四边首尾相接，绕一圈同向流动）。"""
        cv.delete("border")
        self._flow_colors = tuple(colors or self._FLOW_RED)
        horiz = self._flow_tile(False, self._flow_colors)
        vert = self._flow_tile(True, self._flow_colors)
        items = []
        bw, t = self._BORDER_W, self._TILE

        def lay(anchor_x, anchor_y, length, horizontal, forward):
            img = horiz if horizontal else vert
            if img is None:
                return
            n = int(length // t) + 2
            for i in range(n):
                x = anchor_x + (i * t if horizontal else 0)
                y = anchor_y + (0 if horizontal else i * t)
                iid = cv.create_image(x, y, anchor="nw", image=img, tags="border")
                items.append({"id": iid, "horizontal": horizontal, "forward": forward,
                              "anchor": anchor_x if horizontal else anchor_y,
                              "length": length, "span": n * t})

        # 上边往右、右边往下、下边往左、左边往上 = 顺时针绕一圈
        lay(0, 0, w, True, True)
        lay(0, h - bw, w, True, False)
        lay(w - bw, 0, h, False, True)
        lay(0, 0, h, False, False)
        self._flow_canvas = cv
        self._flow_items = items

    def _flow_to_color(self, 目标, 帧数=10, 间隔=45):
        """把流动边框从当前颜色**渐变**过去（红→绿那段过渡就是"特效"）。"""
        cv = getattr(self, "_flow_canvas", None)
        ov = getattr(self, "_lock_overlay", None)
        if cv is None or ov is None:
            return
        起 = tuple(getattr(self, "_flow_colors", None) or self._FLOW_RED)
        终 = tuple(目标)
        w, h = max(2, ov.winfo_width()), max(2, ov.winfo_height())

        def 插(甲, 乙, t):
            return tuple(tuple(int(甲[i][k] + (乙[i][k] - 甲[i][k]) * t) for k in range(3))
                         for i in (0, 1))

        def step(i):
            if getattr(self, "_lock_overlay", None) is None:
                return
            t = i / float(帧数)
            try:
                self._build_flow_border(cv, w, h, 插(起, 终, t))
            except Exception:
                return
            if i < 帧数:
                try:
                    self._lock_color_job = self.root.after(
                        间隔, lambda: step(i + 1))
                except Exception:
                    self._lock_color_job = None
            else:
                self._lock_color_job = None

        step(0)

    def _flow_step(self):
        """每帧把瓦片整体平移几像素，越界的绕回另一端 —— 看着就是红光在边框里流动。"""
        cv = getattr(self, "_flow_canvas", None)
        items = getattr(self, "_flow_items", None)
        if cv is None or not items:
            self._flow_job = None
            return
        # 入场那一下故意跑快（间隔小、步长大），随后自己衰减到稳态 —— 红光像是"被点着了"
        # 再稳住。_flow_boost 从初始值递减到 0，期间在 快/慢 之间插值。
        boost = getattr(self, "_flow_boost", 0)
        span_boost = getattr(self, "_flow_boost_total", 1) or 1
        进度 = 1.0 - max(0, min(boost, span_boost)) / float(span_boost)
        step = int(round(7 + (3 - 7) * 进度))
        interval = int(round(12 + (32 - 12) * 进度))
        if boost > 0:
            self._flow_boost = boost - 1
        try:
            for it in items:
                if it["horizontal"]:
                    cv.move(it["id"], step if it["forward"] else -step, 0)
                else:
                    cv.move(it["id"], 0, step if it["forward"] else -step)
                x, y = cv.coords(it["id"])
                pos = x if it["horizontal"] else y
                anchor, span, length = it["anchor"], it["span"], it["length"]
                if it["forward"]:
                    if pos > anchor + length - 1:                 # 从尾部绕回头部
                        cv.move(it["id"], -span if it["horizontal"] else 0,
                                0 if it["horizontal"] else -span)
                else:
                    if pos + self._TILE < anchor:                 # 从头部绕回尾部
                        cv.move(it["id"], span if it["horizontal"] else 0,
                                0 if it["horizontal"] else span)
        except Exception:
            pass
        self._flow_job = self.root.after(interval, self._flow_step)

    def _lock_main_window(self, text="正在执行迁移任务"):
        """迁移期间给主窗口盖一层遮罩（锁屏）：流动红边 + 内嵌执行日志。

        盖不盖由设置里的 lock_mode 决定（all/real/off）；**不管盖不盖，操作按钮都会禁用**
        （那是 _refresh_busy_state 的职责）。内嵌日志是“第二个视图”：主日志照常记录，
        这里同步追加，锁屏期间不用去关窗口也能看进度。
        """
        if getattr(self, "_lock_overlay", None) is not None:
            return
        # 还没到完成态：任何"按任意键关闭"都不该生效（见 _lock_any_key 的说明）
        self._lock_done = False
        mode = getattr(self, "lock_mode", "all")
        if mode == "off" or (mode == "real" and self.dry_run.get()):
            self.log("🔓 按设置未锁定界面（操作按钮仍全部禁用）", level="INFO", save=False)
            return
        try:
            bg = self.theme.get("bg", "#f0f0f0")
            bw = self._BORDER_W
            ov = tk.Frame(self.root, bg=bg)
            ov.place(x=0, y=0, relwidth=1, relheight=1)
            cv = tk.Canvas(ov, bg="#8e0000", highlightthickness=0, bd=0)
            cv.place(x=0, y=0, relwidth=1, relheight=1)
            inner = tk.Frame(ov, bg=bg)
            inner.place(x=bw, y=bw, relwidth=1, relheight=1,
                        width=-2 * bw, height=-2 * bw)

            head = tk.Frame(inner, bg=bg)
            head.pack(fill="x", pady=(16, 4))
            self._lock_icon = tk.Label(head, text="🔒", font=("微软雅黑", 28), bg=bg,
                                       fg="#ff1744")
            self._lock_icon.pack()
            self._lock_title = tk.Label(head, text=text, font=("微软雅黑", 15, "bold"),
                                        bg=bg, fg=self.theme.get("fg", "#000000"))
            self._lock_title.pack(pady=(4, 2))
            self._lock_sub = tk.Label(head, text="主界面已锁定（操作按钮全部禁用）；"
                                                "下面是执行日志，不用关窗口也能看进度",
                                      font=("微软雅黑", 9), bg=bg,
                                      fg=self.theme.get("muted_fg", "#808080"))
            self._lock_sub.pack()
            self._lock_head = head

            log_box = tk.Frame(inner, bg=bg)
            log_box.pack(fill="both", expand=True, padx=26, pady=(10, 18))
            lt = tk.Text(log_box, wrap="word", state="normal", relief="flat", bd=0,
                         padx=10, pady=8, font=("微软雅黑", 9),
                         bg=self.theme.get("log_bg", "#ffffff"),
                         fg=self.theme.get("log_fg", "#000000"),
                         highlightthickness=1,
                         highlightbackground=self.theme.get("border", "#c8c8c8"))
            sb = tk.Scrollbar(log_box, orient="vertical", command=lt.yview)
            lt.configure(yscrollcommand=sb.set)
            lt.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            try:
                self._configure_log_colors(lt)       # 级别配色和主日志保持一致
                self._smooth(lt)                     # 平滑滚动
                tail = self.log_text.get("1.0", tk.END).splitlines()[-200:]
                if tail:
                    lt.insert("1.0", "\n".join(tail) + "\n")
                lt.see(tk.END)
            except Exception:
                pass
            lt.configure(state="disabled")
            self._lock_log_text = lt

            ov.lift()
            self._lock_overlay = ov
            self._build_flow_border(cv, max(2, ov.winfo_width()), max(2, ov.winfo_height()))
            # 入场：红光先"窜"起来再稳下来（温和一点：23ms 起步 → 稳态 32ms，别把主线程压满）
            self._flow_boost_total = 16
            self._flow_boost = self._flow_boost_total
            self._flow_job = self.root.after(23, self._flow_step)
            # 吃掉落在遮罩上的鼠标/滚轮/按键。遮罩自身原本没有任何绑定，点击会落进里面的
            # 日志 Text（反复获取焦点 + 触发平滑滚动重绘），连点就会让界面发卡 ——
            # 用户报过"锁屏时候连续点击会未响应"。完成态则放行，交给 root 上的
            # "按任意键关闭"处理。
            def _吞事件(event=None):
                if getattr(self, "_lock_done", False):
                    return None
                return "break"

            for _事件 in ("<Button-1>", "<ButtonRelease-1>", "<Double-Button-1>",
                         "<Button-2>", "<Button-3>", "<MouseWheel>",
                         "<Motion>", "<Key>", "<KeyRelease>"):
                try:
                    ov.bind(_事件, _吞事件)
                except Exception:
                    pass
            # 入场特效：一道亮光扫过 + 边框发光 + 标题逐字（纯视觉，不阻塞迁移线程）
            self._play_lock_intro(ov, inner, text)
            # 兜底：迁移线程结束时会在主线程里解锁，这里再盯一道 —— 万一那次跨线程
            # after 没排上（Tk 在“没进 mainloop”的驱动方式下会直接拒绝跨线程调用），
            # 遮罩也不该一直盖着。
            self._mig_watch_job = self.root.after(250, self._watch_migration_end)
            self._lock_cfg_bind = self.root.bind("<Configure>", self._on_lock_configure, add="+")
        except Exception as e:
            # 别裸吞：遮罩建不起来意味着"界面没锁上"，迁移却已经在跑了 —— 必须留痕，
            # 否则只能看到"锁屏没出现"这种没头没尾的现象（查这个 bug 时就吃过一次）。
            try:
                trace_exc("mw_lock", e)
            except Exception:
                pass
            self._lock_overlay = None
            self._lock_log_text = None

    def _play_lock_intro(self, ov, inner, text=""):
        """锁屏入场特效（约 0.5 秒）：边框发光脉冲 + 锁图标热点脉冲 + 标题逐字浮现。

        ⚠ **只改颜色和文字，绝不碰布局**。第一版改的是 `inner` 的边距（边框变粗）+ 图标
        字号（头部变高），逐帧量出来：日志框上下跳了 36px、高度变了 45px —— 整块内容
        在"呼吸式抖动"，看着晕。所以粗细/字号这类会触发重排的都不能动：
          · 边框发光 = 改底层画布 cv 的底色（红 → 亮红 → 红），厚度恒定；
          · 图标脉冲 = 改 🔒 的 fg（暗红 → 亮红 → 近白），字号恒 28；
          · 标题逐字浮现照旧（只影响它自己那一行的宽度，不会推动别的部件）。

        ⚠ 也别用"半透明覆盖层"那套：Tk 的画布**不支持真透明**，盖上去只会把内容整个
        糊掉（更早的一版就是这么翻车的）。驱动已有部件的颜色，稳、不吃性能。

        ⚠ "代数"守卫：逐字浮现会往标题里写字，而迁移可能在动画没播完时就结束 ——
        完成态把标题改成"迁移完成"，动画下一帧又覆盖回去（真踩过）。完成态一递增代数，
        还在排队的入场帧立刻作废。

        纯视觉，不阻塞迁移线程（这些 after 和迁移线程各跑各的）。
        """
        self._intro_gen = getattr(self, "_intro_gen", 0) + 1
        我的代 = self._intro_gen

        def 还轮到我():
            return getattr(self, "_intro_gen", 0) == 我的代

        # 入场只做"不动外观"的两件事：流动边先快后慢（见 _flow_boost）、标题逐字浮现。
        # ⚠ 曾经试过三种更"炫"的做法，全部翻车，别再走回头路：
        #   · 半透明描边覆盖层 / 扫光层 —— Tk 画布不支持真透明，把内容整个盖住；
        #   · 改 inner 边距做"边框变粗" —— 内容跟着缩放抖动，日志框 y 差 36px；
        #   · 改图标字号做脉冲 —— 头部变高，把下面全推下去；
        #   · 加一圈 8px highlight 做发光 —— 和流动瓦片边重复（看着"边框变粗"），
        #     而且完成态只把**流动边**渐变到绿色，那圈红边留着不走 → 绿红并存。
        # 3) 标题逐字浮现（比整句"啪"地出现有仪式感；只影响它自己那一行）
        try:
            全 = text or "正在执行迁移任务"

            def 逐字(i=1):
                if (not 还轮到我() or getattr(self, "_lock_overlay", None) is None
                        or i > len(全)):
                    return
                try:
                    self._lock_title.configure(text=全[:i])
                except Exception:
                    return
                self.root.after(34, lambda: 逐字(i + 1))
            # 别先清空：那样会有一瞬间标题是空的（"锁屏上什么都没有"的观感来源之一）。
            # 直接从第一个字开始写，画面自始至终都有内容。
            逐字()
            # 保底：主线程忙的时候 after 会拖后腿，逐字可能停在半截（"正在执"）——
            # 用户报过"锁屏界面的文字有问题"。到点无论如何补成完整句子。
            def 补全():
                if not 还轮到我() or getattr(self, "_lock_overlay", None) is None:
                    return
                try:
                    if self._lock_title.cget("text") != 全:
                        self._lock_title.configure(text=全)
                except Exception:
                    pass
            self.root.after(len(全) * 34 + 260, 补全)
        except Exception:
            pass
    def _on_lock_configure(self, event=None):
        """窗口大小变了：重铺一圈瓦片（瓦片图定长，不用重渲）。"""
        ov = getattr(self, "_lock_overlay", None)
        cv = getattr(self, "_flow_canvas", None)
        if ov is None or cv is None:
            return
        try:
            self._build_flow_border(cv, max(2, ov.winfo_width()),
                                   max(2, ov.winfo_height()))
        except Exception:
            pass

    def _watch_migration_end(self):
        """盯迁移结束：结束了就解锁（幂等，和线程侧的解锁互为保险）。"""
        self._mig_watch_job = None
        if self._migration_running:
            self._mig_watch_job = self.root.after(250, self._watch_migration_end)
            return
        self._unlock_main_window()
        self._refresh_busy_state()

    def _finish_migration_lock(self):
        # 作废还没播完的入场动画（它会写标题，会和下面的"迁移完成"打架）
        self._intro_gen = getattr(self, "_intro_gen", 0) + 1
        """迁移收尾：锁屏里写总结、边框红→绿渐变，然后停在完成态等用户按键。

        用户要的手感：跑完别“啪”地一下消失 —— 先把总结打出来、边框带过渡地转绿，
        等按任意键（或点一下）再收掉。设置里 lock_wait_key 关掉就改成自动关闭。
        """
        失败 = 0
        开始 = getattr(self, "_mig_t0", None)
        计划 = getattr(self, "_mig_plan", None)
        # 收尾期间由本函数主导流程：先把"盯迁移结束"的兜底轮询停掉。
        # 它看到 _migration_running 已经是 False 就会立刻解锁 —— 那样完成态会
        # “啪”一下消失，用户根本来不及看总结。
        job = getattr(self, "_mig_watch_job", None)
        self._mig_watch_job = None
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        try:
            失败 = len(getattr(self, "_failed_items", []) or [])
        except Exception:
            失败 = 0
        ov = getattr(self, "_lock_overlay", None)
        if ov is None:
            # 没盖遮罩（lock_mode=off / 模拟运行）：不搞完成态，直接收尾
            self._unlock_main_window()
            self._refresh_busy_state()
            return
        # 总结交给主日志 —— log() 会自动同步进锁屏那份"第二个视图"
        try:
            耗时 = ("%.1f 秒" % (time.time() - 开始)) if 开始 else "—"
            结果 = "✅ 迁移完成" if not 失败 else "⚠️ 迁移完成，但有 %d 条出错" % 失败
            等级 = "SUCCESS" if not 失败 else "WARNING"
            self.log("─" * 46, level="INFO", save=False)
            self.log("📊 迁移总结", level="INFO")
            self.log("　结果：%s" % 结果, level=等级)
            self.log("　耗时：%s" % 耗时, level="INFO")
            if 计划:
                self.log("　清单：模组 %d 项 · config %d 项 · 其它文件 %d 项" % 计划,
                         level="INFO")
            if 失败:
                self.log("　出错条目（最多列 5 条）：", level="INFO")
                for 条目 in list(getattr(self, "_failed_items", []) or [])[:5]:
                    名字 = 条目[1] if len(条目) > 1 else str(条目)
                    页名 = 条目[2] if len(条目) > 2 else ""
                    self.log("　　· %s%s" % (名字, ("（%s）" % 页名) if 页名 else ""),
                             level="WARNING")
                self.log("　可点主界面“查看失败条目”定位", level="INFO")
            else:
                self.log("　没有失败条目，可以直接启动游戏了 🎮", level="INFO")
            self.log("─" * 46, level="INFO", save=False)
        except Exception:
            trace_exc("main_window", "锁屏总结")
        # 后台静默执行 / 窗口已经收进托盘：根本没人看着，不能停在完成态等一个
        # 不会来的按键（那样遮罩会一直挂着，直到下次把窗口叫回来）
        try:
            可见 = bool(self.root.winfo_viewable())
        except Exception:
            可见 = True
        if getattr(self, "silent_background", False) or not 可见:
            self._unlock_main_window()
            self._refresh_busy_state()
            return
        # 头部换成完成态：锁 → 勾/警告
        try:
            self._lock_icon.configure(text="⚠️" if 失败 else "✅",
                                      fg="#e65100" if 失败 else "#2e7d32")
            self._lock_title.configure(
                text=("迁移结束（%d 条出错）" % 失败) if 失败 else "迁移完成")
        except Exception:
            pass
        # 边框过渡到完成色（无失败绿 / 有失败橙）——这段就是"特效"
        try:
            self._flow_to_color(self._FLOW_WARN if 失败 else self._FLOW_GREEN)
        except Exception:
            pass
        # 等按键 or 自动关
        self._lock_done = True                  # 从这里开始"按键关闭"才合法
        if getattr(self, "lock_wait_key", True):
            try:
                self._lock_sub.configure(text="按任意键（或点一下）关闭本界面")
            except Exception:
                pass
            try:
                self.root.focus_force()          # 键盘事件得有焦点才收得到
            except Exception:
                pass

            # ⚠ **延迟**注册，别在这里直接 bind：迁移期间用户在遮罩上敲的键没有绑定去处理，
            # 会一直积压在 tkinter 的事件队列里；绑定一注册，那些**旧的**按键就立刻被投递，
            # 锁屏在完成的瞬间就被收掉（用户报过"还没完成时按键 → 完成时立即退出"）。
            # 先空等一小会儿（这段时间没有绑定，积压的旧键会被丢掉），再挂上监听。
            def _挂按键():
                if not getattr(self, "_lock_done", False):
                    return
                self._lock_binds = []
                for 事件, fn in (("<Key>", self._lock_any_key),
                                 ("<Button-1>", self._lock_any_key),
                                 ("<MouseWheel>", self._lock_any_key)):
                    try:
                        self._lock_binds.append(self.root.bind(事件, fn, add="+"))
                    except Exception:
                        pass
            try:
                self._lock_bind_job = self.root.after(260, _挂按键)
            except Exception:
                _挂按键()
        else:
            try:
                self._lock_sub.configure(text="2 秒后自动关闭本界面")
            except Exception:
                pass
            self._lock_auto_job = self.root.after(2000, self._unlock_main_window)

    def _lock_any_key(self, event=None):
        """完成态下按任意键/点一下：收掉锁屏（幂等）。

        ⚠ **必须先确认迁移真的结束了**：迁移期间用户在遮罩上敲的键会积压在事件队列里，
        绑定一注册就会被投递进来（用户报过"还没完成时按键 → 完成时立即退出"）。
        没到完成态就忽略这次事件，别把遮罩提前收掉。
        """
        if not getattr(self, "_lock_done", False):
            return None
        self._unlock_main_window()
        self._refresh_busy_state()
        return None

    def _unlock_main_window(self):
        for attr in ("_mig_watch_job", "_lock_pulse_job", "_flow_job",
                     "_lock_color_job", "_lock_auto_job", "_lock_bind_job"):
            job = getattr(self, attr, None)
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._intro_gen = getattr(self, "_intro_gen", 0) + 1   # 同上，别和完成态抢标题
        self._flow_canvas = None
        self._flow_items = []
        self._lock_log_text = None
        # 入场特效层：解锁时可能还在播（或者 after 已排上），一并收掉
        self._flow_boost = 0
        层 = getattr(self, "_intro_layer", None)
        self._intro_layer = None
        if 层 is not None:
            try:
                层.destroy()
            except Exception:
                pass
        if getattr(self, "_lock_cfg_bind", None) is not None:
            try:
                self.root.unbind("<Configure>", self._lock_cfg_bind)
            except Exception:
                pass
            self._lock_cfg_bind = None
        # 完成态注册的"按任意键关闭"绑定，收尾时必须摘掉，否则下次迁移前
        # 随便敲个键都会去走一遍解锁
        for 事件, 序号 in zip(("<Key>", "<Button-1>", "<MouseWheel>"),
                              getattr(self, "_lock_binds", []) or []):
            try:
                self.root.unbind(事件, 序号)
            except Exception:
                pass
        self._lock_binds = []
        for attr in ("_lock_icon", "_lock_title", "_lock_sub", "_lock_head"):
            setattr(self, attr, None)
        ov = getattr(self, "_lock_overlay", None)
        self._lock_overlay = None
        if ov is not None:
            try:
                ov.destroy()
            except Exception:
                pass

    def _refresh_busy_state(self):
        """迁移/扫描进行中时禁用主界面所有操作/内容变更类按钮，防止连点或误操作。

        注意 `_starting` 也要算"忙"：准备阶段（统计文件那几秒）按钮必须一直是灰的，
        否则轮询收尾的 _watch_migration_end 会把它当成"没在跑"而重新点亮。
        """
        busy = bool(self._busy_task_name()) or bool(getattr(self, "_starting", False))
        state = "disabled" if busy else "normal"
        btns = (
            self.start_btn, self.rollback_btn, self.scan_btn,
            self.btn_changelog, self.mod_magnify_btn, self.add_mods_btn,
            self.clear_mods_btn, self.check_mods_btn, self.config_magnify_btn,
            self.add_config_dir_btn, self.add_config_file_btn, self.clear_config_btn,
            self.config_check_btn,
            # 路径区那三个：迁移期间改来源/目标路径同样是误操作，一起禁掉
            self._btn_widgets.get("browse_source"), self._btn_widgets.get("copy_target"),
            self._btn_widgets.get("browse_target"),
            # 右上角的设置/主题
            getattr(self, "settings_btn", None), getattr(self, "theme_btn", None),
            # 日志区那三个（遮罩不盖日志，这里靠禁用挡住）
            getattr(self, "log_magnify_btn", None),
            self._btn_widgets.get("log_open"), self._btn_widgets.get("log_clear"),
        )
        for btn in btns:
            if btn is None:
                continue
            try:
                btn.state(state)
            except Exception:
                pass
