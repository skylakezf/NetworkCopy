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
import datetime


# ==================== 导出配置 (入口) ====================

def export_config(log_callback=None):
    """导出当前系统配置到 F:\\Appl\\YYYY-MM-DD\\

    优先调用 out.cmd, 不可用时回退到 Python 内置实现。
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
    return _export_config_builtin(log_callback)


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
        # 设置 NETWORKCOPY_HEADLESS 环境变量, 令 out.cmd 跳过交互式弹窗
        env = os.environ.copy()
        env["NETWORKCOPY_HEADLESS"] = "1"

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

def _export_config_builtin(log_callback):
    """Python 内置导出实现 (out.cmd 不可用时的回退方案)。

    仅覆盖核心功能 (注册表/收藏夹/IP 配置/程序列表),
    不包含截图、磁盘空间估算等 PowerShell 脚本功能。
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

    # 1. 导出 Outlook 邮件规则
    results.append(_export_reg(
        r"HKCU\Software\Microsoft\Office\14.0\Outlook\Rules",
        os.path.join(export_root, "Outlook_Rules.reg"),
        "Outlook 邮件规则", log_callback
    ))

    # 2. 导出 Outlook 配置文件
    results.append(_export_reg(
        r"HKCU\Software\Microsoft\Windows NT\CurrentVersion\Windows Messaging Subsystem\Profiles",
        os.path.join(export_root, "Outlook_Profiles.reg"),
        "Outlook 配置文件", log_callback
    ))

    # 3. 导出 Outlook 自动存档
    results.append(_export_reg(
        r"HKCU\Software\Microsoft\Office\14.0\Outlook\Preferences",
        os.path.join(export_root, "Outlook_AutoArchive.reg"),
        "Outlook 自动存档", log_callback
    ))

    # 4. 导出 Chrome 浏览器收藏夹
    results.append(_export_chrome_bookmarks(export_root, log_callback))

    # 5. 导出 Edge 浏览器收藏夹
    results.append(_export_edge_bookmarks(export_root, log_callback))

    # 6. 导出打印机列表
    results.append(_export_printers(export_root, log_callback))

    # 7. 导出输入法设置
    results.append(_export_ime(export_root, log_callback))

    # 8. 导出网卡 IP 地址配置
    results.append(_export_ip_config(export_root, log_callback))

    # 9. 导出已安装程序列表
    results.append(_export_installed_programs(export_root, log_callback))

    # 写入 systemconfig.ini (与 out.cmd 格式一致)
    try:
        ini_path = os.path.join("F:\\", "systemconfig.ini")
        now = datetime.datetime.now()
        with open(ini_path, "w", encoding="utf-8") as f:
            f.write("[ExportInfo]\n")
            f.write(f"LastExportPath={export_root}\n")
            f.write(f"LastExportTime={now.strftime('%Y-%m-%d %H:%M:%S')}\n")
        log_callback(f"已写入配置索引: {ini_path}")
    except Exception as e:
        log_callback(f"写入 systemconfig.ini 失败: {e}")

    success_count = sum(1 for r in results if r)
    fail_count = sum(1 for r in results if not r)
    log_callback(f"\n内置导出完成: 成功 {success_count} 项, 失败 {fail_count} 项")
    log_callback(f"配置已保存到: {export_root}")
    log_callback("注意: 截图/磁盘空间估算等功能仅在 out.cmd 模式下可用")

    return fail_count == 0, export_root


# ==================== 导入配置 ====================

def import_config(config_folder, log_callback=None):
    """从指定配置文件夹导入系统配置

    导入内容: Outlook 注册表、Chrome/Edge 收藏夹、输入法设置、打印机信息
    返回: (success: bool)
    """
    if log_callback is None:
        log_callback = print

    if not os.path.isdir(config_folder):
        log_callback(f"配置文件夹不存在: {config_folder}")
        return False

    log_callback(f"开始导入配置: {config_folder}")
    log_callback("")

    results = []

    # 1. 导入 Outlook 邮件规则
    for name, label in [
        ("Outlook_Rules.reg", "Outlook 邮件规则"),
        ("Outlook_Profiles.reg", "Outlook 配置文件"),
        ("Outlook_AutoArchive.reg", "Outlook 自动存档"),
    ]:
        reg_path = os.path.join(config_folder, name)
        if os.path.exists(reg_path):
            results.append(_import_reg(reg_path, label, log_callback))
        else:
            log_callback(f"跳过 {label}: 备份文件不存在")

    # 2. 导入 Chrome 收藏夹
    results.append(_import_chrome_bookmarks(config_folder, log_callback))

    # 3. 导入 Edge 收藏夹
    results.append(_import_edge_bookmarks(config_folder, log_callback))

    # 4. 导入输入法设置
    reg_path = os.path.join(config_folder, "InputMethod.reg")
    if os.path.exists(reg_path):
        results.append(_import_reg(reg_path, "输入法设置", log_callback))
    else:
        log_callback("跳过 输入法设置: 备份文件不存在")

    # 5. 显示打印机信息 (导入需手动)
    printers_file = os.path.join(config_folder, "Printers.txt")
    if os.path.exists(printers_file):
        results.append(_import_printers(printers_file, log_callback))

    success_count = sum(1 for r in results if r)
    fail_count = sum(1 for r in results if not r)
    log_callback(f"\n导入完成: 成功 {success_count} 项, 失败 {fail_count} 项")

    return fail_count == 0


# ==================== 配置文件夹查找 ====================

def find_config_folders():
    """扫描 F:\\Appl\\ 下所有 YYYY-MM-DD 格式的文件夹。

    同时读取 F:\\systemconfig.ini 获取最后一次导出的路径。
    返回: [(display_name, full_path), ...]
    """
    results = []

    # 1. 读取 systemconfig.ini
    ini_path = os.path.join("F:\\", "systemconfig.ini")
    ini_folder = None
    if os.path.isfile(ini_path):
        try:
            with open(ini_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("PATH="):
                        path = line.split("=", 1)[1].strip()
                        if os.path.isdir(path):
                            ini_folder = path
                        break
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
    """从 F:\\systemconfig.ini 读取配置路径，返回路径或 None"""
    ini_path = os.path.join("F:\\", "systemconfig.ini")
    if not os.path.isfile(ini_path):
        return None
    try:
        with open(ini_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("PATH="):
                    path = line.split("=", 1)[1].strip()
                    if os.path.isdir(path):
                        return path
    except Exception:
        pass
    return None


# ==================== 内部辅助函数 ====================

def _run_command(cmd, log_callback, timeout=30):
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
            log_callback(f"  [失败] {' '.join(cmd)[:60]}: {output[:200]}")
            return False, output
    except FileNotFoundError:
        log_callback(f"  [跳过] 命令不可用: {cmd[0]}")
        return False, ""
    except Exception as e:
        log_callback(f"  [错误] {' '.join(cmd)[:60]}: {e}")
        return False, str(e)


def _export_reg(key, output_path, label, log_callback):
    """导出注册表键到 .reg 文件"""
    log_callback(f"导出 {label}: {key}")
    success, _ = _run_command(
        ["reg", "export", key, output_path, "/y"],
        log_callback
    )
    if success:
        log_callback(f"  [OK] 已导出 {label}")
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
    """导出打印机列表 (wmic)"""
    log_callback("导出打印机列表...")
    output_path = os.path.join(export_root, "Printers.txt")
    success, output = _run_command(
        ["wmic", "printer", "get", "Name,DriverName", "/format:csv"],
        log_callback
    )
    if success:
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(output)
            log_callback("  [OK] 打印机列表已导出")
        except Exception as e:
            log_callback(f"  写入打印机列表失败: {e}")
            return False
    return success


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
    """通过 PowerShell 导出已安装程序列表"""
    log_callback("导出已安装程序列表 (PowerShell)...")
    output_path = os.path.join(export_root, "Installed_Programs.txt")

    ps_script = (
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
            encoding="gbk", errors="replace"
        )
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(result.stdout)
        log_callback("  [OK] 已安装程序列表已导出")
        return True
    except FileNotFoundError:
        log_callback("  [跳过] PowerShell 不可用 (WinPE), 已安装程序列表未导出")
        return True  # PE 下 PowerShell 不存在是正常的
    except Exception as e:
        log_callback(f"  [失败] 导出已安装程序列表失败: {e}")
        return False
