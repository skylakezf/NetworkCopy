"""
控制器 - 连接 UI 与所有业务模块
Phase 1-5 全部集成
"""
import threading
import time
import subprocess
import os
from typing import Literal
from nic_scanner import scan_nics, get_nic_display_list, get_local_ip
from disk_scanner import get_disk_list, get_drive_letter_list, get_disk_number, get_partition_count, get_partition_drive_mapping
from file_transfer import FileServer, download_files, scan_source_device, TRANSFER_PORT
from verifier import run_verification
from ip_config import SOURCE_IP  # 169.254.100.1 (目标设备自身 IP)
DHCP_ASSIGNED_IP = "169.254.100.2"  # DHCP 分配给源设备的 IP


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

        # ---- 校验线程 (独立于传输线程，可并行) ----
        self._verify_thread = None
        self._stop_verify = False  # 新传输开始时通知旧校验停止

    # ==================== 初始化 ====================

    def init(self, ui):
        self.ui = ui
        self._setup_events()
        self._setup_ui_defaults()
        self._populate_nics()

    def _setup_ui_defaults(self):
        """设置 UI 初始状态"""
        self.ui.tk_button_mqfzl35t.config(state="disabled")
        self.ui.tk_select_box_discover.config(values=("等待 DHCP 响应...",))
        self.ui.tk_select_box_discover.set("等待 DHCP 响应...")

    def _setup_events(self):
        """绑定 UI 控件事件"""
        # 网卡选择
        self.ui.tk_select_box_mqfzkd6x.bind(
            "<<ComboboxSelected>>", self._on_nic_selected
        )
        # 设备类型选择
        self.ui.tk_select_box_mqg0hm2h.bind(
            "<<ComboboxSelected>>", self._on_device_type_selected
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

    # ==================== 网卡扫描 ====================

    def _populate_nics(self):
        """扫描网卡并填充下拉列表"""
        self._log("正在扫描网卡...")
        self.ui.tk_select_box_mqfzkd6x["values"] = ["扫描中..."]

        def _scan():
            try:
                nics = scan_nics()
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

    def _on_nic_selected(self, event=None):
        nic_display = self.ui.tk_select_box_mqfzkd6x.get()
        if nic_display in ("扫描中...", "未检测到网卡", ""):
            return

        self._log(f"已选择网卡: {nic_display}")

        adapter_desc = self._get_adapter_desc(nic_display)
        if not adapter_desc:
            return

        # 设备类型已选 → 尝试设置 IP
        dev_type = self.ui.tk_select_box_mqg0hm2h.get()
        if dev_type in ("源设备", "目标设备"):
            self._configure_ip(adapter_desc, dev_type)

    def _on_device_type_selected(self, event=None):
        dev_type = self.ui.tk_select_box_mqg0hm2h.get()
        self._device_type = dev_type
        self._log(f"已选择设备类型: {dev_type}")

        if dev_type == "源设备":
            self.ui.tk_button_mqfzl35t.config(text="开始传输")
            self.ui.hide_discover()
        else:
            self.ui.tk_button_mqfzl35t.config(text="开始接收")
            self.ui.show_discover()

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

                    # 获取分区号→盘符映射，自动填充 D/E/F
                    part_map = get_partition_drive_mapping(disk_num)
                    if part_map:
                        # 按分区号排序，取盘符列表
                        sorted_letters = [letter for _, letter in sorted(part_map.items())]
                        self.ui.after(0, lambda: self._log(
                            f"分区→盘符映射: {dict(sorted(part_map.items()))}"
                        ))
                        # 自动填充: 跳过第一个 (通常是 C 系统分区)
                        self.ui.after(0, lambda: self._auto_fill_drive_mapping(
                            sorted_letters, ntfs_count
                        ))

                    self.ui.after(0, self._check_button_state)
                else:
                    self._ntfs_partition_count = 0
            except Exception as e:
                self._ntfs_partition_count = 0
                import traceback
                tb = traceback.format_exc()
                self.ui.after(0, lambda: self._log(f"分区检测失败: {e}"))
                self.ui.after(0, lambda t=tb: self._log(f"调试: {t}"))

        threading.Thread(target=_detect_partitions, daemon=True).start()

    def _on_partition_map_changed(self, event=None):
        """分区映射变更，更新 partition_map 并检查按钮状态"""
        self._update_partition_map()
        self._check_button_state()

    def _on_start_button(self):
        """开始传输/接收 按钮"""
        if self._transferring:
            self._log("传输正在进行中...")
            return

        self._update_partition_map()

        # 根据 NTFS 分区数验证映射
        ntfs_count: int = self._ntfs_partition_count
        mapped: list[str] = [p for p in ("D", "E", "F") if p in self._partition_map and self._partition_map[p]]

        if ntfs_count == 2:
            # 2 分区: 只需要 D、E
            required_keys: tuple[Literal['D'], Literal['E'], Literal['F']] = ("D", "E")
            required_count = 2
        else:
            # 3/4 分区: 需要 D、E、F
            required_keys: tuple[Literal['D'], Literal['E'], Literal['F']] = ("D", "E", "F")
            required_count = 3

        missing = [k for k in required_keys if k not in self._partition_map or not self._partition_map[k]]
        if len(mapped) < required_count:
            self._log(f"请先完成 {'/'.join(required_keys)} 盘符映射 (当前: {len(mapped)} 个)")
            return

        dev_type = self.ui.tk_select_box_mqg0hm2h.get()

        if dev_type == "源设备":
            self._start_source_server()
        elif dev_type == "目标设备":
            self._start_target_download()
        else:
            self._log("请先选择设备类型")

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

    def _auto_fill_drive_mapping(self, sorted_letters: list, ntfs_count: int):
        """根据分区→盘符映射自动填充 D/E/F 下拉框 (跳过第一个 = 系统分区)"""
        if not sorted_letters:
            return

        # 跳过第一个盘符 (通常是 C 系统分区), 余下的分配给 D/E/F
        data_letters = sorted_letters[1:]  # ["D", "E", "F"]
        combos = (
            (self.ui.tk_select_box_mqfzsdz4, "D"),
            (self.ui.tk_select_box_mqfzuo2y, "E"),
            (self.ui.tk_select_box_mqfzwehm, "F"),
        )

        for i, (cb, label) in enumerate(combos):
            if i < len(data_letters):
                cb.set(data_letters[i])
                self._log(f"自动映射: 源分区 {label} → PE盘符 {data_letters[i]}:")

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
        """根据映射完整性启用/禁用开始按钮"""
        mapped_count = len([v for v in self._partition_map.values() if v])
        dev_type = self.ui.tk_select_box_mqg0hm2h.get()
        nic_selected = self.ui.tk_select_box_mqfzkd6x.get() not in (
            "扫描中...", "未检测到网卡", "", "网卡1", "网卡2"
        )

        # 2 分区需要 2 个映射, 3/4 分区需要 3 个
        required = 2 if self._ntfs_partition_count == 2 else 3
        if mapped_count >= required and dev_type in ("源设备", "目标设备") and nic_selected:
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
            # 目标设备: 设自身 IP + 启动 DHCP
            self.ui.after(0, lambda: self._log("目标设备: 正在启动 DHCP 服务器..."))
            threading.Thread(target=self._setup_target_dhcp, args=(adapter_desc,), daemon=True).start()

    def _setup_source_network(self, adapter_desc):
        """源设备: ipconfig /renew 从目标 DHCP 获取 IP"""
        try:
            subprocess.run(["ipconfig", "/release"], capture_output=True, timeout=10,
                           encoding="utf-8", errors="replace")
            subprocess.run(["ipconfig", "/renew"], capture_output=True, timeout=20,
                           encoding="utf-8", errors="replace")
            time.sleep(2)
            ip = get_local_ip(adapter_desc)
            if ip and ip != "0.0.0.0":
                self._source_ip = ip
                self.ui.after(0, lambda: self._log(f"源设备 IP: {ip}"))
            else:
                self.ui.after(0, lambda: self._log("源设备: 等待 IP (将使用 APIPA)"))
        except Exception as e:
            self.ui.after(0, lambda: self._log(f"源设备网络: {e}"))

    def _setup_target_dhcp(self, adapter_desc):
        """目标设备: 设自身 IP 169.254.100.1 → 启动 DHCP → 等源设备获取 IP"""
        from ip_config import set_ip_via_api
        from dhcp_server import MiniDHCPServer

        self.ui.after(0, lambda: self.ui.tk_select_box_discover.config(
            values=("等待源设备连接...",)
        ))

        success, msg = set_ip_via_api(adapter_desc, SOURCE_IP)
        self.ui.after(0, lambda m=msg: self._log(m))

        if not success:
            try:
                subprocess.run(["ipconfig", "/release"], capture_output=True, timeout=10,
                               encoding="utf-8", errors="replace")
                subprocess.run(["netsh", "interface", "ip", "set", "address",
                                f'"{adapter_desc}"', "static", SOURCE_IP, "255.255.255.0"],
                               capture_output=True, timeout=10,
                               encoding="utf-8", errors="replace")
                self.ui.after(0, lambda: self._log(f"目标 IP: {SOURCE_IP}"))
                success = True
            except Exception:
                pass

        if not success:
            self.ui.after(0, lambda: self._log("无法设置目标 IP, 回退 APIPA"))
            return

        # 获取本地 MAC 地址列表，排除本地网卡的 DHCP 自响应
        from nic_scanner import get_local_mac_addresses
        local_macs = get_local_mac_addresses()
        self._log(f"本地 MAC 排除列表: {local_macs}")

        self._dhcp_server = MiniDHCPServer(exclude_macs=local_macs)

        def _on_client(ip, mac, hostname):
            self._update_discover_list(ip, mac, hostname)

        self._dhcp_server.set_on_client(_on_client)
        self._dhcp_server.start()
        self._use_dhcp = True
        self._source_ip = DHCP_ASSIGNED_IP  # 目标 DHCP 分配的源 IP
        self._discover_count = 0

        self.ui.after(0, lambda: self._log(
            f"DHCP 已启动, 源设备将获取 {DHCP_ASSIGNED_IP} (60s 超时)..."
        ))
        self.ui.after(60000, self._auto_select_target)

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

    def _start_source_server(self):
        """源设备启动 HTTP 文件服务器"""
        self._log("\n" + "=" * 50)
        self._log("源设备模式: 启动文件服务器")
        self._log("=" * 50)
        self._log(f"分区映射: {self._partition_map}")

        # 重命名 GTMC_User_Profiles (如存在)
        self._rename_gtmc_user_profiles()

        self._file_server = FileServer(
            partition_map=self._partition_map,
            log_callback=self._log
        )
        self._file_server.start()
        self._log(f"文件服务器已启动, 监听 0.0.0.0:{TRANSFER_PORT}")
        self._log("等待目标设备连接下载...")

        self._transferring = True
        self.ui.tk_button_mqfzl35t.config(text="传输中...", state="disabled")

    def _rename_gtmc_user_profiles(self):
        """检查源设备 D 盘，若存在 GTMC_User_Profiles 则重命名为 GTMC_User_ProfilesYYMMDD"""
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

    def _start_target_download(self):
        """目标设备连接源设备下载文件"""
        self._log("\n" + "=" * 50)
        self._log("目标设备模式: 连接源设备...")
        self._log("=" * 50)
        self._log(f"分区映射: {self._partition_map}")

        # 如果旧校验还在跑，通知它停止
        if self._verify_thread and self._verify_thread.is_alive():
            self._log("正在停止上一轮校验线程...")
            self._stop_verify = True
            self._verify_thread.join(timeout=3)
        self._stop_verify = False

        self._transferring = True
        self._reset_progress("正在连接源设备...")
        self.ui.tk_button_mqfzl35t.config(text="连接中...", state="disabled")

        def _connect_and_download():
            source_ip = ""

            if self._use_dhcp:
                # DHCP 模式: 源 IP 由目标 DHCP 分配 (169.254.100.2)
                self.ui.after(0, lambda: self._log(
                    f"DHCP 模式: 直连源设备 {DHCP_ASSIGNED_IP}:{TRANSFER_PORT}"
                ))
                source_ip = DHCP_ASSIGNED_IP
            else:
                # APIPA 扫描
                self.ui.after(0, lambda: self._log("APIPA 模式: 扫描源设备..."))
                self.ui.after(0, lambda: self._set_status("正在扫描源设备..."))
                source_ip = scan_source_device(log_callback=self._log)

            if not source_ip:
                self.ui.after(0, lambda: self._log(
                    "未找到源设备。请确保:\n"
                    "  1. 源设备已启动并选择了'源设备'\n"
                    "  2. 网线已连接\n"
                    "  3. 两端网卡已选择"
                ))
                self.ui.after(0, lambda: self._on_transfer_failed())
                return

            self.ui.after(0, lambda: self._log(f"连接源设备: {source_ip}:{TRANSFER_PORT}"))
            self.ui.after(0, lambda: self.ui.tk_button_mqfzl35t.config(text="接收中..."))

            def _progress(files_done, total_files, bytes_done, total_bytes):
                self.ui.after(0, lambda: self._set_status(
                    f"正在传输... {files_done}/{total_files} 文件"
                ))
                if total_files > 0:
                    self.ui.after(0, lambda: self._set_progress(files_done, total_files))

            success, files, bytes_done, errors = download_files(
                server_ip=source_ip,
                partition_map=self._partition_map,
                log_callback=self._log,
                progress_callback=_progress,
                partition_count=self._ntfs_partition_count,
            )
            self.ui.after(0, lambda: self._on_download_complete(success, files, bytes_done, errors))

        threading.Thread(target=_connect_and_download, daemon=True).start()

    def _on_transfer_failed(self):
        """传输失败，恢复按钮"""
        self._transferring = False
        self._use_dhcp = False
        if hasattr(self, "_dhcp_server") and self._dhcp_server:
            self._dhcp_server.stop()
            self._dhcp_server = None
        self._reset_progress("传输失败")
        self.ui.tk_select_box_discover.config(values=("等待 DHCP 响应...",))
        self.ui.tk_button_mqfzl35t.config(text="开始接收", state="normal")

    def _on_download_complete(self, success, files, bytes_done, errors):
        """下载完成回调 — 立即启动后台校验线程，同时恢复按钮"""
        if success:
            self._log("\n传输成功！启动后台校验...")
        else:
            self._log(f"\n传输完成 (有 {len(errors) if errors else 0} 个错误)")
            self._log("启动后台校验已传输的文件...")

        # 传输线程结束，恢复按钮
        self._transferring = False
        self.ui.tk_button_mqfzl35t.config(text="开始接收", state="normal")

        # 重置进度条，为校验做准备
        self._set_progress(0)
        self._set_status("正在校验文件...")

        # 立即启动校验线程（独立于传输线程，边恢复边校验）
        self._start_verification()

    # ==================== 校验 ====================

    def _start_verification(self):
        """启动 CSV 后台校验线程 (下载完成后自动调用, 12线程并行)"""
        f_drive = self._partition_map.get("F", "")
        if not f_drive:
            self._log("F 盘未映射，跳过校验")
            self._reset_progress("校验跳过 (F盘未映射)")
            return

        partition_map = dict(self._partition_map)  # 快照当前映射

        def _verify():
            try:
                def _verify_progress(done, total):
                    self.ui.after(0, lambda: self._set_status(
                        f"正在校验... {done}/{total} 文件"
                    ))
                    if total > 0:
                        self.ui.after(0, lambda: self._set_progress(done, total))

                ok, passed, failed, skipped, total = run_verification(
                    f_drive_pe=f_drive,
                    partition_map=partition_map,
                    log_callback=self._log,
                    stop_check=lambda: self._stop_verify,
                    progress_callback=_verify_progress,
                )
                if self._stop_verify:
                    self.ui.after(0, lambda: self._log("校验已取消 (新一轮传输开始)"))
                    self.ui.after(0, lambda: self._reset_progress("校验已取消"))
                    return
                if ok:
                    self.ui.after(0, lambda: self._log(
                        f"\n{'='*50}\n"
                        f"  校验完成！\n"
                        f"  通过: {passed}  失败: {failed}  跳过: {skipped}  总计: {total}\n"
                        f"{'='*50}"
                    ))
                    self.ui.after(0, lambda: self._reset_progress("校验完成"))
                else:
                    self.ui.after(0, lambda: self._log("校验失败，请检查日志"))
                    self.ui.after(0, lambda: self._reset_progress("校验失败"))
            except Exception as e:
                self.ui.after(0, lambda: self._log(f"校验异常: {e}"))
                self.ui.after(0, lambda: self._reset_progress("校验异常"))

        self._verify_thread = threading.Thread(target=_verify, daemon=True)
        self._verify_thread.start()

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
        """更新状态标签"""
        try:
            self.ui.tk_label_status.config(text=text)
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
