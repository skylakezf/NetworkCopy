"""
配置导入导出模块
提供系统配置导出(发送端)和配置导入(接收端)功能
对应 systemconfig 目录下的 out.cmd / in.cmd 逻辑

导出策略:
  1. 优先调用 systemconfig/out.cmd 子进程 (包含所有 PowerShell 脚本:
     截图、磁盘空间估算、Outlook PST、程序列表等)
  2. 实时逐行捕获输出并回调给 UI (log_callback)
  3. out.cmd 不可用时回退到 Python 内置实现
"""
import os
import re
import sys
import subprocess
import shutil
import zipfile
import datetime
import urllib.request


# ==================== 导出配置 (入口) ====================

def export_config(log_callback=None, status_callback=None):
    """导出当前系统配置到 F:\\Appl\\YYYY-MM-DD\\

    优先调用 out.cmd, 不可用时回退到 Python 内置实现。
    status_callback(name, status) — 每个导出项目完成时回调。
    返回: (success: bool, export_path: str)
    """
    if log_callback is None:
        log_callback = print

    # 1. 尝试查找并运行 out.cmd
    out_cmd = _find_out_cmd()
    if out_cmd:
        log_callback("使用 systemconfig\\out.cmd 导出系统配置...")
        log_callback("")
        success, export_path = _run_out_cmd(out_cmd, log_callback)
        if success:
            return True, export_path
        log_callback("")
        log_callback("out.cmd 执行异常, 回退到内置导出...")
        log_callback("")

    # 2. 回退到 Python 内置实现
    return _export_config_builtin(log_callback, status_callback=status_callback)


# ==================== out.cmd 子进程调用 ====================

def _find_out_cmd():
    """查找 out.cmd 文件路径。
    搜索顺序: exe/脚本同级的 systemconfig/ 目录
    """
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        # __file__ = config_transfer.py 所在目录即项目根目录
        base_dir = os.path.dirname(os.path.abspath(__file__))

    candidates = [
        os.path.join(base_dir, "systemconfig", "out.cmd"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _run_out_cmd(out_cmd_path, log_callback):
    """运行 out.cmd 并通过 log_callback 实时输出每一行。

    out.cmd 内部使用 %~dp0 定位同目录下的 .ps1 脚本,
    因此 cwd 需设置为 out.cmd 所在目录。
    返回: (success: bool, export_path: str)
    """
    today = datetime.datetime.now()
    date_str = today.strftime("%Y-%m-%d")
    export_root = os.path.join("F:\\", "Appl", date_str)

    log_callback(f"执行: {out_cmd_path}")
    log_callback(f"输出目录: {export_root}")
    log_callback("-" * 50)

    try:
        # 设置环境变量:
        #   NETWORKCOPY_HEADLESS=1 → out.cmd 跳过交互式弹窗
        #   HEADLESS=1             → out.cmd 跳过压缩上传 (由 Python 统一处理)
        env = os.environ.copy()
        env["NETWORKCOPY_HEADLESS"] = "1"
        env["HEADLESS"] = "1"

        # bufsize=1 → 行缓冲, 每行 flush 后立即可读
        process = subprocess.Popen(
            ["cmd", "/c", out_cmd_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="gbk",
            errors="replace",
            bufsize=1,
            cwd=os.path.dirname(out_cmd_path),
            env=env,
        )

        # 逐行读取, 实时回调给 UI
        for line in process.stdout:
            line = line.rstrip("\r\n")
            if line:
                log_callback(line)

        process.wait()
        log_callback("-" * 50)

        if process.returncode == 0:
            log_callback("out.cmd 执行完成 (退出码 0)")
            return True, export_root
        else:
            log_callback(f"out.cmd 退出码: {process.returncode} (可能有部分项目失败)")
            # 退出码非 0 不一定代表完全失败, 部分项目可能已成功导出
            return True, export_root

    except FileNotFoundError:
        log_callback("错误: cmd.exe 不可用 (当前环境不支持)")
        return False, export_root
    except Exception as e:
        log_callback(f"运行 out.cmd 异常: {e}")
        return False, export_root


# ==================== Python 内置导出 (回退方案) ====================

def _export_config_builtin(log_callback, status_callback=None):
    """Python 内置导出实现 (out.cmd 不可用时的回退方案)。

    覆盖核心功能 (注册表/收藏夹/IP 配置/程序列表/磁盘空间估算),
    不包含截图、Office 模板宏等 PowerShell 脚本功能。
    status_callback(name, status) — status 为 "成功"|"失败"|"跳过"。
    """
    today = datetime.datetime.now()
    date_str = today.strftime("%Y-%m-%d")
    export_root = os.path.join("F:\\", "Appl", date_str)

    try:
        os.makedirs(export_root, exist_ok=True)
        log_callback(f"创建导出目录: {export_root}")
    except Exception as e:
        log_callback(f"创建导出目录失败: {e}")
        return False, ""

    results = []

    def _track(name, success, skipped=False):
        """记录单项结果并回调状态。"""
        results.append(success)
        if status_callback:
            if skipped:
                status_callback(name, "跳过")
            elif success:
                status_callback(name, "成功")
            else:
                status_callback(name, "失败")

    # 1. 导出 Outlook 邮件规则 (optional: 大部分用户未配置)
    _track("Outlook 邮件规则", _export_reg(
        r"HKCU\Software\Microsoft\Office\14.0\Outlook\Rules",
        os.path.join(export_root, "Outlook_Rules.reg"),
        "Outlook 邮件规则", log_callback, optional=True
    ), skipped=(not os.path.exists(os.path.join(export_root, "Outlook_Rules.reg"))))

    # 2. 导出 Outlook 配置文件
    _track("Outlook 配置文件", _export_reg(
        r"HKCU\Software\Microsoft\Windows NT\CurrentVersion\Windows Messaging Subsystem\Profiles",
        os.path.join(export_root, "Outlook_Profiles.reg"),
        "Outlook 配置文件", log_callback
    ))

    # 3. 导出 Outlook 自动存档 (optional: 大部分用户未配置)
    _track("Outlook 自动存档", _export_reg(
        r"HKCU\Software\Microsoft\Office\14.0\Outlook\Preferences",
        os.path.join(export_root, "Outlook_AutoArchive.reg"),
        "Outlook 自动存档", log_callback, optional=True
    ), skipped=(not os.path.exists(os.path.join(export_root, "Outlook_AutoArchive.reg"))))

    # 4. 导出 Chrome 浏览器收藏夹
    _track("Chrome 收藏夹", _export_chrome_bookmarks(export_root, log_callback))

    # 5. 导出 Edge 浏览器收藏夹
    _track("Edge 收藏夹", _export_edge_bookmarks(export_root, log_callback))

    # 6. 导出打印机列表
    _track("打印机列表", _export_printers(export_root, log_callback))

    # 7. 导出输入法设置
    _track("输入法设置", _export_ime(export_root, log_callback))

    # 8. 导出网卡 IP 配置
    _track("网卡 IP 配置", _export_ip_config(export_root, log_callback))

    # 9. 导出已安装程序列表 (注册表 Win32)
    _track("已安装程序 (注册表)", _export_installed_programs(export_root, log_callback))

    # 10. 磁盘分配单元迁移空间估算 (512B -> 4K)
    _track("磁盘分配单元估算", _export_disk_allocation_estimate(export_root, log_callback))

    # 11. 导出开始菜单 App 列表 (Shell:AppsFolder)
    _track("开始菜单 App", _export_start_menu_apps(export_root, log_callback))

    # 写入 systemconfig.ini (与 out.cmd 格式一致)
    # 用户可能多次导出, 每次导出都追加一条记录, 保留历史
    try:
        ini_path = os.path.join("F:\\", "systemconfig.ini")
        now = datetime.datetime.now()
        lines = []
        if os.path.isfile(ini_path):
            with open(ini_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        with open(ini_path, "w", encoding="utf-8") as f:
            # 保留原内容 (去重 [ExportInfo] 头, 兼容带 BOM 的旧文件)
            if not any(l.strip().lstrip("\ufeff") == "[ExportInfo]" for l in lines):
                f.write("[ExportInfo]\n")
            for l in lines:
                if l.strip().lstrip("\ufeff") == "[ExportInfo]":
                    continue
                f.write(l + "\n")
            # 追加本次导出记录 (最新一条在文件末尾, 各读取方均取最后一条)
            f.write(f"LastExportPath={export_root}\n")
            f.write(f"LastExportTime={now.strftime('%Y-%m-%d %H:%M:%S')}\n")
        log_callback(f"已写入配置索引: {ini_path}")
    except Exception as e:
        log_callback(f"写入 systemconfig.ini 失败: {e}")

    success_count = sum(1 for r in results if r)
    fail_count = sum(1 for r in results if not r)
    log_callback(f"\n内置导出完成: 成功 {success_count} 项, 失败 {fail_count} 项")
    log_callback(f"配置已保存到: {export_root}")
    # log_callback("注意: 截图/Office模板宏等功能需通过 out.cmd 执行")

    return fail_count == 0, export_root


# ==================== 导入配置 ====================

def import_config(config_folder, log_callback=None):
    """从指定配置文件夹导入系统配置

    导入内容: Outlook 注册表、Chrome/Edge 收藏夹、输入法设置、打印机信息
    返回: (success: bool, details: list[(label, status, msg)])
        status: "成功" / "失败" / "跳过"
    """
    if log_callback is None:
        log_callback = print

    if not os.path.isdir(config_folder):
        log_callback(f"配置文件夹不存在: {config_folder}")
        return False, []

    log_callback(f"开始导入配置: {config_folder}")
    log_callback("")

    details = []
    fail_count = 0

    def _record(label, ok, skipped=False, msg=""):
        nonlocal fail_count
        if skipped:
            status = "跳过"
        elif ok:
            status = "成功"
        else:
            status = "失败"
            fail_count += 1
        details.append((label, status, msg))

    # 1. 导入 Outlook 邮件规则/配置文件/自动存档
    for name, label in [
        ("Outlook_Rules.reg", "Outlook 邮件规则"),
        ("Outlook_Profiles.reg", "Outlook 配置文件"),
        ("Outlook_AutoArchive.reg", "Outlook 自动存档"),
    ]:
        reg_path = os.path.join(config_folder, name)
        if os.path.exists(reg_path):
            _record(label, _import_reg(reg_path, label, log_callback))
        else:
            _record(label, False, skipped=True, msg="备份文件不存在")

    # 2. 导入 Chrome 收藏夹
    _record("Chrome 收藏夹", _import_chrome_bookmarks(config_folder, log_callback))

    # 3. 导入 Edge 收藏夹
    _record("Edge 收藏夹", _import_edge_bookmarks(config_folder, log_callback))

    # 4. 导入输入法设置
    reg_path = os.path.join(config_folder, "InputMethod.reg")
    if os.path.exists(reg_path):
        _record("输入法设置", _import_reg(reg_path, "输入法设置", log_callback))
    else:
        _record("输入法设置", False, skipped=True, msg="备份文件不存在")

    # 5. 显示打印机信息 (导入需手动)
    printers_file = os.path.join(config_folder, "Printers.csv")
    if os.path.exists(printers_file):
        _record("打印机列表", _import_printers(printers_file, log_callback))
    else:
        _record("打印机列表", False, skipped=True, msg="备份文件不存在")

    success_count = sum(1 for _, s, _ in details if s == "成功")
    skip_count = sum(1 for _, s, _ in details if s == "跳过")
    log_callback(f"\n导入完成: 成功 {success_count} 项, 失败 {fail_count} 项, 跳过 {skip_count} 项")

    return fail_count == 0, details


# ==================== 配置文件夹查找 ====================

def find_config_folders():
    """扫描 F:\\Appl\\ 下所有 YYYY-MM-DD 格式的文件夹。

    同时读取 F:\\systemconfig.ini 获取最后一次导出的路径。
    返回: [(display_name, full_path), ...]
    """
    results = []

    # 1. 读取 systemconfig.ini (支持 PATH= 和 LastExportPath= 两种格式)
    ini_path = os.path.join("F:\\", "systemconfig.ini")
    ini_folder = None
    if os.path.isfile(ini_path):
        try:
            with open(ini_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    for prefix in ("LastExportPath=", "PATH="):
                        if line.startswith(prefix):
                            path = line.split("=", 1)[1].strip()
                            if os.path.isdir(path):
                                ini_folder = path
                            break
                # 存在多次导出历史时, 取最后一条 (最新一次导出)
        except Exception:
            pass

    if ini_folder:
        results.append((f"[推荐] {os.path.basename(ini_folder)}", ini_folder))

    # 2. 扫描 Appl 目录
    appl_dir = os.path.join("F:\\", "Appl")
    if os.path.isdir(appl_dir):
        date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        folders = []
        try:
            for name in os.listdir(appl_dir):
                full = os.path.join(appl_dir, name)
                if os.path.isdir(full) and date_pattern.match(name):
                    if full != ini_folder:
                        folders.append(full)
        except OSError:
            pass
        folders.sort(reverse=True)
        for f in folders:
            results.append((os.path.basename(f), f))

    return results


def get_config_from_ini():
    """从 F:\\systemconfig.ini 读取配置路径 (支持 PATH= 和 LastExportPath=)，
    返回 (path, time_str) 或 (None, None)。"""
    ini_path = os.path.join("F:\\", "systemconfig.ini")
    if not os.path.isfile(ini_path):
        return None, None
    path = None
    time_str = None
    try:
        with open(ini_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("LastExportTime="):
                    time_str = line.split("=", 1)[1].strip()
                elif line.startswith("LastExportPath=") or line.startswith("PATH="):
                    p = line.split("=", 1)[1].strip()
                    if os.path.isdir(p):
                        path = p
    except Exception:
        pass
    return path, time_str


# ==================== 内部辅助函数 ====================

def _run_command(cmd, log_callback, timeout=30, log_errors=True):
    """运行命令并返回 (success, output)"""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            encoding="gbk", errors="replace"
        )
        output = result.stdout.strip() or result.stderr.strip()
        if result.returncode == 0:
            return True, output
        else:
            if log_errors:
                log_callback(f"  [失败] {' '.join(cmd)[:60]}: {output[:200]}")
            return False, output
    except FileNotFoundError:
        log_callback(f"  [跳过] 命令不可用: {cmd[0]}")
        return False, ""
    except Exception as e:
        log_callback(f"  [错误] {' '.join(cmd)[:60]}: {e}")
        return False, str(e)


def _export_reg(key, output_path, label, log_callback, optional=False):
    """导出注册表键到 .reg 文件。
    optional=True 时，键不存在不视为错误（如 Outlook 规则/自动存档）。"""
    log_callback(f"导出 {label}: {key}")
    success, _ = _run_command(
        ["reg", "export", key, output_path, "/y"],
        log_callback, log_errors=(not optional)
    )
    if success:
        log_callback(f"  [OK] 已导出 {label}")
    elif optional:
        log_callback(f"  [跳过] {label} 未配置（正常）")
        return True  # 视为成功
    return success


def _import_reg(reg_path, label, log_callback):
    """导入 .reg 注册表文件"""
    log_callback(f"导入 {label}: {os.path.basename(reg_path)}")
    success, _ = _run_command(["reg", "import", reg_path], log_callback)
    if success:
        log_callback(f"  [OK] 已导入 {label}")
    return success


def _export_chrome_bookmarks(export_root, log_callback):
    """导出 Chrome 收藏夹 + Login Data"""
    log_callback("导出 Chrome 浏览器收藏夹...")
    localappdata = os.environ.get("LOCALAPPDATA", "")
    chrome_bookmark = os.path.join(
        localappdata, "Google", "Chrome", "User Data", "Default", "Bookmarks"
    )
    chrome_login = os.path.join(
        localappdata, "Google", "Chrome", "User Data", "Default", "Login Data"
    )

    success = True
    if os.path.isfile(chrome_bookmark):
        dst = os.path.join(export_root, "Chrome_Bookmarks")
        try:
            shutil.copy2(chrome_bookmark, dst)
            log_callback("  [OK] Chrome 收藏夹已导出")
        except Exception as e:
            log_callback(f"  [失败] Chrome 收藏夹导出失败: {e}")
            success = False
    else:
        log_callback("  未找到 Chrome 收藏夹 (可能未安装)")

    if os.path.isfile(chrome_login):
        dst = os.path.join(export_root, "Chrome_Login_Data")
        try:
            shutil.copy2(chrome_login, dst)
        except Exception:
            pass

    return success


def _import_chrome_bookmarks(config_folder, log_callback):
    """导入 Chrome 收藏夹"""
    log_callback("导入 Chrome 浏览器收藏夹...")
    src = os.path.join(config_folder, "Chrome_Bookmarks")
    if not os.path.isfile(src):
        log_callback("  未找到 Chrome 收藏夹备份, 跳过")
        return True  # 不算失败

    localappdata = os.environ.get("LOCALAPPDATA", "")
    chrome_dir = os.path.join(
        localappdata, "Google", "Chrome", "User Data", "Default"
    )
    try:
        os.makedirs(chrome_dir, exist_ok=True)
    except Exception:
        pass

    dst = os.path.join(chrome_dir, "Bookmarks")
    try:
        shutil.copy2(src, dst)
        log_callback("  [OK] Chrome 收藏夹已导入")
        return True
    except Exception as e:
        log_callback(f"  [失败] Chrome 收藏夹导入失败: {e}")
        return False


def _export_edge_bookmarks(export_root, log_callback):
    """导出 Edge 收藏夹 + Login Data"""
    log_callback("导出 Edge 浏览器收藏夹...")
    localappdata = os.environ.get("LOCALAPPDATA", "")
    edge_bookmark = os.path.join(
        localappdata, "Microsoft", "Edge", "User Data", "Default", "Bookmarks"
    )
    edge_login = os.path.join(
        localappdata, "Microsoft", "Edge", "User Data", "Default", "Login Data"
    )

    success = True
    if os.path.isfile(edge_bookmark):
        dst = os.path.join(export_root, "Edge_Bookmarks")
        try:
            shutil.copy2(edge_bookmark, dst)
            log_callback("  [OK] Edge 收藏夹已导出")
        except Exception as e:
            log_callback(f"  [失败] Edge 收藏夹导出失败: {e}")
            success = False
    else:
        log_callback("  未找到 Edge 收藏夹 (可能未安装)")

    if os.path.isfile(edge_login):
        dst = os.path.join(export_root, "Edge_Login_Data")
        try:
            shutil.copy2(edge_login, dst)
        except Exception:
            pass

    return success


def _import_edge_bookmarks(config_folder, log_callback):
    """导入 Edge 收藏夹"""
    log_callback("导入 Edge 浏览器收藏夹...")
    src = os.path.join(config_folder, "Edge_Bookmarks")
    if not os.path.isfile(src):
        log_callback("  未找到 Edge 收藏夹备份, 跳过")
        return True

    localappdata = os.environ.get("LOCALAPPDATA", "")
    edge_dir = os.path.join(
        localappdata, "Microsoft", "Edge", "User Data", "Default"
    )
    try:
        os.makedirs(edge_dir, exist_ok=True)
    except Exception:
        pass

    dst = os.path.join(edge_dir, "Bookmarks")
    try:
        shutil.copy2(src, dst)
        log_callback("  [OK] Edge 收藏夹已导入")
        return True
    except Exception as e:
        log_callback(f"  [失败] Edge 收藏夹导入失败: {e}")
        return False


def _export_printers(export_root, log_callback):
    """导出打印机列表 (PowerShell Get-Printer, 替代已移除的 wmic)"""
    log_callback("导出打印机列表...")
    output_path = os.path.join(export_root, "Printers.csv")

    # 用管道 + Out-String 抑制进度条 CLIXML 输出
    ps_script = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "$ProgressPreference='SilentlyContinue';"
        "Get-Printer | Select Name, DriverName |"
        "ConvertTo-Csv -NoTypeInformation | Out-String"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            stderr_snippet = result.stderr.strip()[:200] if result.stderr.strip() else ""
            log_callback(f"  [失败] Get-Printer 退出码 {result.returncode}"
                         + (f": {stderr_snippet}" if stderr_snippet else ""))
            return False

        # 过滤掉 CLIXML 尾部（若有）
        stdout_text = result.stdout
        clixml_pos = stdout_text.find("#< CLIXML")
        if clixml_pos >= 0:
            stdout_text = stdout_text[:clixml_pos]

        lines = [l for l in stdout_text.strip().splitlines() if l.strip()]
        if len(lines) <= 1:
            log_callback("  [警告] 未发现任何打印机")

        with open(output_path, "w", encoding="utf-8-sig") as f:
            f.write(stdout_text.strip() + "\n")
        log_callback(f"  [OK] 打印机列表已导出 ({max(0, len(lines) - 1)} 台)")
        return True
    except FileNotFoundError:
        log_callback("  [跳过] PowerShell 不可用 (WinPE)")
        return True
    except subprocess.TimeoutExpired:
        log_callback("  [失败] Get-Printer 超时")
        return False
    except Exception as e:
        log_callback(f"  [失败] 导出打印机列表异常: {e}")
        return False


def _import_printers(printers_file, log_callback):
    """显示打印机列表供参考 (实际添加需手动)"""
    log_callback("打印机列表 (来自旧设备):")
    try:
        with open(printers_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f.readlines()[:10]:
                line = line.strip()
                if line:
                    log_callback(f"  {line}")
    except Exception as e:
        log_callback(f"  读取打印机列表失败: {e}")
    log_callback("  提示: 打印机需要手动通过控制面板添加")
    return True


def _export_ime(export_root, log_callback):
    """导出输入法设置"""
    log_callback("导出输入法设置...")
    output_path = os.path.join(export_root, "InputMethod.reg")

    keys = [
        r"HKCU\Keyboard Layout\Preload",
        r"HKCU\Control Panel\Input Method",
    ]

    success = False
    for key in keys:
        s, _ = _run_command(
            ["reg", "export", key, output_path, "/y"],
            log_callback, timeout=10
        )
        if s:
            log_callback(f"  [OK] 输入法设置已导出 ({key})")
            success = True
            break

    if not success:
        log_callback("  输入法注册表键不存在或无法导出")

    return success


def _export_ip_config(export_root, log_callback):
    """导出网卡 IP 地址配置 (netsh)"""
    log_callback("导出网卡 IP 地址配置...")
    output_path = os.path.join(export_root, "IP_Config.txt")
    success, output = _run_command(
        ["netsh", "interface", "ip", "show", "config"],
        log_callback
    )
    if success:
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(output)
            log_callback("  [OK] IP 配置已导出")
        except Exception as e:
            log_callback(f"  写入 IP 配置失败: {e}")
            return False
    return success


def _export_installed_programs(export_root, log_callback):
    """通过注册表导出已安装程序列表 (Win32 传统应用)"""
    log_callback("导出已安装程序列表 (注册表 Win32)...")
    output_path = os.path.join(export_root, "Installed_Programs_Regedit.csv")

    ps_script = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "$paths=@('HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
        "'HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
        "'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*');"
        "Get-ItemProperty $paths 2>$null|?{$_.DisplayName}|"
        "Select @{N='Name';E={$_.DisplayName}},@{N='Version';E={$_.DisplayVersion}},"
        "@{N='Publisher';E={$_.Publisher}}|Sort Name|"
        "ConvertTo-Csv -NoTypeInformation"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace"
        )
        with open(output_path, "w", encoding="utf-8-sig") as f:
            f.write(result.stdout)
        log_callback("  [OK] 已安装程序列表已导出")
        return True
    except FileNotFoundError:
        log_callback("  [跳过] PowerShell 不可用 (WinPE), 已安装程序列表未导出")
        return True  # PE 下 PowerShell 不存在是正常的
    except Exception as e:
        log_callback(f"  [失败] 导出已安装程序列表失败: {e}")
        return False


def _export_start_menu_apps(export_root, log_callback):
    """通过 Shell:AppsFolder 枚举"开始"菜单中的所有 App (UWP + 快捷方式)。
    输出 CSV: AppName, AppPath (AppUserModelId 或 .lnk 路径)。
    """
    log_callback("导出开始菜单 App 列表 (Shell:AppsFolder)...")
    output_path = os.path.join(export_root, "StartMenu_Apps.csv")

    # 通过 Shell.Application COM 对象遍历 shell:AppsFolder
    # 注意: 依赖 explorer.exe shell, PE 环境下不可用
    ps_script = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "$sh=New-Object -ComObject Shell.Application;"
        "$f=$sh.NameSpace('shell:AppsFolder');"
        "$f.Items()|%{$n=$_.Name;$p=$_.Path;"
        "[PSCustomObject]@{AppName=$n;AppPath=$p}}|"
        "Sort AppName|"
        "ConvertTo-Csv -NoTypeInformation"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            stderr_snippet = result.stderr.strip()[:200] if result.stderr.strip() else ""
            log_callback(f"  [失败] PowerShell 退出码 {result.returncode}"
                         + (f": {stderr_snippet}" if stderr_snippet else ""))
            return False

        lines = result.stdout.strip().splitlines()
        if len(lines) <= 1:
            log_callback("  [警告] AppsFolder 返回空 (可能无 App 或 shell 不可用)")
            # 空结果也写出表头
            with open(output_path, "w", encoding="utf-8-sig") as f:
                f.write('"AppName","AppPath"\n')
            return True

        with open(output_path, "w", encoding="utf-8-sig") as f:
            f.write(result.stdout.strip() + "\n")
        log_callback(f"  [OK] 开始菜单 App 列表已导出 ({len(lines) - 1} 个)")
        return True
    except FileNotFoundError:
        log_callback("  [跳过] PowerShell 不可用 (WinPE)")
        return True
    except Exception as e:
        log_callback(f"  [失败] 导出开始菜单 App 列表失败: {e}")
        return False


def _export_disk_allocation_estimate(export_root, log_callback):
    """磁盘分配单元迁移空间估算 (512B -> 4K)。
    直接导入 calc_allocation_migration 模块遍历 D/E/F 分区，
    输出全量文件清单及 <4KB 小文件清单 CSV。
    （在 frozen exe 中亦可用，无需 subprocess。）
    """
    log_callback("磁盘分配单元迁移空间估算 (512B->4K)...")

    try:
        from systemconfig import calc_allocation_migration
    except ImportError:
        log_callback("  [跳过] 磁盘扫描模块未找到")
        return True

    def log_wrapper(msg):
        """只记录汇总/关键行到日志，跳过逐文件路径。"""
        stripped = msg.strip()
        if any(kw in stripped for kw in (
            "文件总数", "汇总结果", "计算完成",
            "原占用", "预估迁移", "总计:", "正在扫描",
            "分区不存在", "错误:", "磁盘分配",
            "输出目录", "输出文件", "全量文件清单",
            "小文件清单", "ceil",
        )):
            try:
                log_callback(f"  {stripped}")
            except UnicodeError:
                pass

    try:
        _ok, results = calc_allocation_migration.run_estimate(
            output_dir=export_root,
            log_callback=log_wrapper,
        )

        for r in results:
            log_callback(
                f"  [{r.drive}:] {r.file_count} 文件, "
                + f"<4KB: {r.small_count}, "
                + f"512B->4K: {r.old_aligned_gb}GB -> {r.new_aligned_gb}GB "
                + f"(+{r.diff_gb}GB)"
            )

        log_callback("  [OK] 磁盘空间估算完成")
        return True
    except Exception as e:
        log_callback(f"  [失败] 磁盘空间估算异常: {e}")
        return False


# ============================================================
# 压缩 & 上传到 Profile 服务器
# ============================================================

# Profile 上传服务器地址
PROFILE_UPLOAD_URL = "http://QITV1113.gtmcl.com:3000/api/upload"


def compress_and_upload_config(export_path: str, log_callback=None):
    """将导出文件夹压缩为 zip 并上传到 Profile 服务器。

    命名为 <计算机名>_<日期>.zip，例如 QDNB5098_2026-07-31.zip。
    成功后不会删除本地 ZIP，失败则保留 ZIP 供手工上传。

    Args:
        export_path: 导出文件夹路径 (如 F:\\Appl\\2026-07-31)
        log_callback: 日志回调 (msg: str) -> None

    Returns:
        (compress_ok: bool, zip_path: str, upload_ok: bool)
    """
    if log_callback is None:

        def log_callback(msg):
            print(msg)

    computer_name = os.environ.get("COMPUTERNAME", "UNKNOWN")
    folder_name = os.path.basename(export_path)  # YYYY-MM-DD
    zip_name = f"{computer_name}_{folder_name}.zip"
    zip_dir = os.path.dirname(export_path)  # F:\\Appl
    zip_path = os.path.join(zip_dir, zip_name)

    # ---- 步骤 1: 压缩为 ZIP ----
    log_callback(f"正在压缩配置文件夹...")
    log_callback(f"  源目录: {export_path}")
    log_callback(f"  目标文件: {zip_name}")

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(export_path):
                for fname in files:
                    full = os.path.join(root, fname)
                    arcname = os.path.relpath(full, export_path)
                    zf.write(full, arcname)
        zip_size = os.path.getsize(zip_path)
        log_callback(f"  压缩完成 ({zip_size / 1024:.1f} KB)")
    except Exception as e:
        log_callback(f"  [失败] 压缩异常: {e}")
        return False, "", False

    # ---- 步骤 2: 上传到 Profile 服务器 ----
    log_callback(f"正在上传到 Profile 服务器...")

    try:
        upload_ok, result_body = _upload_zip_multipart(zip_path, zip_name, log_callback)
        if upload_ok:
            log_callback(f"  上传成功! 服务器响应: {result_body}")
        else:
            log_callback(f"  上传返回错误: {result_body}")
        return True, zip_path, upload_ok

    except Exception as e:
        log_callback(f"  [失败] 上传异常: {e}")
        log_callback(f"  ZIP 文件已保留在: {zip_path}")
        return True, zip_path, False


def _upload_zip_multipart(zip_path: str, zip_name: str, log_callback) -> tuple[bool, str]:
    """将 ZIP 文件以 multipart/form-data 上传到 Profile 服务器。

    返回: (upload_ok: bool, result_body: str)
    """
    boundary = "----NetworkCopyUpload"

    # 构造 multipart/form-data body
    body_parts = []

    body_parts.append(f"--{boundary}".encode("utf-8"))
    body_parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{zip_name}"'.encode(
            "utf-8"
        )
    )
    body_parts.append(b"Content-Type: application/zip")
    body_parts.append(b"")

    with open(zip_path, "rb") as fh:
        body_parts.append(fh.read())

    body_parts.append(f"--{boundary}--".encode("utf-8"))
    body_parts.append(b"")

    data = b"\r\n".join(body_parts)

    req = urllib.request.Request(
        PROFILE_UPLOAD_URL,
        data=data,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        result_body = resp.read().decode("utf-8", errors="replace")
        return resp.status == 200, result_body


def upload_zip_to_server(zip_path: str, log_callback=None) -> tuple[bool, str]:
    """上传任意 ZIP 文件到 Profile 服务器 (校验结果上传等场景复用)。

    文件名取自 zip_path 本身, 由调用方负责按规则命名。
    返回: (upload_ok: bool, result_body: str)
    """
    if log_callback is None:

        def log_callback(msg):
            print(msg)

    if not os.path.isfile(zip_path):
        log_callback(f"  [失败] ZIP 文件不存在: {zip_path}")
        return False, ""

    zip_name = os.path.basename(zip_path)
    log_callback(f"正在上传到 Profile 服务器...")
    log_callback(f"  文件: {zip_name}")

    try:
        upload_ok, result_body = _upload_zip_multipart(zip_path, zip_name, log_callback)
        if upload_ok:
            log_callback(f"  上传成功! 服务器响应: {result_body}")
        else:
            log_callback(f"  上传返回错误: {result_body}")
        return upload_ok, result_body
    except Exception as e:
        log_callback(f"  [失败] 上传异常: {e}")
        return False, str(e)
