"""
控制器 - 连接 UI 与所有业务模块
Phase 1-5 全部集成
"""
import threading
import time
import subprocess
import os
from typing import Literal
from tkinter import simpledialog
import tkinter as tk
from nic_scanner import scan_nics, get_nic_display_list, get_local_ip
from disk_scanner import get_disk_list, get_drive_letter_list, get_disk_number, get_partition_count, get_partition_details
from file_transfer import FileServer, download_files, scan_source_device, TRANSFER_PORT, _allow_sleep
from verifier import run_verification
from ip_config import SOURCE_IP, SUBNET_MASK  # 169.254.100.1 (目标设备自身 IP)
import tls_utils
DHCP_ASSIGNED_IP = "169.254.100.2"  # DHCP 分配给源设备的 IP


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

        # ---- 设备类型 ----
        self._device_type = None

        # ---- 分区映射 {"D": "I:", "E": "J:", "F": "K:"} ----
        self._partition_map = {}

        # ---- 目标磁盘 NTFS 分区数 (2/3/4) ----
        self._ntfs_partition_count = 0

        # ---- 文件服务器 (源设备) ----
        self._file_server: FileServer = None

        # ---- 传输状态 ----
        self._transferring = False
        self._transfer_done = False  # 传输是否已完成 (用于启用"校验文件"按钮)

        # ---- 校验线程 (独立于传输线程，可并行) ----
        self._verify_thread = None
        self._stop_verify = False  # 新传输开始时通知旧校验停止

        # ---- 网络/鉴权状态 (避免未选网卡直接点按钮时 AttributeError) ----
        self._use_dhcp = False
        self._source_ip = ""
        self._dhcp_server = None
        self._auth_code = ""        # 源: 生成并显示; 目标: 用户输入
        self._last_source_ip = ""   # 目标: 最近一次成功连接的源设备 IP (供校验重试)

    # ==================== 初始化 ====================

    def init(self, ui):
        self.ui = ui
        self._install_thread_excepthook()
        self._setup_events()
        self._setup_ui_defaults()
        self._populate_nics()

    def _install_thread_excepthook(self):
        """捕获所有后台线程的未处理异常并输出到日志。
        打包成 exe (pythonw, 无控制台) 时, 线程里抛出的异常默认被静默丢弃,
        表现为"点了按钮没反应"。挂上 excepthook 后, 任何线程崩溃都会显示在日志里。"""
        import traceback as _tb

        def _hook(args):
            try:
                msg = "".join(_tb.format_exception(
                    args.exc_type, args.exc_value, args.exc_traceback))
                self.ui.after(0, lambda: self._log(f"[诊断] 后台线程异常:\n{msg}"))
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
        # 网卡选择
        self.ui.tk_select_box_mqfzkd6x.bind(
            "<<ComboboxSelected>>", self._on_nic_selected
        )
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
        # 开启 DHCP 按钮 (标签为"寻找旧电脑")
        self.ui.tk_button_dhcp.config(command=self._on_dhcp_button)
        # CSV 浏览
        if hasattr(self.ui, 'tk_button_browse_csv'):
            self.ui.tk_button_browse_csv.config(command=self._on_browse_csv)
        # 校验按钮 (步骤 5)
        if hasattr(self.ui, 'tk_button_verify'):
            self.ui.tk_button_verify.config(command=self._on_verify_start)
        # 发现设备列表
        if hasattr(self.ui, 'tk_select_box_discover'):
            self.ui.tk_select_box_discover.bind("<<ComboboxSelected>>", self._on_discover_selected)
        # 运行环境
        if hasattr(self.ui, 'winpe_var'):
            self.ui.winpe_var.trace_add("write", lambda *_: self._populate_disks())
        # 验证码输入 (大写自动转)
        if hasattr(self.ui, 'tk_entry_code'):
            self.ui.tk_entry_code.bind("<KeyRelease>", self._on_auth_code_changed)

    # ==================== 网卡扫描 ====================

    def _populate_nics(self):
        """扫描网卡并填充下拉列表"""
        self._log("正在扫描网卡...")
        self.ui.tk_select_box_mqfzkd6x["values"] = ["扫描中..."]

        def _scan():
            try:
                nics = scan_nics()
                # NIC 优先级排序: USB > 169.254 > 内置网卡
                nics = sorted(nics, key=_nic_priority_key)
                self._nic_list = nics
                display_list = [n[0] for n in nics]
                self.ui.after(0, lambda: self._update_combobox(
                    self.ui.tk_select_box_mqfzkd6x,
                    display_list if display_list else ["未检测到网卡"],
                    f"检测到 {len(display_list)} 个网卡" if display_list else "未检测到可用网卡"
                ))
            except Exception as e:
                self.ui.after(0, lambda: self._log(f"网卡扫描失败: {e}"))

        threading.Thread(target=_scan, daemon=True).start()

    # ==================== 事件处理 ====================

    def _on_role_selected(self, role: str):
        """角色选择: 'source'(旧电脑/发送方) 或 'target'(新电脑/接收方)"""
        self._device_type = "源设备" if role == "source" else "目标设备"
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
        else:
            self.ui.show_dhcp()
            self.ui.show_tgt_connect()
            self.ui.show_discover()
            self.ui.show_manual_ip()

        # 切换到网卡选择步骤
        self.ui.go_step(1)
        self.ui.tk_button_prev.config(state="normal")

        # 更新 IP 配置信息
        if role == "source":
            self.ui.set_nic_ip_info(
                "发送方 (旧设备) — 将自动从接收方 DHCP 获取 IP 地址\n"
                "    预期 IP: 169.254.100.2"
            )
        else:
            self.ui.set_nic_ip_info(
                "接收方 (新设备) — 请进入下一步连接页面后点击「寻找旧设备」启动 DHCP 服务器\n"
                "    本机 IP: 169.254.100.1 | 源设备 IP: 169.254.100.2"
            )

        # 不在此时显示开始按钮，等用户到达步骤 3 再显示

    def _on_prev_step(self):
        """上一步"""
        new_step = max(0, self.ui._step - 1)
        self.ui.go_step(new_step)

        if new_step == 0:
            # 退回角色选择页: 禁用下一步
            self.ui.tk_button_prev.config(state="disabled")
            self.ui.set_button_next("disabled")
        else:
            self.ui.tk_button_prev.config(state="normal")

            # 同步"下一步"按钮状态
            if new_step == 1:
                # 网卡选择页: 若网卡已选则启用
                nic_selected = self.ui.tk_select_box_mqfzkd6x.get() not in (
                    "扫描中...", "未检测到网卡", "", "网卡1", "网卡2"
                )
                if nic_selected:
                    self.ui.set_button_next("normal")
                else:
                    self.ui.set_button_next("disabled")
            elif new_step == 2:
                # 磁盘映射页: 需要用户确认磁盘选择后才能继续
                disk = self.ui.tk_select_box_mqfzmzbe.get()
                if disk and disk not in ("未检测到磁盘", "", "请先选择设备类型", "扫描中..."):
                    self.ui.set_button_next("normal")
                else:
                    self.ui.set_button_next("disabled")
            elif new_step == 3:
                # 连接页面: 禁用"下一步", 使用"开始传输"
                self.ui.set_button_next("disabled")
            elif new_step == 4:
                # 传输页面: 若传输已完成(接收方), 启用"校验文件 >"
                if self._transfer_done and self._device_type == "目标设备":
                    self.ui.set_button_next("normal", text="校验文件 >")
                else:
                    self.ui.set_button_next("disabled")
            elif new_step == 5:
                # 校验页面: 禁用"下一步"
                self.ui.set_button_next("disabled")
            else:
                self.ui.set_button_next("normal")

            # 同步"开始传输"按钮状态
            self._check_button_state()

    def _on_next_step(self):
        """下一步"""
        new_step = min(self.ui._total_steps - 1, self.ui._step + 1)
        self.ui.go_step(new_step)
        self.ui.tk_button_prev.config(state="normal")

        if new_step == 2:
            # 进入步骤 2 (磁盘映射): 若磁盘已自动选中则直接启用"下一步"
            disk = self.ui.tk_select_box_mqfzmzbe.get()
            if disk and disk not in ("未检测到磁盘", "", "请先选择设备类型", "扫描中..."):
                self.ui.set_button_next("normal")
            else:
                self.ui.set_button_next("disabled")
        elif new_step == 3:
            # 步骤 3 (连接页面): 禁用"下一步", 用户应点击"开始传输"而非"下一步"
            self.ui.set_button_next("disabled")
        elif new_step == 5:
            # 步骤 5 (校验页面): 禁用"下一步", 这是最后一页
            self.ui.set_button_next("disabled")
        elif new_step == 4:
            # 步骤 4 (传输页面): 传输完成前禁用"下一步"
            if self._transfer_done:
                self.ui.set_button_next("normal", text="校验文件 >")
            else:
                self.ui.set_button_next("disabled")
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

    def _on_nic_selected(self, event=None):
        nic_display = self.ui.tk_select_box_mqfzkd6x.get()
        if nic_display in ("扫描中...", "未检测到网卡", ""):
            return

        self._log(f"已选择网卡: {nic_display}")

        adapter_desc = self._get_adapter_desc(nic_display)
        if not adapter_desc:
            return

        # 设备类型已选 → 尝试设置 IP
        if self._device_type in ("源设备", "目标设备"):
            self._configure_ip(adapter_desc, self._device_type)

        # 网卡选定后: 扫描磁盘并进入下一步
        self._populate_disks()

        # 允许进入下一步 (磁盘选择) — 用 set_button_next 确保 pack 状态正确
        self.ui.set_button_next("normal")

        # 更新 IP 状态显示
        if self._device_type == "源设备":
            self.ui.set_nic_ip_info(
                f"发送方 (旧设备) — 已选定网卡: {nic_display}\n"
                "    将自动从接收方 DHCP 获取 IP: 169.254.100.2"
            )
        else:
            self.ui.set_nic_ip_info(
                f"接收方 (新设备) — 已选定网卡: {nic_display}\n"
                "    请进入连接页面后点击「寻找旧设备」启动 DHCP\n"
                "    本机 IP: 169.254.100.1 | 源设备 IP: 169.254.100.2"
            )

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

        # 网卡已选 → 配置网络
        nic_display = self.ui.tk_select_box_mqfzkd6x.get()
        if nic_display not in ("扫描中...", "未检测到网卡", "", "网卡1", "网卡2"):
            adapter_desc = self._get_adapter_desc(nic_display)
            if adapter_desc:
                self._configure_ip(adapter_desc, dev_type)

        # 扫描磁盘
        self._populate_disks()

    def _on_disk_selected(self, event=None):
        disk = self.ui.tk_select_box_mqfzmzbe.get()
        if disk in ("未检测到磁盘", "", "请先选择设备类型", "扫描中..."):
            return
        self._log(f"已选择磁盘: {disk}")

        # 磁盘已选择: 允许用户确认后进入下一步 — 用 set_button_next 确保 pack 状态正确
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
                    self.ui.after(0, lambda: self._log(
                        f"检测到 {ntfs_count} 个 NTFS 分区 (PhysicalDrive{disk_num})"
                    ))

                    # 获取物理顺序分区详情，自动填充 D/E/F
                    details = get_partition_details(disk_num)
                    if details:
                        ordered = [dl for _, _, dl in details]
                        self.ui.after(0, lambda o=list(ordered): self._log(
                            f"分区物理顺序→盘符: {o}"
                        ))
                        # 自动填充: 按磁盘物理分区顺序映射到 D/E/F
                        self.ui.after(0, lambda o=list(ordered), n=ntfs_count:
                                      self._auto_fill_drive_mapping(o, n))
                else:
                    self._ntfs_partition_count = 0
            except Exception as e:
                self._ntfs_partition_count = 0
                import traceback
                tb = traceback.format_exc()
                self.ui.after(0, lambda: self._log(f"分区检测失败: {e}"))
                self.ui.after(0, lambda t=tb: self._log(f"调试: {t}"))
            finally:
                # 无论检测成功与否, 都重新评估开始按钮状态
                self.ui.after(0, self._check_button_state)

        threading.Thread(target=_detect_partitions, daemon=True).start()

    def _on_partition_map_changed(self, event=None):
        """分区映射变更，更新 partition_map 并检查按钮状态"""
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

            # 先做参数校验, 通过后再切换到传输进度页面 (步骤 4)
            # 避免校验失败时用户已停在传输页却无反应
            if dev_type == "源设备":
                # 源设备必须指定要对外提供(拷贝)的盘符, 否则服务器虽启动但无任何数据可传,
                # 表现为"服务已开启却无法传输文件"。因此这里改为强制校验。
                if not self._partition_map:
                    self._log("错误: 源设备尚未配置任何盘符映射 (D/E/F), "
                              "服务器将无任何数据可拷贝。请先在下拉框选择要提供的盘符。")
                    return
            else:
                # 目标设备需要完成分区映射才能下载
                ntfs_count: int = self._ntfs_partition_count
                required_keys = ("D", "E") if ntfs_count == 2 else ("D", "E", "F")
                mapped = [k for k in required_keys if self._partition_map.get(k)]
                if len(mapped) < len(required_keys):
                    self._log(f"请先完成 {'/'.join(required_keys)} 盘符映射"
                              f"(当前: {len(mapped)} 个)")
                    return

            # 校验通过, 切换到传输进度页面 (步骤 4)
            self.ui.go_step(4)

            if dev_type == "源设备":
                self._start_source_server(manual_ip=manual_ip)
            else:
                self._start_target_download(manual_ip=manual_ip)
        except Exception:
            import traceback
            self._log("[诊断] 开始按钮处理异常:\n" + traceback.format_exc())

    # ==================== 磁盘/分区 ====================

    def _populate_disks(self):
        """扫描物理磁盘"""
        self._log("正在扫描磁盘...")
        self.ui.tk_select_box_mqfzmzbe["values"] = ("扫描中...",)

        def _scan():
            try:
                disks = get_disk_list()
                self.ui.after(0, lambda: self._update_combobox(
                    self.ui.tk_select_box_mqfzmzbe,
                    disks,
                    f"检测到 {len(disks)} 个磁盘"
                ))
                # 自动选择: 只有一个磁盘时自动选中
                if len(disks) == 1:
                    self.ui.after(100, lambda: self.ui.tk_select_box_mqfzmzbe.set(disks[0]))
                    self.ui.after(100, lambda: self._on_disk_selected())
            except Exception as e:
                self.ui.after(0, lambda: self._log(f"磁盘扫描失败: {e}"))

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
        for i, (cb, label) in enumerate(combos):
            if i < len(data_letters):
                try:
                    cb.set(data_letters[i])
                    self._log(f"自动映射(物理顺序): 源 {label} → 目标 {data_letters[i]}")
                except tk.TclError:
                    pass
        self._update_partition_map()

    def _update_partition_map(self):
        """从三个 Combobox 读取分区映射"""
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
        """根据网卡、设备类型、当前步骤及传输状态启用/禁用「开始传输」按钮。

        仅在步骤 3 (连接页面) 才显示并启用开始传输按钮，
        且传输进行中不重新启用, 防止用户在步骤 2 自动填充后误点跳过连接步骤。
        """
        dev_type = self._device_type
        nic_selected = self.ui.tk_select_box_mqfzkd6x.get() not in (
            "扫描中...", "未检测到网卡", "", "网卡1", "网卡2"
        )
        current_step = getattr(self.ui, '_step', 0)
        if (nic_selected and dev_type in ("源设备", "目标设备")
                and current_step >= 3 and not self._transferring):
            self.ui.tk_button_mqfzl35t.config(state="normal")
        else:
            self.ui.tk_button_mqfzl35t.config(state="disabled")

    # ==================== IP 配置 ====================
    #
    # 正确流程:
    #   源设备: 启动 HTTP 文件服务器 (从目标 DHCP 获取 IP)
    #   目标设备: 设自身 IP → 启动 DHCP 服务器 → 等源设备获取 IP → 直连源设备
    #

    def _configure_ip(self, adapter_desc: str, device_type: str):
        """根据设备类型配置网络"""
        self._use_dhcp = False
        self._source_ip = ""

        if "源" in str(device_type):
            # 源设备: 释放并重新获取 IP (从目标 DHCP 获取)
            self.ui.after(0, lambda: self._log("源设备: 正在获取 IP..."))
            threading.Thread(target=self._setup_source_network, args=(adapter_desc,), daemon=True).start()
        else:
            # 目标设备: 不自动启动 DHCP, 等待用户点击「寻找旧电脑」
            self.ui.after(0, lambda: self._log(
                "目标设备: 请先选择网卡并点击「寻找旧电脑」启动 DHCP 服务器，"
                "待源设备分配到 IP 后点击「开始接收」"
            ))

    def _setup_source_network(self, adapter_desc):
        """源设备: 从目标 DHCP 获取 IP —— 使用 IP Helper API 只对目标网卡操作,
        避免 ipconfig /renew 逐个续租所有网卡导致长时间阻塞。
        后台 release+renew, 主线程用 NotifyAddrChange 事件驱动等待 (零 CPU 轮询)。
        """
        from nic_scanner import (get_adapter_index, release_dhcp_ip, renew_dhcp_ip,
                                 wait_for_ip_change)

        try:
            adapter_index = get_adapter_index(adapter_desc)
            if adapter_index <= 0:
                self.ui.after(0, lambda: self._log("错误: 找不到目标网卡索引"))
                return

            # 后台线程: 先释放旧租约, 再用 IpRenewAddress 从目标 DHCP 获取新 IP
            self.ui.after(0, lambda: self._log(
                f"释放目标网卡 DHCP 租约 (索引 {adapter_index})..."
            ))
            self.ui.after(0, lambda: self._log(
                f"请求目标网卡 DHCP 续租 (索引 {adapter_index})..."
            ))

            def _do_dhcp():
                release_dhcp_ip(adapter_index)
                renew_dhcp_ip(adapter_index)
            threading.Thread(target=_do_dhcp, daemon=True).start()

            # 事件驱动等待 IP 变化 (NotifyAddrChange, 不消耗 CPU)
            # release 会导致 IP → 0.0.0.0, renew 会导致 0.0.0.0 → 169.254.100.x
            # 每次 IP 变化都会唤醒, 拿到目标 IP 即返回
            deadline = time.time() + 20
            ip = ""
            while time.time() < deadline:
                remaining = deadline - time.time()
                if not wait_for_ip_change(min(remaining, 5.0)):
                    # 超时, 最后检查一次
                    ip = get_local_ip(adapter_desc)
                    break
                ip = get_local_ip(adapter_desc)
                if ip and ip.startswith("169.254.100."):
                    break
                # IP 变了但不是目标 IP (如 release 后的 0.0.0.0), 继续等下一次变化

            if ip and ip != "0.0.0.0":
                self._source_ip = ip
                self.ui.after(0, lambda: self._log(f"源设备 IP: {ip}"))
            else:
                self.ui.after(0, lambda: self._log("源设备: 等待 IP (将使用 APIPA)"))
        except Exception as e:
            self.ui.after(0, lambda: self._log(f"源设备网络: {e}"))

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
            self.ui.after(0, lambda m=msg: self._log(m))
            if not success:
                success2, msg2 = set_ip_via_netsh(adapter_desc, ip)
                self.ui.after(0, lambda m=msg2: self._log(m))
                if success2:
                    self.ui.after(0, lambda: self._log(
                        f"源设备手动 IP 已生效: {ip} (掩码 {SUBNET_MASK})"))
                else:
                    self.ui.after(0, lambda: self._log(
                        f"警告: 源设备手动 IP 设置失败 ({ip}), 网卡可能停留在 APIPA。\n"
                        f"  由于两端掩码已统一为 {SUBNET_MASK}, 仍可直连通信。"))
            else:
                self.ui.after(0, lambda: self._log(
                    f"源设备手动 IP 已生效: {ip} (掩码 {SUBNET_MASK})"))
        except Exception as e:
            self.ui.after(0, lambda: self._log(f"设置源设备手动 IP 失败: {e}"))

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
        self.ui.after(0, lambda m=msg: self._log(m))
        if not success:
            # API 失败 → 回退 netsh (set_ip_via_netsh 内部已用 GBK 解码且默认 /16 掩码)
            success2, msg2 = set_ip_via_netsh(adapter_desc, target_ip)
            self.ui.after(0, lambda m=msg2: self._log(m))
            if success2:
                self.ui.after(0, lambda: self._log(
                    f"目标手动 IP 已生效: {target_ip} (掩码 {SUBNET_MASK})"))
            else:
                self.ui.after(0, lambda: self._log(
                    f"警告: 目标手动 IP 设置失败 ({target_ip}), 网卡可能停留在 APIPA。\n"
                    f"  由于两端掩码已统一为 {SUBNET_MASK}, 即使本机为 APIPA 地址也可与源端 {source_ip} 直连通信。"))
        else:
            self.ui.after(0, lambda: self._log(
                f"目标手动 IP 已生效: {target_ip} (掩码 {SUBNET_MASK})"))

    def _on_dhcp_button(self):
        """目标设备: 点击「寻找旧电脑」→ 后台启动 DHCP 服务器"""
        if self._transferring:
            self._log("传输正在进行中, 暂时无法操作")
            return
        if self._dhcp_server and self._dhcp_server.is_running():
            self._log("DHCP 服务器已在运行")
            return
        nic_display = self.ui.tk_select_box_mqfzkd6x.get()
        if nic_display in ("扫描中...", "未检测到可用网卡", "", "网卡1", "网卡2"):
            self._log("请先选择网卡，再点击「寻找旧电脑」")
            return
        adapter_desc = self._get_adapter_desc(nic_display)
        if not adapter_desc:
            self._log("无法识别所选网卡，请重新选择")
            return
        self.ui.tk_button_dhcp.config(state="disabled")
        self.ui.tk_button_dhcp.configure(text="正在搜索...")
        threading.Thread(target=self._setup_target_dhcp, args=(adapter_desc,), daemon=True).start()

    def _setup_target_dhcp(self, adapter_desc):
        """目标设备: 不设置自身 IP, 依赖 APIPA (169.254.x.x) 自动地址, 直接启动 DHCP 服务器。

        说明: 目标作为 DHCP 服务器, 为源设备分配 169.254.100.2。两端掩码统一为
        SUBNET_MASK (/16), 目标只要有一个 169.254.x.x 的 APIPA 地址 (Windows 在
        网线连通且无其他 DHCP 响应时自动分配), 就与源端 169.254.100.2 处于同一
        /16 网段, 可直连通信。无需手动为自身设 IP —— 之前调用 set_ip_via_api 在
        部分网卡上返回 ERROR_INVALID_PARAMETER(87) "API 参数无效", 导致 DHCP 服务
        器始终无法启动, 故改为纯 APIPA + DHCP 服务器方案。
        """
        from dhcp_server import MiniDHCPServer

        self.ui.after(0, lambda: self.ui.tk_select_box_discover.config(
            values=("等待源设备连接...",)
        ))

        # 诊断: 打印本机当前地址, 确认是否已获得 APIPA (169.254.x.x)
        _cur_ip = get_local_ip(adapter_desc)
        if _cur_ip:
            self.ui.after(0, lambda: self._log(
                f"DHCP 模式: 接收端不设置自身 IP, 当前本机地址 {_cur_ip} (掩码 {SUBNET_MASK})。"
                f"只要该地址属于 169.254.x.x/16, 即可与源端 {DHCP_ASSIGNED_IP} 直连。"))
        else:
            self.ui.after(0, lambda: self._log(
                f"DHCP 模式: 接收端不设置自身 IP, 等待 APIPA 自动分配 (请确认网线已连接); "
                f"DHCP 服务器将照常启动。"))

        # 获取本地 MAC 地址列表，排除本地网卡的 DHCP 自响应
        from nic_scanner import get_local_mac_addresses
        local_macs = get_local_mac_addresses()
        self._log(f"本地 MAC 排除列表: {local_macs}")

        try:
            self._dhcp_server = MiniDHCPServer(exclude_macs=local_macs, out_ip=_cur_ip or "")

            def _on_client(ip, mac, hostname):
                self._update_discover_list(ip, mac, hostname)

            self._dhcp_server.set_on_client(_on_client)
            self._dhcp_server.start()
        except Exception as e:
            self.ui.after(0, lambda: self._log(f"DHCP 启动失败: {e}"))
            self.ui.after(0, lambda: self.ui.tk_button_dhcp.configure(text="寻找旧电脑", state="normal"))
            return
        self._use_dhcp = True
        self._source_ip = DHCP_ASSIGNED_IP  # 目标 DHCP 分配的源 IP
        self._discover_count = 0

        self.ui.after(0, lambda: self._log(
            f"DHCP 已启动, 源设备将获取 {DHCP_ASSIGNED_IP} (60s 超时)..."
        ))
        self.ui.after(60000, self._auto_select_target)
        self.ui.after(0, lambda: self.ui.tk_button_dhcp.configure(text="重新搜索", state="normal"))

    def _update_discover_list(self, ip, mac, hostname=""):
        """更新发现设备下拉框"""
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
        self.ui.after(0, _update)

    def _auto_select_target(self):
        """60 秒后检查：如果只有 1 个客户端，自动选定"""
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
        if manual_ip:
            adapter_desc = self._get_adapter_desc(self.ui.tk_select_box_mqfzkd6x.get())
            if adapter_desc:
                self._apply_manual_ip(adapter_desc, manual_ip)
            self._log(f"手动 IP 模式: 源设备将绑定 {manual_ip}")

        # 重命名 GTMC_User_Profiles (如存在)
        self._rename_gtmc_user_profiles()

        # 生成验证码 + 固定自签名证书 (HTTPS 必需; 证书与 IP 无关, 统一使用一份)
        self._auth_code = tls_utils.generate_auth_code()
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
        self._set_status(f"验证码: {self._auth_code}  等待目标设备连接...")
        self._log("等待目标设备连接下载...")

        # UI 专用区域常驻醒目显示验证码
        self.ui.show_auth_code(self._auth_code)

        self._transferring = True
        self._transfer_done = False
        self.ui.tk_button_mqfzl35t.config(text="传输中...", state="disabled")

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

    def _start_target_download(self, manual_ip=None):
        """目标设备连接源设备下载文件。

        manual_ip: 若提供 (手动 IP 模式), 直接连接该源设备 IP, 无需 DHCP。
                   接收端【无需设置自身 IP】: 只要本机与源设备网络可达即可直连
                   (两端掩码已统一为 SUBNET_MASK, 即使本机停留在 APIPA 也能直连,
                   因为 169.254.x.x 同属 /16)。曾经"将本机设为源末位+1"的尝试会
                   扰动网卡, 导致随后 Python 的 TLS 握手失败, 现已移除。
        """
        if not manual_ip:
            # DHCP 模式: 必须先开启 DHCP 并等待源设备分配到 IP
            if not self._use_dhcp or not (self._dhcp_server and self._dhcp_server.is_running()):
                self._log("请先点击「开启DHCP」启动 DHCP 服务器并等待源设备分配 IP")
                return
            # DHCP 已完成使命 (源设备已拿到 IP)，停止 DHCP 服务器，释放端口避免干扰后续传输
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
        self._reset_progress("正在连接源设备...")
        self.ui.tk_button_mqfzl35t.config(text="连接中...", state="disabled")

        def _connect_and_download():
            if manual_ip:
                # 手动 IP 模式: 直连填写的源设备 IP
                source_ip = manual_ip
                self.ui.after(0, lambda: self._log(
                    f"手动 IP 模式: 直连源设备 {manual_ip}:{TRANSFER_PORT}"
                ))
            elif self._use_dhcp:
                # DHCP 模式: 源 IP 由目标 DHCP 分配 (169.254.100.2)
                self.ui.after(0, lambda: self._log(
                    f"DHCP 模式: 直连源设备 {DHCP_ASSIGNED_IP}:{TRANSFER_PORT}"
                ))
                source_ip = DHCP_ASSIGNED_IP
            else:
                # APIPA 扫描
                self.ui.after(0, lambda: self._log("APIPA 模式: 扫描源设备..."))
                self.ui.after(0, lambda: self._set_status("正在扫描源设备..."))
                source_ip = scan_source_device(
                    log_callback=self._log, auth_code=self._auth_code
                )

            if not source_ip:
                self.ui.after(0, lambda: self._log(
                    "未找到源设备。请确保:\n"
                    "  1. 源设备已启动并选择了'源设备'\n"
                    "  2. 网线已连接\n"
                    "  3. 两端网卡已选择"
                ))
                self.ui.after(0, lambda: self._on_transfer_failed())
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
                    _s = _ssl.wrap_socket(
                        _sock.create_connection((source_ip, TRANSFER_PORT), timeout=5),
                        server_side=False, context=_ctx,
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
            self.ui.after(0, lambda: self._log(f"连接源设备: {source_ip}:{TRANSFER_PORT}"))
            self.ui.after(0, lambda: self.ui.tk_button_mqfzl35t.config(text="接收中..."))

            def _progress(files_done, total_files, bytes_done, total_bytes):
                self.ui.after(0, lambda: self._set_status(
                    f"正在传输... {files_done}/{total_files} 文件"
                ))
                # 总进度条: 优先按字节占比 (大文件传输时文件数不变但字节在涨,
                # 仅按文件数会导致进度条长时间不动); 无总字节信息时退回按文件数
                if total_bytes > 0:
                    self.ui.after(0, lambda: self._set_progress(bytes_done, total_bytes))
                elif total_files > 0:
                    self.ui.after(0, lambda: self._set_progress(files_done, total_files))

            success, files, bytes_done, errors = download_files(
                server_ip=source_ip,
                partition_map=self._partition_map,
                log_callback=self._log,
                progress_callback=_progress,
                partition_progress_callback=self._partition_progress,
                partition_count=self._ntfs_partition_count,
                auth_code=self._auth_code,
                conflict_callback=self._resolve_conflicts,
            )
            self.ui.after(0, lambda: self._on_download_complete(success, files, bytes_done, errors))

        threading.Thread(target=_connect_and_download, daemon=True).start()

    def _on_transfer_failed(self):
        """传输失败，恢复按钮"""
        self._transferring = False
        self._transfer_done = False
        self._use_dhcp = False
        if hasattr(self, "_dhcp_server") and self._dhcp_server:
            self._dhcp_server.stop()
            self._dhcp_server = None
        self._reset_progress("传输失败")
        self.ui.tk_select_box_discover.config(values=("等待 DHCP 响应...",))
        self.ui.tk_button_mqfzl35t.config(text="开始接收", state="normal")
        self.ui.tk_button_dhcp.configure(text="寻找旧电脑", state="normal")

    def _on_download_complete(self, success, files, bytes_done, errors):
        """下载完成回调"""
        if success:
            self._log("\n传输成功！")
        else:
            self._log(f"\n传输完成 (有 {len(errors) if errors else 0} 个错误)")

        # 传输线程结束
        self._transferring = False
        self._transfer_done = True

        # 对于接收方: 启用「校验文件 >」按钮, 并自动跳转至步骤 5
        if self._device_type == "目标设备":
            self.ui.set_button_next("normal", text="校验文件 >")
            self.ui.go_step(5)
            self._log("传输完成，已进入文件校验页面")

        # 重置进度条
        self._set_progress(0)
        self._set_status("传输完成 — 可进入校验页面")

    # ==================== 校验 ====================

    def _on_verify_start(self):
        """步骤 5 校验页面: 点击「开始校验」按钮"""
        self._start_verification()

    def _start_verification(self):
        """启动 CSV 后台校验线程 (12线程并行)"""
        f_drive = self._partition_map.get("F", "")
        if not f_drive:
            self._log("F 盘未映射，跳过校验")
            self._set_verify_status("F 盘未映射，无法校验")
            return

        partition_map = dict(self._partition_map)  # 快照当前映射
        server_ip = self._last_source_ip
        auth_code = self._auth_code
        gtmc_new_name = self._detect_gtmc_new_name()
        if gtmc_new_name:
            self._log(f"检测到 GTMC 目录已重命名为: {gtmc_new_name} (校验时自动映射)")

        # 接收端手动指定的 FullFilelist_DEF.csv (为空则自动识别最新 Appl 文件夹)
        csv_path = self.ui.get_csv_path()
        if csv_path:
            self._log(f"将使用手动指定的 CSV: {csv_path}")
        else:
            self._log("未手动指定 CSV, 将自动识别最新 Appl 文件夹下的 FullFilelist_DEF.csv")

        # 禁用校验按钮 (防止重复点击) + 禁用上一步 (防止中途返回)
        self.ui.tk_button_verify.config(state="disabled")
        self.ui.set_button_prev("disabled")
        self._set_verify_status("正在校验..." )

        def _verify_log(msg):
            """校验线程日志: 同步写入传输日志 + 校验日志"""
            self._log(msg)
            self.ui.after(0, lambda: self._append_verify_log(msg))

        def _verify():
            try:
                def _verify_progress(done, total):
                    pct = int(done / total * 100) if total > 0 else 0
                    self.ui.after(0, lambda: self._set_verify_progress(
                        pct, f"正在校验... {done}/{total} 文件"
                    ))
                    if total > 0:
                        self.ui.after(0, lambda: self._set_progress(done, total))

                ok, passed, failed, skipped, total = run_verification(
                    f_drive_pe=f_drive,
                    partition_map=partition_map,
                    log_callback=_verify_log,
                    stop_check=lambda: self._stop_verify,
                    progress_callback=_verify_progress,
                    server_ip=server_ip,
                    gtmc_new_name=gtmc_new_name,
                    auth_code=auth_code,
                    winpe=(self.ui.winpe_var.get() == "winpe"),
                    csv_path=csv_path,
                )
                if self._stop_verify:
                    self.ui.after(0, lambda: _verify_log("校验已取消"))
                    self.ui.after(0, lambda: self._set_verify_result("校验已取消"))
                    return
                if ok:
                    result_text = f"校验完成!  通过: {passed}  失败: {failed}  跳过: {skipped}  总计: {total}"
                    self.ui.after(0, lambda: _verify_log(
                        f"\n{'='*50}\n  {result_text}\n{'='*50}"
                    ))
                    self.ui.after(0, lambda: self._set_verify_result(result_text, success=True))
                else:
                    self.ui.after(0, lambda: _verify_log("校验失败，请检查日志"))
                    self.ui.after(0, lambda: self._set_verify_result("校验失败，请检查日志", success=False))
            except Exception as e:
                self.ui.after(0, lambda: _verify_log(f"校验异常: {e}"))
                self.ui.after(0, lambda: self._set_verify_result(f"校验异常: {e}", success=False))
            finally:
                self.ui.after(0, lambda: self.ui.tk_button_verify.config(state="normal"))
                self.ui.after(0, lambda: self.ui.set_button_prev("normal"))

        self._verify_thread = threading.Thread(target=_verify, daemon=True)
        self._verify_thread.start()

    def _append_verify_log(self, msg: str):
        """向步骤 5 校验日志区域追加一行"""
        try:
            tw = self.ui.tk_text_verify_log
            tw.configure(state="normal")
            tw.insert("end", msg + "\n")
            tw.see("end")
            tw.configure(state="disabled")
        except Exception:
            pass

    def _set_verify_status(self, text: str):
        """更新步骤 5 校验进度标签"""
        try:
            self.ui.tk_label_verify_progress.configure(text=text)
        except Exception:
            pass

    def _set_verify_progress(self, pct: int, status: str):
        """更新步骤 5 校验进度条 + 标签"""
        try:
            self.ui.tk_verify_progress_bar.config(value=pct)
            self.ui.tk_label_verify_progress.configure(text=status)
        except Exception:
            pass

    def _set_verify_result(self, text: str, success: bool = True):
        """更新步骤 5 校验结果标签"""
        try:
            fg = "#16a34a" if success else "#dc2626"
            self.ui.tk_label_verify_result.configure(text=text, fg=fg)
            self.ui.tk_verify_progress_bar.config(value=100 if success else 0)
        except Exception:
            pass

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

    def _get_adapter_desc(self, nic_display: str) -> str:
        """根据显示名称获取适配器描述"""
        for nic in self._nic_list:
            if nic[0] == nic_display:
                return nic[1]
        self._log(f"错误: 找不到网卡描述")
        return ""

    def _update_combobox(self, cb, values, log_msg=None):
        """更新 Combobox 并记录日志"""
        cb["values"] = values
        if log_msg:
            self._log(log_msg)

    def _log(self, message: str):
        """向 GUI 日志区域输出日志"""
        try:
            text_widget = self.ui.tk_text_mqg105ch
            text_widget.insert("end", message + "\n")
            text_widget.see("end")
        except Exception:
            pass

    # ==================== 进度条 & 状态 ====================

    def _set_status(self, text: str):
        """更新状态标签 (底部状态栏 + 传输页面状态)"""
        try:
            self.ui.tk_label_status.config(text=text)
        except Exception:
            pass
        try:
            self.ui.tk_label_transfer_status.config(text=text)
        except Exception:
            pass

    def _set_progress(self, value: float, maximum: float = 100):
        """更新进度条 (value 0-100)"""
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
            self.ui.after(0, lambda: self.ui.tk_file_progress_bar.config(value=pct))
            self.ui.after(0, lambda p=partition: self._set_status(f"正在拷贝 {p} 盘 ({done}/{total})"))
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
                    text=f"以下 {len(conflicts)} 个文件在目标端已存在，但大小与源端不同。\n"
                         "请选择保留已存在的文件，或覆盖为目标端重新下载。",
                    justify=tk.LEFT,
                    pady=10,
                    fg="#555",
                ).pack(fill=tk.X, padx=15, pady=(10, 5))

                # 表头
                header_frame = tk.Frame(dialog, bg="#e0e0e0")
                header_frame.pack(fill=tk.X, padx=15, pady=(0, 0))
                tk.Label(header_frame, text="文件路径", width=42, anchor=tk.W,
                         bg="#e0e0e0", font=("", 9, "bold")).pack(side=tk.LEFT, padx=4)
                tk.Label(header_frame, text="源端大小", width=14, anchor=tk.W,
                         bg="#e0e0e0", font=("", 9, "bold")).pack(side=tk.LEFT, padx=4)
                tk.Label(header_frame, text="目标端大小", width=14, anchor=tk.W,
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

        self.ui.after(0, _show_dialog)
        event.wait()  # 阻塞 worker 线程, 等待用户点击
        return result[0]

    # ==================== 清理退出 ====================

    def shutdown(self):
        """清理所有后台进程和资源 (窗口关闭 / 异常退出 / atexit 时调用)

        确保:
        - 校验线程安全终止
        - 文件服务器 (HTTPS) 正确关闭, 释放端口和线程
        - DHCP 服务器 (UDP) 正确关闭, 释放端口和 socket
        - 系统休眠策略恢复 (不再阻止锁屏/休眠)
        """
        self._log("[清理] 正在关闭所有后台进程...")

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
