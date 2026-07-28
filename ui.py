"""
磁盘拷贝工具 GUI
基于 Tkinter/ttk 布局
"""
import tkinter as _tk
from tkinter import *
from tkinter.ttk import *


class WinGUI(Tk):
    def __init__(self):
        super().__init__()
        self.__win()
        # 注册 IP 每段 (0~255) 输入校验命令, 供 4 个文本框复用
        self._octet_vcmd = (self.register(self._octet_validate), "%P")
        # 注册验证码输入校验命令 (仅英文数字, 最多 4 位)
        self._auth_vcmd = (self.register(self._auth_validate), "%P")
        self.tk_label_mqfzd8tn = self.__tk_label_mqfzd8tn(self)
        self.tk_label_mqfzgrmh = self.__tk_label_mqfzgrmh(self)
        self.tk_select_box_mqfzkd6x = self.__tk_select_box_mqfzkd6x(self)
        self.tk_button_mqfzl35t = self.__tk_button_mqfzl35t(self)
        self.tk_select_box_mqfzmzbe = self.__tk_select_box_mqfzmzbe(self)
        self.tk_label_mqfzs0t6 = self.__tk_label_mqfzs0t6(self)
        self.tk_select_box_mqfzsdz4 = self.__tk_select_box_mqfzsdz4(self)
        self.tk_select_box_mqfzuo2y = self.__tk_select_box_mqfzuo2y(self)
        self.tk_select_box_mqfzwehm = self.__tk_select_box_mqfzwehm(self)
        self.tk_label_mqg0h1xg = self.__tk_label_mqg0h1xg(self)
        self.tk_select_box_mqg0hm2h = self.__tk_select_box_mqg0hm2h(self)
        self.tk_label_discover = self.__tk_label_discover(self)
        self.tk_select_box_discover = self.__tk_select_box_discover(self)
        self.tk_button_dhcp = self.__tk_button_dhcp(self)
        self.tk_label_ip_manual = self.__tk_label_ip_manual(self)
        self.ip_octet1_var = _tk.StringVar()
        self.tk_ip_octet1 = self.__tk_ip_octet(self, self.ip_octet1_var, 100)
        self.tk_label_dot1 = self.__tk_dot_label(self, 144)
        self.ip_octet2_var = _tk.StringVar()
        self.tk_ip_octet2 = self.__tk_ip_octet(self, self.ip_octet2_var, 156)
        self.tk_label_dot2 = self.__tk_dot_label(self, 200)
        self.ip_octet3_var = _tk.StringVar()
        self.tk_ip_octet3 = self.__tk_ip_octet(self, self.ip_octet3_var, 212)
        self.tk_label_dot3 = self.__tk_dot_label(self, 256)
        self.ip_octet4_var = _tk.StringVar()
        self.tk_ip_octet4 = self.__tk_ip_octet(self, self.ip_octet4_var, 268)
        self.tk_label_auth_title = self.__tk_label_auth_title(self)
        self.tk_label_auth_code = self.__tk_label_auth_code(self)
        # 目标设备: 验证码输入框 (源设备不需要输入, 只需显示)
        self.tk_label_auth_input_title = self.__tk_label_auth_input_title(self)
        self.tk_entry_auth_input = self.__tk_entry_auth_input(self)
        self.tk_label_status = self.__tk_label_status(self)
        self.tk_progress_bar = self.__tk_progress_bar(self)
        self.tk_file_progress_bar = self.__tk_file_progress_bar(self)
        self.tk_label_mqg0zfmi = self.__tk_label_mqg0zfmi(self)
        self.tk_text_mqg105ch = self.__tk_text_mqg105ch(self)

    def __win(self):
        self.title("磁盘拷贝工具 By ZhiyuChen")
        width = 640
        height = 640
        screenwidth = self.winfo_screenwidth()
        screenheight = self.winfo_screenheight()
        x = (screenwidth - width) // 2
        y = (screenheight - height) // 2
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.resizable(width=False, height=False)

    # ==================== 布局 ====================

    def __tk_label_mqfzd8tn(self, parent):
        label = Label(parent, text="选择网卡", anchor="w")
        label.place(x=20, y=18, width=80, height=24)
        return label

    def __tk_label_mqg0h1xg(self, parent):
        label = Label(parent, text="设备类型", anchor="w")
        label.place(x=340, y=18, width=80, height=24)
        return label

    def __tk_select_box_mqfzkd6x(self, parent):
        cb = Combobox(parent, state="readonly")
        cb['values'] = ("扫描中...",)
        cb.place(x=20, y=44, width=300, height=28)
        return cb

    def __tk_select_box_mqg0hm2h(self, parent):
        cb = Combobox(parent, state="readonly")
        cb['values'] = ("源设备", "目标设备")
        cb.place(x=340, y=44, width=140, height=28)
        return cb

    def __tk_label_mqfzgrmh(self, parent):
        label = Label(parent, text="选择磁盘", anchor="w")
        label.place(x=20, y=90, width=80, height=24)
        return label

    def __tk_label_mqfzs0t6(self, parent):
        label = Label(parent, text="PE 盘符映射:    D →          E →          F →", anchor="w")
        label.place(x=180, y=90, width=320, height=24)
        return label

    def __tk_select_box_mqfzmzbe(self, parent):
        cb = Combobox(parent, state="readonly")
        cb['values'] = ("请先选择设备类型",)
        cb.place(x=20, y=118, width=145, height=28)
        return cb

    def __tk_select_box_mqfzsdz4(self, parent):
        cb = Combobox(parent, state="readonly")
        cb['values'] = ()
        cb.place(x=220, y=118, width=55, height=28)
        return cb

    def __tk_select_box_mqfzuo2y(self, parent):
        cb = Combobox(parent, state="readonly")
        cb['values'] = ()
        cb.place(x=310, y=118, width=55, height=28)
        return cb

    def __tk_select_box_mqfzwehm(self, parent):
        cb = Combobox(parent, state="readonly")
        cb['values'] = ()
        cb.place(x=400, y=118, width=55, height=28)
        return cb

    def __tk_label_discover(self, parent):
        label = Label(parent, text="发现设备", anchor="w")
        label.place(x=20, y=165, width=80, height=24)
        return label

    def __tk_select_box_discover(self, parent):
        cb = Combobox(parent, state="readonly")
        cb['values'] = ("等待 DHCP 响应...",)
        cb.place(x=100, y=165, width=265, height=28)
        return cb

    def __tk_button_dhcp(self, parent):
        btn = Button(parent, text="开启DHCP", takefocus=False)
        btn.place(x=372, y=165, width=108, height=28)
        return btn

    def __tk_button_mqfzl35t(self, parent):
        btn = Button(parent, text="开始传输/接收", takefocus=False)
        btn.place(x=490, y=165, width=115, height=28)
        return btn

    def __tk_label_ip_manual(self, parent):
        label = Label(parent, text="源设备IP(手动):", anchor="w")
        label.place(x=20, y=195, width=80, height=24)
        return label

    def __tk_ip_octet(self, parent, var, x):
        entry = Entry(
            parent, textvariable=var, width=4, justify="center",
            validate="key", validatecommand=self._octet_vcmd,
        )
        entry.place(x=x, y=195, width=42, height=28)
        return entry

    def _octet_validate(self, new_value: str) -> bool:
        """限制手动 IP 每个文本框只能输入 0~255 的纯数字。

        允许空字符串 (便于删除/编辑); 拒绝非数字、超过 3 位、或数值 > 255。
        """
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
        """限制验证码输入框: 最多 4 位, 仅允许英文/数字 (大小写不敏感)。"""
        if len(new_value) > 4:
            return False
        return all(c.isalnum() for c in new_value)

    def __tk_dot_label(self, parent, x):
        label = Label(parent, text=".", font=("Microsoft YaHei UI", 11, "bold"))
        label.place(x=x, y=195, width=10, height=24)
        return label

    def __tk_label_auth_title(self, parent):
        label = Label(parent, text="连接验证码", anchor="e")
        label.place(x=340, y=195, width=80, height=28)
        return label

    def __tk_label_auth_code(self, parent):
        # 用原生 tk.Label 以便自定义大字体/前景/背景 (ttk.Label 受主题限制)
        label = _tk.Label(
            parent, text="----",
            font=("Consolas", 16, "bold"),
            fg="#D32F2F", bg="#FFF3E0",
            relief="groove", bd=1,
        )
        label.place(x=428, y=190, width=187, height=36)
        return label

    def __tk_label_auth_input_title(self, parent):
        label = _tk.Label(parent, text="连接验证码:", anchor="w",
                          font=("Microsoft YaHei UI", 12, "bold"))
        label.place(x=20, y=195, width=95, height=24)
        return label

    def __tk_entry_auth_input(self, parent):
        entry = Entry(parent, font=("Microsoft YaHei UI", 16, "bold"),
                      justify="center", validate="key",
                      validatecommand=self._auth_vcmd)
        entry.place(x=118, y=193, width=160, height=30)
        return entry

    def __tk_label_status(self, parent):
        label = Label(parent, text="就绪", anchor="w", foreground="#555555")
        label.place(x=20, y=235, width=595, height=20)
        return label

    def __tk_progress_bar(self, parent):
        pb = Progressbar(parent, mode="determinate", maximum=100, value=0)
        pb.place(x=20, y=260, width=595, height=22)
        return pb

    def __tk_file_progress_bar(self, parent):
        pb = Progressbar(parent, mode="determinate", maximum=100, value=0)
        pb.place(x=20, y=288, width=595, height=22)
        return pb

    def __tk_label_mqg0zfmi(self, parent):
        label = Label(parent, text="日志", anchor="w")
        label.place(x=20, y=318, width=50, height=24)
        return label

    def __tk_text_mqg105ch(self, parent):
        text = Text(parent)
        text.place(x=20, y=346, width=595, height=265)
        return text

    # ==================== 滚动条 ====================

    def scrollbar_autohide(self, vbar, hbar, widget):
        def show():
            if vbar: vbar.lift(widget)
            if hbar: hbar.lift(widget)
        def hide():
            if vbar: vbar.lower(widget)
            if hbar: hbar.lower(widget)
        hide()
        widget.bind("<Enter>", lambda e: show())
        if vbar: vbar.bind("<Enter>", lambda e: show())
        if vbar: vbar.bind("<Leave>", lambda e: hide())
        if hbar: hbar.bind("<Enter>", lambda e: show())
        if hbar: hbar.bind("<Leave>", lambda e: hide())
        widget.bind("<Leave>", lambda e: hide())

    def v_scrollbar(self, vbar, widget, x, y, w, h, pw, ph):
        widget.configure(yscrollcommand=vbar.set)
        vbar.config(command=widget.yview)
        vbar.place(relx=(w + x) / pw, rely=y / ph, relheight=h / ph, anchor='ne')

    def h_scrollbar(self, hbar, widget, x, y, w, h, pw, ph):
        widget.configure(xscrollcommand=hbar.set)
        hbar.config(command=widget.xview)
        hbar.place(relx=x / pw, rely=(y + h) / ph, relwidth=w / pw, anchor='sw')

    def create_bar(self, master, widget, is_vbar, is_hbar, x, y, w, h, pw, ph):
        vbar, hbar = None, None
        if is_vbar:
            vbar = Scrollbar(master)
            self.v_scrollbar(vbar, widget, x, y, w, h, pw, ph)
        if is_hbar:
            hbar = Scrollbar(master, orient="horizontal")
            self.h_scrollbar(hbar, widget, x, y, w, h, pw, ph)
        self.scrollbar_autohide(vbar, hbar, widget)


class Win(WinGUI):
    def __init__(self, controller):
        self.ctl = controller
        super().__init__()
        self.__style_config()
        self._add_log_scrollbar()
        self.ctl.init(self)

    def _add_log_scrollbar(self):
        vbar = Scrollbar(self)
        vbar.config(command=self.tk_text_mqg105ch.yview)
        self.tk_text_mqg105ch.configure(yscrollcommand=vbar.set)
        vbar.place(x=614, y=346, width=16, height=265)

    def __style_config(self):
        """统一字体和样式"""
        style = Style()
        default_font = ("Microsoft YaHei UI", 9)
        style.configure(".", font=default_font)
        style.configure("TLabel", font=default_font, padding=(2, 0))
        style.configure("TCombobox", font=default_font)
        style.configure("TButton", font=("Microsoft YaHei UI", 9, "bold"))

    def hide_discover(self):
        self.tk_label_discover.place_forget()
        self.tk_select_box_discover.place_forget()

    def show_discover(self):
        self.tk_label_discover.place(x=20, y=165, width=80, height=24)
        self.tk_select_box_discover.place(x=100, y=165, width=265, height=28)

    def hide_dhcp(self):
        self.tk_button_dhcp.place_forget()

    def show_dhcp(self):
        self.tk_button_dhcp.place(x=372, y=165, width=108, height=28)

    def show_auth_code(self, code: str):
        """在专用区域醒目显示连接验证码"""
        self.tk_label_auth_code.config(text=code)
        self.tk_label_auth_title.place(x=340, y=195, width=80, height=28)
        self.tk_label_auth_code.place(x=428, y=190, width=187, height=36)

    def hide_auth_code(self):
        self.tk_label_auth_code.config(text="----")
        self.tk_label_auth_title.place_forget()
        self.tk_label_auth_code.place_forget()

    # ---- 手动 IP (源设备不需要, 默认隐藏) ----
    def hide_manual_ip(self):
        self.tk_label_ip_manual.place_forget()
        self.tk_ip_octet1.place_forget()
        self.tk_label_dot1.place_forget()
        self.tk_ip_octet2.place_forget()
        self.tk_label_dot2.place_forget()
        self.tk_ip_octet3.place_forget()
        self.tk_label_dot3.place_forget()
        self.tk_ip_octet4.place_forget()

    def show_manual_ip(self):
        self.tk_label_ip_manual.place(x=20, y=195, width=80, height=24)
        self.tk_ip_octet1.place(x=100, y=193, width=40, height=28)
        self.tk_label_dot1.place(x=144, y=195, width=10, height=24)
        self.tk_ip_octet2.place(x=156, y=193, width=40, height=28)
        self.tk_label_dot2.place(x=200, y=195, width=10, height=24)
        self.tk_ip_octet3.place(x=212, y=193, width=40, height=28)
        self.tk_label_dot3.place(x=256, y=195, width=10, height=24)
        self.tk_ip_octet4.place(x=268, y=193, width=40, height=28)

    # ---- 目标设备验证码输入框 ----
    def show_auth_input(self):
        self.tk_label_auth_input_title.place(x=20, y=195, width=95, height=24)
        self.tk_entry_auth_input.place(x=118, y=193, width=160, height=30)

    def hide_auth_input(self):
        self.tk_label_auth_input_title.place_forget()
        self.tk_entry_auth_input.place_forget()

    def get_auth_input(self) -> str:
        return self.tk_entry_auth_input.get()

    def set_auth_input(self, code: str):
        self.tk_entry_auth_input.delete(0, "end")
        self.tk_entry_auth_input.insert(0, code)


if __name__ == "__main__":
    win = WinGUI()
    win.mainloop()
