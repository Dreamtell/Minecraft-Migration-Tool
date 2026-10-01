# ui/mw_buttons.py
"""按钮布局：分组、顺序、显隐、拖位动画。

拆自 ui/main_window.py（2026-10 拆分），方法原样搬移、未改逻辑；
状态仍在 MigrationGUI 实例上，这个类只提供方法。
"""
from ui import button_prefs
from ui.mw_common import _BUTTON_GROUPS, _DEFAULT_BUTTON_ORDER, WrapRow


class ButtonsMixin:
    """按钮布局：分组、顺序、显隐、拖位动画。"""

    # ---------- 按钮的显示/隐藏 与 排序 ----------
    def _group_of(self, key):
        for gkey, _label, _side in _BUTTON_GROUPS:
            if key in _DEFAULT_BUTTON_ORDER[gkey]:
                return gkey
        return None

    def _group_anchor(self, gkey):
        """这一排按钮后面还跟着别的控件时返回那个控件。

        必须用它当 pack 的 before= 锚点：pack_forget 之后再 pack 会排到队尾，
        不指定锚点的话按钮会跑到状态标签/图例的右边去。
        """
        return {
            "path": getattr(self, "source_status", None),
            "path_target": getattr(self, "target_status", None),
            "config": getattr(self, "_check_legend", None),
        }.get(gkey)

    def _resolved_order(self, gkey):
        """某一排的最终顺序：配置里的顺序 + 补上配置里还没有的新按钮。"""
        default = _DEFAULT_BUTTON_ORDER[gkey]
        keys = [k for k in (self.button_order.get(gkey) or []) if k in default]
        keys += [k for k in default if k not in keys]
        return keys

    # ---------- 按钮排布：place + 插值动画 ----------
    _ANIM_MS = 12              # 过渡帧间隔
    _ANIM_MAX_FRAMES = 40      # 兜底：万一位置算不收敛，最多跑这么多帧

    def _placeable_row(self, widgets):
        """这一排能不能用 place 摆？

        容器里混着别的控件（状态标签 / 图例）时不能：place 出来的按钮不占 pack
        的位置，那些兄弟控件会当按钮不存在，直接压到它们身上。
        """
        try:
            rows = {w.master for _k, w in widgets}
            if len(rows) != 1:
                return None
            row = rows.pop()
            # 这一排如果是 WrapRow，就交给它自己按宽度包裹 —— 它连图例、分段控件、
            # 没登记进配置的按钮一起摆，不挑内容；place 那条路只认容器里全是登记按钮。
            if isinstance(row, WrapRow):
                return None
            group = {w for _k, w in widgets}
            if set(row.winfo_children()) - group:
                return None
            return row
        except Exception:
            return None

    def _place_row(self, row, entries, side, hidden, animate):
        """把一排按钮用 place 摆好；可见的按钮从当前位置平滑滑到目标位置。

        pack 是排不动的（要么原地要么跳），所以显隐/换序想有过渡只能用 place。
        """
        pad = 5
        try:
            row.update_idletasks()
        except Exception:
            pass
        vis_seq = [(k, w) for k, w in entries if k not in hidden]
        if side != "left":
            vis_seq = list(reversed(vis_seq))
        # 只按单行算：**自动换行交给 WrapRow**（容器是 WrapRow 的排根本不会走到这里，
        # 见 `_placeable_row`）。这里再塞一套换行逻辑，两条路会互相打架 ——
        # 真踩过：动作区那排按钮被算出容器外的坐标，整排看不见。
        offsets, off = {}, pad
        for k, w in vis_seq:
            offsets[k] = off
            off += w.winfo_reqwidth() + pad * 2
        h = max([w.winfo_reqheight() for _k, w in entries] or [30])
        # 关掉 propagate 后容器不再按子控件算尺寸，宽高都得自己给：
        # fill 了 x/both 的排，宽度由父容器决定；其余（side="right" 那种）只能自己算，
        # 否则容器宽度塌成 1px，靠右对齐的按钮会被 place 到负数坐标上去。
        try:
            fill = str(row.pack_info().get("fill", "none"))
        except Exception:
            fill = "none"
        try:
            row.pack_propagate(False)
            if fill in ("x", "both"):
                row.configure(height=h)
            else:
                row.configure(height=h, width=max(1, off))
        except Exception:
            pass
        starts = {}
        for k, w in entries:
            try:
                starts[k] = w.winfo_x() if w.winfo_ismapped() else None
            except Exception:
                starts[k] = None
        for _k, w in entries:
            try:
                w.pack_forget()
            except Exception:
                pass
        # 动画待办**按排**存：以前是一个全局列表，每摆一排就把别排还没跑完的帧全取消掉，
        # 于是"最后摆的那排"正常、前面的排停在半路（表现就是按钮重叠/被裁）。
        # _apply_button_layout 是按组顺序摆的，排在后面的组会把前面组的动画掐死。
        anim_jobs = getattr(self, "_btn_anim_jobs", None)
        if not isinstance(anim_jobs, dict):
            anim_jobs = self._btn_anim_jobs = {}
        row_key = str(row)
        for j in anim_jobs.pop(row_key, []):
            try:
                self.root.after_cancel(j)
            except Exception:
                pass
        jobs = anim_jobs[row_key] = []

        def place_at(w, x):
            y = max(0, (h - w.winfo_reqheight()) // 2)
            if side == "left":
                w.place(x=x, y=y, anchor="nw")
            else:
                # 靠右对齐：用 relx=1.0 定位，窗口拉宽拉窄都跟着右边缘走。
                # `x` 是"距右边缘多远"，钳一下别超过容器宽度 —— 否则最左那个会被摆到
                # 容器外面（英文按钮更宽时，`📋 History` 会在滑动动画途中被裁掉一半）。
                try:
                    x = min(x, max(0, row.winfo_width() - w.winfo_reqwidth()))
                except Exception:
                    pass
                w.place(relx=1.0, x=-(x + w.winfo_reqwidth()), y=y, anchor="nw")

        for k, w in entries:
            if k in hidden:
                try:
                    w.place_forget()
                except Exception:
                    pass
        if not animate or not vis_seq or not row.winfo_ismapped():
            # 还没显示出来时（启动阶段）直接摆到位，别在后台空跑一遍动画
            for k, w in vis_seq:
                try:
                    place_at(w, offsets[k])
                except Exception:
                    pass
            return
        pos = {}
        for k, w in vis_seq:
            start = starts.get(k)
            if start is None:      # 新出现的：从旁边 26px 滑进来，而不是凭空冒出
                start = offsets[k] - 26 if side == "left" else offsets[k] + 26
            pos[k] = float(start)

        def step(n):
            done = True
            for k, w in vis_seq:
                tgt = float(offsets[k])
                cur = pos[k]
                if abs(cur - tgt) < 1.0:
                    cur = tgt
                else:
                    cur += (tgt - cur) * 0.34
                    done = False
                pos[k] = cur
                try:
                    place_at(w, cur)
                except Exception:
                    pass
            if not done and n < self._ANIM_MAX_FRAMES:
                try:
                    jobs.append(self.root.after(self._ANIM_MS, lambda: step(n + 1)))
                except Exception:
                    pass
            elif done:
                anim_jobs.pop(row_key, None)      # 这一排跑完了，登记表里不留空的
        step(0)

    def _apply_button_layout(self, animate=True):
        """按"显示/隐藏 + 自定义顺序"重新摆各排按钮。

        容器里全是本排按钮的排走 place+插值（显隐/换序有一段平滑滑动）；
        混着状态标签的那种排仍用 pack，行为跟以前完全一样。
        """
        hidden = set(self.hidden_buttons or ())
        for gkey, _label, side in _BUTTON_GROUPS:
            order = self._resolved_order(gkey)
            entries = [(k, self._btn_widgets[k]) for k in order if k in self._btn_widgets]
            if not entries:
                continue
            # 先把这一排全部撤下来（含被隐藏的）：只重新 pack 显示的那些是不够的，
            # 之前已经摆上去的隐藏按钮会原地不动，"隐藏"等于没生效。
            for _k, w in entries:
                try:
                    w.pack_forget()
                except Exception:
                    pass
            row = self._placeable_row(entries)
            if row is not None:
                try:
                    self._place_row(row, entries, side, hidden, animate)
                    continue
                except Exception:
                    # place 这条路出问题就地回滚到 pack，别让界面摆不正
                    for _k, w in entries:
                        try:
                            w.place_forget()
                        except Exception:
                            pass
                    try:
                        row.pack_propagate(True)
                    except Exception:
                        pass
            shown = [k for k, _w in entries if k not in hidden]
            # 靠右对齐的那两排：反过来摆，列表顺序就是从左到右看到的顺序
            seq = shown if side == "left" else list(reversed(shown))
            anchor = self._group_anchor(gkey)
            for key in seq:
                w = self._btn_widgets[key]
                kwargs = {"side": side, "padx": 5}
                try:
                    if anchor is not None and anchor.winfo_exists():
                        kwargs["before"] = anchor
                    w.pack(**kwargs)
                except Exception:
                    try:
                        w.pack(side=side, padx=5)
                    except Exception:
                        pass
            # pack 完让 WrapRow 重排一次：pack 子控件不改变容器宽度，容器收不到
            # <Configure>，不显式叫它一次就永远停在单行溢出
            for _k, w in entries:
                m = getattr(w, "master", None)
                if isinstance(m, WrapRow):
                    try:
                        m.relayout(force=True)
                    except Exception:
                        pass
                    break

    def set_button_hidden(self, key, hidden):
        """在设置里勾/取消某个按钮的显示。"""
        keys = set(self.hidden_buttons or ())
        keys.add(key) if hidden else keys.discard(key)
        self.hidden_buttons = sorted(keys)
        button_prefs.update(self.hidden_buttons, self.button_order)
        self._apply_button_layout()
        self.save_config()
        self._refresh_button_tree()

    def move_button(self, key, delta):
        """在所属那一排里把按钮上移/下移一格（整表顺序里含已隐藏的项）。"""
        gkey = self._group_of(key)
        if gkey is None:
            return False
        order = self._resolved_order(gkey)
        i = order.index(key)
        j = i + delta
        if j < 0 or j >= len(order):
            return False
        order[i], order[j] = order[j], order[i]
        self.button_order[gkey] = order
        button_prefs.update(self.hidden_buttons, self.button_order)
        self._apply_button_layout()
        self.save_config()
        self._refresh_button_tree()
        return True

    def reset_button_layout(self):
        """恢复默认：全部显示 + 默认顺序。"""
        self.hidden_buttons = []
        self.button_order = {}
        button_prefs.update(self.hidden_buttons, self.button_order)
        self._apply_button_layout()
        self.save_config()
        self._refresh_button_tree()
        self.log("🧩 界面按钮已恢复默认显示与顺序", level="INFO", save=False)

    def _pack_window_btns(self, gkey, row, entries, side="left", padx=5):
        """按「界面按钮」的配置摆一个窗口工具栏：隐藏的不摆、顺序照配置。

        entries 是 [(key, widget)]，按**默认顺序**给；widget 为 None 的直接跳过
        （例如 config 清单里没有"添加模组"那个按钮）。
        这类窗口每次打开现建，所以设置改完是下次打开生效。
        """
        table = {k: w for k, w in entries if w is not None}
        seq = [k for k in button_prefs.keys(gkey) if k in table]
        # 兜底：完全没登记进表的按钮也得摆出来（但不能把"被隐藏"的又加回来）
        known = set(button_prefs.DEFAULTS.get(gkey, ()))
        seq += [k for k in table if k not in known]
        if side != "left":
            seq = list(reversed(seq))                  # 靠右摆：先摆的最靠右
        for key in seq:
            try:
                table[key].pack(side=side, padx=padx)
            except Exception:
                pass
        return seq
