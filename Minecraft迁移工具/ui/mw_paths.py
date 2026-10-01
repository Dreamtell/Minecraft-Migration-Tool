# ui/mw_paths.py
"""源/目标路径：校验、环境探测、状态语义色、常用目录。

拆自 ui/main_window.py（2026-10 拆分），方法原样搬移、未改逻辑；
状态仍在 MigrationGUI 实例上，这个类只提供方法。
"""
import tkinter as tk
from core.scanner import detect_instance_env
from pathlib import Path
from tkinter import filedialog
from ui.mw_common import _grad_width
from utils.helpers import RoundedEntry, create_gradient_button


class PathsMixin:
    """源/目标路径：校验、环境探测、状态语义色、常用目录。"""

    def _is_valid_instance(self, path_str):
        """
        严格检查路径是否为有效的 Minecraft 整合包实例（借鉴 PCL2 验证逻辑）
        返回: (is_valid, reason, details_dict)
        """
        p = Path(path_str)
        details = {}

        # ----- 第1层：基础路径检查 -----
        if not p.exists():
            return False, "路径不存在", details
        if not p.is_dir():
            return False, "不是目录", details

        # 检查读写权限（尝试创建临时文件）
        try:
            test_file = p / ".permission_test"
            test_file.touch()
            test_file.unlink()
            details["read_write"] = True
        except:
            details["read_write"] = False
            return False, "无读写权限，请以管理员身份运行", details

        # 检查路径是否包含中文（警告级别）
        has_chinese = any('\u4e00' <= char <= '\u9fff' for char in str(p))
        if has_chinese:
            details["has_chinese"] = True
            # 不是致命错误，但给出警告

        # ----- 第2层：核心标识文件检查 -----
        # 2.1 检查关键子目录
        has_mods = (p / "mods").exists() and (p / "mods").is_dir()
        has_config = (p / "config").exists() and (p / "config").is_dir()
        has_saves = (p / "saves").exists() and (p / "saves").is_dir()
        has_libraries = (p / "libraries").exists() and (p / "libraries").is_dir()
        has_versions = (p / "versions").exists() and (p / "versions").is_dir()

        details["has_mods"] = has_mods
        details["has_config"] = has_config
        details["has_saves"] = has_saves
        details["has_libraries"] = has_libraries

        # 2.2 检查 Minecraft 核心标识文件
        has_options = (p / "options.txt").exists()
        has_launcher_profiles = (p / "launcher_profiles.json").exists()

        details["has_options"] = has_options
        details["has_launcher_profiles"] = has_launcher_profiles

        # 2.3 检查版本目录下的核心文件
        version_dirs = []
        if has_versions:
            for v_dir in (p / "versions").iterdir():
                if v_dir.is_dir():
                    version_json = v_dir / "version.json"
                    if version_json.exists():
                        version_dirs.append(v_dir.name)
            details["valid_versions"] = version_dirs

        # 2.4 检查 Fabric/Forge 标识（如果存在 mods 目录）
        if has_mods:
            mods_dir = p / "mods"
            jar_files = list(mods_dir.glob("*.jar"))
            details["mod_count"] = len(jar_files)
            # 检查是否有 Fabric 或 Forge 模组
            fabric_mods = list(mods_dir.glob("*fabric*.jar")) + list(mods_dir.glob("*.fabric.mod.json*"))
            forge_mods = list(mods_dir.glob("*forge*.jar"))
            details["fabric_mods"] = len(fabric_mods) > 0
            details["forge_mods"] = len(forge_mods) > 0

        # 2.5 检查 PCL2 特有标识
        has_pcl_ini = (p / "PCL.ini").exists()
        details["has_pcl_ini"] = has_pcl_ini

        # ----- 第3层：综合判断 -----
        # 判断标准：
        # 1. 必须有 mods 和 config（整合包基本要素）
        if not has_mods:
            return False, "缺少 mods 目录（不是有效的整合包）", details
        if not has_config:
            return False, "缺少 config 目录（不是有效的整合包）", details

        # 2. mods 目录不能为空
        if details.get("mod_count", 0) == 0:
            return False, "mods 目录为空（没有模组文件）", details

        # 3. 必须有至少一个有效版本（有 version.json）
        if not version_dirs:
            # 如果没有 version.json，但 options.txt 存在，可能是旧版整合包
            if not has_options:
                return False, "缺少 version.json 或 options.txt，无法识别为有效实例", details

        # 通过所有检查
        details["is_valid"] = True
        return True, "✅ 有效实例目录", details

    # 状态标签的语义色：颜色由"状态"决定，但主题一变就得立刻换成新主题里的那支色。
    _SEMANTIC_FG = {"ok": "ok_fg", "fail": "fail_fg", "muted": "muted_fg",
                    "warn": "warn_fg"}

    def _set_status_semantic(self, label, kind, text=None, tip=None):
        """给状态标签上语义色，并记住是哪一种。

        为什么要记：切主题时应用新颜色**不能等重新校验**——validate_path 要扫
        实例目录（模组多的实例要好几百毫秒），那期间标签会一直挂着旧主题的颜色，
        看起来就是"有颜色的文字闪一下"。记住状态后，apply_theme 里可以直接换色。

        tip：这一状态的悬停说明。不传 = 沿用上一次的 —— 切主题时会不带 text/tip
        再调一次，那是纯换色，不能顺手把说明抹掉。
        """
        try:
            label._semantic = kind
            if tip is not None:
                label._tip_text = tip
            color = self.theme.get(self._SEMANTIC_FG.get(kind, "muted_fg"),
                                   self.theme["fg"])
            if text is None:
                label.config(fg=color)
            else:
                label.config(text=text, fg=color)
        except Exception:
            pass

    def _apply_status_semantic_colors(self):
        """切主题时按上次记住的状态，立刻把三个状态标签的颜色换成新主题的。"""
        for name in ("source_status", "target_status", "world_status"):
            label = getattr(self, name, None)
            if label is None:
                continue
            self._set_status_semantic(label, getattr(label, "_semantic", "muted"))

    def _instance_env(self):
        """源实例的 MC 版本 / 加载器（联网搜索拿去过滤候选池）。

        `detect_instance_env` 自己带缓存，所以这里可以随手调；探测不出来就返回 None，
        调用方按"不过滤"处理 —— 猜错会把本该搜得到的模组挡掉，比不过滤糟得多。
        """
        try:
            path = self.source_path.get().strip()
        except Exception:
            return None
        if not path:
            return None
        try:
            return detect_instance_env(path)
        except Exception:
            return None

    def validate_path(self, path_str, status_label, label_text):
        """校验路径是不是有效的 Minecraft 整合包实例。

        界面上**只留一个语义色图标**（✅ / ⚠️ / ❌），详细结论全写进悬停提示。
        以前把"有效（有存档，72个模组，Fabric, Forge）"整句铺在路径行上，
        路径框本来就被浏览按钮挤，这行字再占一百多像素就太长了。
        """
        if not path_str:
            self._set_status_semantic(status_label, "muted", "（未选择）",
                                      tip=f"尚未选择「{label_text}」整合包路径")
            return

        is_valid, reason, details = self._is_valid_instance(path_str)

        # 悬停提示：路径本身 + 这次校验到底看出了什么
        tip_lines = [f"📁 {path_str}", ""]
        if is_valid:
            tip_lines.append("✅ 有效的 Minecraft 整合包实例")
            tip_lines.append("· 有 saves/ 存档目录" if details.get("has_saves")
                             else "· 无 saves/ 存档目录")
            tip_lines.append(f"· 模组 {details.get('mod_count', 0)} 个")
            if details.get("valid_versions"):
                tip_lines.append(f"· 版本：{', '.join(details['valid_versions'][:3])}")
            loaders = []
            if details.get("fabric_mods"):
                loaders.append("Fabric")
            if details.get("forge_mods"):
                loaders.append("Forge")
            if loaders:
                tip_lines.append("· 加载器：" + ", ".join(loaders))
            launchers = []
            if details.get("has_launcher_profiles"):
                launchers.append("官方启动器")
            if details.get("has_pcl_ini"):
                launchers.append("PCL2")
            if launchers:
                tip_lines.append("· 启动器：" + ", ".join(launchers))

            if details.get("has_chinese"):
                tip_lines += ["", "⚠️ 路径含中文，建议改成纯英文（个别模组/存档读取会出问题）"]
                self._set_status_semantic(status_label, "warn", "⚠️",
                                          tip="\n".join(tip_lines))
            else:
                self._set_status_semantic(status_label, "ok", "✅",
                                          tip="\n".join(tip_lines))
        else:
            tip_lines.append("❌ 不是有效的整合包实例")
            tip_lines.append("· 原因：" + reason)
            self._set_status_semantic(status_label, "fail", "❌",
                                      tip="\n".join(tip_lines))

    def on_path_change(self, *args):
        src = self.source_path.get().strip()
        tgt = self.target_path.get().strip()
        self.validate_path(src, self.source_status, "源")
        self.validate_path(tgt, self.target_status, "目标")
        self._update_world_status()
        # 源路径变化后重新按目录识别 config 文件夹条目（末尾加 "/"）
        try:
            self._mark_config_folders()
        except Exception:
            pass
        self.save_config()

    def _create_path_widgets(self):
        # 源目录
        frame_source = tk.LabelFrame(self.root, text="📤 旧版整合包（要迁移出去的源）", padx=5, pady=5)
        frame_source.pack(fill="x", padx=10, pady=5)
        self.source_entry = RoundedEntry(frame_source, self.theme,
                                         textvariable=self.source_path, chars=58,
                                         fg_key="data_fg")
        self.source_entry.pack(side="left", padx=5)
        btn_source_browse = create_gradient_button(
            frame_source, "📂 浏览...", self.select_source,
            colors=("#607d8b", "#90a4ae"),
            width=_grad_width("📂 浏览..."), height=30, font=("微软雅黑", 9, "bold"))
        btn_source_browse.pack(side="left", padx=5)
        self._btn_widgets["browse_source"] = btn_source_browse
        self._stage()
        btn_copy = create_gradient_button(
            frame_source, "← 使用新版路径填充", self.copy_target_to_source,
            colors=("#fb8c00", "#ffb74d"),
            width=_grad_width("← 使用新版路径填充"), height=30, font=("微软雅黑", 9, "bold"))
        btn_copy.pack(side="left", padx=5)
        self._btn_widgets["copy_target"] = btn_copy
        self.create_tooltip(btn_copy, "将右侧“新版”的路径复制到左侧“旧版”栏，用于快速测试或反向操作")
        # 只显示一个状态图标，细节问悬停（validate_path 每次校验都会刷新 _tip_text）
        self.source_status = tk.Label(frame_source, text="", fg=self.theme["muted_fg"],
                                      font=("微软雅黑", 11))
        self.source_status._keep_fg = True      # 颜色由状态决定，别被主题统一刷掉
        self.source_status.pack(side="left", padx=10)
        self.create_tooltip(self.source_status,
                            lambda: getattr(self.source_status, "_tip_text", ""))
        self._stage()

        # 目标目录
        # 迁移方向箭头直接写进标题（⬇ 表示上面「旧版」的数据往下流到这里）。
        # 以前它自己占一整行，20pt 的箭头把那一行撑到 40 多像素；放进标题既不占
        # 高度，也不会把这一框的路径框挤得和上面那框不对齐。
        frame_target = tk.LabelFrame(self.root, text="⬇ 新版整合包（迁移目的地）",
                                     padx=5, pady=5)
        frame_target.pack(fill="x", padx=10, pady=5)
        self.target_entry = RoundedEntry(frame_target, self.theme,
                                         textvariable=self.target_path, chars=66,
                                         fg_key="data_fg")
        self.target_entry.pack(side="left", padx=5)
        btn_target_browse = create_gradient_button(
            frame_target, "📂 浏览...", self.select_target,
            colors=("#607d8b", "#90a4ae"),
            width=_grad_width("📂 浏览..."), height=30, font=("微软雅黑", 9, "bold"))
        btn_target_browse.pack(side="left", padx=5)
        self._btn_widgets["browse_target"] = btn_target_browse
        self.target_status = tk.Label(frame_target, text="", fg=self.theme["muted_fg"],
                                      font=("微软雅黑", 11))
        self.target_status._keep_fg = True      # 同上
        self.target_status.pack(side="left", padx=10)
        self.create_tooltip(self.target_status,
                            lambda: getattr(self.target_status, "_tip_text", ""))

        # 存档名称
        frame_world = tk.LabelFrame(self.root, text="存档文件夹名称", padx=5, pady=5)
        frame_world.pack(fill="x", padx=10, pady=5)
        self.world_entry = RoundedEntry(frame_world, self.theme,
                                        textvariable=self.world_name, chars=30,
                                        fg_key="data_id_fg")
        self.world_entry.pack(side="left", padx=5)
        tk.Label(frame_world, text="（例如：新的世界）").pack(side="left")
        self.world_status = tk.Label(frame_world, text="", fg=self.theme["muted_fg"])
        self.world_status._keep_fg = True       # 同上
        self.world_status.pack(side="left", padx=10)

    # ---------- 路径选择 ----------
    def select_source(self):
        path = filedialog.askdirectory(title="选择源整合包的实例根目录")
        if path:
            self.source_path.set(path)

    def select_target(self):
        path = filedialog.askdirectory(title="选择目标整合包的实例根目录")
        if path:
            self.target_path.set(path)

    def copy_target_to_source(self):
        tgt = self.target_path.get().strip()
        if tgt:
            self.source_path.set(tgt)
            self.log("已将目标路径复制到源路径", level="INFO")
        else:
            self.root.bell()
            self.log("⚠️ 目标路径为空，无法复制", level="WARNING")

    # ---------- 检查存档（实时） ----------
    def _update_world_status(self):
        """实时检测源存档是否存在，只更新状态标签（不弹窗/不打日志，避免输入时刷屏）。"""
        src = self.source_path.get().strip()
        world = self.world_name.get().strip()
        try:
            if not src or not world:
                self._set_status_semantic(self.world_status, "muted", "")
                return
            src_path = Path(src)
            save_dir = src_path / "saves" / world
            if save_dir.is_dir():
                self._set_status_semantic(self.world_status, "ok", "✅ 存档已存在")
            else:
                self._set_status_semantic(self.world_status, "fail", "❌ 存档不存在")
        except Exception:
            self._set_status_semantic(self.world_status, "muted", "")
