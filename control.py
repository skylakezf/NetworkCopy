"""
控制器 - 连接 UI 与所有业务模块
Phase 1-5 全部集成
"""
import threading
import time
import socket
import subprocess
import os
import queue
from typing import Literal
from tkinter import simpledialog
import tkinter as tk
from nic_scanner import scan_nics, get_nic_display_list, get_local_ip, get_wired_adapters
from disk_scanner import get_disk_list, get_drive_letter_list, get_disk_number, get_partition_count, get_partition_details
from file_transfer import FileServer, FileServerHandler, download_files, scan_source_device, TRANSFER_PORT, _allow_sleep
from verifier import run_verification
from ip_config import SOURCE_IP, SUBNET_MASK  # 169.254.100.1 (目标设备自身 IP)
import tls_utils
import config_transfer
DHCP_ASSIGNED_IP = "169.254.100.2"  # DHCP 分配给源设备的 IP

# 源端判定"接收端已断开"的空闲超时 (秒):
# 接收端传输期间每 1 秒上报心跳 (last_activity 持续刷新); 下载完成后到 /report done
# 之间最长约 30 秒 (边传边校验收尾 join), 取 45 秒留足余量, 避免正常收尾被误判为断开
SOURCE_IDLE_TIMEOUT = 45.0

# ==================== 传输异常提示文案 ====================
# 规则:
#   1) 状态行 (tk_label_transfer_status) 与红色错误区必须讲同一件事 —— 不允许出现
#      "状态行说请修改验证码 / 错误区说网络中断" 这类自相矛盾;
#   2) 文案里提到的操作必须是当前界面真实可点的 (传输页 step3 的"开始传输/开始接收"
#      按钮是隐藏的, 因此统一引导用户点左下角「< 上一步」返回后重试);
#   3) 网络中断时源端必须隐藏验证码横幅, 否则会出现"上方让输入验证码 / 下方又说
#      网络已中断" 的矛盾提示。
NET_LOST_TITLE = "网络连接已中断，传输未完成"
NET_LOST_MSG_SRC = (
    "网络连接已中断，传输未完成。请检查两台设备之间的网线是否插紧"
    "（网口指示灯是否亮起）；确认连接恢复后，点击左下角「< 上一步」返回，"
    "重新点击「开始传输」即可。若反复中断，请依次关闭新旧设备上的本程序后重新运行。"
)
NET_LOST_MSG_TGT = (
    "网络连接已中断，传输未完成。请检查两台设备之间的网线是否插紧"
    "（网口指示灯是否亮起）；确认连接恢复后，点击左下角「< 上一步」返回，"
    "重新点击「开始接收」即可。若反复中断，请依次关闭新旧设备上的本程序后重新运行。"
)
# 验证码错误 (源端视角: 对方填错, 本机无需操作, 继续等待)
AUTH_FAIL_TITLE = "等待新设备重新输入正确的验证码"
AUTH_FAIL_MSG_SRC = (
    "验证码错误：新设备填写的验证码与本机显示的不一致。请在新设备上核对本机显示的 "
    "4 位验证码（字母不区分大小写）后重新输入并点击「开始接收」。"
    "本机无需任何操作，会继续等待新设备连接。"
)
# 验证码错误 (接收端视角: 本机填错, 需返回修改)
AUTH_FAIL_MSG_TGT = (
    "验证码错误：请填写旧设备（发送端）屏幕上显示的 4 位验证码"
    "（字母不区分大小写），再重新点击「开始接收」。"
)
# 传输中途失败 (部分文件未传完, 可能由网络中断引起)
TRANSFER_INCOMPLETE_TITLE = "传输未完成，请检查网络后重新接收"
TRANSFER_INCOMPLETE_MSG = (
    "传输未完成：部分文件未能传输完毕。请检查两台设备之间的网线是否插紧"
    "（网口指示灯是否亮起），确认后点击左下角「< 上一步」返回，重新点击「开始接收」。"
)

# ==================== 用户协议 (EULA) ====================
EULA_URL = "http://qitv1113.gtmcl.com:3000/eula.html"  # 应用启动后用默认浏览器打开

# Tk 非线程安全: 后台线程对 widget 的任何写操作必须经此锁串行化,
# 否则多个线程并发调用 insert/after 会竞争 Tcl 内部锁导致界面死锁 (测试环境曾复现卡死)
_TK_LOCK = threading.Lock()


def _open_eula_url_silent(url: str):
    """后台线程: 用默认浏览器打开 URL, 任何异常都静默吞掉 (daemon 线程不报错)"""
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass


def _nic_priority_key(nic):
    """NIC 排序键: USB > 169.254 网段 > 内置网卡"""
    name = nic[0].lower()
    ip = nic[1] if len(nic) > 1 else ""
    if "usb" in name:
        return 0
    if ip.startswith("169.254"):
        return 1
    return 2


def is_running_in_winpe() -> bool:
    """检测当前是否运行在 Windows PE (WinPE) 环境。

    判定依据 (任一满足即视为 PE):
      1. 注册表 HKLM\\SYSTEM\\CurrentControlSet\\Control\\MiniNT 存在 (PE 标志键);
      2. 存在 X:\\Windows\\System32 (典型 PE 启动盘)。
    正常 Windows 系统两项均不成立。
    """
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\MiniNT"
        )
        key.Close()
        return True
    except OSError:
        pass
    if os.path.exists(os.path.join("X:\\Windows", "System32")):
        return True
    return False


class Controller:
    ui: object

    def __init__(self):
        # ---- 网卡 ----
        self._nic_list = []
        self._auto_nic = None      # 自动选择的有线网卡 (display_name, desc, adapter_name, speed_str, index, ip)
        self._disks_scanned = False  # 磁盘扫描是否已完成

        # ---- 设备类型 ----
        self._device_type = None

        # ---- 分区映射 {"D": "I:", "E": "J:", "F": "K:"} ----
        self._partition_map = {}

        # ---- 配置导入 ----
        self._config_folders = []

        # ---- 目标磁盘 NTFS 分区数 (2/3/4) ----
        self._ntfs_partition_count = 0

        # ---- 文件服务器 (源设备) ----
        self._file_server: FileServer = None

        # ---- 传输状态 ----
        self._transferring = False
        self._transfer_done = False  # 传输是否已完成 (用于启用"校验文件"按钮)
        self._pre_verified_file = None  # 边传边校验确认清单 (供校验阶段增量跳过磁盘校验)

        # ---- 校验线程 (独立于传输线程，可并行) ----
        self._verify_thread = None
        self._stop_verify = False  # 新传输开始时通知旧校验停止
        self._verify_done = False  # 校验是否已完成 (完成后"跳过校验"不再生效)

        # ---- 传输取消 ----
        self._stop_transfer = False  # 窗口关闭时通知传输线程停止

        # ---- 网络断开检测 ----
        self._network_down = False          # 传输过程中网络是否断开
        self._network_monitor_stop = threading.Event()  # 停止监控信号

        # ---- 网络/鉴权状态 (避免未选网卡直接点按钮时 AttributeError) ----
        self._use_dhcp = False
        self._source_ip = ""
        self._dhcp_server = None
        self._auth_code = ""        # 源: 生成并显示; 目标: 用户输入
        self._last_source_ip = ""   # 目标: 最近一次成功连接的源设备 IP (供校验重试)

        # ---- UI 交互状态 ----
        self._config_export_done = False   # 发送方: 是否已完成系统配置导出
        self._auto_selected_disk = None    # 程序自动选中的磁盘 (检测用户手动更改)
        self._auto_partition_map = {}      # 程序自动填充分区映射 {id(cb): value}
        self._dhcp_countdown = 60          # DHCP 服务关闭倒计时 (秒)
        self._dhcp_countdown_after = None  # 倒计时 after id

        # ---- 配置导入状态 ----
        self._auto_import_active = False    # 是否处于"自动导入"(从检测配置直接导入)
        self._config_import_done = False    # 配置导入是否已完成 (完成后禁用"跳过")
        self._send_skip_verify_done = False  # 关闭程序时是否已发送跳过校验信息

        # ---- 线程安全 UI 调度 ----
        # 本环境 (嵌入式 Python 3.13) 的 tkinter 禁止后台线程调用任何 tk 接口
        # (直接调 ui.after 会抛 RuntimeError, 打包环境下可能触发 Tcl 致命崩溃)。
        # 因此后台线程一律把 UI 操作放入 _ui_q 队列, 由主线程 _poll_ui_q 轮询执行。
        self._main_thread = threading.current_thread()
        self._ui_q = queue.Queue()

    def _post_ui(self, fn, *args, **kwargs):
        """线程安全 UI 调度: 主线程直接执行; 后台线程放入队列,
        由主线程 _poll_ui_q 轮询执行 (Tk 非线程安全, 必须只在主线程操作 widget)。"""
        if threading.current_thread() is self._main_thread:
            try:
                fn(*args, **kwargs)
            except Exception:
                pass
        else:
            try:
                self._ui_q.put((fn, args, kwargs))
            except Exception:
                pass

    def _ui_after(self, ms, callback):
        """线程安全调度一次性延时任务 (供后台线程使用):
        主线程直接调用 self.ui.after 并返回 after id;
        后台线程入队, 由主线程轮询调度 (返回 None, 无法取消)。
        需要 after id 做 after_cancel 的周期任务, 应让主线程方法
        自行注册 (如 _dhcp_tick), 后台线程经 _post_ui 转主线程启动。"""
        if threading.current_thread() is self._main_thread:
            return self.ui.after(ms, callback)
        try:
            self._ui_q.put(("_after", ms, callback))
        except Exception:
            pass
        return None

    def _poll_ui_q(self):
        """主线程轮询执行后台线程提交的 UI 动作 (常驻 50ms 自轮询)。
        destroy 后 self.ui.after 抛 TclError 被吞, 不影响进程退出。"""
        try:
            while True:
                try:
                    item = self._ui_q.get_nowait()
                except Exception:
                    break
                try:
                    if item[0] == "_after":
                        self.ui.after(item[1], item[2])
                    else:
                        item[0](*item[1], **item[2])
                except Exception:
                    pass
            self.ui.after(50, self._poll_ui_q)
        except Exception:
            pass

    # ==================== 初始化 ====================

    def init(self, ui):
        self.ui = ui
        self._main_thread = threading.current_thread()
        self._install_thread_excepthook()
        self._setup_events()
        self._setup_ui_defaults()
        self._populate_nics()
        self._open_eula_browser()
        # 启动主线程 UI 动作轮询 (后台线程经 _post_ui 入队, 主线程在此执行)
        self.ui.after(50, self._poll_ui_q)

    def _open_eula_browser(self):
        """应用启动后用默认浏览器打开《用户协议》页面。
        在后台线程执行, 避免浏览器启动或系统调用无响应时阻塞主线程导致界面卡死;
        远程 EULA 站点不可达时回退到打包内的 eula.html; 无浏览器时静默失败;
        测试环境可用 NETCOPY_SKIP_EULA_BROWSER=1 关闭。"""
        try:
            if os.environ.get("NETCOPY_SKIP_EULA_BROWSER"):
                return
            threading.Thread(
                target=self._open_eula_with_fallback,
                daemon=True,
                name="eula-browser",
            ).start()
        except Exception:
            pass

    @staticmethod
    def _open_eula_with_fallback():
        """后台线程: 先探活远程 EULA 地址, 不可达时打开打包内的 eula.html"""
        try:
            import urllib.request
            try:
                req = urllib.request.Request(EULA_URL, method="HEAD")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        _open_eula_url_silent(EULA_URL)
                        return
            except Exception:
                pass
            # 回退: PyInstaller 打包的 eula.html (--add-data "eula.html;.")
            candidates = [
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "eula.html"),
                os.path.join(os.getcwd(), "eula.html"),
            ]
            for local in candidates:
                if os.path.exists(local):
                    _open_eula_url_silent("file:///" + local.replace("\\", "/"))
                    return
        except Exception:
            pass

    def _on_eula_toggle(self, agreed: bool):
        """协议确认勾选状态 (发送方/接收方按钮的激活由 ui 直接处理)"""
        pass

    def _install_thread_excepthook(self):
        """捕获所有后台线程的未处理异常并输出到日志。
        打包成 exe (pythonw, 无控制台) 时, 线程里抛出的异常默认被静默丢弃,
        表现为"点了按钮没反应"。挂上 excepthook 后, 任何线程崩溃都会显示在日志里。"""
        import traceback as _tb

        def _hook(args):
            try:
                msg = "".join(_tb.format_exception(
                    args.exc_type, args.exc_value, args.exc_traceback))
                # _log 线程安全: 后台线程调用自动入队, 由主线程渲染到日志区
                self._log(f"[诊断] 后台线程异常:\n{msg}")
            except Exception:
                pass

        threading.excepthook = _hook

    def _setup_ui_defaults(self):
        """设置 UI 初始状态"""
        # 导航按钮初始状态
        self.ui.set_button_prev("disabled")
        self.ui.set_button_next("disabled")
        # 隐藏所有步骤特定控件
        self.ui.hide_dhcp()
        self.ui.hide_auth_code()
        self.ui.hide_connect_panels()
        # 运行环境单选: 默认按检测结果预选
        if hasattr(self.ui, 'winpe_var'):
            self.ui.winpe_var.set("winpe" if is_running_in_winpe() else "normal")

    def _setup_events(self):
        """绑定 UI 控件事件"""
        # 角色选择按钮 — 通过 ui._on_select_role 更新角色标签
        if hasattr(self.ui, 'tk_btn_source'):
            self.ui.tk_btn_source.config(command=lambda: self.ui._on_select_role("source"))
        if hasattr(self.ui, 'tk_btn_target'):
            self.ui.tk_btn_target.config(command=lambda: self.ui._on_select_role("target"))
        # 上一步/下一步导航
        if hasattr(self.ui, 'tk_button_prev'):
            self.ui.tk_button_prev.config(command=self._on_prev_step)
        if hasattr(self.ui, 'tk_button_next'):
            self.ui.tk_button_next.config(command=self._on_next_step)
        # 磁盘选择
        self.ui.tk_select_box_mqfzmzbe.bind(
            "<<ComboboxSelected>>", self._on_disk_selected
        )
        # 分区映射 (三个 Combobox)
        self.ui.tk_select_box_mqfzsdz4.bind(
            "<<ComboboxSelected>>", self._on_partition_map_changed
        )
        self.ui.tk_select_box_mqfzuo2y.bind(
            "<<ComboboxSelected>>", self._on_partition_map_changed
        )
        self.ui.tk_select_box_mqfzwehm.bind(
            "<<ComboboxSelected>>", self._on_partition_map_changed
        )
        # 开始按钮
        self.ui.tk_button_mqfzl35t.config(command=self._on_start_button)
        # 查看校验报告按钮 (校验完成后显示, 定位到报告文件)
        if hasattr(self.ui, 'tk_button_open_report'):
            self.ui.tk_button_open_report.config(command=self._on_open_report)
        # 开启 DHCP 按钮 (标签为"寻找旧电脑")
        self.ui.tk_button_dhcp.config(command=self._on_dhcp_button)
        # 导出系统配置按钮 (步骤 1, 发送端)
        if hasattr(self.ui, 'tk_button_export_config'):
            self.ui.tk_button_export_config.config(command=self._on_export_config)
        # 导入配置页面按钮 (步骤 5)
        if hasattr(self.ui, 'tk_button_import_config'):
            self.ui.tk_button_import_config.config(command=self._on_import_config)
        if hasattr(self.ui, 'tk_button_skip_import'):
            self.ui.tk_button_skip_import.config(command=self._on_skip_import)
        if hasattr(self.ui, 'tk_button_browse_config'):
            self.ui.tk_button_browse_config.config(command=self._on_browse_config_folder)
        # 总结页: 查看校验报告按钮
        if hasattr(self.ui, 'tk_button_view_report'):
            self.ui.tk_button_view_report.config(command=self._on_open_report)
        # 步骤 4 配置检测按钮 (接收端传输完成后)
        if hasattr(self.ui, 'tk_button_use_config'):
            self.ui.tk_button_use_config.config(command=self._on_use_detected_config)
        if hasattr(self.ui, 'tk_button_skip_config'):
            self.ui.tk_button_skip_config.config(command=self._on_skip_detected_config)
        # 发送端完成页"完成并关闭"按钮 (step 5)
        if hasattr(self.ui, 'tk_button_source_done'):
            self.ui.tk_button_source_done.config(command=self._on_source_done_close)
        # 接收端总结页"完成并关闭"按钮 (step 4)
        if hasattr(self.ui, 'tk_button_summary_done'):
            self.ui.tk_button_summary_done.config(command=self._on_summary_done)
        # 发现设备列表
        if hasattr(self.ui, 'tk_select_box_discover'):
            self.ui.tk_select_box_discover.bind("<<ComboboxSelected>>", self._on_discover_selected)
        # 运行环境: WinPE → 扫描磁盘; 正常系统 → 盘符一一对应
        if hasattr(self.ui, 'winpe_var'):
            self.ui.winpe_var.trace_add("write", self._on_winpe_changed)
        # 验证码输入 (大写自动转)
        if hasattr(self.ui, 'tk_entry_code'):
            self.ui.tk_entry_code.bind("<KeyRelease>", self._on_auth_code_changed)

    # ==================== 网卡扫描 ====================

    def _populate_nics(self):
        """扫描网卡并自动选择最佳有线网卡 (无需用户手动选择)。
        后台线程只做扫描, 所有 Tk 操作统一调度回主线程执行 (Tk 非线程安全)。
        窗口已销毁时 after 回调会抛 TclError 并被吞掉, 不会造成并发死锁。"""
        self._log("正在检测有线网卡...")

        def _scan():
            try:
                nics = scan_nics()
                # NIC 优先级排序: USB > 169.254 > 内置网卡
                nics = sorted(nics, key=_nic_priority_key)
                err = None
            except Exception as e:
                nics = None
                err = str(e)
            # 后台线程禁止直接调 tk: 经 _post_ui 转主线程执行
            self._post_ui(self._apply_nic_scan_result, nics, err)

        threading.Thread(target=_scan, daemon=True).start()

    def _apply_nic_scan_result(self, nics, err):
        """主线程: 应用网卡扫描结果 (仅在主线程执行, 避免跨线程 Tk 死锁)"""
        if err is not None:
            self._log(f"网卡检测失败: {err}")
            self.ui.update_auto_nic_display("", "", "", 0)
            return
        self._nic_list = nics
        self._auto_select_wired_nic()

    # ==================== 自动网卡选择 ====================

    def _auto_select_wired_nic(self):
        """自动选择最佳有线网卡 (Type == 6)，并更新 UI 显示。
        优先级: 已有 169.254.x.x 地址 > 有其他 IP > 无 IP。
        网卡已为全自动检测, 不再提供手动选择下拉框。"""
        wired = get_wired_adapters()
        if wired:
            # 优先级: 169.254.x.x > 有 IP > 无 IP
            wired_sorted = sorted(wired, key=lambda w: (
                0 if w[5].startswith("169.254") else (1 if w[5] else 2)
            ))
            self._auto_nic = wired_sorted[0]  # (display_name, desc, adapter_name, speed_str, index, ip)
            nic = self._auto_nic
            vip = nic[5] or "无"
            vspeed = nic[3] or ""
            self._log(f"自动选择有线网卡: {nic[0]} (IP: {vip})")
            # 更新步骤 1 的自动检测网卡信息
            self.ui.after(0, lambda: self.ui.update_auto_nic_display(
                nic[0], nic[5], vspeed, len(wired)))
            # 若当前在步骤 1，启用「下一步」按钮
            self.ui.after(0, lambda: self.ui.set_button_next("normal")
                          if getattr(self.ui, '_step', 0) == 1 else None)
        else:
            self._auto_nic = None
            self._log("警告: 未检测到有线网卡，请检查网线连接")
            self.ui.after(0, lambda: self.ui.update_auto_nic_display("", "", "", 0))

    def _get_wired_nic_ips(self) -> list:
        """获取所有有线网卡的当前 IP 地址列表（非空）"""
        wired = get_wired_adapters()
        return [w[5] for w in wired if w[5]]

    def _get_adapter_desc_from_auto(self) -> str:
        """从自动选择的网卡获取适配器描述"""
        if self._auto_nic:
            return self._auto_nic[1]
        return ""

    def _ensure_normal_mapping(self):
        """正常系统下: 盘符一一对应 (D→D:, E→E:, F→F:), 无需用户映射。
        仅保留当前系统中实际存在的盘符。"""
        mapping = {}
        for letter in ("D", "E", "F"):
            try:
                if os.path.isdir(letter + ":\\"):
                    mapping[letter] = letter + ":\\"
            except Exception:
                pass
        self._partition_map = mapping
        if mapping:
            self._log(f"正常系统模式: 盘符自动一一对应 {mapping}")

    def _on_winpe_changed(self, *args):
        """运行环境切换: WinPE → 扫描物理磁盘; 正常系统 → 盘符一一对应"""
        try:
            if self.ui.winpe_var.get() == "winpe":
                self._populate_disks()
            else:
                self._ensure_normal_mapping()
                self._check_button_state()
        except Exception:
            pass

    # ==================== 事件处理 ====================

    def _on_role_selected(self, role: str):
        """角色选择: 'source'(旧电脑/发送方) 或 'target'(新电脑/接收方)"""
        self._device_type = "源设备" if role == "source" else "目标设备"
        # 切换角色时重置导出状态与自动选择标记
        self._config_export_done = False
        self._auto_selected_disk = None
        self._auto_partition_map = {}
        role_display = "旧设备 (发送方)" if role == "source" else "新设备 (接收方)"
        self._log(f"已选择角色: {role_display}")

        # 更新右上角角色标签
        if hasattr(self.ui, 'tk_label_role'):
            self.ui.tk_label_role.configure(text=f"当前角色: {role_display}")

        # 步骤 0 → 步骤 1: 进入网卡选择 — 用 set_button_next 确保 pack 状态正确
        self.ui.set_button_next("normal")
        if role == "source":
            self.ui.show_auth_code("----")
            self.ui.hide_dhcp()
            self.ui.show_src_connect()
            self.ui.hide_discover()
            self.ui.hide_manual_ip()
            # 显示导出配置区域，隐藏接收端信息
            if hasattr(self.ui, '_export_frame'):
                self.ui._export_frame.pack(fill=tk.BOTH, expand=True)
            if hasattr(self.ui, '_target_info_frame'):
                self.ui._target_info_frame.pack_forget()
        else:
            self.ui.show_dhcp()
            self.ui.show_tgt_connect()
            self.ui.show_discover()
            self.ui.show_manual_ip()
            # 接收端: 隐藏验证码横幅 (避免从发送方角色切换过来时残留显示)
            self.ui.hide_auth_code()
            # 显示接收端信息，隐藏导出配置区域
            if hasattr(self.ui, '_target_info_frame'):
                self.ui._target_info_frame.pack(fill=tk.BOTH, expand=True)
            if hasattr(self.ui, '_export_frame'):
                self.ui._export_frame.pack_forget()

        # 切换到网卡选择步骤
        self.ui.go_step(1)
        self.ui.tk_button_prev.config(state="normal")

        # IP 配置信息已在高级选项面板中展示，无需额外更新

        # 不在此时显示开始按钮，等用户到达步骤 3 再显示

    def _on_prev_step(self):
        """上一步"""
        current = self.ui._step
        new_step = max(0, current - 1)
        self.ui.go_step(new_step)

        if new_step == 0:
            # 退回角色选择页: 禁用下一步
            self.ui.tk_button_prev.config(state="disabled")
            self.ui.set_button_next("disabled")
        else:
            self.ui.tk_button_prev.config(state="normal")

            # 同步"下一步"按钮状态
            if new_step == 1:
                # 高级设置页: 网卡为全自动检测, 角色已选即可进入下一步
                # (磁盘选择/映射已移入高级选项面板, 不阻塞主流程)
                self.ui.set_button_next("normal")
            elif new_step == 2:
                # 连接页面: 禁用"下一步", 使用"开始传输/重新接收"
                self.ui.set_button_next("disabled")
                # 网络断开/传输失败后回到验证码页: 激活"重新接收"按钮
                if (self._device_type == "目标设备" and not self._transferring
                        and not self._transfer_done):
                    try:
                        self.ui.tk_button_mqfzl35t.config(text="重新接收", state="normal")
                    except Exception:
                        pass
            elif new_step == 3:
                # 传输页面: 传输完成(接收方)后可进入"传输总结"; 发送方传输完成即结束
                if self._transfer_done:
                    if self._device_type == "目标设备":
                        self.ui.set_button_next("normal", text="查看总结 >")
                    else:
                        self.ui.set_button_next("disabled")
                else:
                    self.ui.set_button_next("disabled")
            elif new_step == 4:
                # 传输总结页 (最后一步): 下一步 = 完成
                self.ui.set_button_next("normal", text="完成")
                self._populate_config_folders()
                self._render_summary()
            else:
                self.ui.set_button_next("normal")

            # 同步"开始传输"按钮状态
            self._check_button_state()

    def _on_next_step(self):
        """下一步"""
        # 防护: 如果按钮被禁用, 不允许前进 (防止逻辑绕过 UI 状态)
        try:
            if str(self.ui.tk_button_next.cget("state")) == "disabled":
                return
        except Exception:
            pass
        current = self.ui._step

        # 步骤 1 (发送方): 未导出系统配置时, 点击"下一步"需弹窗提醒
        if current == 1 and self._device_type == "源设备" and not self._config_export_done:
            from tkinter import messagebox
            if not messagebox.askyesno(
                "未导出系统配置",
                "尚未导出系统配置！\n\n"
                "若不导出，新设备将无法恢复旧设备的系统配置。\n\n"
                "是否仍要继续？（建议先点击「导出系统配置」）",
                parent=self.ui,
            ):
                return

        # 总结页 (最后一步): 点击"完成"结束向导
        if current == 4 and self._device_type == "目标设备":
            self._on_summary_done()
            return

        # 发送端完成页 (step 5): 点击"完成"结束程序
        if current == 5:
            self._on_source_done_close()
            return

        # 特殊: 步骤 3 接收端传输完成后 → "下一步" = 进入传输总结页
        if current == 3 and self._device_type == "目标设备":
            if getattr(self, "_transfer_done", False):
                self._summary_entered = True
                self.ui.go_step(4)
                self._render_summary()
                return

        new_step = min(self.ui._total_steps - 1, current + 1)
        self.ui.go_step(new_step)
        self.ui.tk_button_prev.config(state="normal")

        if new_step == 2:
            # 进入连接页面: PE 下确保磁盘/映射就绪; 正常系统下盘符自动一一对应
            self.ui.set_button_next("disabled")
            try:
                if self.ui.winpe_var.get() == "winpe":
                    self._on_disk_selected()
                else:
                    self._ensure_normal_mapping()
            except Exception:
                pass
        elif new_step == 3:
            # 进入传输页面: 传输完成前禁用"下一步"
            if self._transfer_done:
                if self._device_type == "目标设备":
                    self.ui.set_button_next("normal", text="查看总结 >")
                else:
                    # 发送端: 传输完成即结束, 校验自动在后台进行
                    self.ui.set_button_next("disabled")
            else:
                self.ui.set_button_next("disabled")
        elif new_step == 4:
            # 传输总结页 (最后一步): 下一步 = 完成
            self.ui.set_button_next("normal", text="完成")
            self._populate_config_folders()
            self._render_summary()
        elif new_step >= self.ui._total_steps - 1:
            self.ui.set_button_next("disabled")
        else:
            self.ui.set_button_next("normal")

        # 同步"开始传输"按钮状态
        self._check_button_state()

    def _on_auth_code_changed(self, event=None):
        """验证码输入: 自动转大写 + 限制 4 位"""
        try:
            current = self.ui.tk_entry_code.get()
            upper = current.upper()
            if upper != current:
                self.ui.tk_entry_code.delete(0, "end")
                self.ui.tk_entry_code.insert(0, upper[:4])
            elif len(current) > 4:
                self.ui.tk_entry_code.delete(4, "end")
        except Exception:
            pass

    def _on_discover_selected(self, event=None):
        """发现设备下拉框选择"""
        selected = self.ui.tk_select_box_discover.get()
        if selected and selected != "等待 DHCP 响应..." and "DHCP" not in selected:
            # 提取 IP
            if " | " in selected:
                parts = selected.split(" | ")
                self._source_ip = parts[1].strip() if len(parts) > 1 else ""
            self._use_dhcp = True
            self._log(f"已选择发现设备: {selected}")

    def _on_browse_csv(self):
        """浏览 CSV 文件"""
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="选择 FullFilelist_DEF.csv",
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
        )
        if path:
            self.ui.csv_path_var.set(path)
            self._log(f"已选择 CSV: {path}")

    def _on_device_type_selected(self, event=None):
        dev_type = self._device_type
        if not dev_type:
            return
        self._log(f"已选择设备类型: {dev_type}")

        if dev_type == "源设备":
            self.ui.tk_button_mqfzl35t.config(text="开始传输")
            self.ui.hide_discover()
            self.ui.hide_dhcp()
            # 源设备不需要手动 IP / 验证码输入
            self.ui.hide_manual_ip()
            self.ui.hide_auth_input()
        else:
            self.ui.tk_button_mqfzl35t.config(text="开始接收")
            self.ui.show_discover()
            self.ui.show_dhcp()
            # 目标设备: 需要输入源设备显示的验证码
            # 同时保留「手动输入源设备IP」直连功能 (对方网卡已有 IP / 无需 DHCP 时)
            self.ui.show_manual_ip()
            self.ui.show_auth_input()
            # 接收端: 显示 FullFilelist_DEF.csv 手动选择框
            self.ui.show_csv_selector()
        # 切换设备类型后旧验证码作废
        self._auth_code = ""
        self.ui.hide_auth_code()

        # 设备类型选定后重新评估开始按钮状态
        self._check_button_state()

        # 自动检测到有线网卡 → 预配置网络 (源设备走 DHCP / 目标设备仅记录)
        # 网卡全自动检测, 无需选定具体网卡: 源设备自己枚举有线网卡续租,
        # 接收端 DHCP 广播自行枚举所有有线网卡
        self._configure_ip(dev_type)

        # 扫描磁盘
        self._populate_disks()

    def _on_disk_selected(self, event=None):
        disk = self.ui.tk_select_box_mqfzmzbe.get()
        if disk in ("未检测到磁盘", "", "请先选择设备类型", "扫描中..."):
            return
        # 程序已自动选定磁盘后, 用户手动更改 → 弹窗确认
        auto = self._auto_selected_disk
        if auto and disk != auto:
            from tkinter import messagebox
            changed = messagebox.askyesno(
                "修改自动选择",
                f"程序已自动选定正确磁盘: {auto}\n\n是否仍要继续更改？",
                parent=self.ui,
            )
            if not changed:
                self.ui.tk_select_box_mqfzmzbe.set(auto)
                return
            self._auto_selected_disk = None
        self._log(f"已选择磁盘: {disk}")

        # 磁盘已选择: 确保高级设置页(步骤 1)的下一步可用
        # (正常系统下无需映射, 此步骤仅影响 PE 环境)
        if self.ui._step == 1:
            self.ui.set_button_next("normal")

        # 立即填充盘符 (不依赖分区检测结果)
        self._populate_drive_letters()

        # 后台线程检测 NTFS 分区数 (IOCTL 可能耗时，不阻塞 UI)
        disk_index = self.ui.tk_select_box_mqfzmzbe.current()

        def _detect_partitions():
            try:
                disk_num = get_disk_number(disk_index)
                if disk_num >= 0:
                    ntfs_count = get_partition_count(disk_num)
                    self._ntfs_partition_count = ntfs_count
                    self._log(f"检测到 {ntfs_count} 个 NTFS 分区 (PhysicalDrive{disk_num})")

                    # 获取物理顺序分区详情，自动填充 D/E/F
                    details = get_partition_details(disk_num)
                    if details:
                        ordered = [dl for _, _, dl in details]
                        self._log(f"分区物理顺序→盘符: {ordered}")
                        # 自动填充: 按磁盘物理分区顺序映射到 D/E/F
                        self._post_ui(self._auto_fill_drive_mapping, ordered, ntfs_count)
                else:
                    self._ntfs_partition_count = 0
            except Exception as e:
                self._ntfs_partition_count = 0
                import traceback
                tb = traceback.format_exc()
                self._log(f"分区检测失败: {e}")
                self._log(f"调试: {tb}")
            finally:
                # 无论检测成功与否, 都重新评估开始按钮状态
                self._post_ui(self._check_button_state)

        threading.Thread(target=_detect_partitions, daemon=True).start()

    def _on_partition_map_changed(self, event=None):
        """分区映射变更，更新 partition_map 并检查按钮状态"""
        # 程序已自动填充分区映射后, 用户手动更改 → 弹窗确认
        widget = getattr(event, "widget", None)
        if widget is not None and self._auto_partition_map:
            auto_val = self._auto_partition_map.get(id(widget), "")
            if auto_val:
                try:
                    cur = widget.get().strip()
                except Exception:
                    cur = ""
                if cur != auto_val:
                    from tkinter import messagebox
                    changed = messagebox.askyesno(
                        "修改自动映射",
                        f"程序已自动选定正确盘符: {auto_val}\n\n是否仍要继续更改？",
                        parent=self.ui,
                    )
                    if not changed:
                        widget.set(auto_val)
                        return
                    self._auto_partition_map.pop(id(widget), None)
        self._update_partition_map()
        self._check_button_state()

    def _on_start_button(self):
        """开始传输/接收 按钮"""
        try:
            if self._transferring:
                self._log("传输正在进行中...")
                return

            self._update_partition_map()
            dev_type = self._device_type
            if dev_type not in ("源设备", "目标设备"):
                self._log("请先选择设备类型")
                return

            manual_ip = self._get_manual_ip()
            self._log(
                f"[诊断] 点击开始: 类型={dev_type}, "
                f"模式={'手动IP(' + manual_ip + ')' if manual_ip else 'DHCP/扫描'}"
            )

            # 先做参数校验, 通过后再切换到传输进度页面 (步骤 3)
            # 避免校验失败时用户已停在传输页却无反应
            if dev_type == "源设备":
                # 源设备必须指定要对外提供(拷贝)的盘符, 否则服务器虽启动但无任何数据可传,
                # 表现为"服务已开启却无法传输文件"。因此这里改为强制校验。
                if not self._partition_map:
                    self._log("错误: 源设备尚未配置任何盘符映射 (D/E/F), "
                              "服务器将无任何数据可拷贝。请先在下拉框选择要提供的盘符。")
                    return
            else:
                if self.ui.winpe_var.get() == "winpe":
                    # PE 环境: 目标设备需要完成分区映射才能下载
                    ntfs_count: int = self._ntfs_partition_count
                    required_keys = ("D", "E") if ntfs_count == 2 else ("D", "E", "F")
                    mapped = [k for k in required_keys if self._partition_map.get(k)]
                    if len(mapped) < len(required_keys):
                        self._log(f"请先完成 {'/'.join(required_keys)} 盘符映射"
                                  f"(当前: {len(mapped)} 个)")
                        return
                elif not self._partition_map:
                    # 正常系统: 盘符一一对应已自动生成, 无需手动映射
                    self._log("错误: 目标设备未检测到可接收的分区 (D/E/F)")
                    return

            # 校验通过, 切换到传输进度页面 (步骤 3)
            self.ui.go_step(3)

            if dev_type == "源设备":
                self._start_source_server(manual_ip=manual_ip)
            else:
                self._start_target_download(manual_ip=manual_ip)
        except Exception:
            import traceback
            self._log("[诊断] 开始按钮处理异常:\n" + traceback.format_exc())

    # ==================== 磁盘/分区 ====================

    def _try_auto_select_disk(self) -> bool:
        """若当前仅有 1 个有效磁盘且未选中任何磁盘，自动选中并触发 _on_disk_selected。
        返回 True 表示执行了自动选择。"""
        values = self.ui.tk_select_box_mqfzmzbe["values"]
        if not values or values == ("扫描中...",):
            return False
        current = self.ui.tk_select_box_mqfzmzbe.get()
        if current and current not in ("未检测到磁盘", "", "请先选择设备类型", "扫描中..."):
            return False  # 已有有效选择
        valid_disks = [v for v in values if v not in ("未检测到磁盘", "请先选择设备类型", "扫描中...")]
        if len(valid_disks) == 1:
            self.ui.tk_select_box_mqfzmzbe.set(valid_disks[0])
            self._auto_selected_disk = valid_disks[0]
            self._log(f"自动选择磁盘: {valid_disks[0]}")
            if self.ui._step == 1:
                self._on_disk_selected()
            return True
        return False

    def _populate_disks(self, callback=None):
        """扫描物理磁盘。扫描完成后若仅 1 个磁盘则自动选中。
        callback 在扫描完成后 (含自动选择后) 在 UI 线程回调。
        防止重复扫描: 若已扫描过或正在扫描中，直接返回。"""
        if self._disks_scanned:
            self._try_auto_select_disk()
            if callback:
                self.ui.after(0, callback)
            return
        cur = self.ui.tk_select_box_mqfzmzbe.get()
        if cur == "扫描中...":
            return  # 正在扫描中
        self._log("正在扫描磁盘...")
        self.ui.tk_select_box_mqfzmzbe["values"] = ("扫描中...",)

        def _scan():
            try:
                disks = get_disk_list()
                self._disks_scanned = True
                # 后台线程禁止直接调 tk: 一律经 _post_ui / _log 转主线程
                self._post_ui(self._update_combobox,
                              self.ui.tk_select_box_mqfzmzbe, disks,
                              f"检测到 {len(disks)} 个磁盘")
                # 自动选择: 仅 1 个磁盘时自动选中并触发分区检测
                if len(disks) == 1:
                    self._auto_selected_disk = disks[0]
                    self._post_ui(lambda: (
                        self.ui.tk_select_box_mqfzmzbe.set(disks[0])
                        if self.ui._step == 1 else None
                    ))
                    self._post_ui(lambda: (
                        self._on_disk_selected()
                        if self.ui._step == 1 else None
                    ))
                # 扫描完成回调
                if callback:
                    self._post_ui(callback)
            except Exception as e:
                self._disks_scanned = False  # 失败允许重试
                self._log(f"磁盘扫描失败: {e}")
                if callback:
                    self._post_ui(callback)

        threading.Thread(target=_scan, daemon=True).start()

    def _populate_drive_letters(self):
        """扫描 PE 下可用盘符，填充三个分区 Combobox"""
        self._log("正在扫描可用分区...")
        try:
            letters = get_drive_letter_list()
            for cb in (
                self.ui.tk_select_box_mqfzsdz4,
                self.ui.tk_select_box_mqfzuo2y,
                self.ui.tk_select_box_mqfzwehm,
            ):
                cb["values"] = letters
            self._log(f"可用分区: {', '.join(letters)}")
        except Exception as e:
            self._log(f"分区扫描失败: {e}")

    def _auto_fill_drive_mapping(self, ordered_letters: list, ntfs_count: int):
        """按磁盘物理分区顺序 (StartingOffset) 自动填充 D/E/F 下拉框。

        规则 (兼容 GPT/MBR, 盘符可任意乱序):
          1. ordered_letters 已按分区在磁盘上的物理顺序排列;
          2. 去掉系统分区 (盘符 C);
          3. 若剩余仍多于 3 个, 从头部跳过多余分区 (视为系统/保留分区);
          4. 剩余分区按物理顺序依次填充 D → E → F。

        示例:
          4 分区识别为 C,E,F,G → 去掉 C → D=E, E=F, F=G
          3 分区机械盘识别为 E,D,F (物理顺序) → D=E, E=D, F=F
        """
        if not ordered_letters:
            return

        # 去掉系统分区 C
        data_letters = [dl for dl in ordered_letters if dl.upper() != "C"]
        # 仍超过 3 个: 头部多余的视为系统/保留分区, 跳过
        while len(data_letters) > 3:
            skipped = data_letters.pop(0)
            self._log(f"自动映射: 跳过头部分区 {skipped} (视为系统/保留分区)")

        combos = (
            (self.ui.tk_select_box_mqfzsdz4, "D"),
            (self.ui.tk_select_box_mqfzuo2y, "E"),
            (self.ui.tk_select_box_mqfzwehm, "F"),
        )
        self._auto_partition_map = {}
        for i, (cb, label) in enumerate(combos):
            if i < len(data_letters):
                try:
                    cb.set(data_letters[i])
                    self._log(f"自动映射(物理顺序): 源 {label} → 目标 {data_letters[i]}")
                except tk.TclError:
                    pass
            # 记录程序自动填充的映射值 (用于检测用户手动更改)
            try:
                self._auto_partition_map[id(cb)] = cb.get().strip()
            except Exception:
                self._auto_partition_map[id(cb)] = ""
        self._update_partition_map()

    def _update_partition_map(self):
        """更新分区盘符映射。
        正常系统下盘符一一对应 (D→D 等, 无需用户映射);
        仅 WinPE 环境从三个 Combobox 读取用户映射。"""
        try:
            if self.ui.winpe_var.get() != "winpe":
                self._ensure_normal_mapping()
                return
        except Exception:
            pass
        d_letter = self.ui.tk_select_box_mqfzsdz4.get().strip()
        e_letter = self.ui.tk_select_box_mqfzuo2y.get().strip()
        f_letter = self.ui.tk_select_box_mqfzwehm.get().strip()

        self._partition_map = {}
        if d_letter and d_letter not in ("无可用分区", ""):
            self._partition_map["D"] = d_letter + ":\\"
        if e_letter and e_letter not in ("无可用分区", ""):
            self._partition_map["E"] = e_letter + ":\\"
        if f_letter and f_letter not in ("无可用分区", ""):
            self._partition_map["F"] = f_letter + ":\\"

        self._log(f"分区映射: {self._partition_map}")

    def _check_button_state(self):
        """根据设备类型、当前步骤及传输状态启用/禁用「开始传输」按钮。

        网卡已改为全自动检测, 按钮状态不再依赖网卡选择。
        仅在步骤 2 (连接页面) 才显示并启用开始传输按钮，
        且传输进行中不重新启用, 防止用户误点跳过连接步骤。
        """
        dev_type = self._device_type
        current_step = getattr(self.ui, '_step', 0)
        if (dev_type in ("源设备", "目标设备")
                and current_step >= 2 and not self._transferring):
            self.ui.tk_button_mqfzl35t.config(state="normal")
        else:
            self.ui.tk_button_mqfzl35t.config(state="disabled")

    # ==================== IP 配置 ====================
    #
    # 正确流程:
    #   源设备: 启动 HTTP 文件服务器 (从目标 DHCP 获取 IP)
    #   目标设备: 设自身 IP → 启动 DHCP 服务器 → 等源设备获取 IP → 直连源设备
    #

    def _configure_ip(self, device_type: str):
        """根据设备类型配置网络 (网卡全自动检测, 无需指定网卡)"""
        self._use_dhcp = False
        self._source_ip = ""

        if "源" in str(device_type):
            # 源设备: 在所有有线网卡上释放并重新获取 IP (从目标 DHCP 获取)
            self._log("源设备: 正在在所有有线网卡上获取 IP...")
            threading.Thread(target=self._setup_source_network, daemon=True).start()
        else:
            # 目标设备: 不自动启动 DHCP, 等待用户点击「寻找旧电脑」
            self._log("目标设备: 请进入连接页面后点击「寻找旧电脑」启动 DHCP 服务器，"
                      "待源设备分配到 IP 后点击「开始接收」")

    def _setup_source_network(self):
        """源设备: 在所有有线网卡上释放+续租 DHCP，从目标 DHCP 获取 IP。
        后台 release+renew 所有有线网卡, 主线程用 NotifyAddrChange 事件驱动等待。
        """
        from nic_scanner import (release_dhcp_ip, renew_dhcp_ip, wait_for_ip_change)

        try:
            wired_nics = get_wired_adapters()
            if not wired_nics:
                self._log("错误: 未找到有线网卡")
                return

            # 后台线程: 释放所有有线网卡旧租约, 再逐个续租
            self._log("释放所有有线网卡 DHCP 租约...")
            self._log("请求所有有线网卡 DHCP 续租...")

            def _do_dhcp():
                for nic in wired_nics:
                    idx = nic[4]  # index
                    if idx > 0:
                        release_dhcp_ip(idx)
                        renew_dhcp_ip(idx)

            threading.Thread(target=_do_dhcp, daemon=True).start()

            # 事件驱动等待 IP 变化, 检查任一有线网卡是否获取到目标 IP
            deadline = time.time() + 20
            ip = ""
            while time.time() < deadline:
                remaining = deadline - time.time()
                if not wait_for_ip_change(min(remaining, 5.0)):
                    # 超时, 最后检查所有有线网卡
                    for nic in wired_nics:
                        ip = get_local_ip(nic[1])  # nic[1] = description
                        if ip and ip.startswith("169.254.100."):
                            break
                    else:
                        ip = ""  # 无网卡获得目标 IP
                    break
                for nic in wired_nics:
                    ip = get_local_ip(nic[1])
                    if ip and ip.startswith("169.254.100."):
                        break
                if ip and ip.startswith("169.254.100."):
                    break

            if ip and ip != "0.0.0.0":
                self._source_ip = ip
                self._log(f"源设备 IP: {ip}")
            else:
                self._log("源设备: 首次续租未命中, 进入持续重试模式 "
                          "(每轮 release+renew, 最长 5 分钟), 将使用 APIPA 兜底")
                # 关键修复 (2026-09-10): 每轮必须 release + renew。
                # release 会把 Windows DHCP 客户端重置回 INIT 状态, 使本轮 renew
                # 立即广播 DHCPDISCOVER; 只 renew 不 release 时, 客户端可能仍停在
                # 指数退避 (1/2/4/.../64 秒) 里 —— 于是"接收端稍后才启动 DHCP"的
                # 场景要等几十秒到一分钟才拿到 IP, 表现为"获取 IP 超级慢"。
                # renew_dhcp_ip 是阻塞式 Win32 调用 (无 DHCP 响应时可能阻塞数十秒),
                # 必须放后台线程, 否则下面的观察循环无法按秒级节奏执行。
                renew_idle = threading.Event()
                renew_idle.set()  # 初始"空闲", 允许第一轮立即启动

                def _renew_all():
                    try:
                        for _nic in wired_nics:
                            _idx = _nic[4]
                            if _idx > 0:
                                try:
                                    release_dhcp_ip(_idx)
                                    renew_dhcp_ip(_idx)
                                except Exception:
                                    pass
                    finally:
                        renew_idle.set()

                retry_deadline = time.time() + 300
                while (time.time() < retry_deadline
                       and not getattr(self, "_transferring", False)):
                    if renew_idle.is_set():
                        renew_idle.clear()
                        threading.Thread(target=_renew_all, daemon=True).start()
                    # 观察 2 秒: 网卡一旦拿到目标 IP 立即结束等待
                    obs_deadline = time.time() + 2
                    while time.time() < obs_deadline:
                        for nic in wired_nics:
                            ip = get_local_ip(nic[1])
                            if ip and ip.startswith("169.254.100."):
                                break
                        if ip and ip.startswith("169.254.100."):
                            break
                        time.sleep(0.5)
                    if ip and ip.startswith("169.254.100."):
                        break
                if ip and ip.startswith("169.254.100."):
                    self._source_ip = ip
                    self._log(f"源设备 IP: {ip}")
                else:
                    self._log("源设备: 重试仍未获得目标 IP, 使用 APIPA 地址兜底 (两端 /16 网段仍可直连)")
        except Exception as e:
            self._log(f"源设备网络: {e}")

    # ==================== 手动 IP 辅助 ====================

    def _get_manual_ip(self):
        """读取手动 IP 输入框: 有效返回 'x.x.x.x', 否则返回 None。

        注: 该 IP 代表「源设备 IP」, 目标设备据此直连源设备 (无需 DHCP)。
        该输入框默认隐藏, 仅在需要时由 UI 显示。
        """
        try:
            raw = getattr(self.ui, 'tk_entry_const', None)
            if raw is None:
                return None
            ip_str = raw.get().strip()
            if not ip_str:
                return None
            parts = ip_str.split(".")
            if len(parts) != 4:
                return None
            if all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
                return ip_str
            return None
        except Exception:
            return None

    def _apply_manual_ip(self, adapter_desc, ip):
        """源设备: 将本机网卡 IP 设为手动输入的 IP (供目标直连)。
        掩码使用 SUBNET_MASK (/16), 与 169.254.x.x (APIPA) 一致, 避免掩码非对称。"""
        from ip_config import set_ip_via_api, set_ip_via_netsh
        try:
            success, msg = set_ip_via_api(adapter_desc, ip, mask_str=SUBNET_MASK)
            # _log 线程安全: 后台线程调用自动转主线程渲染
            self._log(msg)
            if not success:
                success2, msg2 = set_ip_via_netsh(adapter_desc, ip)
                self._log(msg2)
                if success2:
                    self._log(f"源设备手动 IP 已生效: {ip} (掩码 {SUBNET_MASK})")
                else:
                    self._log(f"警告: 源设备手动 IP 设置失败 ({ip}), 网卡可能停留在 APIPA。\n"
                              f"  由于两端掩码已统一为 {SUBNET_MASK}, 仍可直连通信。")
            else:
                self._log(f"源设备手动 IP 已生效: {ip} (掩码 {SUBNET_MASK})")
        except Exception as e:
            self._log(f"设置源设备手动 IP 失败: {e}")

    def _apply_target_manual_ip(self, adapter_desc, source_ip):
        """目标设备: 将本机网卡 IP 设为与源 IP 同网段 (源末位+1), 以便直连。

        掩码统一使用 SUBNET_MASK (/16), 与 169.254.x.x (APIPA) 地址段一致,
        避免两端掩码非对称导致源端无法回包 (连接超时)。
        若 IP 设置失败, 网卡可能停留在 APIPA (/16); 由于两端均为 /16, 仍可直连通信。
        """
        from ip_config import set_ip_via_api, set_ip_via_netsh
        parts = source_ip.split(".")
        try:
            last = int(parts[3])
            last = last + 1 if last < 254 else 1
            target_ip = ".".join(parts[:3] + [str(last)])
        except Exception:
            target_ip = SOURCE_IP
        success, msg = set_ip_via_api(adapter_desc, target_ip)
        # _log 线程安全: 后台线程调用自动转主线程渲染
        self._log(msg)
        if not success:
            # API 失败 → 回退 netsh (set_ip_via_netsh 内部已用 GBK 解码且默认 /16 掩码)
            success2, msg2 = set_ip_via_netsh(adapter_desc, target_ip)
            self._log(msg2)
            if success2:
                self._log(f"目标手动 IP 已生效: {target_ip} (掩码 {SUBNET_MASK})")
            else:
                self._log(f"警告: 目标手动 IP 设置失败 ({target_ip}), 网卡可能停留在 APIPA。\n"
                          f"  由于两端掩码已统一为 {SUBNET_MASK}, 即使本机为 APIPA 地址也可与源端 {source_ip} 直连通信。")
        else:
            self._log(f"目标手动 IP 已生效: {target_ip} (掩码 {SUBNET_MASK})")

    def _on_dhcp_button(self):
        """目标设备: 点击「寻找旧电脑」→ 后台启动 DHCP 服务器"""
        if self._transferring:
            self._log("传输正在进行中, 暂时无法操作")
            return
        if self._dhcp_server and self._dhcp_server.is_running():
            self._log("DHCP 服务器已在运行")
            return
        # 接收端不依赖具体网卡: DHCP 广播自行枚举所有有线网卡 (含 APIPA 等待),
        # 因此无需检查/选定网卡, 即使启动时网线未插也可正常发起
        self.ui.tk_button_dhcp.config(state="disabled")
        self.ui.tk_button_dhcp.configure(text="正在搜索...")
        threading.Thread(target=self._setup_target_dhcp, daemon=True).start()

    def _setup_target_dhcp(self):
        """目标设备: 不设置自身 IP, 依赖 APIPA (169.254.x.x) 自动地址, 直接启动 DHCP 服务器。

        说明: 目标作为 DHCP 服务器, 为源设备分配 169.254.100.2。两端掩码统一为
        SUBNET_MASK (/16), 目标只要有一个 169.254.x.x 的 APIPA 地址 (Windows 在
        网线连通且无其他 DHCP 响应时自动分配), 就与源端 169.254.100.2 处于同一
        /16 网段, 可直连通信。无需手动为自身设 IP —— 之前调用 set_ip_via_api 在
        部分网卡上返回 ERROR_INVALID_PARAMETER(87) "API 参数无效", 导致 DHCP 服务
        器始终无法启动, 故改为纯 APIPA + DHCP 服务器方案。
        """
        from dhcp_server import MiniDHCPServer

        # 后台线程禁止直接调 tk: 一律经 _post_ui / _log / _ui_after 转主线程
        self._post_ui(self.ui.tk_select_box_discover.config,
                      values=("等待源设备连接...",))

        # 等待 APIPA 地址出现: 刚插网线时 Windows 需约 15 秒 ARP 探测后才分配
        # 169.254.x.x。轮询等待可保证: ① 广播出口网卡已就绪 (OFFER/ACK 有正确
        # 出口); ② 接收端自身处于 /16 网段, 后续才能路由到源端 169.254.100.2。
        wired_ips = self._get_wired_nic_ips()
        # 仅当有线网卡完全没有 IP 时才等待 APIPA 出现 (刚插网线时 Windows 需约
        # 15 秒 ARP 探测)。若网卡已有地址 (APIPA 或 DHCP/静态), 立即启动 DHCP
        # 服务器 —— 原实现"只要没有 169.254 就等 20 秒", 会让已经连好网线的用户
        # 白等 20 秒, 是"点寻找旧电脑后半天没反应"的一个直接原因。
        if not wired_ips:
            self._log("等待网卡获得 APIPA 地址 (169.254.x.x), 最多 20 秒...")
            _apipa_deadline = time.time() + 20
            while time.time() < _apipa_deadline:
                time.sleep(1)
                wired_ips = self._get_wired_nic_ips()
                if wired_ips:
                    break
        _cur_ip = wired_ips[0] if wired_ips else ""
        if wired_ips:
            self._log(f"有线网卡 IP 列表: {wired_ips}, DHCP 广播将从所有有线网卡发出")
        if _cur_ip:
            self._log(f"DHCP 模式: 接收端不设置自身 IP, 当前本机地址 {_cur_ip} (掩码 {SUBNET_MASK})。"
                      f"只要该地址属于 169.254.x.x/16, 即可与源端 {DHCP_ASSIGNED_IP} 直连。")
            if not any(ip.startswith("169.254.") for ip in wired_ips):
                self._log(f"警告: 本机有线网卡地址 {wired_ips} 均不在 169.254.x.x/16 网段, "
                          f"源端获取 {DHCP_ASSIGNED_IP} 后可能无法与接收端直连。"
                          f"请拔掉其它网络连接 (或重新插拔网线) 后再点「寻找旧电脑」。")
        else:
            self._log(f"DHCP 模式: 接收端不设置自身 IP, 等待 APIPA 自动分配 (请确认网线已连接); "
                      f"DHCP 服务器将照常启动。")

        # 获取本地 MAC 地址列表，排除本地网卡的 DHCP 自响应
        from nic_scanner import get_local_mac_addresses
        local_macs = get_local_mac_addresses()
        self._log(f"本地 MAC 排除列表: {local_macs}")

        try:
            self._dhcp_server = MiniDHCPServer(exclude_macs=local_macs, out_ips=wired_ips)

            def _on_client(ip, mac, hostname):
                self._update_discover_list(ip, mac, hostname)

            self._dhcp_server.set_on_client(_on_client)
            self._dhcp_server.start()
        except Exception as e:
            self._log(f"DHCP 启动失败: {e}")
            self._post_ui(self.ui.tk_button_dhcp.configure,
                          text="寻找旧电脑", state="normal")
            return
        self._use_dhcp = True
        self._source_ip = DHCP_ASSIGNED_IP  # 目标 DHCP 分配的源 IP
        self._discover_count = 0

        self._log(f"DHCP 已启动, 源设备将获取 {DHCP_ASSIGNED_IP} (60s 超时)...")
        # 60 秒后自动选择目标 (后台线程经 _ui_after 转主线程调度)
        self._ui_after(60000, self._auto_select_target)
        # 60 秒内禁用搜索按钮 (与 DHCP 自动关闭倒计时一致), 归零后由 _dhcp_tick 恢复
        self._post_ui(self.ui.tk_button_dhcp.configure,
                      text="搜索中 (60 秒)...", state="disabled")

        # 倒计时: 显示 DHCP 服务剩余运行时间, 归零后自动关闭
        # (周期任务需 after id 做 after_cancel, 转主线程方法启动)
        self._post_ui(self._start_dhcp_countdown)

    def _start_dhcp_countdown(self):
        """主线程: 初始化 DHCP 60 秒倒计时 (由后台线程经 _post_ui 转主线程调用,
        因为周期 after 需要 after id 供 after_cancel)"""
        self._dhcp_countdown = 60
        if self._dhcp_countdown_after:
            try:
                self.ui.after_cancel(self._dhcp_countdown_after)
            except Exception:
                pass
            self._dhcp_countdown_after = None
        self.ui.tk_label_dhcp_status.config(
            text="设备发现服务将在 60 秒后自动关闭")
        self._dhcp_countdown_after = self.ui.after(1000, self._dhcp_tick)

    def _update_discover_list(self, ip, mac, hostname=""):
        """更新发现设备下拉框 (由 DHCP 服务器线程回调, 经 _post_ui 转主线程)"""
        self._discover_count += 1
        if not hostname:
            hostname = "Unknown"

        clients = self._dhcp_server.get_clients()
        values = [
            f"{c['ip']} | {c['mac']} | {c['hostname']}" for c in clients
        ]
        if not values:
            values = [f"{ip} | {mac} | {hostname}"]

        def _update():
            self.ui.tk_select_box_discover.config(values=values)
            if values:
                self.ui.tk_select_box_discover.current(0)
            self._log(f"[DHCP] 发现设备: {ip} ({mac}) {hostname}")
        self._post_ui(_update)

    def _reset_dhcp_button(self):
        """恢复「寻找旧电脑」按钮为可点击状态 (倒计时结束或服务已停止)"""
        try:
            self.ui.tk_button_dhcp.configure(text="寻找旧电脑", state="normal")
        except Exception:
            pass

    def _dhcp_tick(self):
        """每秒更新 DHCP 服务关闭倒计时, 归零后自动停止服务"""
        self._dhcp_countdown_after = None
        if not (self._dhcp_server and self._dhcp_server.is_running()):
            # DHCP 已停止 (提前结束/连接成功): 恢复搜索按钮
            self._reset_dhcp_button()
            return
        # 动态刷新广播出口: APIPA 地址可能刚出现, 立即加入广播出口,
        # 保证源端 DISCOVER 时 OFFER/ACK 有正确出口网卡, 提高发现成功率
        try:
            self._dhcp_server.set_out_ips(self._get_wired_nic_ips())
            self._dhcp_server.refresh_out_ips()
        except Exception:
            pass
        self._dhcp_countdown -= 1
        if self._dhcp_countdown <= 0:
            self._dhcp_countdown = 0
            try:
                self._dhcp_server.stop()
            except Exception:
                pass
            self.ui.tk_label_dhcp_status.config(
                text="设备发现服务已自动关闭 (60 秒内未连接旧设备)")
            self._log("设备发现已自动关闭 (60 秒内未连接旧设备)")
            self._reset_dhcp_button()
            return
        self.ui.tk_label_dhcp_status.config(
            text=f"设备发现服务将在 {self._dhcp_countdown} 秒后自动关闭")
        self._dhcp_countdown_after = self.ui.after(1000, self._dhcp_tick)

    def _auto_select_target(self):
        """60 秒后检查：如果只有 1 个客户端，自动选定"""
        # 如果用户已经离开网络发现页面（步骤 2），不再修改下拉框
        if getattr(self.ui, '_step', -1) != 2:
            return
        if not self._dhcp_server or not self._use_dhcp:
            return

        clients = self._dhcp_server.get_clients()
        if len(clients) == 1:
            c = clients[0]
            self.ui.after(0, lambda: self._log(
                f"[/] 自动选定目标设备: {c['ip']} ({c['mac']})"
            ))
            self.ui.after(0, lambda: self.ui.tk_select_box_discover.config(
                values=[f"{c['ip']} | {c['mac']} | {c['hostname']}"]
            ))
            self.ui.after(0, lambda: self.ui.tk_select_box_discover.current(0))
        elif len(clients) > 1:
            self.ui.after(0, lambda: self._log(
                f"发现 {len(clients)} 个设备, 请从下拉框中选择目标设备"
            ))
        else:
            self.ui.after(0, lambda: self._log("DHCP 超时: 未发现任何设备"))

    # ==================== 源设备：启动服务器 ====================

    def _add_firewall_exception(self, port: int):
        """源设备: 放行入站 TCP port —— 使用【标准 Windows 防火墙提示】, 而非管理员/UAC 提权。

        说明: 标准 Windows 防火墙机制是 —— 当程序开始监听某端口且没有放行规则时,
        系统会自动弹出"Windows 安全中心警报"对话框, 用户勾选网络类型并点【允许访问】即可,
        全程不需要管理员权限。因此这里不再用 UAC 提权去写规则, 而是:
          1) 先做一次非提权的 netsh 尝试 (若进程本身已是管理员则直接成功, 不弹任何窗);
          2) 失败(非管理员)则直接依赖服务器监听端口后由 Windows 弹出的标准防火墙提示框,
             并在日志里引导用户点击"允许访问"。
        WinPE 默认无防火墙, 直接跳过。
        """
        import subprocess

        rule_name = f"DiskCopyTool_In_TCP{port}"
        netsh_cmd = (
            f'netsh advfirewall firewall delete rule name={rule_name} & '
            f'netsh advfirewall firewall add rule name={rule_name} dir=in '
            f'action=allow protocol=TCP localport={port} profile=any'
        )
        try:
            res = subprocess.run(
                ["cmd", "/c", netsh_cmd],
                capture_output=True, timeout=10, encoding="gbk", errors="replace",
            )
            if res.returncode == 0:
                self._log(f"已放行防火墙入站规则: TCP {port} (接收端跨机可访问)")
                return
        except FileNotFoundError:
            self._log("提示: 当前环境无 netsh advfirewall (如 WinPE), 防火墙默认关闭, 可忽略。")
            return
        except Exception:
            pass

        # 非管理员 / netsh 不可用: 不弹 UAC, 改用标准防火墙提示框
        self._log(
            f"防火墙: 未能自动放行 (当前非管理员权限, 不再请求 UAC 提权)。\n"
            f"  源端开始监听 TCP {port} 后, Windows 会弹出【标准防火墙提示】"
            f"(Windows 安全中心警报)。\n"
            f"  请在弹窗中勾选网络类型并点击【允许访问】, 接收端即可跨机连接。\n"
            f"  (若长时间未弹窗, 也可右键以管理员身份运行本工具自动放行。)")

    def _start_source_server(self, manual_ip=None):
        """源设备启动 HTTP 文件服务器 (manual_ip 非空时为手动 IP 模式)"""
        self._log("\n" + "=" * 50)
        self._log("源设备模式: 启动文件服务器")
        self._log("=" * 50)

        # 手动 IP 模式: 先把本机网卡设为该 IP, 再启动服务器
        # 网卡为全自动检测, 直接使用自动选择的有线网卡
        if manual_ip:
            adapter_desc = self._get_adapter_desc_from_auto()
            if adapter_desc:
                self._apply_manual_ip(adapter_desc, manual_ip)
            self._log(f"手动 IP 模式: 源设备将绑定 {manual_ip}")

        # 重命名 GTMC_User_Profiles (如存在)
        self._rename_gtmc_user_profiles()

        # 生成验证码 + 固定自签名证书 (HTTPS 必需; 证书与 IP 无关, 统一使用一份)
        self._auth_code = tls_utils.generate_auth_code()

        # 立即在传输页显示验证码横幅 — 必须在证书生成/防火墙等耗时操作之前,
        # 否则用户进入传输页后长时间看不到验证码 (防火墙 subprocess 最多阻塞 10 秒)
        self._set_status(f"验证码: {self._auth_code} ")
        self.ui.show_auth_code(self._auth_code)

        try:
            cert_paths = tls_utils.get_or_create_fixed_cert()
            self._log(f"使用固定 TLS 证书: {cert_paths[0]}")
            self._log(f"证书 SAN: {tls_utils.cert_san_info(cert_paths[0])}")
        except Exception as e:
            self._log(f"错误: 生成 TLS 证书失败, 无法启动服务器: {e}")
            self._set_status("证书生成失败")
            return

        self._file_server = FileServer(
            partition_map=self._partition_map,
            log_callback=self._log,
            auth_code=self._auth_code,
            cert_paths=cert_paths,
            status_callback=self._on_source_report,
        )
        self._file_server.start()
        self._add_firewall_exception(TRANSFER_PORT)
        self._log(f"文件服务器已启动, 监听 0.0.0.0:{TRANSFER_PORT}")
        self._log(f"将对外提供以下盘符数据: {self._partition_map}")
        self._log(
            f"本地测试地址: https://127.0.0.1:{TRANSFER_PORT} "
            f"(浏览器会提示自签名证书, 点击'高级'→'继续访问'即可; API 需带 ?pwd={self._auth_code})"
        )
        self._log("\n" + "*" * 50)
        self._log(f"  连接验证码: {self._auth_code}")
        self._log("  请在目标设备上输入此验证码")
        self._log("*" * 50 + "\n")
        self._set_status(f"验证码: {self._auth_code} ")

        # UI 专用区域常驻醒目显示验证码
        self.ui.show_auth_code(self._auth_code)

        self._transferring = True
        self._transfer_done = False
        self._source_done_marked = False
        self._source_interrupted_marked = False
        # 重置服务器连接/完成标志: 上次传输可能残留 done/连接状态, 影响新一轮判定
        FileServerHandler.transfer_done_flag = False
        FileServerHandler.ever_connected = False
        FileServerHandler.last_activity = 0.0
        self.ui.tk_button_mqfzl35t.config(text="传输中...", state="disabled")
        # 传输期间霸占全屏, 避免用户误操作电脑
        try:
            self.ui.lock_screen()
        except Exception:
            pass
        # 源端无完成回调: 轮询检测"接收端已完成(/report done)" 或"已断开(长时间无请求)"
        self._source_done_after = self.ui.after(2000, self._poll_source_done)

    def _poll_source_done(self):
        """源设备: 检测接收端已完成 (显式 /report done) 或已断开 (长时间无请求) → 解除全屏锁定"""
        self._source_done_after = None
        try:
            if self._file_server and self._file_server.is_transfer_done():
                self._source_mark_done()
                return
            # 接收端曾连接, 但长时间无任何请求且未显式完成 → 连接已中断
            # (网络断开 / 接收端程序关闭 / 验证码输入错误), 此时绝不进入"完成页"
            if (FileServerHandler.ever_connected
                    and not FileServerHandler.transfer_done_flag
                    and time.time() - FileServerHandler.last_activity > SOURCE_IDLE_TIMEOUT):
                if FileServerHandler.auth_failed_flag:
                    # 验证码错误: 提示后保持服务器运行, 继续等待接收端修正验证码重试
                    self._source_mark_auth_failed()
                    return
                self._source_mark_interrupted()
                return
            self._source_done_after = self.ui.after(2000, self._poll_source_done)
        except Exception:
            pass

    def _on_source_report(self, payload):
        """源设备: 接收端 /report 上报回调 (请求线程中调用, 经 _post_ui 转主线程更新 UI)"""
        self._post_ui(self._handle_source_report, payload)

    def _handle_source_report(self, payload):
        """源设备 (主线程): 处理接收端的进度/完成上报"""
        try:
            if not payload:
                return
            if payload.get("done"):
                self._source_mark_done()
                return
            # 进度上报: 更新源端进度条与状态文字
            total_bytes = payload.get("bytes_total") or 0
            bytes_done = payload.get("bytes_done") or 0
            total_files = payload.get("files_total") or 0
            files_done = payload.get("files_done") or 0
            if total_bytes > 0:
                self._set_progress(bytes_done, total_bytes)
            elif total_files > 0:
                self._set_progress(files_done, total_files)
            status = payload.get("status") or ""
            if status:
                self._set_status(f"[接收端] {status}")
        except Exception:
            pass

    def _source_mark_done(self):
        """源设备: 接收端传输完成 → 解除全屏锁定并进入完成页, 允许正常关闭"""
        if getattr(self, "_source_done_marked", False):
            return
        self._source_done_marked = True
        try:
            self.ui.unlock_screen()
        except Exception:
            pass
        self._set_progress(100, 100)
        self._set_status("接收端已完成拷贝，可安全关闭此窗口")
        # 进入发送端"传输完成"页面
        try:
            self.ui.go_step(5)
        except Exception:
            pass
        self._log("接收端已完成拷贝，进入完成页，可正常关闭窗口")

    def _source_mark_auth_failed(self):
        """源设备: 接收端输入的验证码有误 → 解除全屏锁定并提示。
        保持文件服务器运行并继续轮询, 等待接收端修正验证码后重试;
        绝不进入"传输完成"页, 也不停止服务器 (否则接收端无法重试)。"""
        if getattr(self, "_source_auth_notified", False):
            # 已提示过: 仅继续轮询等待接收端修正验证码后重试
            self._source_done_after = self.ui.after(2000, self._poll_source_done)
            return
        self._source_auth_notified = True
        try:
            self.ui.unlock_screen()
        except Exception:
            pass
        # 验证码错误: 保留验证码横幅 (供两端对照), 状态行与错误区文案保持一致
        try:
            self.ui.show_transfer_error(AUTH_FAIL_MSG_SRC, status_text=AUTH_FAIL_TITLE)
        except Exception:
            pass
        self._set_status(AUTH_FAIL_TITLE)
        self._log("接收端输入的验证码有误，已提示；保持服务器运行，等待接收端修正验证码后重试")
        # 不停止服务器/不进入完成页: 继续轮询, 接收端修正验证码重试后即恢复正常
        self._source_done_after = self.ui.after(2000, self._poll_source_done)

    def _source_mark_interrupted(self):
        """源设备: 接收端连接已中断 (断网/接收端程序关闭) → 解除全屏锁定并提示中断。
        绝不进入"传输完成"页; 停止文件服务器, 传输不再继续。"""
        if (getattr(self, "_source_done_marked", False)
                or getattr(self, "_source_interrupted_marked", False)):
            return
        self._source_interrupted_marked = True
        # 结束本轮传输状态并停掉完成轮询: 否则 _transferring 一直为 True,
        # 用户按提示返回后点"开始传输"会被"传输正在进行中"直接拦下, 无法重传
        self._transferring = False
        if getattr(self, "_source_done_after", None):
            try:
                self.ui.after_cancel(self._source_done_after)
            except Exception:
                pass
            self._source_done_after = None
        try:
            self.ui.unlock_screen()
        except Exception:
            pass
        self._set_progress(0)
        # 网络已中断: 先隐藏验证码横幅 —— 断网时"请在新设备上输入此验证码"已无意义,
        # 否则会出现"上方提示输入验证码 / 下方提示网络中断"的矛盾画面
        try:
            self.ui.hide_auth_code()
        except Exception:
            pass
        try:
            self.ui.show_transfer_error(NET_LOST_MSG_SRC, status_text=NET_LOST_TITLE)
        except Exception:
            pass
        # 状态行写在 show_transfer_error 之后, 确保两处文案一致
        self._set_status(NET_LOST_TITLE)
        # 恢复"重新启动传输"按钮
        try:
            self.ui.tk_button_mqfzl35t.config(text="重新启动传输", state="normal")
        except Exception:
            pass
        # 停止文件服务器: 不再继续等待
        try:
            if self._file_server:
                self._file_server.stop()
                self._file_server = None
        except Exception:
            pass
        self._log("与接收端的连接已中断，传输未完成；已隐藏验证码提示，请检查网线后重新开始传输")

    def _on_source_done_close(self):
        """发送端完成页「完成并关闭」/ 底部「完成」: 清理后台并退出程序"""
        self._log("发送端流程完成，正在关闭程序")
        try:
            self.shutdown()
        except Exception:
            pass
        try:
            self.ui.destroy()
        except Exception:
            pass
        import os
        os._exit(0)

    def _rename_gtmc_user_profiles(self):
        """检查源设备 D 盘，若存在 GTMC_User_Profiles 则重命名为 GTMC_User_ProfilesYYMMDD。

        仅在「运行环境 = WinPE 下」时执行重命名；正常 Windows 系统不改动用户文件夹，
        以免误改真实系统的用户配置目录。
        """
        if self.ui.winpe_var.get() != "winpe":
            self._log("当前非 WinPE 环境, 跳过 GTMC_User_Profiles 重命名")
            return

        d_drive = self._partition_map.get("D", "")
        if not d_drive:
            return

        src_path = os.path.join(d_drive, "GTMC_User_Profiles")
        if not os.path.isdir(src_path):
            return

        import datetime
        date_suffix = datetime.datetime.now().strftime("%y%m%d")
        new_name = f"GTMC_User_Profiles{date_suffix}"
        dst_path = os.path.join(d_drive, new_name)

        # 避免重名冲突
        counter = 1
        original_dst = dst_path
        while os.path.exists(dst_path):
            dst_path = f"{original_dst}_{counter}"
            counter += 1

        try:
            os.rename(src_path, dst_path)
            self._log(f"已将 GTMC_User_Profiles 重命名为 {os.path.basename(dst_path)}")
        except Exception as e:
            self._log(f"重命名 GTMC_User_Profiles 失败: {e}")

    # ==================== 目标设备：下载文件 ====================

    def _host_reachable(self, ip: str) -> bool:
        """检测源设备是否可达: 纯 TCP 9999 端口探测。
        传输实际使用 TCP 9999, 该端口已放行防火墙 — 探测它最能反映传输是否可用,
        且 TCP 握手由内核完成 (即使服务器应用繁忙/限流, 新连接仍能建立)。
        不依赖 ICMP ping: Windows 防火墙默认拦截 ping, 仅靠 ping 会在网络正常时误报。

        注意: 本探测"连上即 close、不发任何数据", 源端会把它识别为端口探活连接并静默
        忽略 (file_transfer.py: ThreadingFileServer.get_request), 不会污染源端日志。"""
        try:
            s = socket.create_connection((ip, TRANSFER_PORT), timeout=2)
            s.close()
            return True
        except (OSError, TimeoutError, Exception):
            return False

    def _start_network_monitor(self, source_ip):
        """启动后台网络监控线程: 检测传输过程中是否断网。
        每 1 秒 TCP 探测一次 9999 端口 (传输真实通道),
        连续 3 次无响应判定断网, 并立即在主线程提示用户。"""
        self._network_down = False
        self._network_monitor_stop.clear()

        def _monitor():
            fail_count = 0
            max_fails = 3  # 连续 3 次失败判定为断网
            while not self._network_monitor_stop.is_set():
                self._network_monitor_stop.wait(timeout=1)  # 每 1 秒探测一次
                if self._network_monitor_stop.is_set():
                    break
                if self._host_reachable(source_ip):
                    fail_count = 0  # 可达, 重置失败计数
                else:
                    fail_count += 1
                    if fail_count >= max_fails:
                        self._network_down = True
                        self._log(f"\n[诊断] 网络连接已断开! "
                                  f"(连续 {fail_count} 次无法连接 "
                                  f"{source_ip}:{TRANSFER_PORT})")
                        # 立即在主线程弹出提示, 不等传输结束/网络恢复
                        self._post_ui(self._on_network_lost_ui)
                        break

        t = threading.Thread(target=_monitor, daemon=True)
        t.start()

    def _stop_network_monitor(self):
        """停止网络监控线程"""
        self._network_monitor_stop.set()

    def _on_network_lost_ui(self):
        """网络断开时的立即 UI 提示 (在主线程执行)。
        只负责提示与按钮状态; 传输线程会在下次 stop_check 时自行停止。"""
        # 网络断开: 解除全屏锁定, 允许用户检查网线
        try:
            self.ui.unlock_screen()
        except Exception:
            pass
        self.ui.show_transfer_error(NET_LOST_MSG_TGT, status_text=NET_LOST_TITLE)
        self._set_status(NET_LOST_TITLE)
        if hasattr(self.ui, 'tk_button_mqfzl35t'):
            try:
                self.ui.tk_button_mqfzl35t.config(text="重新接收", state="normal")
            except Exception:
                pass

    def _start_target_download(self, manual_ip=None):
        """目标设备连接源设备下载文件。

        manual_ip: 若提供 (手动 IP 模式), 直接连接该源设备 IP, 无需 DHCP。
                   接收端【无需设置自身 IP】: 只要本机与源设备网络可达即可直连
                   (两端掩码已统一为 SUBNET_MASK, 即使本机停留在 APIPA 也能直连,
                   因为 169.254.x.x 同属 /16)。曾经"将本机设为源末位+1"的尝试会
                   扰动网卡, 导致随后 Python 的 TLS 握手失败, 现已移除。
        """
        if not manual_ip:
            # DHCP 模式: 必须先开启 DHCP 并等待源设备分配到 IP。
            # 允许继续的两种情况:
            #   ① DHCP 服务器仍在运行 (分配流程进行中, 源设备可能已拿到 IP)
            #   ② 服务器已因 60 秒倒计时自动关闭, 但源设备已通过 DHCP 拿到 IP
            #      (self._source_ip 已设置), 此时不再需要 DHCP, 可直接继续
            if not self._use_dhcp:
                self._log("请先点击「开启DHCP」启动 DHCP 服务器并等待源设备分配 IP")
                return
            if not (self._source_ip or (self._dhcp_server and self._dhcp_server.is_running())):
                self._log("DHCP 服务器未运行且未获得源设备 IP, 请重新点击「开启DHCP」")
                return
            # DHCP 已完成使命 (源设备已拿到 IP)，若服务器仍在运行则停止,
            # 释放端口避免干扰后续传输
            if self._dhcp_server and self._dhcp_server.is_running():
                self._log("源设备已通过 DHCP 获取 IP，正在关闭 DHCP 服务器...")
                self._dhcp_server.stop()
                self._log("DHCP 服务器已关闭")

        self._log("\n" + "=" * 50)
        self._log("目标设备模式: 连接源设备...")
        self._log("=" * 50)

        if manual_ip:
            # 手动 IP 模式: 接收端【不再设置自身 IP】。只要网络可达即直连,
            # 避免网卡被扰动导致随后的 TLS 握手失败 (浏览器能连、工具连不上即此因)。
            self._log(f"手动 IP 模式: 将直连源设备 {manual_ip}:{TRANSFER_PORT}")
            self._log(
                f"说明: 接收端不设置自身 IP (掩码已统一为 {SUBNET_MASK}), "
                f"网络可达即直连, 无需与本机设同网段地址。")

        # 输入源设备显示的验证码 (HTTPS 鉴权必需)
        # 优先读取主界面上的验证码输入框; 为空时再弹出对话框作为兜底
        code = (self.ui.get_auth_input() or "").strip()
        if not code:
            code = simpledialog.askstring(
                "验证码",
                "请输入源设备屏幕上显示的 4 位验证码:",
                parent=self.ui,
            )
        if not code or not code.strip():
            self._log("未输入验证码, 已取消接收")
            return
        self._auth_code = code.strip().upper()
        # 回填主界面输入框, 便于核对 (接收方不显示红色验证码横幅)
        self.ui.set_auth_input(self._auth_code)

        # 如果旧校验还在跑，通知它停止
        if self._verify_thread and self._verify_thread.is_alive():
            self._log("正在停止上一轮校验线程...")
            self._stop_verify = True
            self._verify_thread.join(timeout=3)
        self._stop_verify = False

        self._transferring = True
        self._transfer_done = False
        # 传输期间霸占全屏, 避免用户误操作电脑
        try:
            self.ui.lock_screen()
        except Exception:
            pass
        self._reset_progress("正在连接源设备...")
        self.ui.hide_transfer_error()  # 清除上次失败的错误提示
        self.ui.tk_button_mqfzl35t.config(text="连接中...", state="disabled")

        def _connect_and_download():
            # 后台线程禁止直接调 tk: 一律经 _log/_set_status/_post_ui 转主线程
            if manual_ip:
                # 手动 IP 模式: 直连填写的源设备 IP
                source_ip = manual_ip
                self._log(f"手动 IP 模式: 直连源设备 {manual_ip}:{TRANSFER_PORT}")
            elif self._use_dhcp:
                # DHCP 模式: 源 IP 由目标 DHCP 分配 (169.254.100.2)
                self._log(f"DHCP 模式: 直连源设备 {DHCP_ASSIGNED_IP}:{TRANSFER_PORT}")
                source_ip = DHCP_ASSIGNED_IP
            else:
                # APIPA 扫描
                self._log("APIPA 模式: 扫描源设备...")
                self._set_status("正在扫描源设备...")
                source_ip = scan_source_device(
                    log_callback=self._log, auth_code=self._auth_code
                )

            if not source_ip:
                self._log("未找到源设备。请确保:\n"
                          "  1. 源设备已启动并选择了'源设备'\n"
                          "  2. 网线已连接\n"
                          "  3. 两端网卡已选择")
                self._post_ui(self._on_transfer_failed)
                return

            # ---- 网络自检诊断: 打印本机 IP / 目标 IP / TCP 端口可达性 ----
            try:
                import socket as _sock
                self._log(f"[诊断] 拟连接源设备: {source_ip}:{TRANSFER_PORT}")
                # 本机所有网卡 IP
                try:
                    _hostname = _sock.gethostname()
                    _ips = _sock.getaddrinfo(_hostname, None)
                    _local_ips = sorted({i[4][0] for i in _ips if ":" not in i[4][0]})
                    self._log(f"[诊断] 本机 IP 列表: {_local_ips}")
                except Exception as e:
                    self._log(f"[诊断] 获取本机 IP 失败: {e}")
                # TLS 可达性探测: 直接做 TLS 握手 (同时验证 TCP+TLS, 无需验证码)
                # 说明: 旧版用裸 create_connection 探端口后立刻 close, Windows 上会产生 RST,
                # 触发源端服务器误报"读取握手头失败/超时 (按明文处理)"。改用真实 TLS 握手探测,
                # 既能干净验证握手是否成功, 也不会让源端误以为遭受明文攻击而拒绝连接。
                try:
                    import ssl as _ssl
                    _ctx = _ssl._create_unverified_context()
                    # Python 3.13 已移除 ssl.wrap_socket, 改用 context.wrap_socket
                    _s = _ctx.wrap_socket(
                        _sock.create_connection((source_ip, TRANSFER_PORT), timeout=5),
                        server_side=False,
                    )
                    _s.close()
                    self._log(f"[诊断] TLS 端口 {source_ip}:{TRANSFER_PORT} 可达 (TCP+TLS 握手成功)")
                except Exception as e:
                    _reason = getattr(e, "reason", e)
                    _winerr = getattr(_reason, "errno", getattr(e, "winerror", None))
                    if isinstance(e, TimeoutError) or _winerr in (10060, 110):
                        _hint = ("超时/无响应 → 典型防火墙拦截 (TCP 被丢弃)。\n"
                                 "      请在源端: 以管理员运行本工具, 或手动允许入站 TCP 9999。")
                    elif _winerr in (10061, 111, 61):
                        _hint = "连接被拒绝 → 源端服务器未启动或端口错误"
                    elif _winerr in (10065, 10051, 101, 51):
                        _hint = "主机不可达 → 检查网线/同网段/源端 IP 是否正确"
                    else:
                        _hint = f"TLS 握手失败 → {e} (若源端为旧版 exe 未完成 TLS 握手, 请重新打包)"
                    self._log(f"[诊断] TLS 端口 {source_ip}:{TRANSFER_PORT} 不可达: {type(e).__name__}: {e}")
                    self._log(f"  → {_hint}")
            except Exception as e:
                self._log(f"[诊断] 网络自检异常: {e}")

            self._last_source_ip = source_ip  # 供校验阶段缺失文件重试下载
            self._log(f"连接源设备: {source_ip}:{TRANSFER_PORT}")
            self._post_ui(self.ui.tk_button_mqfzl35t.config, text="接收中...")

            # 速度追踪: [_last_bytes, _last_time] 可变列表用于跨闭包共享
            _speed_tracker = [0, time.time()]
            _report_ts = [0.0]  # 向源端同步进度的节流时间戳

            def _fmt_speed(byte_rate: float) -> str:
                """字节/秒 → 人类可读速度字符串"""
                if byte_rate < 1024:
                    return f"{byte_rate:.0f} B/s"
                elif byte_rate < 1024 * 1024:
                    return f"{byte_rate / 1024:.1f} KB/s"
                else:
                    return f"{byte_rate / (1024 * 1024):.1f} MB/s"

            def _progress(files_done, total_files, bytes_done, total_bytes):
                now = time.time()
                elapsed = now - _speed_tracker[1]
                if elapsed >= 1.0 and bytes_done > _speed_tracker[0]:
                    rate = (bytes_done - _speed_tracker[0]) / elapsed
                    _speed_tracker[0] = bytes_done
                    _speed_tracker[1] = now
                    speed_str = _fmt_speed(rate)
                else:
                    speed_str = ""
                status = f"正在传输... {files_done}/{total_files} 文件"
                if speed_str:
                    status += f"  ({speed_str})"
                # 向源端同步拷贝进度 (节流 1 秒, 后台线程发送, 失败静默)
                if now - _report_ts[0] >= 1.0:
                    _report_ts[0] = now
                    self._report_to_source({
                        "done": False,
                        "files_done": files_done,
                        "files_total": total_files,
                        "bytes_done": bytes_done,
                        "bytes_total": total_bytes,
                        "status": status,
                    })
                self._set_status(status)
                # 总进度条: 优先按字节占比 (大文件传输时文件数不变但字节在涨,
                # 仅按文件数会导致进度条长时间不动); 无总字节信息时退回按文件数
                if total_bytes > 0:
                    self._set_progress(bytes_done, total_bytes)
                elif total_files > 0:
                    self._set_progress(files_done, total_files)

            def _check_stop():
                """供 file_transfer.download_files 轮询, 窗口关闭时返回 'cancel', 断网返回 'network_down'"""
                if self._stop_transfer:
                    return "cancel"
                if self._network_down:
                    return "network_down"
                return None

            # ---- 启动网络断开监控 ----
            self._start_network_monitor(source_ip)

            # 启用边传边校验: 先下载 FullFilelist_DEF.csv 再传输, 文件传输完成后由
            # 独立校验线程立即复核"存在+大小", 与传输并行; 校验结果写入确认清单,
            # 供后续校验阶段增量跳过, 大幅缩短校验时间
            pre_verified_out = [None]
            success, files, bytes_done, errors = download_files(
                server_ip=source_ip,
                partition_map=self._partition_map,
                log_callback=self._log,
                progress_callback=_progress,
                partition_progress_callback=self._partition_progress,
                partition_count=self._ntfs_partition_count,
                auth_code=self._auth_code,
                conflict_callback=self._resolve_conflicts,
                stop_check=_check_stop,
                verify_after_transfer=True,
                pre_verified_out=pre_verified_out,
                verify_progress_callback=self._verify_online_progress,
            )
            self._pre_verified_file = pre_verified_out[0]

            # ---- 停止网络断开监控 ----
            self._stop_network_monitor()

            self._post_ui(self._on_download_complete, success, files, bytes_done, errors)

        threading.Thread(target=_connect_and_download, daemon=True).start()

    def _on_transfer_failed(self):
        """传输失败，恢复按钮"""
        self._transferring = False
        self._transfer_done = False
        self._use_dhcp = False
        # 传输失败: 解除全屏锁定, 允许用户操作
        try:
            self.ui.unlock_screen()
        except Exception:
            pass
        self.ui.hide_transfer_error()
        if hasattr(self, "_dhcp_server") and self._dhcp_server:
            self._dhcp_server.stop()
            self._dhcp_server = None
        self._reset_progress("传输失败")
        self.ui.tk_select_box_discover.config(values=("等待 DHCP 响应...",))
        self.ui.tk_button_mqfzl35t.config(text="开始接收", state="normal")
        self.ui.tk_button_dhcp.configure(text="寻找旧电脑", state="normal")

    def _report_to_source(self, payload: dict):
        """后台线程: 向源端上报进度/完成 (HTTPS POST /report, 失败静默不影响传输)"""
        host = self._last_source_ip or self._source_ip
        auth = self._auth_code
        if not host or not auth:
            return
        try:
            import file_transfer as _ft
            _ft.post_report(host, TRANSFER_PORT, auth, payload)
        except Exception:
            pass

    def _on_download_complete(self, success, files, bytes_done, errors):
        """下载完成回调"""
        self._transferring = False
        # 传输结束 (成功/失败/断开): 解除全屏锁定, 允许用户操作
        try:
            self.ui.unlock_screen()
        except Exception:
            pass
        # 传输过程中网络断开: 停止等待并提示用户
        if self._network_down:
            self._network_down = False
            self._log("\n传输中断: 网络连接已断开")
            self._reset_progress("网络已断开")
            self.ui.hide_config_detect()
            self.ui.set_button_next("disabled")
            self.ui.set_button_prev("normal", text="< 返回")
            self.ui.show_transfer_error(NET_LOST_MSG_TGT, status_text=NET_LOST_TITLE)
            self._set_status(NET_LOST_TITLE)
            self.ui.tk_button_mqfzl35t.config(text="重新接收", state="normal")
            if hasattr(self, "_dhcp_server") and self._dhcp_server:
                self._dhcp_server.stop()
                self._dhcp_server = None
            if hasattr(self, "_tgt_server") and self._tgt_server:
                self._tgt_server.stop()
                self._tgt_server = None
            return

        # 传输未启动就失败 (files==0: 验证码错误、网络不通等)
        if not success and files == 0:
            self._log("\n传输未启动: 验证码错误或无法连接到发送端")
            self._reset_progress("传输失败")
            self.ui.hide_config_detect()
            self.ui.set_button_next("disabled")
            # 上一步按钮可用: 引导用户回到验证码输入页修正验证码
            if self._device_type == "目标设备":
                self.ui.set_button_prev("normal", text="< 返回修改验证码")
                # 显示醒目红色错误提示
                self.ui.show_transfer_error(
                    AUTH_FAIL_MSG_TGT, status_text="请返回修改验证码"
                )
            else:
                self.ui.set_button_prev("normal")
                self.ui.show_transfer_error(
                    "连接失败 — 无法连接到接收端，请检查网络后重试"
                )
            # 重置传输按钮，允许重试
            self.ui.tk_button_mqfzl35t.config(text="重试接收", state="normal")
            if hasattr(self, "_dhcp_server") and self._dhcp_server:
                self._dhcp_server.stop()
                self._dhcp_server = None
            if hasattr(self, "_tgt_server") and self._tgt_server:
                self._tgt_server.stop()
                self._tgt_server = None
            return

        # 传输中途失败 (部分文件未传完, 网络中断等): 不进入完成/配置导入流程
        if not success:
            self._log("\n传输未完成: 网络中断或部分文件传输失败")
            self._reset_progress("传输中断")
            self.ui.hide_config_detect()
            self.ui.set_button_next("disabled")
            self.ui.set_button_prev("normal", text="< 返回")
            self.ui.show_transfer_error(
                TRANSFER_INCOMPLETE_MSG, status_text=TRANSFER_INCOMPLETE_TITLE
            )
            self._set_status(TRANSFER_INCOMPLETE_TITLE)
            self.ui.tk_button_mqfzl35t.config(text="重新接收", state="normal")
            if hasattr(self, "_dhcp_server") and self._dhcp_server:
                self._dhcp_server.stop()
                self._dhcp_server = None
            if hasattr(self, "_tgt_server") and self._tgt_server:
                self._tgt_server.stop()
                self._tgt_server = None
            return

        # 传输成功: 通知源端 (发送端据此解除全屏锁定并进入完成页)
        self._report_to_source({"done": True})
        self._log("\n传输成功！")
        self._transfer_done = True
        self.ui.hide_transfer_error()
        # 边传边校验: 传输阶段结束后展示最终确认状态 (hide_transfer_error 已重置该标签)
        if self._pre_verified_file:
            self.ui.set_verify_online_status(
                "文件确认：已完成，已确认文件将在校验阶段跳过重复校验"
            )
        # 传输完成: 隐藏"开始传输"按钮, 清理 UI
        self.ui.hide_start_button()

        # 传输完成 → 自动启动数据校验 (后台线程): 增量确认 + 报告打包 + 自动上传
        # (校验页已取消, 校验进度/日志通过传输页状态行与日志展示)
        self._start_auto_verification()

        # 对于接收方: 检测 F:\\systemconfig.ini
        if self._device_type == "目标设备":
            manual_mode = False
            try:
                manual_mode = bool(getattr(self.ui, "tk_var_manual_import", None)
                                   and self.ui.tk_var_manual_import.get())
            except Exception:
                pass
            config_path, time_str = config_transfer.get_config_from_ini()
            self.ui.hide_config_detect()
            if config_path and not manual_mode:
                self._detected_config_path = config_path
                self._log(f"在 F 盘发现系统配置文件: {config_path}")
                self._log("检测到系统配置，后台自动导入中...")
                # 自动导入 (不跳转总结页, 校验完成后统一进入)
                self._populate_config_folders()
                self._select_config_folder(config_path)
                self._auto_import_active = True
                self.ui.after(150, self._on_import_config)
            else:
                # 手动模式或无配置: 直接进入传输总结页 (校验结果完成后自动刷新)
                if manual_mode:
                    self._log("手动导入模式: 请在总结页选择配置文件夹")
                else:
                    self._log("未检测到系统配置备份")
                self._summary_entered = True
                self.ui.go_step(4)
                self._render_summary()
        else:
            # 发送端: 传输完成即结束 (校验自动在后台进行, 无后续页面)
            self.ui.set_button_next("disabled")
            self._log("传输完成，数据校验报告将自动生成并上传")

        # 重置进度条
        self._set_progress(0)
        self._set_status("传输完成 — 数据校验自动进行中")

    def _on_use_detected_config(self):
        """接收端: 使用检测到的配置文件并自动导入 (不跳转总结页)"""
        if not hasattr(self, '_detected_config_path'):
            return
        self.ui.hide_config_detect()
        self._log(f"将使用检测到的配置: {self._detected_config_path}")
        # 先填充配置文件夹列表, 再选中检测到的路径
        self._populate_config_folders()
        self._select_config_folder(self._detected_config_path)
        # 自动开始导入 (已自动选中配置路径, 无需用户再点击)
        self._auto_import_active = True
        self.ui.after(150, self._on_import_config)

    def _on_skip_detected_config(self):
        """接收端步骤 4: 用户跳过检测到的配置文件"""
        self.ui.hide_config_detect()
        self._log("已跳过自动检测的配置文件")
        self._populate_config_folders()
        self.ui.go_step(4)

    def _select_config_folder(self, target_path: str):
        """在步骤 5 的配置文件夹下拉框中选中指定路径"""
        try:
            values = list(self.ui.tk_combo_config_folder["values"])
            for i, v in enumerate(values):
                if v == "未检测到配置备份":
                    continue
                # v 是 display_name, 需从 _config_folders 反查
                if hasattr(self, '_config_folders') and self._config_folders:
                    for display, path in self._config_folders:
                        if path == target_path:
                            if display in values:
                                idx = values.index(display)
                                self.ui.tk_combo_config_folder.current(idx)
                                self.ui.tk_label_config_status.config(
                                    text=f"已选中: {display} (自动检测)"
                                )
                                return
            # 未在列表中找到: 手动插入
            folder_name = os.path.basename(target_path)
            if hasattr(self, '_config_folders'):
                self._config_folders.insert(0, (folder_name, target_path))
            if "未检测到配置备份" in values:
                values = [folder_name]
            else:
                values.insert(0, folder_name)
            self.ui.tk_combo_config_folder["values"] = values
            self.ui.tk_combo_config_folder.current(0)
            self.ui.tk_label_config_status.config(
                text=f"已选中: {folder_name} (自动检测)"
            )
        except Exception as e:
            self._log(f"选中配置文件夹时出错: {e}")

    # ==================== 配置导入导出 ====================

    def _on_export_config(self):
        """步骤 1: 导出系统配置按钮 (发送端)"""
        button = self.ui.tk_button_export_config
        button.config(state="disabled", text="导出中")
        self._set_status("正在导出系统配置...")

        # 清空并启用导出日志区域，重置状态表格
        self._clear_export_log()
        if hasattr(self.ui, '_init_export_table'):
            self.ui._init_export_table()
        self._log_export("开始导出系统配置...")
        self._log_export(f"目标: F:\\Appl\\{self._today_str()}\\")
        self._log_export("")

        def _status_cb(name, status):
            """线程安全地更新导出状态表格 (经 _post_ui 转主线程)"""
            self._post_ui(self.ui._update_export_item_status, name, status)

        def _export():
            try:
                success, export_path = config_transfer.export_config(
                    # _log_export 线程安全: 自动转主线程渲染
                    log_callback=lambda msg: self._log_export(msg),
                    status_callback=_status_cb,
                )
                self._post_ui(self._on_export_done, success, export_path)
            except Exception as e:
                self._post_ui(self._on_export_error, str(e))

        threading.Thread(target=_export, daemon=True).start()

    def _on_export_done(self, success, export_path):
        button = self.ui.tk_button_export_config
        # 只要导出目录有效(非空字符串), 就尝试压缩上传;
        # 部分导出项失败不影响已导出的数据上传
        if export_path:
            self._config_export_done = True  # 已完成导出, 步骤1不再提醒
            button.config(text="正在压缩", bootstyle="info", state="disabled")
            if success:
                self._set_status(f"系统配置已导出到: {export_path}")
            else:
                self._set_status(f"部分配置导出失败，仍将压缩并上传 {export_path}")
            self._log(f"\n配置导出完成!")
            self._log(f"  路径: {export_path}")
            if not success:
                self._log(f"  注意: 部分导出项目失败，但已导出的配置仍会压缩上传")
            self._log(f"  文件传输时, F 盘数据将包含此配置文件夹")
            self._log_export("")
            self._log_export("配置导出完成!")
            self._log_export(f"  路径: {export_path}")
            if not success:
                self._log_export("  注意: 部分项目失败，已导出配置仍将压缩上传")
            # 在后台线程中压缩并上传 (无论成功与否都上传)
            self._start_compress_upload(export_path)
        else:
            button.config(text="导出失败(可重试)", bootstyle="danger", state="normal")
            self._set_status("配置导出失败，未生成导出目录")
            self._log_export("")
            self._log_export("配置导出失败，请查看上方日志")

    def _start_compress_upload(self, export_path):
        """在后台线程中压缩导出文件夹并上传到 Profile 服务器。"""
        import config_transfer

        # 线程安全的日志写入 (_log_export 自动转主线程操作 Tkinter)
        def _log_safe(msg):
            self._log_export(msg)

        def _run():
            try:
                _log_safe("")
                _log_safe("=" * 50)
                _log_safe("开始压缩并上传系统配置...")
                compress_ok, zip_path, upload_ok = \
                    config_transfer.compress_and_upload_config(
                        export_path, log_callback=_log_safe
                    )
                # 在主线程中更新按钮状态
                self._post_ui(self._on_upload_done, compress_ok, upload_ok, zip_path)
            except Exception as e:
                self._post_ui(self._on_upload_error, str(e))

        t = threading.Thread(target=_run, daemon=True)
        t.start()

    def _on_upload_done(self, compress_ok, upload_ok, zip_path):
        button = self.ui.tk_button_export_config
        self._log_export("=" * 50)
        if upload_ok:
            button.config(text="导出完成", bootstyle="success", state="normal")
            self._set_status("系统配置已导出并上传成功")
            self._log_export("导出并上传完成!")
            self._log(f"\n系统配置已上传到 Profile 服务器")
            self._log(f"  ZIP: {zip_path}")
        elif compress_ok:
            button.config(text="导出完成", bootstyle="warning", state="normal")
            self._set_status("配置已压缩，但上传失败(ZIP 已本地保留)")
            self._log_export(f"上传失败, ZIP 已本地保留: {zip_path}")
            self._log(f"\n压缩完成但上传失败, ZIP 已本地保留")
            self._log(f"  ZIP: {zip_path}")
        else:
            button.config(text="导出完成(压缩失败)", bootstyle="danger", state="normal")
            self._set_status("配置导出完成，但压缩失败")
            self._log_export("压缩失败，详见上方日志")
            self._log(f"\n压缩失败，未生成 ZIP 文件")

    def _on_upload_error(self, error_msg):
        """压缩或上传过程中发生未预期异常"""
        button = self.ui.tk_button_export_config
        button.config(text="导出完成(异常)", bootstyle="danger", state="normal")
        self._set_status("压缩/上传异常，可重试")
        self._log(f"\n压缩/上传过程异常: {error_msg}")
        self._log_export(f"压缩/上传异常: {error_msg}")
        self._log_export("请检查网络连接或手动上传 ZIP 文件")

    def _on_export_error(self, error_msg):
        button = self.ui.tk_button_export_config
        button.config(text="导出失败(可重试)", bootstyle="danger", state="normal")
        self._log(f"导出配置出错: {error_msg}")
        self._set_status("配置导出出错")
        self._log_export(f"")
        self._log_export(f"导出配置出错: {error_msg}")

    @staticmethod
    def _today_str():
        import datetime
        return datetime.datetime.now().strftime("%Y-%m-%d")

    def _populate_config_folders(self):
        """步骤 5: 扫描并填充配置文件夹下拉列表"""
        try:
            folders = config_transfer.find_config_folders()
            if folders:
                values = [display for display, _ in folders]
                self.ui.tk_combo_config_folder["values"] = values
                self.ui.tk_combo_config_folder.current(0)
                self.ui.tk_label_config_status.config(
                    text=f"检测到 {len(folders)} 个配置备份。默认选中推荐文件夹。"
                )
                self._log(f"检测到 {len(folders)} 个配置备份文件夹")
            else:
                self.ui.tk_combo_config_folder["values"] = ["未检测到配置备份"]
                self.ui.tk_combo_config_folder.current(0)
                self.ui.tk_label_config_status.config(
                    text="未在 F:\\Appl\\ 下检测到配置备份文件夹。"
                         "请确保源设备已导出配置且 F 盘数据已成功传输。"
                )
                self._log("未检测到配置备份文件夹")
                # 存储文件夹列表以便后续查找
            self._config_folders = folders
        except Exception as e:
            self._log(f"扫描配置文件夹出错: {e}")

    def _on_browse_config_folder(self):
        """步骤 5: 手动浏览配置文件夹"""
        from tkinter import filedialog
        folder = filedialog.askdirectory(title="选择配置备份文件夹")
        if folder:
            display = os.path.basename(folder)
            current_values = list(self.ui.tk_combo_config_folder["values"])
            if folder not in [v for _, v in (self._config_folders or [])]:
                self._config_folders.insert(0, (display, folder))
                if "未检测到配置备份" in current_values:
                    current_values = []
                current_values.insert(0, display)
                self.ui.tk_combo_config_folder["values"] = current_values
            self.ui.tk_combo_config_folder.current(0)
            self.ui.tk_label_config_status.config(text=f"已选择: {folder}")

    def _on_import_config(self):
        """步骤 5: 点击「导入配置」按钮"""
        selected_idx = self.ui.tk_combo_config_folder.current()
        if selected_idx < 0 or not self._config_folders:
            self._log("请先选择一个配置备份文件夹")
            return

        selected_display = self.ui.tk_combo_config_folder.get()
        config_folder = None
        for display, path in self._config_folders:
            if display == selected_display:
                config_folder = path
                break

        if not config_folder or not os.path.isdir(config_folder):
            self._log(f"配置文件夹无效: {config_folder}")
            return

        # 禁用按钮防重复点击
        self.ui.tk_button_import_config.config(state="disabled", text="导入中...")
        self.ui.tk_button_skip_import.config(state="disabled")
        self.ui.tk_button_browse_config.config(state="disabled")
        self.ui.tk_combo_config_folder.config(state="disabled")
        self.ui.tk_import_progress_bar.pack(fill=tk.X, pady=(2, 10))
        self.ui.tk_import_progress_bar.start()

        self._log(f"开始导入配置: {config_folder}")
        self._log("")

        def _import():
            try:
                success, details = config_transfer.import_config(
                    config_folder,
                    # _log_import 线程安全: 自动转主线程渲染
                    log_callback=lambda msg: self._log_import(msg)
                )
                self._post_ui(self._on_import_done, success, details)
            except Exception as e:
                self._post_ui(self._on_import_error, str(e))

        threading.Thread(target=_import, daemon=True).start()

    def _on_import_done(self, success, details=None):
        self.ui.tk_import_progress_bar.stop()
        self.ui.tk_import_progress_bar.pack_forget()
        self._auto_import_active = False
        if details is not None:
            self._import_details = details
        # 若传输总结页已显示, 刷新导入明细
        if getattr(self, "_summary_entered", False):
            self.ui.render_import_details(self._import_details or [])

        if success:
            self._config_import_done = True  # 配置已导入 → 禁用"跳过"按钮
            self._log("\n配置导入完成!")
            self.ui.tk_button_import_config.config(text="导入完成", bootstyle="success", state="disabled")
            self.ui.tk_button_skip_import.config(state="disabled")
            self.ui.tk_label_import_progress.config(text="配置导入成功!")
            self._set_status("配置导入完成")
        else:
            self._log("\n部分配置导入失败, 请查看日志")
            self.ui.tk_button_import_config.config(text="重试导入", bootstyle="warning", state="normal")
            self.ui.tk_button_skip_import.config(state="normal")
            self.ui.tk_label_import_progress.config(text="部分配置导入失败，可点击重试导入")
            self._set_status("配置导入部分失败")

        # 总结页是最后一步: 下一步 = 完成
        if getattr(self, "_summary_entered", False):
            self.ui.set_button_next("normal", text="完成")

        self.ui.tk_combo_config_folder.config(state="readonly")
        self.ui.tk_button_browse_config.config(state="normal")

    def _on_import_error(self, error_msg):
        self.ui.tk_import_progress_bar.stop()
        self.ui.tk_import_progress_bar.pack_forget()
        self._auto_import_active = False
        self._log(f"导入配置出错: {error_msg}")
        self._import_details = [("系统配置导入", "失败", error_msg)]
        if getattr(self, "_summary_entered", False):
            self.ui.render_import_details(self._import_details)
        self.ui.tk_button_import_config.config(text="重试导入", bootstyle="danger", state="normal")
        self.ui.tk_button_skip_import.config(state="normal")
        self.ui.tk_combo_config_folder.config(state="readonly")
        self.ui.tk_button_browse_config.config(state="normal")
        self._set_status("配置导入出错")

    def _on_skip_import(self):
        """总结页: 点击「跳过」按钮 (手动跳过配置导入)"""
        # 配置已成功导入: 不允许再"跳过" (跳过按钮已禁用, 此处兜底防护)
        if self._config_import_done:
            self._log("配置已导入, 无需跳过")
            return
        self._log("已跳过配置导入")
        self._import_details = [("系统配置导入", "跳过", "已由用户手动跳过")]
        if getattr(self, "_summary_entered", False):
            self.ui.render_import_details(self._import_details)
        self.ui.tk_label_import_progress.config(text="已跳过配置导入")
        self._set_status("已跳过配置导入")
        self.ui.set_button_next("normal", text="完成")
        self.ui.tk_button_prev.config(state="normal")

    def _log_import(self, msg):
        """写入导入日志区域 (线程安全: 自动转主线程执行)"""
        self._post_ui(self._log_import_direct, msg)

    def _log_import_direct(self, msg):
        log_widget = getattr(self.ui, 'tk_text_import_log', None)
        if log_widget:
            try:
                log_widget.config(state="normal")
                log_widget.insert("end", msg + "\n")
                log_widget.see("end")
                log_widget.config(state="disabled")
            except Exception:
                pass

    def _log_export(self, msg):
        """写入导出日志 (通过 UI 层的弹窗/缓冲区管理)。线程安全 (自动转主线程执行)。"""
        if hasattr(self.ui, '_write_export_log'):
            self._post_ui(self.ui._write_export_log, msg)

    def _clear_export_log(self):
        """清空导出日志 (通过 UI 层的弹窗/缓冲区管理)"""
        if hasattr(self.ui, '_clear_export_log'):
            self.ui._clear_export_log()

    # ==================== 校验 ====================
    # 说明: 校验页面已取消 (2026-08-26) — 传输完成后自动在后台执行
    #       _start_auto_verification + 报告打包 + 自动上传

    def _start_auto_verification(self):
        """传输完成后自动启动校验线程 (后台执行, 无校验页面)。

        基于边传边校验确认清单增量确认未覆盖文件 → 生成校验报告并自动上传。
        校验进度/日志通过传输页状态行与日志展示。

        线程安全: 本环境 (嵌入式 Python 3.13) 的 tkinter 禁止后台线程直接调用
        (任何 tk 调用都会抛 RuntimeError: main thread is not in main loop)。
        因此后台线程只把日志/状态/进度消息放入 self._verify_ui_q 队列,
        由主线程 _poll_verify_ui 定时轮询并渲染到界面。
        """
        f_drive = self._partition_map.get("F", "")
        if not f_drive:
            self._log("F 盘未映射，跳过自动校验 (无法生成校验报告)")
            # 仍启动轮询, 以便接收端自动进入传输总结页 (校验线程不存在时会立即结束)
            self._verify_ui_q = queue.Queue()
            self.ui.after(100, self._poll_verify_ui)
            return
        if getattr(self, '_verify_thread', None) and self._verify_thread.is_alive():
            self._log("自动校验已在运行中")
            return

        partition_map = dict(self._partition_map)  # 快照当前映射
        server_ip = self._last_source_ip
        auth_code = self._auth_code
        gtmc_new_name = self._detect_gtmc_new_name()
        if gtmc_new_name:
            self._log(f"检测到 GTMC 目录已重命名为: {gtmc_new_name} (校验时自动映射)")

        # 手动指定的 FullFilelist_DEF.csv (为空则自动识别最新 Appl 文件夹)
        csv_path = self.ui.get_csv_path()
        if csv_path:
            self._log(f"将使用手动指定的 CSV: {csv_path}")
        else:
            self._log("未手动指定 CSV, 将自动识别最新 Appl 文件夹下的 FullFilelist_DEF.csv")

        # 主线程读取 winpe 标志 (后台线程禁止访问 tk 变量)
        try:
            winpe_mode = (self.ui.winpe_var.get() == "winpe")
        except Exception:
            winpe_mode = None

        self._verify_result_text = ""
        self._verify_report_path = None
        self._verify_done = False
        self._verify_filelist_csv = csv_path or None
        self._verify_auto_found = not csv_path
        # 校验结果统计与失败明细 (供传输总结页展示)
        self._verify_stats = None
        self._verify_fail_list = []
        self._set_status("传输完成, 正在进行数据校验 ...")
        self.ui.set_verify_online_status("数据校验中 ... (报告将自动生成并上传)")

        # 后台线程 → 主线程 UI 消息队列 (queue 是线程安全的)
        self._verify_ui_q = queue.Queue()
        self._verify_upload_thread = None

        def _verify_log(msg):
            """校验线程日志: 入队, 由主线程 _poll_verify_ui 渲染到日志 + 状态行"""
            try:
                self._verify_ui_q.put(("log", str(msg)))
                self._verify_ui_q.put(("status", str(msg)))
            except Exception:
                pass

        def _verify():
            try:
                def _verify_progress(done, total):
                    pct = int(done / total * 100) if total > 0 else 0
                    status = f"数据校验中... {done}/{total} 文件 ({pct}%)"
                    try:
                        self._verify_ui_q.put(("status", status))
                        if total > 0:
                            self._verify_ui_q.put(("progress", done, total))
                    except Exception:
                        pass

                report_zip_out = []
                # 读取边传边校验确认清单: 已确认的文件在校验阶段直接标记 Y 跳过磁盘校验
                # 行格式: "源盘符完整路径|大小" (兼容旧格式纯路径)
                pre_ok_paths = None
                pre_ok_sizes = None
                pre_file = getattr(self, "_pre_verified_file", "")
                if pre_file and os.path.isfile(pre_file):
                    try:
                        pre_ok_paths = set()
                        pre_ok_sizes = {}
                        with open(pre_file, "r", encoding="utf-8") as _pf:
                            for _ln in _pf:
                                _ln = _ln.strip()
                                if not _ln:
                                    continue
                                if "|" in _ln:
                                    _p, _, _s = _ln.rpartition("|")
                                    pre_ok_paths.add(_p)
                                    try:
                                        pre_ok_sizes[_p] = int(_s)
                                    except (ValueError, TypeError):
                                        pass
                                else:
                                    pre_ok_paths.add(_ln)  # 兼容旧格式(纯路径)
                        _verify_log(
                            f"已读取文件确认清单: {len(pre_ok_paths)} 个文件将跳过磁盘校验(已复核大小)"
                        )
                    except Exception as _e:
                        _verify_log(f"读取文件确认清单失败: {_e}")
                ok, passed, failed, skipped, total = run_verification(
                    f_drive_pe=f_drive,
                    partition_map=partition_map,
                    log_callback=_verify_log,
                    stop_check=lambda: self._stop_verify,
                    progress_callback=_verify_progress,
                    server_ip=server_ip,
                    gtmc_new_name=gtmc_new_name,
                    auth_code=auth_code,
                    winpe=winpe_mode,
                    csv_path=csv_path,
                    report_zip_out=report_zip_out,
                    pre_ok_paths=pre_ok_paths,
                    pre_ok_sizes=pre_ok_sizes,
                    fail_list_out=self._verify_fail_list,
                )
                if self._stop_verify:
                    _verify_log("数据校验已取消")
                    return
                # 记录校验统计 (供传输总结页展示)
                self._verify_stats = (passed, failed, skipped, total)
                if ok:
                    result_text = f"校验完成!  通过: {passed}  失败: {failed}  跳过: {skipped}  总计: {total}"
                    _verify_log(f"\n{'='*50}\n  {result_text}\n{'='*50}")
                    self._verify_done = True
                    self._verify_ui_q.put(("status", "数据校验完成"))
                    self._verify_ui_q.put(("status_line", result_text))
                    # 校验报告已由 verifier 打包, 后台线程自动上传到 Profile 服务器
                    if report_zip_out:
                        self._verify_report_path = report_zip_out[0]
                        self._upload_verifier_report(report_zip_out[0], _verify_log)
                else:
                    _verify_log("数据校验失败，请检查日志")
                    self._verify_ui_q.put(("status", "数据校验失败"))
                    self._verify_ui_q.put(("status_line", "数据校验失败"))
            except Exception as e:
                _verify_log(f"数据校验异常: {e}")
                self._verify_ui_q.put(("status", "数据校验异常"))

        self._verify_thread = threading.Thread(target=_verify, daemon=True)
        # 主线程启动 UI 轮询 (在主线程调用 after 是安全的)
        self.ui.after(100, self._poll_verify_ui)
        self._verify_thread.start()

    def _poll_verify_ui(self):
        """主线程轮询自动校验 UI 消息队列, 渲染后台线程产生的日志/状态/进度。
        校验或上传线程仍在运行期间持续自轮询。"""
        try:
            q = getattr(self, "_verify_ui_q", None)
            if q is None:
                return
            while True:
                try:
                    item = q.get_nowait()
                except Exception:
                    break
                kind = item[0]
                if kind == "log":
                    self._log(item[1])
                elif kind == "status":
                    self.ui.set_verify_online_status(item[1])
                elif kind == "status_line":
                    self._set_status(item[1])
                elif kind == "progress":
                    self._set_progress(item[1], item[2])
            # 校验完成且报告已生成: 显示"查看校验报告"按钮
            if (not getattr(self, "_report_btn_shown", False)
                    and getattr(self, "_verify_done", False)
                    and getattr(self, "_verify_report_path", None)
                    and os.path.isfile(self._verify_report_path)):
                self.ui.show_report_button()
                self._report_btn_shown = True
            alive = False
            vth = getattr(self, "_verify_thread", None)
            if vth is not None and vth.is_alive():
                alive = True
            uth = getattr(self, "_verify_upload_thread", None)
            if uth is not None and uth.is_alive():
                alive = True
            if alive:
                self.ui.after(150, self._poll_verify_ui)
            else:
                # 校验流程结束: 接收端传输完成后自动进入/刷新传输总结页
                if (self._device_type == "目标设备"
                        and getattr(self, "_transfer_done", False)):
                    if not getattr(self, "_summary_entered", False):
                        self._summary_entered = True
                        self.ui.go_step(4)
                    self._render_summary()
        except Exception:
            pass

    def _on_open_report(self):
        """点击『查看校验报告』: 在资源管理器中定位到校验报告文件"""
        path = getattr(self, "_verify_report_path", "")
        if not path or not os.path.isfile(path):
            self._log("校验报告尚未生成")
            return
        try:
            import subprocess
            subprocess.Popen(["explorer", "/select,", path])
            self._log(f"已定位校验报告: {path}")
        except Exception as _e:
            try:
                os.startfile(path)
            except Exception as _e2:
                self._log(f"无法打开校验报告: {_e2}")

    def _render_summary(self):
        """渲染步骤4 传输总结页 (仅接收端): 配置导入结果 + 校验结果 + 失败文件列表"""
        try:
            self.ui.set_button_next("normal", text="完成")
            self.ui.set_button_prev("normal", text="< 返回")
            # ① 配置导入明细 (区分: 导入中/等待手动/未检测到/明细)
            details = getattr(self, "_import_details", None)
            manual = False
            try:
                manual = bool(getattr(self.ui, "tk_var_manual_import", None)
                              and self.ui.tk_var_manual_import.get())
            except Exception:
                pass
            # 手动导入控件 (文件夹/浏览/导入/跳过) 仅在手动模式下显示
            try:
                self.ui.set_manual_import_visible(manual)
            except Exception:
                pass
            if details is not None:
                self.ui.render_import_details(details)
            elif getattr(self, "_auto_import_active", False):
                self.ui.render_import_details(None)  # 自动导入中
            elif manual:
                self.ui.render_import_details("manual")  # 等待手动导入
            else:
                self.ui.render_import_details([])  # 未检测到配置备份
            # ② 校验统计 + 查看报告按钮 + 失败文件列表 (统一展示)
            stats = getattr(self, "_verify_stats", None)
            report_zip = getattr(self, "_verify_report_path", "") or ""
            self.ui.render_verify_stats(stats, report_zip)
            self.ui.render_fail_list(getattr(self, "_verify_fail_list", None) or [])
            # 手动模式提示
            if manual:
                self.ui.tk_label_import_progress.config(
                    text="手动模式: 请选择配置文件夹后点击「导入配置」"
                )
        except Exception as e:
            self._log(f"渲染传输总结页出错: {e}")

    def _on_summary_done(self):
        """总结页「完成并关闭」/ 底部「完成」按钮: 清理后台并退出程序"""
        self._log("接收端流程完成，正在关闭程序")
        try:
            self.shutdown()
        except Exception:
            pass
        try:
            self.ui.destroy()
        except Exception:
            pass
        import os
        os._exit(0)

    def _upload_verifier_report(self, zip_path, log_callback=None):
        """后台线程上传校验报告 ZIP 到 Profile 服务器。"""
        import config_transfer

        def _log_safe(msg):
            if log_callback:
                log_callback(msg)

        def _run():
            try:
                _log_safe("")
                _log_safe("=" * 50)
                _log_safe("开始上传校验报告...")
                config_transfer.upload_zip_to_server(zip_path, log_callback=_log_safe)
                _log_safe("校验报告上传完成")
                _log_safe("=" * 50)
            except Exception as e:
                _log_safe(f"上传校验报告异常: {e}")

        self._verify_upload_thread = threading.Thread(target=_run, daemon=True)
        self._verify_upload_thread.start()

    def _on_skip_verify(self):
        """跳过文件校验: 创建空 <设备名>_Unverifi.zip 并上传 (校验未完成时兜底)。"""
        from verifier import create_unverifi_zip
        import config_transfer

        # 校验进行中不允许跳过
        if getattr(self, '_verify_thread', None) and self._verify_thread.is_alive():
            self._log("校验正在进行中, 无法跳过")
            return

        # 校验已完成则无需再跳过 (报告已打包并上传)
        if self._verify_done:
            self._log("校验已完成, 校验报告已上传")
            return

        self.ui.set_button_next("disabled")
        self._log("正在跳过校验, 创建空校验报告...")

        # 复用校验 UI 消息队列: 后台线程只入队, 主线程 _poll_verify_ui 渲染
        if getattr(self, "_verify_ui_q", None) is None:
            self._verify_ui_q = queue.Queue()
        self._verify_upload_thread = None
        self.ui.after(100, self._poll_verify_ui)

        def _log_safe(msg):
            """后台线程日志: 入队, 由主线程轮询渲染 (禁止直接操作 tkinter 控件)"""
            try:
                self._verify_ui_q.put(("log", str(msg)))
                self._verify_ui_q.put(("status", str(msg)))
            except Exception:
                pass

        def _run():
            try:
                f_drive = self._partition_map.get("F", "")
                appl_dir = os.path.join(f_drive.rstrip("\\") + "\\", "Appl") if f_drive else ""
                ok, zip_path = create_unverifi_zip(log_callback=_log_safe, save_dir=appl_dir)
                if ok and zip_path:
                    _log_safe("")
                    _log_safe("=" * 50)
                    _log_safe("开始上传校验报告(空包)...")
                    config_transfer.upload_zip_to_server(zip_path, log_callback=_log_safe)
                    _log_safe("=" * 50)
                    self._verify_ui_q.put(("status", "已跳过校验 (空校验报告已上传)"))
                    self._verify_ui_q.put(("status_line", "已跳过校验 (空校验报告已上传)"))
            except Exception as e:
                _log_safe(f"跳过校验/上传异常: {e}")

        self._verify_upload_thread = threading.Thread(target=_run, daemon=True)
        self._verify_upload_thread.start()

    def _append_verify_log(self, msg: str):
        """校验日志: 写入传输日志 (校验页已取消)"""
        self._log(msg)

    def _set_verify_status(self, text: str):
        """校验状态: 同步到底部状态条 + 边传边校验状态行"""
        try:
            self._set_status(text)
            self.ui.set_verify_online_status(text)
        except Exception:
            pass

    def _set_verify_progress(self, pct: int, status: str):
        """校验进度: 更新边传边校验状态行 (校验页已取消)"""
        try:
            self.ui.set_verify_online_status(status)
        except Exception:
            pass

    def _set_verify_result(self, text: str, success: bool = True):
        """校验结果: 写入状态条与传输日志 (校验页已取消)"""
        self._log(text)
        self._set_status(text)

    def _detect_gtmc_new_name(self) -> str:
        """
        目标端检测 D 盘中被源端重命名后的 GTMC_User_Profiles 目录
        (源端下载前已重命名为 GTMC_User_ProfilesYYMMDD, 而 CSV 记录的是旧名,
         校验时需要将 CSV 路径映射到新目录名)
        返回新目录名; 未找到返回空字符串
        """
        d_drive = self._partition_map.get("D", "")
        if not d_drive or not os.path.isdir(d_drive):
            return ""
        candidates = []
        try:
            for name in os.listdir(d_drive):
                if name.startswith("GTMC_User_Profiles") and name != "GTMC_User_Profiles":
                    p = os.path.join(d_drive, name)
                    if os.path.isdir(p):
                        try:
                            candidates.append((os.path.getmtime(p), name))
                        except OSError:
                            candidates.append((0, name))
        except OSError:
            return ""
        if not candidates:
            return ""
        return max(candidates)[1]  # 取最近修改的一个

    # ==================== 工具方法 ====================

    def _update_combobox(self, cb, values, log_msg=None):
        """更新 Combobox 并记录日志"""
        cb["values"] = values
        if log_msg:
            self._log(log_msg)

    def _log(self, message: str):
        """向 GUI 日志区域输出日志。
        线程安全: 后台线程调用时自动入队, 由主线程 _poll_ui_q 渲染
        (本环境 tkinter 禁止后台线程直接操作 widget)。"""
        self._post_ui(self._log_direct, str(message))

    def _log_direct(self, message: str):
        try:
            with _TK_LOCK:
                text_widget = self.ui.tk_text_mqg105ch
                text_widget.insert("end", message + "\n")
                text_widget.see("end")
        except Exception:
            pass

    # ==================== 进度条 & 状态 ====================

    def _set_status(self, text: str):
        """更新状态标签 (底部状态栏 + 传输页面状态)。线程安全 (自动转主线程执行)。"""
        self._post_ui(self._set_status_direct, text)

    def _set_status_direct(self, text: str):
        try:
            self.ui.tk_label_status.config(text=text)
        except Exception:
            pass
        try:
            self.ui.tk_label_transfer_status.config(text=text)
        except Exception:
            pass

    def _set_progress(self, value: float, maximum: float = 100):
        """更新进度条 (value 0-100)。线程安全 (自动转主线程执行)。"""
        self._post_ui(self._set_progress_direct, value, maximum)

    def _set_progress_direct(self, value: float, maximum: float = 100):
        try:
            if maximum != 100:
                pct = min(value / maximum * 100, 100) if maximum > 0 else 0
            else:
                pct = min(value, 100)
            self.ui.tk_progress_bar.config(value=pct)
        except Exception:
            pass

    def _set_progress_mode(self, indeterminate: bool = False):
        """设置进度条模式: determinate (百分比) / indeterminate (动画)"""
        try:
            if indeterminate:
                self.ui.tk_progress_bar.config(mode="indeterminate")
                self.ui.tk_progress_bar.start(10)
            else:
                self.ui.tk_progress_bar.stop()
                self.ui.tk_progress_bar.config(mode="determinate", value=0)
        except Exception:
            pass

    def _reset_progress(self, status_text: str = "就绪"):
        """重置进度条和状态"""
        self._set_status(status_text)
        self._set_progress_mode(indeterminate=False)
        self._set_progress(0)
        try:
            self.ui.tk_file_progress_bar.config(value=0)
        except Exception:
            pass

    def _partition_progress(self, partition: str, done: int, total: int):
        """第二个进度条: 当前分区拷贝进度 (D/E/F 盘各自 0-100%)

        file_transfer.download_files 在逐分区串行下载时持续回调此函数。
        """
        try:
            pct = min(done / total * 100, 100) if total > 0 else 0
            # 后台传输线程回调: 经 _post_ui / _set_status 转主线程
            self._post_ui(self.ui.tk_file_progress_bar.config, value=pct)
            self._set_status(f"正在拷贝 {partition} 盘 ({done}/{total})")
        except Exception:
            pass

    def _verify_online_progress(self, ok: int, fail: int, total: int):
        """文件确认进度回调 (在传输的校验线程中调用, 经 _post_ui 转主线程更新 UI)

        file_transfer.download_files 的文件确认线程每确认一个文件调用一次。
        """
        try:
            if total <= 0:
                return
            if fail > 0:
                text = f"文件确认：已确认 {ok}/{total} 个文件（{fail} 个待校验阶段复核）"
            else:
                text = f"文件确认：已确认 {ok}/{total} 个文件"
            self._post_ui(self.ui.set_verify_online_status, text)
        except Exception:
            pass

    # ==================== 冲突处理 ====================

    def _resolve_conflicts(self, conflicts: list, log_callback) -> set:
        """同名文件冲突回调 (在 worker 线程中调用, 阻塞等待用户决定)

        通过 threading.Event + ui.after 实现跨线程对话框:
          worker 线程 → ui.after → 主线程弹窗 → 用户点击 → Event.set → worker 线程继续

        Args:
            conflicts: [{"rel_path", "target_path", "src_size", "dst_size",
                          "src_mtime", "dst_mtime"}, ...]
            log_callback: 日志回调
        Returns:
            set[str]: 用户选择保留的目标路径 (这些文件不会被重新下载)
        """
        if not conflicts:
            return set()

        event = threading.Event()
        result: list = [set()]  # 可变容器供闭包写入; 默认空集 = 全部覆盖

        def _show_dialog():
            try:
                dialog = tk.Toplevel(self.ui)
                dialog.title(f"文件冲突 ({len(conflicts)} 个同名文件)")
                dialog.geometry("750x480")
                dialog.resizable(True, True)
                dialog.transient(self.ui)
                dialog.grab_set()

                # 说明文字
                tk.Label(
                    dialog,
                    text=f"以下 {len(conflicts)} 个文件在新电脑端已存在，但大小与旧电脑端不同。\n"
                         "请选择保留已存在的文件，或覆盖为新电脑端重新下载。",
                    justify=tk.LEFT,
                    pady=10,
                    fg="#555",
                ).pack(fill=tk.X, padx=15, pady=(10, 5))

                # 表头
                header_frame = tk.Frame(dialog, bg="#e0e0e0")
                header_frame.pack(fill=tk.X, padx=15, pady=(0, 0))
                tk.Label(header_frame, text="文件路径", width=42, anchor=tk.W,
                         bg="#e0e0e0", font=("", 9, "bold")).pack(side=tk.LEFT, padx=4)
                tk.Label(header_frame, text="旧电脑端大小", width=14, anchor=tk.W,
                         bg="#e0e0e0", font=("", 9, "bold")).pack(side=tk.LEFT, padx=4)
                tk.Label(header_frame, text="新电脑端大小", width=14, anchor=tk.W,
                         bg="#e0e0e0", font=("", 9, "bold")).pack(side=tk.LEFT, padx=4)

                # 可滚动冲突列表
                list_frame = tk.Frame(dialog)
                list_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)

                canvas = tk.Canvas(list_frame, highlightthickness=0)
                scrollbar = tk.Scrollbar(list_frame, orient=tk.VERTICAL, command=canvas.yview)
                inner = tk.Frame(canvas)

                inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
                canvas.create_window((0, 0), window=inner, anchor=tk.NW)
                canvas.configure(yscrollcommand=scrollbar.set)

                # 鼠标滚轮支持
                def _on_mousewheel(event):
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                canvas.bind_all("<MouseWheel>", _on_mousewheel)
                dialog.bind("<Destroy>", lambda e: canvas.unbind_all("<MouseWheel>"))

                canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
                scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

                # 渲染每个冲突文件
                for i, c in enumerate(conflicts):
                    bg = "#f8f8f8" if i % 2 == 0 else "#ffffff"
                    row = tk.Frame(inner, bg=bg)
                    row.pack(fill=tk.X)

                    path_text = c.get("rel_path", c.get("target_path", "?"))
                    src_size = c.get("src_size", 0)
                    dst_size = c.get("dst_size", 0)

                    import file_transfer as _ft
                    tk.Label(row, text=path_text, width=42, anchor=tk.W, bg=bg,
                             font=("Consolas", 8)).pack(side=tk.LEFT, padx=4)
                    tk.Label(row, text=_ft._fmt_size(src_size), width=14, anchor=tk.E, bg=bg,
                             font=("Consolas", 8)).pack(side=tk.LEFT, padx=4)
                    tk.Label(row, text=_ft._fmt_size(dst_size), width=14, anchor=tk.E, bg=bg,
                             font=("Consolas", 8)).pack(side=tk.LEFT, padx=4)

                # 底部按钮栏
                btn_frame = tk.Frame(dialog)
                btn_frame.pack(fill=tk.X, padx=15, pady=(10, 15))

                def _on_skip_all():
                    """保留所有已存在文件 (跳过下载)"""
                    result[0] = {c["target_path"] for c in conflicts}
                    log_callback(f"冲突: 用户选择保留 {len(conflicts)} 个已存在文件")
                    event.set()
                    dialog.destroy()

                def _on_overwrite_all():
                    """覆盖所有 (重新下载)"""
                    result[0] = set()
                    log_callback(f"冲突: 用户选择覆盖 {len(conflicts)} 个文件, 重新下载")
                    event.set()
                    dialog.destroy()

                tk.Button(
                    btn_frame, text=f"保留已有文件 ({len(conflicts)} 个)",
                    command=_on_skip_all, bg="#4CAF50", fg="white",
                    font=("", 10, "bold"), width=22, height=2,
                ).pack(side=tk.LEFT, padx=(0, 10))

                tk.Button(
                    btn_frame, text=f"覆盖重新下载 ({len(conflicts)} 个)",
                    command=_on_overwrite_all, bg="#f44336", fg="white",
                    font=("", 10, "bold"), width=22, height=2,
                ).pack(side=tk.LEFT)

                # 窗口关闭按钮 = 保留已有文件 (安全默认)
                dialog.protocol("WM_DELETE_WINDOW", _on_skip_all)

                # 5 分钟超时自动选择保留 (安全默认)
                dialog.after(300000, lambda: (_on_skip_all() if not event.is_set() else None))

            except Exception as ex:
                log_callback(f"[X] 冲突对话框异常: {ex}")
                event.set()

        # worker 线程禁止直接调 tk: 经 _post_ui 转主线程弹窗
        self._post_ui(_show_dialog)
        event.wait()  # 阻塞 worker 线程, 等待用户点击
        return result[0]

    # ==================== 清理退出 ====================

    def _send_skip_verify_on_close(self):
        """窗口关闭时: 若接收方传输已完成但校验未完成, 参照"跳过校验"向服务器
        发送跳过校验信息 (创建空 <设备名>_Unverifi.zip 并上传)。

        在 shutdown 中同步调用, 进程退出前需完成上传 (否则 os._exit 中断后台线程)。
        """
        # 仅接收方 + 传输已完成 + 校验未完成 时触发 (校验页已取消, 校验自动后台执行)
        if self._device_type != "目标设备":
            return
        if not self._transfer_done:
            return  # 传输未完成, 无需发送
        if self._verify_done:
            return  # 已正常完成校验, 报告已上传
        if self._send_skip_verify_done:
            return  # 已发送过

        # 校验正在运行中 (未完成) → 同样视为跳过
        self._log("[清理] 校验未完成, 关闭程序时按「跳过校验」处理...")

        try:
            import config_transfer
            from verifier import create_unverifi_zip

            f_drive = self._partition_map.get("F", "")
            appl_dir = os.path.join(f_drive.rstrip("\\") + "\\", "Appl") if f_drive else ""
            ok, zip_path = create_unverifi_zip(
                log_callback=lambda m: self._log(m),
                save_dir=appl_dir,
            )
            if ok and zip_path:
                # 同步上传 (阻塞), 确保进程退出前完成
                self._log("[清理] 正在上传空校验报告 (跳过校验)...")
                upload_ok, _ = config_transfer.upload_zip_to_server(
                    zip_path,
                    log_callback=lambda m: self._log(m),
                )
                if upload_ok:
                    self._log("[清理] 已发送跳过校验信息")
                    self._send_skip_verify_done = True
        except Exception as e:
            self._log(f"[清理] 发送跳过校验信息失败: {e}")

    def shutdown(self):
        """清理所有后台进程和资源 (窗口关闭 / 异常退出 / atexit 时调用)

        确保:
        - 校验线程安全终止
        - 文件服务器 (HTTPS) 正确关闭, 释放端口和线程
        - DHCP 服务器 (UDP) 正确关闭, 释放端口和 socket
        - 系统休眠策略恢复 (不再阻止锁屏/休眠)
        """
        self._log("[清理] 正在关闭所有后台进程...")

        # -1. 解除传输期间的全屏锁定 (兜底, 防窗口关闭时仍被锁住)
        try:
            self.ui.unlock_screen()
        except Exception:
            pass
        # 取消源端完成检测轮询
        if getattr(self, "_source_done_after", None):
            try:
                self.ui.after_cancel(self._source_done_after)
            except Exception:
                pass
            self._source_done_after = None

        # 0. 校验页未完成校验就直接关闭程序 → 参照"跳过校验"发送跳过信息
        #     需在进程退出前同步完成上传, 否则 os._exit 会中断后台线程
        try:
            self._send_skip_verify_on_close()
        except Exception:
            pass

        # 0. 通知传输线程停止 (最高优先级)
        self._stop_transfer = True

        # 1. 停止校验线程
        if self._verify_thread and self._verify_thread.is_alive():
            self._stop_verify = True
            try:
                self._verify_thread.join(timeout=3)
            except Exception:
                pass

        # 2. 停止文件服务器 (释放 HTTPS 端口 + 线程)
        if self._file_server:
            try:
                self._file_server.stop()
            except Exception:
                pass
            self._file_server = None

        # 3. 停止 DHCP 服务器 (释放 UDP 端口 + socket)
        if self._dhcp_server:
            try:
                self._dhcp_server.stop()
            except Exception:
                pass
            self._dhcp_server = None

        # 4. 恢复系统休眠策略 (即使 FileServer.stop() 已调过, 这里再兜底一次)
        try:
            _allow_sleep()
        except Exception:
            pass

        self._transferring = False
        self._use_dhcp = False
