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
C_INFO_BG    = "#E3F2FD"   # 信息提示背景 (浅蓝)


class WinGUI(ttk.Window):
    def __init__(self):
        super().__init__(themename="flatly")
        self.__win()
        self._octet_vcmd = (self.register(self._octet_validate), "%P")
        self._auth_vcmd = (self.register(self._auth_validate), "%P")
        self._build_layout()
        self._step = 0
        self._total_steps = 7  # 0=角色 1=网卡 2=磁盘 3=连接 4=传输 5=导入配置 6=校验
        self._device_type = None  # "source" or "target"
        self._role_display = "未选择"

    def __win(self):
        self.title("数据迁移工具")
        width = 1300
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
        self._build_step5_config_import()
        self._build_step6_verify()

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
            ("4", "传输 & 数据校验", "文件传输 · 配置导入 · 完整性校验"),
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
        map_ui_to_side = {0: 0, 1: 0, 2: 1, 3: 2, 4: 3, 5: 3, 6: 3}
        side_current = map_ui_to_side.get(current, 0)

        desc_map = {
            0: ("选择发送方或接收方", "请选择[旧设备(发送方)]或[新设备(接收方)]"),
            1: ("已选择直连网卡", "请选择设备类型"),
            2: ("高级设置 — 磁盘 / 分区 / IP", "确认磁盘和分区映射，查看网络配置"),
            3: ("准备连接", "启动服务，输入验证码并开始传输"),
            4: ("传输进度", "文件正在传输中..."),
            5: ("导入系统配置", "将旧设备的配置导入新设备"),
            6: ("数据校验", "校验已传输文件的完整性"),
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
            if not self.tk_button_prev.winfo_ismapped():
                self.tk_button_prev.pack(side=LEFT, padx=(0, 8))
        self.tk_button_prev.configure(text=text)

    def set_button_next(self, state: str, text: str = "下一步 >"):
        # 仅禁用/启用, 不隐藏按钮 — 保持按钮始终可见 (灰色 = 禁用), 标准 UX
        if state == "disabled":
            self.tk_button_next.configure(state=DISABLED)
        else:
            # 同时使用 configure(state=NORMAL) 和 state(["!disabled"]) 确保
            # 在 ttkbootstrap 和标准 ttk 下都能正确清除禁用标志
            self.tk_button_next.configure(state=NORMAL)
            try:
                self.tk_button_next.state(["!disabled"])
            except Exception:
                pass  # 某些 ttk 版本不支持 state()
            self.tk_button_next.configure(text=text)
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
        # 上一步按钮: step 0 禁用, 其他步骤启用
        if step == 0:
            self.set_button_prev("disabled")
        else:
            self.set_button_prev("normal")
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
        # 步骤 4: 发送端传输页 — 重新断言验证码横幅可见
        # (页面 pack_forget 切换后可能丢失横幅显示, 这里按需重新 pack)
        if step == 4:
            ctl_type = getattr(getattr(self, 'ctl', None), '_device_type', '')
            if ctl_type == "源设备" or self._device_type == "source":
                code = getattr(getattr(self, 'ctl', None), '_auth_code', '') or ''
                self.show_auth_code(code)
        # 根据步骤设置「下一步」按钮默认状态
        transfer_done = getattr(getattr(self, 'ctl', None), '_transfer_done', False)
        is_target = getattr(getattr(self, 'ctl', None), '_device_type', '') == "目标设备"
        if step == 3:
            self.set_button_next("disabled")
        elif step == 6:  # step 6 (校验) 是最后一步: 下一步 = 跳过校验
            self.set_button_next("normal", text="跳过校验 >")
        elif step >= self._total_steps - 1:  # 越界保护
            self.set_button_next("disabled")
        elif step == 4:
            if transfer_done:
                if is_target:
                    self.set_button_next("normal", text="导入配置 >")  # → step 5
                else:
                    self.set_button_next("normal", text="校验文件 >")  # → step 6 (跳过导入)
            else:
                self.set_button_next("disabled")
        elif step == 5:
            self.set_button_next("normal", text="校验文件 >")
        else:
            # step 0/1/2: 显式管理按钮状态, 避免仅依赖回调
            if step == 0:
                self.set_button_next("disabled")
            elif step == 1:
                nic_ok = (self.tk_select_box_mqfzkd6x.get() not in
                          ("扫描中...", "未检测到网卡", "", "网卡1", "网卡2"))
                if nic_ok:
                    self.set_button_next("normal")
                else:
                    self.set_button_next("disabled")
            # step 2: 由 controller 回调控制(依赖磁盘选择)
        # 更新按钮状态
        if hasattr(self.ctl, '_check_button_state'):
            self.ctl._check_button_state()
        # step 0 已在上面同步设置按钮状态, 无需额外异步确认

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
        if hasattr(self, 'ctl') and hasattr(self.ctl, '_on_role_selected'):
            self.ctl._on_role_selected(role)

    # ==================== 步骤 1: 网络设置 & 配置导出 ====================

    def _build_step1_nic(self):
        page = _tk.Frame(self._content_frame, bg=C_WHITE)
        self._pages[1] = page

        inner = _tk.Frame(page, bg=C_WHITE)
        inner.pack(fill=BOTH, expand=True, padx=40, pady=28)

        # ========== 发送端：系统配置导出 (主内容) ==========
        self._export_frame = _tk.Frame(inner, bg=C_WHITE)

        _tk.Label(self._export_frame, text="系统配置导出",
                  font=("Microsoft YaHei UI", 14, "bold"),
                  fg=C_TEXT, bg=C_WHITE).pack(anchor=W, pady=(0, 4))

        _tk.Label(self._export_frame,
                  text="导出旧设备的系统配置，\n"
                       "导出后将随 F 盘数据一起传输到新设备。",
                  font=("Microsoft YaHei UI", 9), fg=C_TEXT_SEC,
                  wraplength=700, justify=LEFT, bg=C_WHITE).pack(anchor=W, pady=(0, 12))

        # 按钮 + 显示日志复选框
        btn_row = _tk.Frame(self._export_frame, bg=C_WHITE)
        btn_row.pack(fill=X, pady=(0, 10))

        self.tk_button_export_config = ttk.Button(btn_row, text="导出系统配置",
                                                   takefocus=False,
                                                   bootstyle="info-outline", width=18)
        self.tk_button_export_config.pack(side=LEFT)

        self._export_show_log_var = _tk.BooleanVar(value=False)
        self.tk_check_export_log = ttk.Checkbutton(
            btn_row,
            text="显示详细日志",
            variable=self._export_show_log_var,
            command=self._toggle_export_log_window,
            takefocus=False,
        )
        self.tk_check_export_log.pack(side=LEFT, padx=(16, 0))

        _tk.Frame(self._export_frame, height=1, bg=C_SEP).pack(fill=X, pady=(12, 6))

        # 日志弹窗相关
        self._export_log_popup = None   # Toplevel 引用
        self._export_log_text = None    # 弹窗中的 Text 控件
        self._export_log_buffer = []    # 日志消息缓冲区

        # 表格标题
        _tk.Label(self._export_frame, text="导出项目",
                  font=("Microsoft YaHei UI", 9, "bold"),
                  fg=C_TEXT, bg=C_WHITE).pack(anchor=W, pady=(0, 4))

        # 全宽表格: 项目名称 | 状态 (固定高度 + 滚动条, 避免挤压下排按钮)
        tree_frame = _tk.Frame(self._export_frame, bg=C_WHITE, bd=1, relief=SOLID)
        tree_frame.pack(fill=X, pady=(0, 0))

        self.tk_table_export_status = ttk.Treeview(
            tree_frame,
            columns=("item", "status"),
            show="headings",
            height=6,
            selectmode="none",
        )
        self.tk_table_export_status.heading("item", text="导出项目")
        self.tk_table_export_status.column("item", minwidth=120, stretch=True)
        self.tk_table_export_status.heading("status", text="状态")
        self.tk_table_export_status.column("status", width=80, anchor="center", stretch=False)

        table_scroll = ttk.Scrollbar(tree_frame, orient=VERTICAL,
                                     command=self.tk_table_export_status.yview)
        self.tk_table_export_status.configure(yscrollcommand=table_scroll.set)
        self.tk_table_export_status.pack(side=LEFT, fill=BOTH, expand=True)
        table_scroll.pack(side=RIGHT, fill=Y)

        # 状态标签配色
        self.tk_table_export_status.tag_configure("pending", foreground=C_TEXT_MUTED)
        self.tk_table_export_status.tag_configure("success", foreground=C_GREEN)
        self.tk_table_export_status.tag_configure("fail", foreground=C_RED)
        self.tk_table_export_status.tag_configure("skip", foreground="#FF9800")

        # 初始化表格数据
        self._init_export_table()

        # ========== 接收端：DHCP 连接设置 (主内容) ==========
        self._target_info_frame = _tk.Frame(inner, bg=C_WHITE)

        _tk.Label(self._target_info_frame, text="网络设置",
                  font=("Microsoft YaHei UI", 14, "bold"),
                  fg=C_TEXT, bg=C_WHITE).pack(anchor=W, pady=(0, 4))

        _tk.Label(self._target_info_frame,
                  text="现在请先点击‘寻找旧电脑’然后用网线将新电脑与旧电脑互相连接\n"
                       "如果长时间没有响应，请插拔新电脑端的网线",
                  font=("Microsoft YaHei UI", 9), fg=C_TEXT_SEC,
                  wraplength=700, justify=LEFT, bg=C_WHITE).pack(anchor=W, pady=(0, 16))

        # ---- DHCP 寻找旧设备 ----
        self.tk_button_dhcp = ttk.Button(self._target_info_frame,
                                         text="寻找旧电脑", takefocus=False,
                                         bootstyle="primary", width=16)
        self.tk_button_dhcp.pack(anchor=W, pady=(0, 4))

        self.tk_label_dhcp_status = _tk.Label(self._target_info_frame, text="",
                                              font=("Microsoft YaHei UI", 8),
                                              fg=C_TEXT_SEC,
                                              wraplength=700, justify=LEFT,
                                              bg=C_WHITE)
        self.tk_label_dhcp_status.pack(fill=X, pady=(0, 6))

        # ---- 发现的设备 ----
        self.tk_label_discover = _tk.Label(self._target_info_frame, text="发现的设备",
                                           font=("Microsoft YaHei UI", 9, "bold"),
                                           fg=C_TEXT, bg=C_WHITE)
        self.tk_label_discover.pack(anchor=W, pady=(6, 4))

        self.tk_select_box_discover = ttk.Combobox(self._target_info_frame, state="readonly",
                                                    font=("Microsoft YaHei UI", 9))
        self.tk_select_box_discover['values'] = ("等待 旧电脑 响应...",)
        self.tk_select_box_discover.set("等待 旧电脑 响应...")
        self.tk_select_box_discover.pack(fill=X, pady=(0, 6))

        # ---- 高级: 手动 IP ----
        self.tk_var_advanced = _tk.BooleanVar(value=False)
        self.tk_check_advanced = ttk.Checkbutton(
            self._target_info_frame, text="高级: 手动输入 IP 地址",
            variable=self.tk_var_advanced,
            bootstyle="secondary",
            command=self._toggle_advanced_ip
        )

        self._advanced_frame = _tk.Frame(self._target_info_frame, bg=C_SIDEBAR_BG, padx=10, pady=8)

        _tk.Label(self._advanced_frame, text="源设备 (旧电脑) IP 地址:",
                  font=("Microsoft YaHei UI", 8), fg=C_TEXT_SEC,
                  bg=C_SIDEBAR_BG).pack(side=LEFT, padx=(0, 4))
        self.tk_entry_const = ttk.Entry(self._advanced_frame, width=16,
                                         font=("Microsoft YaHei UI", 8))
        self.tk_entry_const.pack(side=LEFT, padx=(0, 4))

        # ========== 高级选项（折叠面板：网卡信息 + 手动选择）==========
        self._advanced_nic_toggle_btn = ttk.Button(inner, text="高级选项 ▸",
                                                   takefocus=False,
                                                   bootstyle="link",
                                                   command=self._toggle_advanced_nic)
        self._advanced_nic_toggle_btn.pack(anchor=W, pady=(0, 8))

        # 高级选项内容面板 (默认隐藏)
        self._advanced_nic_frame = _tk.Frame(inner, bg=C_WHITE)

        # 高级选项内：自动检测网卡详情卡片 (绿色背景)
        self._auto_nic_frame = _tk.Frame(self._advanced_nic_frame, bg="#E8F5E9",
                                         bd=1, relief=SOLID)
        self._auto_nic_frame.pack(fill=X, pady=(0, 12))

        self.tk_label_auto_nic_detail = _tk.Label(self._auto_nic_frame,
                                                  text="正在检测有线网卡...",
                                                  font=("Microsoft YaHei UI", 10),
                                                  fg=C_TEXT, bg="#E8F5E9",
                                                  wraplength=650, justify=LEFT)
        self.tk_label_auto_nic_detail.pack(fill=X, padx=12, pady=8)

        _tk.Label(self._advanced_nic_frame, text="手动选择网卡",
                  font=("Microsoft YaHei UI", 9, "bold"),
                  fg=C_TEXT, bg=C_WHITE).pack(anchor=W, pady=(0, 4))

        _tk.Label(self._advanced_nic_frame, text="网卡优先级: USB 网卡 > 169.254 网段 > 内置网卡",
                  font=("Microsoft YaHei UI", 8), fg=C_TEXT_MUTED,
                  bg=C_WHITE).pack(anchor=W, pady=(0, 8))

        self.tk_select_box_mqfzkd6x = ttk.Combobox(self._advanced_nic_frame, state="readonly",
                                                    font=("Microsoft YaHei UI", 9))
        self.tk_select_box_mqfzkd6x['values'] = ("扫描中...",)
        self.tk_select_box_mqfzkd6x.pack(fill=X, pady=(0, 10))

        self.tk_label_nic_detail = _tk.Label(self._advanced_nic_frame, text="",
                                             font=("Microsoft YaHei UI", 8),
                                             fg=C_TEXT_SEC, bg=C_WHITE,
                                             wraplength=700, justify=LEFT)
        self.tk_label_nic_detail.pack(fill=X, pady=(0, 12))

        # ---- 初始状态: 两个主内容框架均隐藏 ----
        self._export_frame.pack(fill=BOTH, expand=True)
        self._export_frame.pack_forget()
        self._target_info_frame.pack(fill=BOTH, expand=True)
        self._target_info_frame.pack_forget()

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



    def _toggle_partition_map(self):
        """显示/隐藏分区盘符映射高级选项"""
        if self.tk_show_partition_map.get():
            self._partition_map_frame.pack(fill=X, after=self.tk_cb_partition_map, pady=(4, 0))
        else:
            self._partition_map_frame.pack_forget()

    def show_config_detect(self, config_path: str, time_str: str):
        """步骤 4 接收端: 显示配置检测提示区域 (位于控制台日志上方)"""
        info = f"在 F 盘发现系统配置文件 (F:\\systemconfig.ini)"
        if time_str:
            info += f"\n导出时间: {time_str}"
        info += f"\n配置路径: {config_path}"
        info += "\n是否使用此配置文件进行导入？"
        self.tk_label_config_detect.config(text=info)
        # 配置检测区域需放在日志区上方; 若日志区当前折叠, 先展开
        if hasattr(self, '_transfer_log_frame'):
            if not self._transfer_log_frame.winfo_ismapped():
                self._transfer_log_show_var.set(True)
                self._transfer_log_frame.pack(fill=BOTH, expand=True)
            self._config_detect_frame.pack(fill=X, pady=(0, 10),
                                           before=self._transfer_log_frame)
        else:
            self._config_detect_frame.pack(fill=X, pady=(0, 10))

    def hide_config_detect(self):
        """隐藏步骤 4 的配置检测提示区域"""
        if hasattr(self, '_config_detect_frame'):
            self._config_detect_frame.pack_forget()

    def show_transfer_error(self, message: str):
        """步骤 4: 显示传输错误提示（红色醒目区域）
        同时将传输状态标签改为红色错误文本"""
        self.tk_label_transfer_error.config(text=message)
        # 错误区放在"显示详细日志"复选框上方 (进度条已隐藏, 不再作为锚点)
        if hasattr(self, '_transfer_error_frame'):
            if hasattr(self, 'tk_check_transfer_log'):
                self._transfer_error_frame.pack(fill=X, pady=(0, 10),
                                                before=self.tk_check_transfer_log)
            else:
                self._transfer_error_frame.pack(fill=X, pady=(0, 10))
        # 状态标签也变红
        self.tk_label_transfer_status.config(
            text="传输失败 — 请返回上一步检查后重试",
            fg=C_RED,
            font=("Microsoft YaHei UI", 9, "bold"),
        )

    def hide_transfer_error(self):
        """隐藏步骤 4 的传输错误提示"""
        if hasattr(self, '_transfer_error_frame'):
            self._transfer_error_frame.pack_forget()
        self.tk_label_transfer_status.config(
            text="等待开始...",
            fg=C_TEXT_SEC,
            font=("Microsoft YaHei UI", 8),
        )
        if hasattr(self, 'tk_label_verify_online'):
            self.tk_label_verify_online.config(text="边传边校验：等待中")

    def set_verify_online_status(self, text: str):
        """步骤 4: 更新"边传边校验"状态行文本 (由 control.py 通过 after 在主线程调用)"""
        try:
            if hasattr(self, 'tk_label_verify_online'):
                self.tk_label_verify_online.config(text=text)
        except Exception:
            pass

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

        _tk.Label(self._src_connect, text="点击下方按钮启动传输",
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
                                             text="验证码: ----",
                                             font=("Microsoft YaHei UI", 8),
                                             fg=C_TEXT_MUTED, bg=C_WHITE)
        self.tk_label_src_status.pack(anchor=W, pady=(6, 0))

        # ---- 接收方 (新电脑) 页面 ----
        self._tgt_connect = _tk.Frame(inner, bg=C_WHITE)

        _tk.Label(self._tgt_connect, text="接收方 - 连接验证码",
                  font=("Microsoft YaHei UI", 14, "bold"),
                  fg=C_BLUE, bg=C_WHITE).pack(anchor=W, pady=(0, 12))

        _tk.Label(self._tgt_connect,
                  text="请确认你已经选择了旧设备，\n然后输入旧设备上显示的连接验证码:",
                  font=("Microsoft YaHei UI", 9), fg=C_TEXT_SEC,
                  wraplength=700, justify=LEFT, bg=C_WHITE).pack(anchor=W, pady=(0, 12))

        # 验证码输入区 (居中、放大)
        code_section = _tk.Frame(self._tgt_connect, bg=C_WHITE)
        code_section.pack(fill=X, pady=(20, 4))

        _tk.Label(code_section, text="请输入旧电脑上显示的连接验证码:",
                  font=("Microsoft YaHei UI", 10, "bold"),
                  fg=C_TEXT, bg=C_WHITE).pack(anchor=CENTER)

        self.tk_entry_code = ttk.Entry(code_section, font=("Consolas", 20, "bold"),
                                       justify=CENTER, width=10,
                                       validate="key", validatecommand=self._auth_vcmd)
        self.tk_entry_code.pack(pady=(12, 0))

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
        self.tk_label_transfer_status.pack(fill=X, pady=(0, 4))

        # 传输错误提示区 (默认隐藏，验证码错误/传输失败时显示)
        self._transfer_error_frame = _tk.Frame(inner, bg=C_RED_BG, padx=14, pady=10)
        self.tk_label_transfer_error = _tk.Label(
            self._transfer_error_frame,
            text="",
            font=("Microsoft YaHei UI", 10, "bold"),
            fg=C_RED, bg=C_RED_BG,
            wraplength=680, justify=LEFT,
        )
        self.tk_label_transfer_error.pack(fill=X)
        # 不在此处 pack frame — 由 show_transfer_error() 按需显示

        # 提示文字: 传输期间不可操作
        self.tk_label_transfer_hint = _tk.Label(
            inner, text="在数据拷贝期间不可操作电脑上的任何文档",
            font=("Microsoft YaHei UI", 10, "bold"),
            fg="#E65100", bg=C_WHITE,
            wraplength=700, justify=LEFT,
        )
        self.tk_label_transfer_hint.pack(fill=X, pady=(0, 8))

        # 总进度条 (保留显示, 避免页面过于空旷)
        self.tk_label_transfer_total = _tk.Label(
            inner, text="总进度",
            font=("Microsoft YaHei UI", 8, "bold"),
            fg=C_TEXT, bg=C_WHITE,
        )
        self.tk_label_transfer_total.pack(anchor=W)
        self.tk_progress_bar = ttk.Progressbar(inner, mode="determinate",
                                                maximum=100, value=0, bootstyle="success")
        self.tk_progress_bar.pack(fill=X, pady=(2, 8))

        # 边传边校验状态行: 文件传输完成后由独立线程立即复核"存在+大小", 实时展示确认进度
        self.tk_label_verify_online = _tk.Label(
            inner, text="边传边校验：等待中",
            font=("Microsoft YaHei UI", 8),
            fg=C_TEXT_SEC, bg=C_WHITE,
            anchor=W,
        )
        self.tk_label_verify_online.pack(fill=X, pady=(0, 6))

        # 当前分区进度条 (隐藏不显示, 仅保留供 control.py 更新)
        self.tk_file_progress_bar = ttk.Progressbar(inner, mode="determinate",
                                                     maximum=100, value=0, bootstyle="info")

        # ---- 详细日志折叠区 (勾选"显示详细日志"后才显示) ----
        self._transfer_log_frame = _tk.Frame(inner, bg=C_WHITE)

        log_header = _tk.Frame(self._transfer_log_frame, bg=C_WHITE)
        log_header.pack(fill=X, pady=(4, 2))
        _tk.Label(log_header, text="传输日志",
                  font=("Microsoft YaHei UI", 9, "bold"),
                  fg=C_TEXT, bg=C_WHITE).pack(side=LEFT)

        self.tk_text_mqg105ch = _tk.Text(self._transfer_log_frame, wrap=WORD,
                                         font=("Consolas", 8),
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

        # "显示详细日志"复选框 (默认不显示, 点击后才展开日志区)
        self._transfer_log_show_var = _tk.BooleanVar(value=False)
        self.tk_check_transfer_log = ttk.Checkbutton(
            inner, text="显示详细日志",
            variable=self._transfer_log_show_var,
            command=self._toggle_transfer_log,
            takefocus=False,
        )
        self.tk_check_transfer_log.pack(anchor=W, pady=(0, 4))

        # ---- 配置检测提示区 (接收端传输完成后显示, 默认隐藏) ----
        self._config_detect_frame = _tk.Frame(inner, bg=C_INFO_BG, padx=14, pady=10)

        _tk.Label(self._config_detect_frame, text="检测到系统配置文件",
                  font=("Microsoft YaHei UI", 9, "bold"),
                  fg=C_TEXT, bg=C_INFO_BG).pack(anchor=W)

        self.tk_label_config_detect = _tk.Label(
            self._config_detect_frame,
            text="",
            font=("Microsoft YaHei UI", 9),
            fg=C_TEXT, bg=C_INFO_BG,
            wraplength=680, justify=LEFT,
        )
        self.tk_label_config_detect.pack(anchor=W, pady=(2, 8))

        detect_btn_row = _tk.Frame(self._config_detect_frame, bg=C_INFO_BG)
        detect_btn_row.pack(fill=X)

        self.tk_button_use_config = ttk.Button(
            detect_btn_row, text="使用此配置", takefocus=False,
            bootstyle="success", width=14,
        )
        self.tk_button_use_config.pack(side=LEFT, padx=(0, 10))

        self.tk_button_skip_config = ttk.Button(
            detect_btn_row, text="跳过", takefocus=False,
            bootstyle="secondary-outline", width=10,
        )
        self.tk_button_skip_config.pack(side=LEFT)

    # ==================== 步骤 5: 导入系统配置 ====================

    def _build_step5_config_import(self):
        page = _tk.Frame(self._content_frame, bg=C_WHITE)
        self._pages[5] = page

        inner = _tk.Frame(page, bg=C_WHITE)
        inner.pack(fill=BOTH, expand=True, padx=40, pady=20)

        _tk.Label(inner, text="导入系统配置",
                  font=("Microsoft YaHei UI", 14, "bold"),
                  fg=C_TEXT, bg=C_WHITE).pack(anchor=W, pady=(0, 4))

        _tk.Label(inner, text="从旧设备导出的系统配置（收藏夹、Outlook 规则、输入法等）导入到新设备",
                  font=("Microsoft YaHei UI", 9), fg=C_TEXT_SEC,
                  wraplength=700, justify=LEFT, bg=C_WHITE).pack(anchor=W, pady=(0, 14))

        # ---- 配置文件夹选择 ----
        folder_section = _tk.Frame(inner, bg=C_SIDEBAR_BG, padx=14, pady=12)
        folder_section.pack(fill=X, pady=(0, 10))

        _tk.Label(folder_section, text="选择配置文件夹",
                  font=("Microsoft YaHei UI", 9, "bold"),
                  fg=C_TEXT, bg=C_SIDEBAR_BG).pack(anchor=W, pady=(0, 6))

        folder_row = _tk.Frame(folder_section, bg=C_SIDEBAR_BG)
        folder_row.pack(fill=X)

        self.tk_combo_config_folder = ttk.Combobox(folder_row, state="readonly",
                                                     font=("Microsoft YaHei UI", 9), width=50)
        self.tk_combo_config_folder.pack(side=LEFT, padx=(0, 8))

        self.tk_button_browse_config = ttk.Button(folder_row, text="浏览...", takefocus=False,
                                                   width=10, bootstyle="secondary")
        self.tk_button_browse_config.pack(side=LEFT)

        self.tk_label_config_status = _tk.Label(folder_section, text="",
                                                font=("Microsoft YaHei UI", 8),
                                                fg=C_TEXT_SEC, bg=C_SIDEBAR_BG,
                                                wraplength=680, justify=LEFT)
        self.tk_label_config_status.pack(anchor=W, pady=(6, 0))

        # ---- 操作按钮 ----
        btn_row = _tk.Frame(inner, bg=C_WHITE)
        btn_row.pack(fill=X, pady=(10, 12))

        self.tk_button_import_config = ttk.Button(btn_row, text="导入配置", takefocus=False,
                                                   bootstyle="success", width=16)
        self.tk_button_import_config.pack(side=LEFT, padx=(0, 10))

        self.tk_button_skip_import = ttk.Button(btn_row, text="跳过", takefocus=False,
                                                 bootstyle="secondary-outline", width=10)
        self.tk_button_skip_import.pack(side=LEFT)

        self.tk_label_import_progress = _tk.Label(inner, text="",
                                                   font=("Microsoft YaHei UI", 9),
                                                   fg=C_TEXT_SEC, bg=C_WHITE,
                                                   wraplength=700, justify=LEFT)
        self.tk_label_import_progress.pack(anchor=W, pady=(0, 4))

        self.tk_import_progress_bar = ttk.Progressbar(inner, mode="indeterminate",
                                                       bootstyle="info")
        # 初始隐藏
        self.tk_import_progress_bar.pack(fill=X, pady=(2, 10))
        self.tk_import_progress_bar.pack_forget()

        # ---- 日志区域 ----
        log_header = _tk.Frame(inner, bg=C_WHITE)
        log_header.pack(fill=X, pady=(4, 2))
        _tk.Label(log_header, text="导入日志",
                  font=("Microsoft YaHei UI", 9, "bold"),
                  fg=C_TEXT, bg=C_WHITE).pack(side=LEFT)

        self.tk_text_import_log = _tk.Text(inner, wrap=WORD, font=("Consolas", 8),
                                            bg=C_CONSOLE_BG, fg=C_CONSOLE_FG,
                                            bd=1, relief=SOLID,
                                            insertbackground=C_CONSOLE_FG,
                                            selectbackground="#404040",
                                            height=10)
        self.tk_text_import_log.pack(fill=BOTH, expand=True)

        scroll = ttk.Scrollbar(self.tk_text_import_log, orient=VERTICAL,
                               bootstyle="dark-round")
        scroll.config(command=self.tk_text_import_log.yview)
        self.tk_text_import_log.configure(yscrollcommand=scroll.set)
        self.tk_text_import_log.configure(state=DISABLED)

    # ==================== 步骤 6: 文件校验 ====================

    def _build_step6_verify(self):
        page = _tk.Frame(self._content_frame, bg=C_WHITE)
        self._pages[6] = page

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

    # ==================== 导出状态表格方法 ====================

    _EXPORT_ITEMS = [
        "Outlook 邮件规则",
        "Outlook 配置文件",
        "Outlook 自动存档",
        "Chrome 收藏夹",
        "Edge 收藏夹",
        "打印机列表",
        "输入法设置",
        "网卡 IP 配置",
        "已安装程序 (注册表)",
        "磁盘分配单元估算",
        "开始菜单 App",
    ]

    def _init_export_table(self):
        """清空并重新填充导出状态表格 (初始状态: 等待中...)"""
        table = self.tk_table_export_status
        for item in table.get_children():
            table.delete(item)
        for name in self._EXPORT_ITEMS:
            table.insert("", "end", iid=name, values=(name, "等待中..."),
                         tags=("pending",))

    def _update_export_item_status(self, name, status):
        """更新导出项目在表格中的状态显示。
        status: "成功" | "失败" | "跳过"
        """
        table = self.tk_table_export_status
        tag_map = {"成功": "success", "失败": "fail", "跳过": "skip"}
        tag = tag_map.get(status, "pending")
        if table.exists(name):
            table.item(name, values=(name, status), tags=(tag,))

    # ==================== 导出日志弹窗管理 ====================

    def _toggle_transfer_log(self):
        """勾选/取消"显示详细日志" → 展开/折叠传输日志区"""
        if self._transfer_log_show_var.get():
            self._transfer_log_frame.pack(fill=BOTH, expand=True)
        else:
            self._transfer_log_frame.pack_forget()

    def _toggle_export_log_window(self):
        """勾选/取消"显示详细日志" → 打开/关闭日志弹窗"""
        if self._export_show_log_var.get():
            self._open_export_log_popup()
        else:
            self._close_export_log_popup()

    def _open_export_log_popup(self):
        """创建日志弹窗并回放缓冲区内容"""
        if self._export_log_popup is not None:
            return
        popup = _tk.Toplevel(self)
        popup.title("导出日志 — 详细输出")
        popup.geometry("640x420")
        popup.minsize(400, 250)
        popup.configure(bg=C_WHITE)
        popup.transient(self)
        popup.protocol("WM_DELETE_WINDOW", self._on_log_popup_close)

        text_frame = _tk.Frame(popup, bg=C_WHITE)
        text_frame.pack(fill=BOTH, expand=True, padx=10, pady=10)

        log_text = _tk.Text(text_frame, wrap=WORD,
                            font=("Consolas", 9),
                            bg=C_CONSOLE_BG, fg=C_CONSOLE_FG,
                            bd=1, relief=SOLID,
                            insertbackground=C_CONSOLE_FG,
                            selectbackground="#404040")
        log_text.pack(side=LEFT, fill=BOTH, expand=True)

        scroll = ttk.Scrollbar(text_frame, orient=VERTICAL,
                               command=log_text.yview,
                               bootstyle="dark-round")
        log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side=RIGHT, fill=Y)

        # 回放缓冲区
        log_text.config(state="normal")
        for msg in self._export_log_buffer:
            log_text.insert("end", msg + "\n")
        log_text.see("end")
        log_text.config(state="disabled")

        self._export_log_popup = popup
        self._export_log_text = log_text

    def _close_export_log_popup(self):
        """关闭日志弹窗"""
        if self._export_log_popup is not None:
            try:
                self._export_log_popup.destroy()
            except Exception:
                pass
        self._export_log_popup = None
        self._export_log_text = None

    def _on_log_popup_close(self):
        """用户点击弹窗关闭按钮 → 同步取消复选框"""
        self._close_export_log_popup()
        self._export_show_log_var.set(False)

    def _write_export_log(self, msg):
        """写入导出日志：追加到缓冲区，若弹窗打开则同步写入"""
        self._export_log_buffer.append(msg)
        log_text = self._export_log_text
        if log_text is not None:
            try:
                log_text.config(state="normal")
                log_text.insert("end", msg + "\n")
                log_text.see("end")
                log_text.config(state="disabled")
            except Exception:
                pass

    def _clear_export_log(self):
        """清空导出日志缓冲区和弹窗"""
        self._export_log_buffer.clear()
        log_text = self._export_log_text
        if log_text is not None:
            try:
                log_text.config(state="normal")
                log_text.delete("1.0", "end")
                log_text.config(state="disabled")
            except Exception:
                pass

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
            if not self.tk_button_dhcp.winfo_ismapped():
                self.tk_button_dhcp.pack(anchor=W, pady=(0, 4))

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
            # before= 固定横幅位置在状态标签之前 (紧接"传输中..."标题) —
            # 首次 pack 若发生在角色选择阶段 (此时步骤4其它控件已 pack 完),
            # 不带 before 会被排到页面底部(日志区下方)导致不可见
            if hasattr(self, 'tk_label_transfer_status'):
                self._auth_banner_frame.pack(
                    fill=X, pady=(0, 8), before=self.tk_label_transfer_status
                )
            else:
                self._auth_banner_frame.pack(fill=X, pady=(0, 8))

    def hide_auth_code(self):
        """隐藏验证码横幅 (接收端调用)"""
        if hasattr(self, 'tk_label_auth_code'):
            self.tk_label_auth_code.config(text="----")
        if hasattr(self, 'tk_label_src_status'):
            self.tk_label_src_status.config(text="验证码: ----")
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
            if not self.tk_check_advanced.winfo_ismapped():
                self.tk_check_advanced.pack(anchor=W, pady=(12, 0))

    def hide_manual_ip(self):
        if hasattr(self, 'tk_check_advanced'):
            self.tk_check_advanced.pack_forget()
        if hasattr(self, '_advanced_frame'):
            self._advanced_frame.pack_forget()

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

    def update_auto_nic_display(self, display_name: str, ip_addr: str, speed: str, wired_count: int):
        """更新步骤 1 高级选项内的自动检测网卡信息卡片"""
        speed_text = f" {speed}" if speed else ""
        ip_text = f"IP: {ip_addr}" if ip_addr else "IP: 未获取"
        if wired_count > 0:
            detail = (f"已自动检测到 {wired_count} 个有线网卡\n"
                      f"  首选: {display_name}{speed_text}  |  {ip_text}")
        else:
            detail = "未检测到有线网卡，请检查网线连接\n  可在「高级选项」中手动选择网卡"
        if hasattr(self, 'tk_label_auto_nic_detail'):
            self.tk_label_auto_nic_detail.configure(text=detail)

    def _toggle_advanced_nic(self):
        """展开/折叠步骤 1 的高级网卡选项面板"""
        if hasattr(self, '_advanced_nic_frame') and self._advanced_nic_frame.winfo_ismapped():
            self._advanced_nic_frame.pack_forget()
            if hasattr(self, '_advanced_nic_toggle_btn'):
                self._advanced_nic_toggle_btn.configure(text="高级选项 ▸")
        else:
            if hasattr(self, '_advanced_nic_frame'):
                self._advanced_nic_frame.pack(fill=X, after=self._advanced_nic_toggle_btn, pady=(0, 12))
                if hasattr(self, '_advanced_nic_toggle_btn'):
                    self._advanced_nic_toggle_btn.configure(text="高级选项 ▾")

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
