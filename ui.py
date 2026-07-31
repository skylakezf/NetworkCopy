"""
磁盘拷贝工具 GUI - 横板向导式布局 (ttkbootstrap)
适配: 1280x700 低分辨率低色域屏幕
左侧: 步骤内容区  右侧: 步骤指示器 (200px)
设计: 参照页面展示.pptx
"""
import tkinter as _tk
from tkinter import *
import ttkbootstrap as ttk
from ttkbootstrap.constants import *


# ===== 设计系统颜色 (低色域优化) =====
C_PRIMARY    = "#2B2D42"   # 深蓝灰 - 标题、主按钮
C_TEXT       = "#1a1a2e"   # 主文字
C_TEXT_SEC   = "#555555"   # 次要文字 (加深以适应低色域)
C_TEXT_MUTED = "#777777"   # 辅助文字 (加深)
C_GREEN      = "#4CAF50"   # 完成/成功
C_GREEN_BG   = "#E8F5E9"   # 完成背景
C_BLUE       = "#2196F3"   # 当前步骤
C_GRAY       = "#999999"   # 未开始
C_GRAY_BG    = "#dddddd"   # 未开始背景 (加深以适应低色域)
C_SIDEBAR_BG = "#f0f0f0"   # 侧边栏背景 (加深)
C_CARD_BORDER= "#cccccc"   # 卡片边框 (加深)
C_CONSOLE_BG = "#2B2B2B"   # 控制台深色背景
C_CONSOLE_FG = "#E0E0E0"   # 控制台文字
C_RED        = "#D32F2F"   # 验证码红色
C_RED_BG     = "#FFF3E0"   # 验证码背景
C_SEP        = "#cccccc"   # 分隔线 (加深)
C_WHITE      = "#ffffff"


class WinGUI(ttk.Window):
    def __init__(self):
        super().__init__(themename="flatly")
        self.__win()
        self._octet_vcmd = (self.register(self._octet_validate), "%P")
        self._auth_vcmd = (self.register(self._auth_validate), "%P")
        self._build_layout()
        self._step = 0
        self._total_steps = 6  # 0=角色 1=网卡 2=磁盘 3=连接 4=传输 5=校验
        self._device_type = None  # "source" or "target"
        self._role_display = "未选择"

    def __win(self):
        self.title("数据迁移工具")
        width = 1280
        height = 700
        screenwidth = self.winfo_screenwidth()
        screenheight = self.winfo_screenheight()
        x = (screenwidth - width) // 2
        y = (screenheight - height) // 2
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.resizable(width=False, height=False)

    # ==================== 主布局 ====================

    def _build_layout(self):
        # 顶部标题栏
        title_frame = _tk.Frame(self, bg=C_WHITE)
        title_frame.pack(fill=X, padx=0, pady=0)

        _tk.Label(title_frame, text="磁盘拷贝工具",
                  font=("Microsoft YaHei UI", 14, "bold"),
                  fg=C_TEXT, bg=C_WHITE).pack(side=LEFT, padx=14, pady=8)

        self.tk_label_role = _tk.Label(title_frame,
                                       text="当前角色：未选择",
                                       font=("Microsoft YaHei UI", 9),
                                       fg=C_BLUE, bg=C_WHITE)
        self.tk_label_role.pack(side=RIGHT, padx=14, pady=8)

        # 分隔线
        _tk.Frame(self, height=1, bg=C_SEP).pack(fill=X, padx=0)

        # 主体: 左右分栏
        main = _tk.Frame(self, bg=C_WHITE)
        main.pack(fill=BOTH, expand=True, padx=0, pady=0)

        # 左侧内容区
        self._content_frame = _tk.Frame(main, bg=C_WHITE)
        self._content_frame.pack(side=LEFT, fill=BOTH, expand=True)

        # 右侧步骤指示器 (200px)
        self._build_step_indicator(main)

        # 底部导航按钮
        self._build_nav_buttons()

        # 底部状态栏
        self._build_status_bar()

        # 初始化 CSV 路径变量 (必须在 _build_step5_verify 之前)
        self.csv_path_var = _tk.StringVar()

        # 构建各步骤页面
        self._build_step0_role()
        self._build_step1_nic()
        self._build_step2_disk()
        self._build_step3_connect()
        self._build_step4_transfer()
        self._build_step5_verify()

        # 初始显示步骤 0
        self._show_step(0)

    # ==================== 右侧步骤指示器 ====================

    def _build_step_indicator(self, parent):
        side = _tk.Frame(parent, width=280, bg=C_SIDEBAR_BG)
        side.pack(side=RIGHT, fill=Y)
        side.pack_propagate(False)

        hdr = _tk.Frame(side, bg=C_SIDEBAR_BG)
        hdr.pack(fill=X, padx=14, pady=(14, 8))
        _tk.Label(hdr, text="步骤指引", font=("Microsoft YaHei UI", 13, "bold"),
                  fg=C_TEXT, bg=C_SIDEBAR_BG).pack(anchor=W)
        _tk.Frame(side, height=1, bg=C_SEP).pack(fill=X, padx=14)

        self._step_items = []
        steps = [
            ("1", "选择设备类型", "旧设备 (发送方) 或新设备 (接收方)"),
            ("2", "高级设置", "磁盘选择 · 分区映射 · IP 配置"),
            ("3", "连接并开始传输", "启动服务并输入验证码"),
            ("4", "传输 & 数据校验", "文件传输进度与完整性校验"),
        ]
        for num, title, desc in steps:
            item = self._build_step_item(side, num, title, desc)
            self._step_items.append(item)

    def _build_step_item(self, parent, num, title, desc):
        container = _tk.Frame(parent, bg=C_SIDEBAR_BG)
        container.pack(fill=X, padx=14, pady=(10, 0))

        row = _tk.Frame(container, bg=C_SIDEBAR_BG)
        row.pack(fill=X)

        circle = _tk.Label(row, text=num,
                           font=("Microsoft YaHei UI", 10, "bold"),
                           fg=C_GRAY, bg=C_GRAY_BG,
                           width=4, anchor=CENTER)
        circle.pack(side=LEFT, padx=(0, 10))

        lbl = _tk.Label(row, text=title,
                        font=("Microsoft YaHei UI", 10),
                        fg=C_GRAY, bg=C_SIDEBAR_BG, anchor=W)
        lbl.pack(side=LEFT, fill=X)

        desc_lbl = _tk.Label(container, text=desc,
                             font=("Microsoft YaHei UI", 9),
                             fg=C_TEXT_MUTED, bg=C_SIDEBAR_BG,
                             anchor=W, wraplength=240, justify=LEFT)
        desc_lbl.pack(fill=X, padx=(36, 0), pady=(2, 0))

        return {
            "circle": circle,
            "title": lbl,
            "desc": desc_lbl,
            "num": num,
            "title_text": title,
            "desc_text": desc,
        }

    def _update_step_indicator(self, current: int):
        map_ui_to_side = {0: 0, 1: 0, 2: 1, 3: 2, 4: 3, 5: 3}
        side_current = map_ui_to_side.get(current, 0)

        desc_map = {
            0: ("选择发送方或接收方", "请选择[旧设备(发送方)]或[新设备(接收方)]"),
            1: ("已选择直连网卡", "请选择设备类型"),
            2: ("高级设置 — 磁盘 / 分区 / IP", "确认磁盘和分区映射，查看网络配置"),
            3: ("准备连接", "启动服务，输入验证码并开始传输"),
            4: ("传输进度", "文件正在传输中..."),
            5: ("数据校验", "校验已传输文件的完整性"),
        }

        for i, item in enumerate(self._step_items):
            circle = item["circle"]
            lbl = item["title"]
            desc = item["desc"]

            if i < side_current:
                circle.configure(fg=C_GREEN, bg=C_GREEN_BG)
                lbl.configure(fg=C_GREEN, font=("Microsoft YaHei UI", 10, "bold"))
                if i == 0:
                    role_text = ("已选择：发送方" if self._device_type == "source"
                                 else "已选择：接收方" if self._device_type
                                 else "已完成")
                    desc.configure(text=role_text, fg=C_GREEN)
                elif i == 1:
                    desc.configure(text="已配置分区盘符映射", fg=C_GREEN)
                else:
                    desc.configure(text="传输服务已启动", fg=C_GREEN)

            elif i == side_current:
                circle.configure(fg=C_WHITE, bg=C_BLUE)
                lbl.configure(fg=C_BLUE, font=("Microsoft YaHei UI", 10, "bold"))
                d_title, d_detail = desc_map.get(current, ("", ""))
                desc.configure(text=f"{d_title}\n{d_detail}", fg=C_TEXT_SEC)

            else:
                circle.configure(fg=C_GRAY, bg=C_GRAY_BG)
                lbl.configure(fg=C_GRAY, font=("Microsoft YaHei UI", 10))
                desc.configure(text=item["desc_text"], fg=C_TEXT_MUTED)

    # ==================== 底部导航 ====================

    def _build_nav_buttons(self):
        nav = _tk.Frame(self, bg=C_WHITE)
        nav.pack(fill=X, padx=14, pady=(6, 10))

        btn_frame = _tk.Frame(nav, bg=C_WHITE)
        btn_frame.pack(side=RIGHT)

        self.tk_button_prev = ttk.Button(btn_frame, text="< 上一步", takefocus=False,
                                         bootstyle="secondary-outline", width=12)
        self.tk_button_prev.pack(side=LEFT, padx=(0, 8))

        self.tk_button_next = ttk.Button(btn_frame, text="下一步 >", takefocus=False,
                                         bootstyle="primary", width=12)
        self.tk_button_next.pack(side=LEFT)

        self.tk_button_mqfzl35t = ttk.Button(btn_frame, text="开始传输", takefocus=False,
                                             bootstyle="success", width=14)

    def set_button_prev(self, state: str, text: str = "< 上一步"):
        if state == "disabled":
            self.tk_button_prev.configure(state=DISABLED)
        else:
            self.tk_button_prev.configure(state=NORMAL)
        self.tk_button_prev.configure(text=text)

    def set_button_next(self, state: str, text: str = "下一步 >"):
        # 仅禁用/启用, 不隐藏按钮 — 保持按钮始终可见 (灰色 = 禁用), 标准 UX
        if state == "disabled":
            self.tk_button_next.configure(state=DISABLED)
        else:
            self.tk_button_next.configure(state=NORMAL, text=text)
            if not self.tk_button_next.winfo_ismapped():
                self.tk_button_next.pack(side=LEFT, padx=(0, 8))

    def show_start_button(self):
        self.tk_button_mqfzl35t.pack(side=LEFT, padx=(0, 0))

    def hide_start_button(self):
        self.tk_button_mqfzl35t.pack_forget()

    # ==================== 步骤页面切换 ====================

    _pages = {}

    def _show_step(self, step: int):
        for s, frame in self._pages.items():
            frame.pack_forget()
        if step in self._pages:
            self._pages[step].pack(fill=BOTH, expand=True)
        self._step = step
        self._update_step_indicator(step)

    def go_step(self, step: int):
        self._show_step(step)
        # 步骤 3: 根据设备类型显式显示对应子面板 (解决 pack_forget 后子面板丢失问题)
        if step == 3:
            ctl_type = getattr(getattr(self, 'ctl', None), '_device_type', '')
            if ctl_type == "目标设备" or self._device_type == "target":
                self.show_tgt_connect()
            elif ctl_type == "源设备" or self._device_type == "source":
                self.show_src_connect()
            self.show_start_button()
        else:
            self.hide_start_button()
        # 根据步骤设置「下一步」按钮默认状态
        transfer_done = getattr(getattr(self, 'ctl', None), '_transfer_done', False)
        is_target = getattr(getattr(self, 'ctl', None), '_device_type', '') == "目标设备"
        if step >= self._total_steps - 1:
            self.set_button_next("disabled")
        elif step == 3:
            self.set_button_next("disabled")
        elif step == 4:
            if transfer_done and is_target:
                self.set_button_next("normal", text="校验文件 >")
            else:
                self.set_button_next("disabled")
        else:
            # step 0/1/2 保持现有逻辑不变, 由 controller 回调进一步控制
            pass
        # 更新按钮状态
        if hasattr(self.ctl, '_check_button_state'):
            self.ctl._check_button_state()

    def _build_status_bar(self):
        status_frame = _tk.Frame(self, bg=C_SIDEBAR_BG)
        status_frame.pack(fill=X, padx=0, pady=0)

        self.tk_label_status = _tk.Label(status_frame, text="",
                                          font=("Microsoft YaHei UI", 8),
                                          fg=C_TEXT_MUTED, bg=C_SIDEBAR_BG,
                                          padx=14, pady=1, anchor=W)
        self.tk_label_status.pack(fill=X)

    # ==================== 步骤 0: 选择角色 ====================

    def _build_step0_role(self):
        page = _tk.Frame(self._content_frame, bg=C_WHITE)
        self._pages[0] = page

        inner = _tk.Frame(page, bg=C_WHITE)
        inner.place(relx=0.5, rely=0.42, anchor=CENTER)

        _tk.Label(inner, text="请选择当前设备的角色",
                  font=("Microsoft YaHei UI", 16, "bold"),
                  fg=C_TEXT, bg=C_WHITE).pack(pady=(0, 4))

        _tk.Label(inner, text="新设备是接收方，旧设备是发送方",
                  font=("Microsoft YaHei UI", 10),
                  fg=C_TEXT_SEC, bg=C_WHITE).pack(pady=(0, 30))

        cards = _tk.Frame(inner, bg=C_WHITE)
        cards.pack()

        # 发送方卡片
        old_card = _tk.Frame(cards, relief=SOLID, bd=1,
                             padx=20, pady=20, bg=C_WHITE,
                             highlightbackground=C_CARD_BORDER,
                             highlightthickness=1)
        old_card.pack(side=LEFT, padx=(0, 20))
        old_card.bind("<Button-1>", lambda e: self._on_select_role("source"))

        _tk.Label(old_card, text="发送方",
                  font=("Microsoft YaHei UI", 16, "bold"),
                  fg=C_TEXT, bg=C_WHITE).pack(pady=(0, 2))
        _tk.Label(old_card, text="源设备 - 旧设备",
                  font=("Microsoft YaHei UI", 9),
                  fg=C_TEXT_SEC, bg=C_WHITE).pack()
        _tk.Label(old_card, text="本设备上有旧数据，需要发送到新设备",
                  font=("Microsoft YaHei UI", 8),
                  fg=C_TEXT_MUTED, bg=C_WHITE).pack(pady=(6, 14))
        self.tk_btn_source = ttk.Button(old_card, text="选择发送方",
                                        command=lambda: self._on_select_role("source"),
                                        bootstyle="primary", takefocus=False, width=14)
        self.tk_btn_source.pack()

        # 接收方卡片
        new_card = _tk.Frame(cards, relief=SOLID, bd=1,
                             padx=20, pady=20, bg=C_WHITE,
                             highlightbackground=C_CARD_BORDER,
                             highlightthickness=1)
        new_card.pack(side=LEFT)
        new_card.bind("<Button-1>", lambda e: self._on_select_role("target"))

        _tk.Label(new_card, text="接收方",
                  font=("Microsoft YaHei UI", 16, "bold"),
                  fg=C_TEXT, bg=C_WHITE).pack(pady=(0, 2))
        _tk.Label(new_card, text="目标设备 - 新设备",
                  font=("Microsoft YaHei UI", 9),
                  fg=C_TEXT_SEC, bg=C_WHITE).pack()
        _tk.Label(new_card, text="这是一台新设备，需要接收旧设备的数据",
                  font=("Microsoft YaHei UI", 8),
                  fg=C_TEXT_MUTED, bg=C_WHITE).pack(pady=(6, 14))
        self.tk_btn_target = ttk.Button(new_card, text="选择接收方",
                                        command=lambda: self._on_select_role("target"),
                                        bootstyle="primary", takefocus=False, width=14)
        self.tk_btn_target.pack()

        for card in (old_card, new_card):
            _bind_card_hover(card, "#f5f5f5")

    def _on_select_role(self, role: str):
        self._device_type = role
        self._role_display = "旧设备 (发送方)" if role == "source" else "新设备 (接收方)"
        if hasattr(self, 'tk_label_role'):
            self.tk_label_role.configure(text=f"当前角色: {self._role_display}")
        if hasattr(self, 'tk_label_nic_ip'):
            self.tk_label_nic_ip.configure(text="等待网卡选择...")
        if hasattr(self, 'ctl') and hasattr(self.ctl, '_on_role_selected'):
            self.ctl._on_role_selected(role)

    # ==================== 步骤 1: 选择网卡 ====================

    def _build_step1_nic(self):
        page = _tk.Frame(self._content_frame, bg=C_WHITE)
        self._pages[1] = page

        inner = _tk.Frame(page, bg=C_WHITE)
        inner.pack(fill=BOTH, expand=True, padx=40, pady=28)

        _tk.Label(inner, text="选择网卡",
                  font=("Microsoft YaHei UI", 14, "bold"),
                  fg=C_TEXT, bg=C_WHITE).pack(anchor=W, pady=(0, 4))

        _tk.Label(inner, text="请选择用于直连传输的网卡（网线直连的 IP 通常为 169.254 开头）",
                  font=("Microsoft YaHei UI", 9), fg=C_TEXT_SEC,
                  wraplength=700, justify=LEFT, bg=C_WHITE).pack(anchor=W, pady=(0, 12))

        _tk.Label(inner, text="网卡优先级: USB 网卡 > 169.254 网段 > 内置网卡",
                  font=("Microsoft YaHei UI", 8), fg=C_TEXT_MUTED,
                  bg=C_WHITE).pack(anchor=W, pady=(0, 8))

        self.tk_select_box_mqfzkd6x = ttk.Combobox(inner, state="readonly",
                                                    font=("Microsoft YaHei UI", 9))
        self.tk_select_box_mqfzkd6x['values'] = ("扫描中...",)
        self.tk_select_box_mqfzkd6x.pack(fill=X, pady=(0, 10))

        self.tk_label_nic_detail = _tk.Label(inner, text="",
                                             font=("Microsoft YaHei UI", 8),
                                             fg=C_TEXT_SEC, bg=C_WHITE,
                                             wraplength=700, justify=LEFT)
        self.tk_label_nic_detail.pack(fill=X, pady=(0, 12))

        # 兼容旧控件 (隐藏)
        self.tk_select_box_mqg0hm2h = ttk.Combobox(page, state="readonly")
        self.winpe_var = _tk.StringVar(
            value="winpe" if __import__('os').path.exists("X:\\Windows\\System32") else "normal")

    # ==================== 步骤 2: 选择磁盘 ====================

    def _build_step2_disk(self):
        page = _tk.Frame(self._content_frame, bg=C_WHITE)
        self._pages[2] = page

        inner = _tk.Frame(page, bg=C_WHITE)
        inner.pack(fill=BOTH, expand=True, padx=40, pady=28)

        _tk.Label(inner, text="选择磁盘",
                  font=("Microsoft YaHei UI", 14, "bold"),
                  fg=C_TEXT, bg=C_WHITE).pack(anchor=W, pady=(0, 4))

        _tk.Label(inner, text="识别物理磁盘并配置分区盘符映射",
                  font=("Microsoft YaHei UI", 9), fg=C_TEXT_SEC,
                  wraplength=700, justify=LEFT, bg=C_WHITE).pack(anchor=W, pady=(0, 12))

        # 磁盘选择
        self.tk_select_box_mqfzmzbe = ttk.Combobox(inner, state="readonly",
                                                    font=("Microsoft YaHei UI", 9))
        self.tk_select_box_mqfzmzbe['values'] = ("请先选择设备类型",)
        self.tk_select_box_mqfzmzbe.pack(fill=X, pady=(0, 4))

        self.tk_label_auto_disk = _tk.Label(inner, text="",
                                            font=("Microsoft YaHei UI", 8, "italic"),
                                            fg=C_TEXT_MUTED, bg=C_WHITE)
        self.tk_label_auto_disk.pack(anchor=W, pady=(0, 14))

        # 分区盘符映射 (高级选项, 默认隐藏)
        self.tk_show_partition_map = _tk.BooleanVar(value=False)
        self.tk_cb_partition_map = ttk.Checkbutton(
            inner,
            text="分区盘符映射 (高级)",
            variable=self.tk_show_partition_map,
            command=self._toggle_partition_map,
            bootstyle="primary-outline",
        )
        self.tk_cb_partition_map.pack(anchor=W, pady=(0, 8))

        # 可折叠的分区映射区域
        self._partition_map_frame = _tk.Frame(inner, bg=C_WHITE)

        _tk.Label(self._partition_map_frame, text="分区盘符映射（源 D / E / F > 当前系统盘符）",
                  font=("Microsoft YaHei UI", 10, "bold"),
                  fg=C_TEXT, bg=C_WHITE).pack(anchor=W, pady=(4, 10))

        map_frame = _tk.Frame(self._partition_map_frame, bg=C_WHITE)
        map_frame.pack(fill=X)

        for drive, attr in [("D", "tk_select_box_mqfzsdz4"),
                            ("E", "tk_select_box_mqfzuo2y"),
                            ("F", "tk_select_box_mqfzwehm")]:
            row = _tk.Frame(map_frame, bg=C_WHITE)
            row.pack(fill=X, pady=3)
            _tk.Label(row, text=f"{drive} >", width=5, anchor=E,
                      font=("Microsoft YaHei UI", 10), fg=C_TEXT,
                      bg=C_WHITE).pack(side=LEFT, padx=(0, 6))
            cb = ttk.Combobox(row, state="readonly", width=8,
                              font=("Microsoft YaHei UI", 9))
            cb.pack(side=LEFT)
            setattr(self, attr, cb)

        # 分隔线
        _tk.Frame(self._partition_map_frame, height=1, bg=C_SEP).pack(fill=X, pady=(18, 0))

        # 运行环境
        env_frame = _tk.Frame(inner, bg=C_WHITE)
        env_frame.pack(fill=X, pady=(18, 0))

        _tk.Label(env_frame, text="运行环境",
                  font=("Microsoft YaHei UI", 10, "bold"),
                  fg=C_TEXT, bg=C_WHITE).pack(anchor=W, pady=(0, 6))

        radio_frame = _tk.Frame(env_frame, bg=C_WHITE)
        radio_frame.pack(anchor=W)

        self.winpe_var = _tk.StringVar(value="normal")
        ttk.Radiobutton(radio_frame, text="WinPE 环境",
                        variable=self.winpe_var, value="winpe",
                        bootstyle="primary").pack(side=LEFT, padx=(0, 20))
        ttk.Radiobutton(radio_frame, text="正常系统",
                        variable=self.winpe_var, value="normal",
                        bootstyle="primary").pack(side=LEFT)

        # ===== IP 配置 (高级设置) =====
        _tk.Frame(inner, height=1, bg=C_SEP).pack(fill=X, pady=(16, 10))

        _tk.Label(inner, text="IP 状态",
                  font=("Microsoft YaHei UI", 10, "bold"),
                  fg=C_TEXT, bg=C_WHITE).pack(anchor=W, pady=(0, 6))

        self.tk_label_nic_ip = _tk.Label(inner, text="等待网卡选择...",
                                         font=("Microsoft YaHei UI", 9),
                                         fg=C_TEXT_SEC, bg=C_WHITE,
                                         wraplength=700, justify=LEFT)
        self.tk_label_nic_ip.pack(anchor=W, pady=(0, 4))

    def _toggle_partition_map(self):
        """显示/隐藏分区盘符映射高级选项"""
        if self.tk_show_partition_map.get():
            self._partition_map_frame.pack(fill=X, after=self.tk_cb_partition_map, pady=(4, 0))
        else:
            self._partition_map_frame.pack_forget()

    # ==================== 步骤 3: 连接设置 ====================

    def _build_step3_connect(self):
        page = _tk.Frame(self._content_frame, bg=C_WHITE)
        self._pages[3] = page

        inner = _tk.Frame(page, bg=C_WHITE)
        inner.pack(fill=BOTH, expand=True, padx=40, pady=28)

        # ---- 发送方 (旧电脑) 页面 ----
        self._src_connect = _tk.Frame(inner, bg=C_WHITE)

        _tk.Label(self._src_connect, text="准备就绪",
                  font=("Microsoft YaHei UI", 16, "bold"),
                  fg=C_TEXT, bg=C_WHITE).pack(anchor=CENTER, pady=(0, 4))

        _tk.Label(self._src_connect, text="点击下方按钮启动 HTTP 文件传输服务",
                  font=("Microsoft YaHei UI", 10), fg=C_TEXT_SEC,
                  bg=C_WHITE).pack(anchor=CENTER, pady=(0, 20))

        # 验证码框
        auth_box = _tk.Frame(self._src_connect, bg=C_WHITE,
                             relief=SOLID, bd=1,
                             highlightbackground=C_CARD_BORDER,
                             highlightthickness=1)
        auth_box.pack(fill=X, pady=(0, 10))

        _tk.Label(auth_box, text="连接验证码",
                  font=("Microsoft YaHei UI", 9, "bold"),
                  fg=C_TEXT_SEC, bg=C_WHITE).pack(anchor=W, padx=14, pady=(10, 4))

        code_row = _tk.Frame(auth_box, bg=C_WHITE)
        code_row.pack(fill=X, padx=14, pady=(4, 10))

        _tk.Label(code_row, text="验证码:",
                  font=("Microsoft YaHei UI", 10),
                  fg=C_TEXT, bg=C_WHITE).pack(side=LEFT, padx=(0, 8))

        self.tk_label_auth_code = _tk.Label(
            code_row, text="----",
            font=("Consolas", 24, "bold"),
            fg=C_RED, bg=C_RED_BG,
            padx=14, pady=4,
        )
        self.tk_label_auth_code.pack(side=LEFT)

        # 醒目提醒
        remind = _tk.Frame(auth_box, bg=C_WHITE)
        remind.pack(fill=X, padx=14, pady=(0, 10))
        _tk.Label(remind, text="=请退出所有应用程序关闭所有文档，确认后请点击右下角“开始传输”",
                  font=("Microsoft YaHei UI", 9, "bold"),
                  fg=C_RED, bg=C_WHITE).pack(anchor=W)

        # 状态信息
        self.tk_label_src_status = _tk.Label(self._src_connect,
                                             text="验证码: ---- 等待目标设备连接...",
                                             font=("Microsoft YaHei UI", 8),
                                             fg=C_TEXT_MUTED, bg=C_WHITE)
        self.tk_label_src_status.pack(anchor=W, pady=(6, 0))

        # ---- 接收方 (新电脑) 页面 ----
        self._tgt_connect = _tk.Frame(inner, bg=C_WHITE)

        _tk.Label(self._tgt_connect, text="接收方 - 连接设置",
                  font=("Microsoft YaHei UI", 14, "bold"),
                  fg=C_BLUE, bg=C_WHITE).pack(anchor=W, pady=(0, 12))

        self.tk_button_dhcp = ttk.Button(self._tgt_connect,
                                         text="寻找旧电脑", takefocus=False,
                                         bootstyle="primary", width=16)
        self.tk_button_dhcp.pack(anchor=W, pady=(0, 4))

        self.tk_label_dhcp_status = _tk.Label(self._tgt_connect, text="",
                                              font=("Microsoft YaHei UI", 8),
                                              fg=C_TEXT_SEC,
                                              wraplength=700, justify=LEFT,
                                              bg=C_WHITE)
        self.tk_label_dhcp_status.pack(fill=X, pady=(0, 6))

        self.tk_label_discover = _tk.Label(self._tgt_connect, text="发现的设备",
                                           font=("Microsoft YaHei UI", 9, "bold"),
                                           fg=C_TEXT, bg=C_WHITE)
        self.tk_label_discover.pack(anchor=W, pady=(6, 4))

        self.tk_select_box_discover = ttk.Combobox(self._tgt_connect, state="readonly",
                                                    font=("Microsoft YaHei UI", 9))
        self.tk_select_box_discover['values'] = ("等待 DHCP 响应...",)
        self.tk_select_box_discover.set("等待 DHCP 响应...")
        self.tk_select_box_discover.pack(fill=X, pady=(0, 6))

        # 高级: 手动 IP (初始隐藏, 勾选后才显示)
        self._advanced_frame = _tk.Frame(self._tgt_connect, bg=C_SIDEBAR_BG, padx=10, pady=8)

        self.tk_var_advanced = _tk.BooleanVar(value=False)
        self.tk_check_advanced = ttk.Checkbutton(
            self._tgt_connect, text="高级: 手动输入 IP 地址",
            variable=self.tk_var_advanced,
            bootstyle="secondary",
            command=self._toggle_advanced_ip
        )

        _tk.Label(self._advanced_frame, text="源设备 (旧电脑) IP 地址:",
                  font=("Microsoft YaHei UI", 8), fg=C_TEXT_SEC,
                  bg=C_SIDEBAR_BG).pack(side=LEFT, padx=(0, 4))
        self.tk_entry_const = ttk.Entry(self._advanced_frame, width=16,
                                         font=("Microsoft YaHei UI", 8))
        self.tk_entry_const.pack(side=LEFT, padx=(0, 4))

        # 验证码输入区 (整齐居中)
        code_section = _tk.Frame(self._tgt_connect, bg=C_WHITE)
        code_section.pack(fill=X, pady=(14, 4))

        _tk.Label(code_section, text="请输入旧电脑上显示的连接验证码:",
                  font=("Microsoft YaHei UI", 9, "bold"),
                  fg=C_TEXT, bg=C_WHITE).pack(anchor=CENTER)

        self.tk_entry_code = ttk.Entry(code_section, font=("Consolas", 16, "bold"),
                                       justify=CENTER, width=6,
                                       validate="key", validatecommand=self._auth_vcmd)
        self.tk_entry_code.pack(pady=(8, 0))

        self.tk_entry_code.bind("<KeyRelease>", self._on_auth_key)

    def _toggle_advanced_ip(self):
        if self.tk_var_advanced.get():
            self._advanced_frame.pack(fill=X, pady=(6, 0))
            for w in self._advanced_frame.winfo_children():
                w.pack(side=LEFT, padx=(0, 4))
        else:
            for w in self._advanced_frame.winfo_children():
                w.pack_forget()
            self._advanced_frame.pack_forget()

    def _on_auth_key(self, event=None):
        current = self.tk_entry_code.get()
        upper = current.upper()
        if upper != current:
            self.tk_entry_code.delete(0, END)
            self.tk_entry_code.insert(0, upper[:4])
        elif len(current) > 4:
            self.tk_entry_code.delete(4, END)

    def _on_browse_csv(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="选择 FullFilelist_DEF.csv",
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
        )
        if path:
            self.csv_path_var.set(path)

    # ==================== 步骤 4: 传输进度 ====================

    def _build_step4_transfer(self):
        page = _tk.Frame(self._content_frame, bg=C_WHITE)
        self._pages[4] = page

        inner = _tk.Frame(page, bg=C_WHITE)
        inner.pack(fill=BOTH, expand=True, padx=40, pady=20)

        _tk.Label(inner, text="传输中...",
                  font=("Microsoft YaHei UI", 14, "bold"),
                  fg=C_TEXT, bg=C_WHITE).pack(anchor=W, pady=(0, 4))

        # 验证码醒目展示区 (仅发送端显示, 默认隐藏)
        self._auth_banner_frame = _tk.Frame(inner, bg=C_RED_BG, padx=14, pady=10)
        # 不在此处 pack — 由 show_auth_code() 按需显示

        _tk.Label(self._auth_banner_frame, text="验证码",
                  font=("Microsoft YaHei UI", 8, "bold"),
                  fg=C_RED, bg=C_RED_BG).pack(anchor=W)

        code_row = _tk.Frame(self._auth_banner_frame, bg=C_RED_BG)
        code_row.pack(fill=X, pady=(2, 0))

        self.tk_label_transfer_auth_code = _tk.Label(
            code_row, text="----",
            font=("Consolas", 28, "bold"),
            fg=C_RED, bg=C_WHITE,
            padx=14, pady=4,
        )
        self.tk_label_transfer_auth_code.pack(side=LEFT, padx=(0, 10))

        _tk.Label(code_row,
                  text="请在新设备上输入此验证码连接",
                  font=("Microsoft YaHei UI", 10, "bold"),
                  fg=C_RED, bg=C_RED_BG).pack(side=LEFT)

        self.tk_label_transfer_status = _tk.Label(inner, text="等待开始...",
                                                   font=("Microsoft YaHei UI", 8),
                                                   fg=C_TEXT_SEC,
                                                   wraplength=700, justify=LEFT,
                                                   bg=C_WHITE)
        self.tk_label_transfer_status.pack(fill=X, pady=(0, 8))

        # 总进度条
        _tk.Label(inner, text="总进度",
                  font=("Microsoft YaHei UI", 8, "bold"),
                  fg=C_TEXT, bg=C_WHITE).pack(anchor=W)
        self.tk_progress_bar = ttk.Progressbar(inner, mode="determinate",
                                                maximum=100, value=0, bootstyle="success")
        self.tk_progress_bar.pack(fill=X, pady=(2, 8))

        # 分区进度条
        _tk.Label(inner, text="当前分区进度",
                  font=("Microsoft YaHei UI", 8, "bold"),
                  fg=C_TEXT, bg=C_WHITE).pack(anchor=W)
        self.tk_file_progress_bar = ttk.Progressbar(inner, mode="determinate",
                                                     maximum=100, value=0, bootstyle="info")
        self.tk_file_progress_bar.pack(fill=X, pady=(2, 8))

        # 日志区域 - 深色背景
        log_header = _tk.Frame(inner, bg=C_WHITE)
        log_header.pack(fill=X, pady=(4, 2))
        _tk.Label(log_header, text="传输日志",
                  font=("Microsoft YaHei UI", 9, "bold"),
                  fg=C_TEXT, bg=C_WHITE).pack(side=LEFT)

        self.tk_text_mqg105ch = _tk.Text(inner, wrap=WORD, font=("Consolas", 8),
                                         bg=C_CONSOLE_BG, fg=C_CONSOLE_FG,
                                         bd=1, relief=SOLID,
                                         insertbackground=C_CONSOLE_FG,
                                         selectbackground="#404040")
        self.tk_text_mqg105ch.pack(fill=BOTH, expand=True)
        self.tk_text_mqg105ch.configure(state=NORMAL)

        scroll = ttk.Scrollbar(self.tk_text_mqg105ch, orient=VERTICAL,
                               bootstyle="dark-round")
        scroll.config(command=self.tk_text_mqg105ch.yview)
        self.tk_text_mqg105ch.configure(yscrollcommand=scroll.set)

    # ==================== 步骤 5: 文件校验 ====================

    def _build_step5_verify(self):
        page = _tk.Frame(self._content_frame, bg=C_WHITE)
        self._pages[5] = page

        inner = _tk.Frame(page, bg=C_WHITE)
        inner.pack(fill=BOTH, expand=True, padx=40, pady=20)

        _tk.Label(inner, text="数据校验",
                  font=("Microsoft YaHei UI", 14, "bold"),
                  fg=C_TEXT, bg=C_WHITE).pack(anchor=W, pady=(0, 4))

        _tk.Label(inner, text="传输完成后, 校验文件完整性",
                  font=("Microsoft YaHei UI", 9), fg=C_TEXT_SEC,
                  bg=C_WHITE).pack(anchor=W, pady=(0, 14))

        # ---- CSV 文件选择 ----
        csv_section = _tk.Frame(inner, bg=C_SIDEBAR_BG, padx=14, pady=12)
        csv_section.pack(fill=X, pady=(0, 10))

        _tk.Label(csv_section, text="校验清单 (FullFilelist_DEF.csv)",
                  font=("Microsoft YaHei UI", 9, "bold"),
                  fg=C_TEXT, bg=C_SIDEBAR_BG).pack(anchor=W, pady=(0, 6))

        csv_row = _tk.Frame(csv_section, bg=C_SIDEBAR_BG)
        csv_row.pack(fill=X)

        self.tk_entry_csv = ttk.Entry(csv_row, textvariable=self.csv_path_var,
                                       state="readonly",
                                       font=("Microsoft YaHei UI", 9), width=50)
        self.tk_entry_csv.pack(side=LEFT, padx=(0, 8))

        self.tk_button_browse_csv = ttk.Button(csv_row, text="浏览...", takefocus=False,
                                                width=10, bootstyle="secondary",
                                                command=self._on_browse_csv)
        self.tk_button_browse_csv.pack(side=LEFT)

        _tk.Label(csv_section, text="留空则自动识别最新 Appl 文件夹下的 CSV",
                  font=("Microsoft YaHei UI", 7),
                  fg=C_TEXT_MUTED, bg=C_SIDEBAR_BG).pack(anchor=W, pady=(6, 0))

        # ---- 校验按钮 ----
        self.tk_button_verify = ttk.Button(inner, text="开始校验", takefocus=False,
                                            bootstyle="success", width=16)
        self.tk_button_verify.pack(anchor=W, pady=(10, 12))

        # ---- 校验进度 ----
        self.tk_label_verify_progress = _tk.Label(inner, text="",
                                                   font=("Microsoft YaHei UI", 9),
                                                   fg=C_TEXT_SEC, bg=C_WHITE,
                                                   wraplength=700, justify=LEFT)
        self.tk_label_verify_progress.pack(anchor=W, pady=(0, 4))

        self.tk_verify_progress_bar = ttk.Progressbar(inner, mode="determinate",
                                                       maximum=100, value=0,
                                                       bootstyle="info")
        self.tk_verify_progress_bar.pack(fill=X, pady=(2, 10))

        # ---- 校验日志 ----
        log_header = _tk.Frame(inner, bg=C_WHITE)
        log_header.pack(fill=X, pady=(4, 2))
        _tk.Label(log_header, text="校验日志",
                  font=("Microsoft YaHei UI", 9, "bold"),
                  fg=C_TEXT, bg=C_WHITE).pack(side=LEFT)

        self.tk_text_verify_log = _tk.Text(inner, wrap=WORD, font=("Consolas", 8),
                                            bg=C_CONSOLE_BG, fg=C_CONSOLE_FG,
                                            bd=1, relief=SOLID,
                                            insertbackground=C_CONSOLE_FG,
                                            selectbackground="#404040",
                                            height=10)
        self.tk_text_verify_log.pack(fill=BOTH, expand=True)

        scroll = ttk.Scrollbar(self.tk_text_verify_log, orient=VERTICAL,
                               bootstyle="dark-round")
        scroll.config(command=self.tk_text_verify_log.yview)
        self.tk_text_verify_log.configure(yscrollcommand=scroll.set)
        self.tk_text_verify_log.configure(state=DISABLED)

    # ==================== 显示/隐藏方法 (兼容 control.py) ====================

    def show_discover(self):
        if hasattr(self, 'tk_label_discover'):
            self.tk_label_discover.pack(anchor=W, pady=(6, 4))
        if hasattr(self, 'tk_select_box_discover'):
            self.tk_select_box_discover.pack(fill=X, pady=(0, 6))

    def hide_discover(self):
        if hasattr(self, 'tk_label_discover'):
            self.tk_label_discover.pack_forget()
        if hasattr(self, 'tk_select_box_discover'):
            self.tk_select_box_discover.pack_forget()

    def show_src_connect(self):
        """显示发送方(旧设备)连接页面"""
        if hasattr(self, '_tgt_connect'):
            self._tgt_connect.pack_forget()
        if hasattr(self, '_src_connect'):
            self._src_connect.pack(fill=BOTH, expand=True)

    def show_tgt_connect(self):
        """显示接收方(新设备)连接页面"""
        if hasattr(self, '_src_connect'):
            self._src_connect.pack_forget()
        if hasattr(self, '_tgt_connect'):
            self._tgt_connect.pack(fill=BOTH, expand=True)

    def hide_connect_panels(self):
        """隐藏所有连接面板"""
        for attr in ('_src_connect', '_tgt_connect'):
            if hasattr(self, attr):
                getattr(self, attr).pack_forget()

    def show_dhcp(self):
        if hasattr(self, 'tk_button_dhcp'):
            self.tk_button_dhcp.pack(anchor=W, pady=(0, 4),
                                     before=self.tk_label_dhcp_status)

    def hide_dhcp(self):
        if hasattr(self, 'tk_button_dhcp'):
            self.tk_button_dhcp.pack_forget()

    def show_auth_code(self, code: str):
        """发送端: 显示验证码 (步骤3连接页 + 步骤4传输页红色横幅)"""
        if hasattr(self, 'tk_label_auth_code'):
            self.tk_label_auth_code.config(text=code)
        if hasattr(self, 'tk_label_src_status'):
            self.tk_label_src_status.config(
                text=f"请在新设备上输入此验证码: {code}"
            )
        # 传输页面顶部红色验证码横幅 — 仅发送端显示
        if hasattr(self, 'tk_label_transfer_auth_code'):
            self.tk_label_transfer_auth_code.config(text=code)
        if hasattr(self, '_auth_banner_frame'):
            self._auth_banner_frame.pack(fill=X, pady=(0, 8))

    def hide_auth_code(self):
        """隐藏验证码横幅 (接收端调用)"""
        if hasattr(self, 'tk_label_auth_code'):
            self.tk_label_auth_code.config(text="----")
        if hasattr(self, 'tk_label_src_status'):
            self.tk_label_src_status.config(text="验证码: ---- 等待目标设备连接...")
        if hasattr(self, 'tk_label_transfer_auth_code'):
            self.tk_label_transfer_auth_code.config(text="----")
        if hasattr(self, '_auth_banner_frame'):
            self._auth_banner_frame.pack_forget()

    def show_auth_input(self):
        pass

    def hide_auth_input(self):
        pass

    def show_manual_ip(self):
        if hasattr(self, 'tk_check_advanced'):
            self.tk_check_advanced.pack(anchor=W, pady=(12, 0))

    def hide_manual_ip(self):
        if hasattr(self, 'tk_check_advanced'):
            self.tk_check_advanced.pack_forget()
        if hasattr(self, '_advanced_frame'):
            for w in self._advanced_frame.winfo_children():
                w.pack_forget()

    def show_csv_selector(self):
        pass

    def hide_csv_selector(self):
        pass

    def get_auth_input(self) -> str:
        try:
            return self.tk_entry_code.get().strip().upper()
        except Exception:
            return ""

    def set_auth_input(self, code: str):
        try:
            self.tk_entry_code.delete(0, "end")
            self.tk_entry_code.insert(0, code)
        except Exception:
            pass

    def get_csv_path(self) -> str:
        return self.csv_path_var.get().strip() if hasattr(self, 'csv_path_var') else ""

    def set_csv_path(self, path: str):
        if hasattr(self, 'csv_path_var'):
            self.csv_path_var.set(path)

    # ==================== 状态方法 ====================

    def set_step_state(self, step: int, state: str):
        if step < len(self._step_items):
            item = self._step_items[step]
            if state == "done":
                item["circle"].configure(fg=C_GREEN, bg=C_GREEN_BG)
                item["title"].configure(fg=C_GREEN)
            elif state == "active":
                item["circle"].configure(fg=C_WHITE, bg=C_BLUE)
                item["title"].configure(fg=C_BLUE)

    def set_status(self, text: str):
        """更新控制台/状态标签信息"""
        if hasattr(self, 'tk_label_src_status'):
            self.tk_label_src_status.config(text=text)

    def set_nic_ip_info(self, info_text: str):
        """更新步骤 2 高级设置中的 IP 状态信息"""
        if hasattr(self, 'tk_label_nic_ip'):
            self.tk_label_nic_ip.configure(text=info_text)

    def log(self, text: str):
        if hasattr(self, 'tk_text_mqg105ch'):
            self.tk_text_mqg105ch.configure(state=NORMAL)
            self.tk_text_mqg105ch.insert(END, text + "\n")
            self.tk_text_mqg105ch.see(END)
            self.tk_text_mqg105ch.configure(state=DISABLED)

    def clear_log(self):
        if hasattr(self, 'tk_text_mqg105ch'):
            self.tk_text_mqg105ch.configure(state=NORMAL)
            self.tk_text_mqg105ch.delete("1.0", END)
            self.tk_text_mqg105ch.configure(state=DISABLED)

    # ==================== 验证函数 ====================

    def _octet_validate(self, new_value: str) -> bool:
        if new_value == "":
            return True
        if not new_value.isdigit():
            return False
        if len(new_value) > 3:
            return False
        if int(new_value) > 255:
            return False
        return True

    def _auth_validate(self, new_value: str) -> bool:
        if len(new_value) > 4:
            return False
        return all(c.isalnum() or c.isalpha() for c in new_value)


# ==================== 工具函数 ====================

def _bind_card_hover(card: _tk.Frame, hover_color: str):
    def _apply(color):
        card.configure(bg=color)
        for child in card.winfo_children():
            if isinstance(child, _tk.Label):
                try:
                    child.configure(bg=color)
                except Exception:
                    pass

    card.bind("<Enter>", lambda e: _apply(hover_color))
    card.bind("<Leave>", lambda e: _apply(C_WHITE))


class Win(WinGUI):
    def __init__(self, controller):
        self.ctl = controller
        super().__init__()
        self.__style_config()
        self.ctl.init(self)

    def __style_config(self):
        style = ttk.Style()
        default_font = ("Microsoft YaHei UI", 9)
        style.configure(".", font=default_font)
        style.configure("TLabel", font=default_font)
        style.configure("TCombobox", font=default_font)
        style.configure("TButton", font=("Microsoft YaHei UI", 9))
        style.configure("TCheckbutton", font=default_font)
        style.configure("TEntry", font=default_font)
        style.configure("success.Horizontal.TProgressbar", background=C_GREEN)
        style.configure("info.Horizontal.TProgressbar", background=C_BLUE)


if __name__ == "__main__":
    win = WinGUI()
    win.mainloop()
