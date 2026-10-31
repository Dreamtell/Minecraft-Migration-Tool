# ui/mw_lock.py
"""锁屏遮罩：流动边框、迁移期间的锁与解锁。

拆自 ui/main_window.py（2026-10 拆分），方法原样搬移、未改逻辑；
状态仍在 MigrationGUI 实例上，这个类只提供方法。
"""
import time
import tkinter as tk
from utils import anim
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

    def _flow_set_tiles(self, colors):
        """只把瓦片图换成新配色的那张 —— **不动图元几何**。

        红→绿过渡原来每帧走 _build_flow_border：delete("border") + 重建一整圈图元
        （900x600 是 18 块），实测 10 帧 482ms，而且这段正好和"迁移总结写进日志"撞在一起
        （日志每条还要 update_idletasks）。换图只有十几次 itemconfigure，图元不重建，
        流动动画也不会被打断（它手上握的是 item id，几何一重建就对不上了）。
        """
        cv = getattr(self, "_flow_canvas", None)
        items = getattr(self, "_flow_items", None) or []
        if cv is None or not items:
            return
        self._flow_colors = tuple(colors)
        horiz = self._flow_tile(False, self._flow_colors)
        vert = self._flow_tile(True, self._flow_colors)
        for it in items:
            try:
                cv.itemconfigure(it["id"],
                                 image=(horiz if it["horizontal"] else vert))
            except Exception:
                pass

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
                self._flow_set_tiles(插(起, 终, t))     # 换图，不重建几何
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

    _FLOW_MS_FLOOR = 8      # 边流动画最快 8ms 一帧（125fps）：再快十几块瓦片的 move 会压主线程
    _FLOW_REF_MS = 12.0     # 速度标定基准帧（原实现"入场加速"那一段就是 12ms 一帧）。
    #                         只在 dt 缺失（第一帧）时当兜底用。

    def _flow_hold_hr(self):
        if not getattr(self, "_flow_hr", False):
            self._flow_hr = True
            try:
                anim.hold()
            except Exception:
                pass

    def _flow_release_hr(self):
        if getattr(self, "_flow_hr", False):
            self._flow_hr = False
            try:
                anim.release()
            except Exception:
                pass

    def _flow_step(self):
        """每帧把瓦片整体平移几像素，越界的绕回另一端 —— 看着就是红光在边框里流动。

        ⚠ 位移必须按真实 dt 算（原实现是"每帧固定 3 像素"，帧率一提到 125fps 就会快 15 倍）。
        速度仍按原来的标定：入场 7px/12ms 衰减到稳态 3px/32ms，换算成 px/ms 之后与帧率无关。
        排帧走 anim：跟随刷新率（本机 4ms，地板 8ms），动画期间才占 1ms 定时器精度
        —— 不 hold 的话 after(8) 会被 15.6ms 粒度卡成 64fps，入场加速段也跑不准。
        """
        cv = getattr(self, "_flow_canvas", None)
        items = getattr(self, "_flow_items", None)
        if cv is None or not items:
            self._flow_job = None
            self._flow_release_hr()
            return
        now = time.perf_counter()
        dt = (now - getattr(self, "_flow_t", 0.0)) * 1000.0 if self._flow_t else self._FLOW_REF_MS
        self._flow_t = now
        dt = max(0.5, min(200.0, dt))       # 掉帧（拖窗口/GC）时别一步跳太远
        # 入场那一下故意跑快，随后自己衰减到稳态 —— 红光像是"被点着了"再稳住。
        boost = getattr(self, "_flow_boost", 0)
        span_boost = getattr(self, "_flow_boost_total", 1) or 1
        进度 = 1.0 - max(0, min(boost, span_boost)) / float(span_boost)
        速度 = (7.0 / 12.0) + ((3.0 / 32.0) - (7.0 / 12.0)) * 进度     # px/ms
        if boost > 0:
            # 按**毫秒**衰减：原设计是"16 帧、间隔从 12ms 拉到 32ms"，也就是约 350ms 才稳。
            # 早先按帧数 ÷12ms 换算只有 192ms，实测 200~260ms 就落到稳态了 —— 入场"窜"得不够久。
            self._flow_boost = max(0.0, boost - dt)
        self._flow_carry = getattr(self, "_flow_carry", 0.0) + 速度 * dt
        step = int(self._flow_carry)        # 不足 1 像素的零头留到下一帧（帧密时步长很小）
        self._flow_carry -= step
        if step <= 0:
            step = 0
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
        self._flow_hold_hr()
        try:
            self._flow_job = self.root.after(
                max(self._FLOW_MS_FLOOR, anim.frame_ms(self.root)), self._flow_step)
        except Exception:
            self._flow_job = None
            self._flow_release_hr()

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
                # ⚠ 这里**不装平滑滚动**：日志在迁移期间是持续追加的，平滑滚动会给每次
                #   滚动挂一条 after 动画链 → 动画永不结束、主线程一直在重绘，用户点鼠标
                #   就是"未响应"（实测每条日志 5.6ms，600 条 = 3.4 秒满载）。
                #   锁屏日志只要"能看到最后一行"就够了，直接 see 到底。
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
            self._flow_boost_total = 350.0   # 入场加速持续多少毫秒（原设计 16 帧×均 22ms）
            self._flow_boost = self._flow_boost_total
            self._flow_t = 0.0          # 上一帧时刻（0 = 还没起过步，见 _flow_step）
            self._flow_carry = 0.0      # 不足 1 像素的位移零头
            self._flow_hold_hr()
            self._flow_job = self.root.after(23, self._flow_step)
            # 吃掉落在遮罩上的鼠标/滚轮/按键 —— 而且**连同遮罩内部的所有控件**一起绑。
            # 只绑 ov 是不够的：点击会先命中**点到的那个子控件**（日志框占了半屏，最常被点），
            # 由它自己处理（获得焦点、开始选择、触发平滑滚动重绘），连点就会发卡
            # （用户报过"迁移过程中如果点击鼠标会未响应"）。
            #
            # ⚠ 键鼠分两套处理，这是用户明确要求的语义：
            #   · 鼠标/滚轮：**任何时候都不响应**（迁移中吃掉；完成态也吃掉 —— 不让它选中、
            #     滚动、更不会凭一次点击就把锁屏收掉）。
            #   · 键盘：迁移期间吃掉（否则会积压，见下面"延迟注册"那条坑）；
            #     完成态放行，交给 root 上的"按任意键关闭"。
            _吃的事件 = ("<Button-1>", "<ButtonRelease-1>", "<Double-Button-1>",
                        "<Triple-Button-1>", "<Button-2>", "<Button-3>",
                        "<B1-Motion>", "<B2-Motion>", "<B3-Motion>",
                        "<MouseWheel>", "<Key>", "<KeyRelease>")
            _键事件 = ("<Key>", "<KeyRelease>")

            def _吞鼠标(event=None):
                return "break"

            def _吞键(event=None):
                return None if getattr(self, "_lock_done", False) else "break"

            def _绑吃事件(w):
                for _事件 in _吃的事件:
                    try:
                        w.bind(_事件, _吞键 if _事件 in _键事件 else _吞鼠标)
                    except Exception:
                        pass
                for _子 in w.winfo_children():
                    _绑吃事件(_子)

            _绑吃事件(ov)
            # ⚠ 不绑 <Motion>：鼠标只要动就会进 Python，纯属白烧 CPU（需要的只是点击/滚轮）
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

    # 关于入场动画：试过 8 版，全部翻车，结论是**别做**。
    # 逐字浮现这类效果依赖 after 的**准时性**，而真实迁移时主线程忙于刷日志，
    # after 会被大幅延后 —— 标题就一直停在"正"（用户两次截图都是这个）。
    # 盖层类（半透明描边 / 扫光）更糟：Tk 画布不支持真透明，直接把内容盖住。
    # 现在的做法：锁屏**直接完整出现**，动感交给本来就在跑的流动边
    # （入场先快后慢，见 _flow_boost）—— 它不依赖 after 准时，也从不遮内容。

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
        """盯迁移结束：结束了就解锁（幂等，和线程侧的解锁互为保险）。

        ⚠ 迁移线程收尾时会先立 `_mig_finish_pending`，再翻 `_migration_running`：
        这中间如果本函数抢到执行权，就会把「完成态」整个跳过去（用户报的
        "完成迁移后锁屏界面直接退出"）。所以收尾期间这里只排队等，不解锁。
        """
        self._mig_watch_job = None
        if getattr(self, "_mig_finish_pending", False):
            self._mig_watch_job = self.root.after(250, self._watch_migration_end)
            return
        if self._migration_running:
            self._mig_watch_job = self.root.after(250, self._watch_migration_end)
            return
        self._unlock_main_window()
        self._refresh_busy_state()

    def _finish_migration_lock(self):
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
            self._mig_finish_pending = False
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
        # 完成态已经建立（总结写完、边框转绿、按键已挂）—— 收尾结束，标志清掉
        self._mig_finish_pending = False
        if getattr(self, "lock_wait_key", True):
            try:
                self._lock_sub.configure(text="按任意键关闭本界面（鼠标不会关闭）")
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
                # 只挂键盘：完成态**鼠标不做任何反应**（连点击都不关），这是用户要求的。
                for 事件, fn in (("<Key>", self._lock_any_key),):
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
        """完成态下**按键盘任意键**：收掉锁屏（幂等）。鼠标不参与（见 _吞鼠标）。

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
        self._flow_boost = 0                # 入场加速的计数，复位
        self._flow_release_hr()
        self._flow_t = 0.0
        self._flow_carry = 0.0
        self._flow_canvas = None
        self._flow_items = []
        self._lock_log_text = None
        if getattr(self, "_lock_cfg_bind", None) is not None:
            try:
                self.root.unbind("<Configure>", self._lock_cfg_bind)
            except Exception:
                pass
            self._lock_cfg_bind = None
        # 完成态注册的"按任意键关闭"绑定，收尾时必须摘掉，否则下次迁移前
        # 随便敲个键都会去走一遍解锁
        for 事件, 序号 in zip(("<Key>",),
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

    def _set_edit_lock(self, locked):
        """把"现在不能改东西"落到输入控件上：路径框 / 存档名 / 两个开关 / 清单文本框。

        只管"能不能改"，不管"能不能点"——按钮的禁用由 _refresh_busy_state 负责。
        解锁时交给 _sync_edit_lock 决定编辑开关该不该恢复（镜像窗口还开着就不能恢复）。
        """
        locked = bool(locked)
        if getattr(self, "_ui_locked", None) == locked:
            return
        self._ui_locked = locked
        state = "disabled" if locked else "normal"
        for 名 in ("source_entry", "target_entry", "world_entry"):
            框 = getattr(self, 名, None)
            try:
                if 框 is not None:
                    # RoundedEntry 是 Frame + 内层 tk.Entry：state 只有内层认
                    # （直接对 Frame configure(state=...) 会抛 unknown option -state，
                    #  被 except 吞掉 → 看着"锁了"，其实没锁）。
                    内 = getattr(框, "entry", 框)
                    if locked:
                        # tk.Entry 一旦 disabled，Windows 会拿系统的"灰底灰字"来画，
                        # 落在深色圆角框里就是一块亮灰色砖头（用户截图："难看"）。
                        # readonly 也一样走系统色，没用。正解是把 disabled 的两套颜色
                        # 指回它自己的底色/字色 —— 看不出变化，但改不动了。
                        内.configure(disabledbackground=内.cget("bg"),
                                    disabledforeground=内.cget("fg"))
                    内.configure(state=state)
            except Exception as e:
                trace_exc("main_window/锁输入框 %s" % 名, e)
        for 名 in ("dry_run_sw", "overwrite_sw"):
            开 = getattr(self, 名, None)
            try:
                if 开 is not None:
                    开.set_enabled(not locked)
            except Exception as e:
                trace_exc("main_window/锁开关 %s" % 名, e)
        编 = getattr(self, "edit_switch", None)
        try:
            if locked and 编 is not None:
                编.set_enabled(False)
        except Exception as e:
            trace_exc("main_window/锁编辑开关", e)
        try:
            self._update_text_states()
        except Exception as e:
            trace_exc("main_window/刷新文本状态", e)
        if not locked:
            try:
                self._sync_edit_lock()
            except Exception as e:
                trace_exc("main_window/恢复编辑开关", e)

    def _refresh_busy_state(self):
        """迁移/扫描进行中时禁用主界面所有操作/内容变更类按钮，防止连点或误操作。

        注意 `_starting` 也要算"忙"：准备阶段（统计文件那几秒）按钮必须一直是灰的，
        否则轮询收尾的 _watch_migration_end 会把它当成"没在跑"而重新点亮。
        """
        # 开始前倒计时也算"忙"：那几秒里别的按钮不该能点，
        # 唯一还能点的是「开始迁移」—— 它就是取消键（见下面那段恢复 normal）。
        busy = (bool(self._busy_task_name())
                or bool(getattr(self, "_starting", False))
                or bool(getattr(self, "_countdown_running", False)))
        state = "disabled" if busy else "normal"
        # 标签栏**必须一起锁**：切到一个还没建过的页会当场构建整页（377 个模组 =
        # 377 个卡片控件），主线程卡好几秒 —— 用户"点几下标签就弹未响应"就是这么来的。
        # 这里不能只靠遮罩：准备阶段（_starting）那几秒遮罩还没盖上，点击照样能落到标签栏。
        try:
            self.list_tabs.set_locked(busy)
        except Exception:
            pass
        # 两个复选开关（模拟运行 / 覆盖已存在的模组）由下面的 switch 树兜底处理
        btns = (
            self.start_btn, self.rollback_btn,
            # 「查看历史」原来漏在名单外：迁移/倒计时期间它一直亮着（用户要求全部禁用）
            getattr(self, "history_btn", None), self.scan_btn,
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
        # 按钮之外，输入类也要跟着锁：路径框、存档名、两个开关、清单文本框。
        # 倒计时那几秒用户完全可能正在改路径或往清单里粘贴，不锁就会带着改动开跑。
        try:
            self._set_edit_lock(busy)
        except Exception as e:
            trace_exc("main_window/锁定输入", e)
        # 倒计时期间把「开始迁移」点亮：再点一下就是取消（start_migration 里判断）。
        if getattr(self, "_countdown_running", False):
            try:
                self.start_btn.state("normal")
            except Exception:
                pass
