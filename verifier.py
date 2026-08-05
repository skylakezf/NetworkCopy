"""
数据校验模块
Phase 4: CSV 校验
- 定位 F:\\Appl\\ 下最新 YYYY/MM/DD 文件夹
- 解析 FullFilelist_DEF.csv
- CSV 格式: Drive | FullPath | FileName | SizeBytes
- 只校验文件是否存在 + 大小是否匹配 (无 MD5)
- 通过 A 列 Drive + partition_map 获取 PE 下实际路径
- 跳过 AppData / $前缀 / System Volume Information / WeChat Files 目录
- 新增 E 列填入 Y/N/S
"""
import os
import csv
import re
import ssl
import threading
import json
import urllib.request
import urllib.parse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed


# ---- 编码自动检测 ----

def _detect_csv_encoding(csv_path: str) -> str:
    """根据文件头 BOM 自动检测 CSV 编码。

    支持的 BOM:
      - FF FE       → UTF-16 LE (Windows PowerShell Out-File -Encoding Unicode 等)
      - FE FF       → UTF-16 BE
      - EF BB BF    → UTF-8-SIG
      - 无 BOM      → UTF-8 (回退)

    返回: 编码名称字符串 (如 "utf-16-le", "utf-8-sig")
    """
    if not os.path.isfile(csv_path):
        return "utf-8-sig"
    try:
        with open(csv_path, "rb") as f:
            head = f.read(4)
    except OSError:
        return "utf-8-sig"

    if len(head) >= 2:
        if head[:2] == b"\xff\xfe":
            return "utf-16-le"
        if head[:2] == b"\xfe\xff":
            return "utf-16-be"
    if len(head) >= 3 and head[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    return "utf-8"

# 客户端 SSL 上下文: 不校验自签名证书 (与 file_transfer.py 一致)
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# 与 file_transfer.py 保持一致
SKIP_DIRS = {"AppData", "System Volume Information", "WeChat Files"}
SKIP_PREFIXES = ("$",)
TRANSFER_PORT = 9999

# 校验线程数 (文件多时 I/O 是瓶颈，多线程可大幅加速)
DEFAULT_VERIFY_WORKERS = 64


def is_running_in_winpe() -> bool:
    """检测当前是否运行在 Windows PE 环境 (与 control.py 中逻辑一致, 本地副本避免循环导入)。

    判定依据 (任一满足即视为 PE):
      1. 注册表 HKLM\\SYSTEM\\CurrentControlSet\\Control\\MiniNT 存在;
      2. 存在 X:\\Windows\\System32。
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


def _parse_date_from_folder(folder_name: str):
    """
    解析文件夹名中的日期，返回 datetime 或 None。

    支持的命名格式:
      1. 纯日期: 2024-01-15 / 20240115 / 2024/01/15
      2. 带前缀 (配置修改后新格式): <主机名>_YYYY-MM-DD, 例如:
            QDNBS537_2026-07-28
            DESKTOP-K5JBCND_2026-07-07
         前缀与日期用下划线分隔，取最后一个下划线后的子串作为日期解析。
    """
    name = folder_name.strip()

    # 1) 整体当作纯日期 (兼容旧版 YYYY/MM/DD 单层目录)
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(name, fmt)
        except ValueError:
            continue

    # 2) 带前缀: 取最后一个 '_' 之后的子串解析日期
    if "_" in name:
        last = name.rsplit("_", 1)[-1]
        for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(last, fmt)
            except ValueError:
                continue

    # 3) 兜底: 在任意位置搜索 YYYY-MM-DD / YYYY/MM/DD / YYYYMMDD
    m = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", name)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = re.search(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)", name)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    return None


def find_latest_appl_folder(f_drive: str) -> str:
    """
    在 F:\\Appl\\ 下找到日期最新的文件夹
    f_drive: PE 下 F 盘的实际盘符，如 "K:"
    返回最新文件夹的完整路径
    """
    appl_dir = os.path.join(f_drive.rstrip("\\") + "\\", "Appl")
    if not os.path.isdir(appl_dir):
        raise FileNotFoundError(f"Appl 目录不存在: {appl_dir}")

    best_path = None
    best_date = None

    for entry in os.listdir(appl_dir):
        entry_path = os.path.join(appl_dir, entry)
        if not os.path.isdir(entry_path):
            continue
        parsed = _parse_date_from_folder(entry)
        if parsed:
            if best_date is None or parsed > best_date:
                best_date = parsed
                best_path = entry_path

    if not best_path:
        raise FileNotFoundError(f"在 {appl_dir} 中未找到 YYYY/MM/DD 格式的文件夹")

    return best_path


def find_csv_file(folder_path: str) -> str:
    """
    在文件夹中查找 FullFilelist_DEF.csv
    """
    csv_path = os.path.join(folder_path, "FullFilelist_DEF.csv")
    if os.path.isfile(csv_path):
        return csv_path

    # 也尝试搜索 (不区分大小写)
    for f in os.listdir(folder_path):
        if f.lower() == "fullfilelist_def.csv":
            return os.path.join(folder_path, f)

    raise FileNotFoundError(f"在 {folder_path} 中未找到 FullFilelist_DEF.csv")


def _find_csv_from_ini(f_drive_pe: str) -> str | None:
    """从 systemconfig.ini 读取配置路径，定位对应的 FullFilelist_DEF.csv。

    发送端导出配置时会将路径写入 F:\\systemconfig.ini (如 LastExportPath=F:\\Appl\\2026-07-28\\)。
    接收端数据传输完成后, F 盘内容完整复制, systemconfig.ini 也随之到达。
    此函数读取 ini, 将路径中的原始盘符(F:)映射为 PE 下实际盘符(f_drive_pe),
    然后查找对应的 CSV 文件。

    f_drive_pe: PE 下 F 盘的实际盘符，如 "K:"
    返回 CSV 完整路径，找不到则返回 None。
    """
    ini_path = os.path.join(f_drive_pe.rstrip("\\") + "\\", "systemconfig.ini")
    if not os.path.isfile(ini_path):
        return None

    config_path = None
    try:
        with open(ini_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("LastExportPath=") or line.startswith("PATH="):
                    p = line.split("=", 1)[1].strip()
                    # 路径中的盘符是原始盘符(如 F:), PE 下需映射为 f_drive_pe
                    if len(p) >= 3 and p[1:3] == ":\\":
                        rel = p[3:]  # Appl\\2026-07-28\\
                        mapped = os.path.join(f_drive_pe.rstrip("\\") + "\\", rel)
                        if os.path.isdir(mapped):
                            config_path = mapped
                    elif os.path.isdir(p):
                        config_path = p
    except Exception:
        pass

    if config_path:
        csv_path = os.path.join(config_path, "FullFilelist_DEF.csv")
        if os.path.isfile(csv_path):
            return csv_path
        # 也搜索不区分大小写
        try:
            for fname in os.listdir(config_path):
                if fname.lower() == "fullfilelist_def.csv":
                    return os.path.join(config_path, fname)
        except OSError:
            pass
    return None


# ---- 文件类型查询 (fileinfo.com) ----

# 模块级缓存: 已查询过的扩展名 → 文件类型描述
_EXTENSION_CACHE: dict = {}
_ext_cache_lock = threading.Lock()

# 常用 Windows 扩展名内置映射 (减少网络请求, 即时返回)
_BUILTIN_EXTENSIONS: dict[str, str] = {
    # 可执行文件 / 库
    "exe": "Executable File",
    "dll": "Dynamic Link Library",
    "sys": "System File",
    "drv": "Device Driver",
    "ocx": "ActiveX Control",
    "ax": "ActiveX Control",
    "cpl": "Control Panel Applet",
    "scr": "Screen Saver",
    "msi": "Windows Installer Package",
    "msp": "Windows Installer Patch",
    "com": "Command File",
    # 脚本 / 批处理
    "bat": "Batch File",
    "cmd": "Windows Command Script",
    "ps1": "PowerShell Script",
    "vbs": "VBScript File",
    "js": "JavaScript File",
    "wsf": "Windows Script File",
    # 配置文件
    "ini": "Configuration Settings",
    "cfg": "Configuration File",
    "conf": "Configuration File",
    "inf": "Setup Information File",
    "reg": "Registry File",
    "pol": "Policy File",
    "manifest": "Assembly Manifest",
    # 文档
    "doc": "Microsoft Word Document",
    "docx": "Microsoft Word Document",
    "xls": "Microsoft Excel Spreadsheet",
    "xlsx": "Microsoft Excel Spreadsheet",
    "ppt": "Microsoft PowerPoint Presentation",
    "pptx": "Microsoft PowerPoint Presentation",
    "pdf": "Portable Document Format",
    "txt": "Text Document",
    "rtf": "Rich Text Format",
    "csv": "Comma Separated Values File",
    # 数据 / 数据库
    "mdb": "Microsoft Access Database",
    "accdb": "Microsoft Access Database",
    "db": "Database File",
    "sqlite": "SQLite Database",
    "xml": "XML File",
    "json": "JSON Data File",
    "dat": "Data File",
    "bin": "Binary Data File",
    # 日志 / 临时 / 缓存
    "log": "Log File",
    "log1": "Registry Transaction Log",
    "log2": "Registry Transaction Log",
    "tmp": "Temporary File",
    "temp": "Temporary File",
    "bak": "Backup File",
    "old": "Old/Backup File",
    "cache": "Cache File",
    "etl": "Event Trace Log",
    "evtx": "Windows Event Log",
    # 注册表相关
    "blf": "Registry Transaction Log",
    "regtrans-ms": "Registry Transaction File",
    # 快捷方式
    "lnk": "Windows Shortcut",
    "url": "Internet Shortcut",
    "pif": "Program Information File",
    # 字体
    "ttf": "TrueType Font",
    "ttc": "TrueType Collection Font",
    "otf": "OpenType Font",
    "fon": "Font File",
    # 媒体
    "jpg": "JPEG Image",
    "jpeg": "JPEG Image",
    "png": "PNG Image",
    "gif": "GIF Image",
    "bmp": "Bitmap Image",
    "ico": "Icon File",
    "svg": "SVG Image",
    "tif": "TIFF Image",
    "tiff": "TIFF Image",
    "wav": "WAV Audio",
    "mp3": "MP3 Audio",
    "wma": "Windows Media Audio",
    "mp4": "MP4 Video",
    "avi": "AVI Video",
    "wmv": "Windows Media Video",
    "mkv": "Matroska Video",
    "mov": "QuickTime Movie",
    # 压缩
    "zip": "Compressed Zip Archive",
    "rar": "WinRAR Archive",
    "7z": "7-Zip Archive",
    "tar": "Tape Archive",
    "gz": "Gzip Compressed Archive",
    "cab": "Windows Cabinet File",
    "msu": "Windows Update Standalone Package",
    # 系统文件
    "cat": "Security Catalog",
    "mui": "Multilingual User Interface File",
    "pf": "Prefetch File",
    "dmp": "Memory Dump File",
    "wer": "Windows Error Report",
    "hdmp": "Heap Dump File",
    "mdmp": "Minidump File",
    # 证书 / 安全
    "cer": "Security Certificate",
    "crt": "Security Certificate",
    "pem": "Privacy Enhanced Mail Certificate",
    "pfx": "Personal Information Exchange",
    "der": "DER Encoded Certificate",
    # 其他常见
    "chm": "Compiled HTML Help",
    "hlp": "Windows Help File",
    "cur": "Cursor File",
    "ani": "Animated Cursor",
    "icl": "Icon Library",
    "theme": "Windows Theme File",
    "themepack": "Windows Theme Pack",
    "diagcab": "Troubleshooting Pack",
    "sdb": "Application Compatibility Database",
    "nls": "National Language Support File",
    "luac": "Compiled Lua Script",
    "lua": "Lua Script",
    "py": "Python Script",
    "pyc": "Python Compiled File",
    "pyd": "Python Dynamic Module",
    "cs": "C# Source Code",
    "h": "C/C++ Header File",
    "cpp": "C++ Source Code",
    "lib": "Static Library",
    "obj": "Object File",
    "pdb": "Program Database",
    "lib": "Static Library",
    "exp": "Exports Library File",
    "res": "Compiled Resource File",
    "rc": "Resource Script",
    # 打印机
    "ppd": "PostScript Printer Description",
    "gpd": "Generic Printer Description",
    "inf_loc": "Localized INF File",
    "pnf": "Precompiled INF File",
}


def _fetch_extension_info(ext: str) -> str:
    """查询 fileinfo.com 获取扩展名对应的文件类型描述 (线程安全, 带缓存)。

    优先使用内置映射, 其次模块级缓存, 最后通过 HTTP 查询 JSON-LD 结构化数据。
    """
    ext_lower = ext.lower()
    if not ext_lower:
        return ""

    with _ext_cache_lock:
        if ext_lower in _BUILTIN_EXTENSIONS:
            return _BUILTIN_EXTENSIONS[ext_lower]
        if ext_lower in _EXTENSION_CACHE:
            return _EXTENSION_CACHE[ext_lower]

    result = f"{ext.upper()} File"
    try:
        url = f"https://fileinfo.com/extension/{ext_lower}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept-Language": "en-US,en;q=0.9",
        })
        resp = urllib.request.urlopen(req, timeout=10, context=_SSL_CTX)
        html = resp.read().decode("utf-8", errors="replace")

        # 策略: 解析 JSON-LD 结构化数据 (alternateName = 主文件类型名称)
        m = re.search(
            r'<script\s+type="application/ld\+json">(.*?)</script>',
            html, re.I | re.S,
        )
        if m:
            try:
                data = json.loads(m.group(1))
                items = data.get("@graph", [data]) if isinstance(data, dict) else [data]
                if isinstance(items, list):
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        alt = item.get("alternateName", "")
                        if alt and alt.lower() != f"{ext_lower} file extension":
                            result = alt
                            break
            except json.JSONDecodeError:
                pass

    except Exception:
        pass

    with _ext_cache_lock:
        _EXTENSION_CACHE[ext_lower] = result
    return result


def _get_file_extension(file_path: str) -> str:
    """从文件路径提取扩展名 (不含点号, 小写)"""
    _, ext = os.path.splitext(file_path)
    return ext.lstrip(".").lower()


def add_file_type_column(csv_path: str, log_callback=None) -> bool:
    """为校验后的 CSV 添加 G 列 (FileType), 通过 fileinfo.com 查询扩展名描述。

    收集 CSV 中所有唯一扩展名 → 并行查询 fileinfo.com (内置映射即时 + 未知扩展名在线查询)
    → 写入 G 列, 供用户判断缺失文件的重要性。

    返回 True 表示成功添加。
    """
    def log(msg):
        if log_callback:
            log_callback(msg)

    # ---- 读取 CSV ----
    rows = []
    header = []
    enc = _detect_csv_encoding(csv_path)
    try:
        with open(csv_path, "r", encoding=enc, newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header:
                log("  CSV 为空，跳过文件类型标记")
                return False
            rows = list(reader)
    except Exception as e:
        log(f"  读取 CSV 失败: {e}")
        return False

    if not rows:
        log("  无数据行，跳过文件类型标记")
        return False

    # ---- 收集所有唯一扩展名 ----
    try:
        col_fullpath = header.index("FullPath")
    except ValueError:
        col_fullpath = 1

    extensions: set = set()
    for row in rows:
        if len(row) > col_fullpath:
            ext = _get_file_extension(row[col_fullpath])
            if ext:
                extensions.add(ext)

    if not extensions:
        log("  未检测到任何文件扩展名")
        return False

    log(f"\n查询文件类型: {len(extensions)} 种扩展名...")

    # ---- 分两阶段查询 ----
    # 阶段 1: 内置映射 (即时)
    builtin_hits = [e for e in extensions if e in _BUILTIN_EXTENSIONS]
    if builtin_hits:
        log(f"  内置映射命中: {len(builtin_hits)} 种扩展名")

    # 阶段 2: 在线查询未知扩展名 (线程池并发)
    network_exts = sorted(e for e in extensions if e not in _BUILTIN_EXTENSIONS and e not in _EXTENSION_CACHE)
    if network_exts:
        log(f"  在线查询: {len(network_exts)} 种扩展名...")
        done = [0]
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(_fetch_extension_info, e): e
                for e in network_exts
            }
            for future in as_completed(futures):
                done[0] += 1
                if done[0] % 30 == 0 or done[0] == len(network_exts):
                    log(f"    扩展名查询进度: {done[0]}/{len(network_exts)}")

    # ---- 构建映射表 ----
    ext_map: dict = {}
    for ext in extensions:
        ext_map[ext] = _fetch_extension_info(ext)

    # ---- 写入 G 列 (index 6, column F 留空) ----
    col_g = 6
    full_header = list(header)
    while len(full_header) <= col_g:
        full_header.append("")
    full_header[col_g] = "FileType"

    for row in rows:
        while len(row) <= col_g:
            row.append("")
        if len(row) > col_fullpath:
            ext = _get_file_extension(row[col_fullpath])
            row[col_g] = ext_map.get(ext, "")
        else:
            row[col_g] = ""

    # ---- 写回 CSV ----
    try:
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(full_header)
            writer.writerows(rows)
        log(f"文件类型已写入 G 列 (共 {len(extensions)} 种扩展名, "
            f"内置 {len(builtin_hits)}, 在线查询 {len(network_exts)})")
        return True
    except Exception as e:
        log(f"  写入文件类型失败: {e}")
        return False


def _patch_csv_gtmc_paths(csv_path: str, gtmc_new_name: str) -> None:
    """
    直接修改 CSV 文件内容: 将 GTMC_User_Profiles 替换为 GTMC_User_ProfilesYYMMDD
    修改后 CSV 中的路径与实际文件系统一致，后续校验无需额外处理
    """
    old_name = "GTMC_User_Profiles"
    new_name = gtmc_new_name

    enc = _detect_csv_encoding(csv_path)
    with open(csv_path, "r", encoding=enc, newline="") as f:
        content = f.read()

    if old_name not in content or new_name in content:
        return  # 无需替换 (或已替换过, 避免重复拼接后缀)

    content = content.replace(old_name, new_name)

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(content)


def _should_skip_path(full_path: str) -> bool:
    """
    判断路径是否属于需要跳过的目录 (AppData / $前缀 / System Volume Information)
    full_path: 完整路径如 "D:\\Users\\xxx\\AppData\\Local\\..."
    返回 True 表示应跳过校验
    """
    # 标准化路径分隔符
    normalized = full_path.replace("/", "\\")
    parts = normalized.split("\\")
    for part in parts:
        if part in SKIP_DIRS:
            return True
        if part.startswith(SKIP_PREFIXES):
            return True
    return False


def _verify_single_row(
    row: list,
    idx: int,
    col_a: int,
    col_b: int,
    col_d: int,
    col_e: int,
    partition_map: dict,
    log_callback=None,
) -> tuple:
    """
    校验单行 (在线程池中执行) —— 纯校验，不做重试下载
    校验逻辑: 通过 A 列 Drive + partition_map 定位 PE 路径，比对 D 列大小
    CSV 格式: A=Drive, B=FullPath, C=FileName, D=SizeBytes
    返回: (idx, updated_row, result, log_msg)
    """
    full_path = ""
    try:
        full_path = row[col_b].strip() if len(row) > col_b else ""

        # 检查是否需要跳过
        if _should_skip_path(full_path):
            while len(row) <= col_e:
                row.append("")
            row[col_e] = "S"  # S = Skipped
            return (idx, row, "S", None)

        # 通过 A 列 Drive (D/E/F) 查找 PE 盘符
        drive_letter = row[col_a].strip().upper() if len(row) > col_a else ""
        if drive_letter and drive_letter in partition_map:
            pe_drive = partition_map[drive_letter].rstrip("\\") + "\\"
        elif len(full_path) >= 2 and full_path[1] == ":":
            src_drive = full_path[0].upper()
            raw = partition_map.get(src_drive, "")
            pe_drive = raw.rstrip("\\") + "\\" if raw else ""
        else:
            pe_drive = ""

        # 从 B 列路径中提取相对路径部分
        if len(full_path) >= 2 and full_path[1] == ":":
            rel_path = full_path[3:]  # 去掉 "X:\"
        else:
            rel_path = full_path

        actual_path = os.path.join(pe_drive, rel_path) if pe_drive else full_path

        # 获取 CSV 中记录的文件大小 (D 列)
        expected_size_str = row[col_d].strip() if len(row) > col_d else "0"
        try:
            expected_size = int(expected_size_str)
        except (ValueError, TypeError):
            expected_size = -1

        # 校验文件存在性
        if not os.path.isfile(actual_path):
            while len(row) <= col_e:
                row.append("")
            row[col_e] = "N"
            return (idx, row, "N", f"  [N] 文件不存在: {actual_path}")

        actual_size = os.path.getsize(actual_path)

        # 比对文件大小
        if expected_size >= 0 and actual_size != expected_size:
            while len(row) <= col_e:
                row.append("")
            row[col_e] = "N"
            return (idx, row, "N",
                    f"  [N] 大小不匹配: 期望{expected_size} 实际{actual_size} - {actual_path}")

        # 存在 + 大小匹配 → 通过
        while len(row) <= col_e:
            row.append("")
        row[col_e] = "Y"
        return (idx, row, "Y", None)

    except Exception as e:
        while len(row) <= col_e:
            row.append("")
        row[col_e] = "N"
        return (idx, row, "N", f"  [N] 校验异常: {full_path[:80]} - {e}")


def _retry_missing_files(
    server_ip: str,
    port: int,
    partition_map: dict,
    csv_path: str,
    col_a: int,
    col_b: int,
    col_d: int,
    col_e: int,
    log_callback=None,
    auth_code: str = "",
) -> int:
    """
    校验完成后，对缺失文件进行批量重试下载
    返回成功下载的文件数
    """
    import socket as _socket
    import struct as _struct
    import time as _time

    def log(msg):
        if log_callback:
            log_callback(msg)

    # 设置 socket 默认超时, 防止 resp.read() 在 HTTP/1.1 Content-Length 与
    # 实际发送字节数不匹配时无限阻塞 (服务端线程崩溃 / 网络中断 / 缓冲区未刷)
    _old_timeout = _socket.getdefaulttimeout()
    _socket.setdefaulttimeout(60)

    try:
        return _retry_missing_files_inner(
            server_ip, port, partition_map, csv_path,
            col_a, col_b, col_d, col_e,
            log, auth_code,
        )
    finally:
        _socket.setdefaulttimeout(_old_timeout)


def _retry_missing_files_inner(
    server_ip: str,
    port: int,
    partition_map: dict,
    csv_path: str,
    col_a: int,
    col_b: int,
    col_d: int,
    col_e: int,
    log,
    auth_code: str,
) -> int:
    import struct as _struct
    import time as _time

    # 收集所有 N 行中文件确实不存在的条目
    missing_entries = []  # [(row_idx, row, drive, rel_path, actual_path, expected_size), ...]
    _skipped_parents = set()  # 已知无法创建/写入的父目录 (与首次下载一致)

    try:
        enc = _detect_csv_encoding(csv_path)
        with open(csv_path, "r", encoding=enc, newline="") as f:
            reader = csv.reader(f)
            _header = next(reader, None)
            for idx, row in enumerate(reader):
                if len(row) <= col_e or row[col_e].strip() != "N":
                    continue
                full_path = row[col_b].strip() if len(row) > col_b else ""
                drive_letter = row[col_a].strip().upper() if len(row) > col_a else ""
                if not drive_letter:
                    continue

                raw = partition_map.get(drive_letter, "")
                if not raw:
                    continue
                pe_drive = raw.rstrip("\\") + "\\"

                if len(full_path) >= 2 and full_path[1] == ":":
                    rel_path = full_path[3:]
                else:
                    rel_path = full_path

                actual_path = os.path.join(pe_drive, rel_path)

                if os.path.isfile(actual_path):
                    continue

                # 预检查父目录: 若已知无法创建 (与首次下载跳过的一致), 直接跳过
                parent_dir = os.path.dirname(actual_path)
                if parent_dir in _skipped_parents:
                    continue
                if not os.path.isdir(parent_dir):
                    try:
                        os.makedirs(parent_dir, exist_ok=True)
                    except (PermissionError, FileExistsError, OSError):
                        _skipped_parents.add(parent_dir)
                        continue

                try:
                    expected_size = int(row[col_d].strip()) if len(row) > col_d else -1
                except (ValueError, TypeError):
                    expected_size = -1

                missing_entries.append((idx, row, drive_letter, rel_path, actual_path, expected_size))
    except Exception as e:
        log(f"  [X] 读取 CSV 失败，无法重试: {e}")
        return 0

    if _skipped_parents:
        log(f"  跳过 {len(_skipped_parents)} 个无写入权限的目录 (与首次下载一致)")

    if not missing_entries:
        log("没有可重试的缺失文件")
        return 0

    log(f"\n========== 重试下载 {len(missing_entries)} 个缺失文件 ==========")

    # 按分区分组
    by_partition = {}  # {partition: [entries]}
    for entry in missing_entries:
        p = entry[2]
        if p not in by_partition:
            by_partition[p] = []
        by_partition[p].append(entry)

    downloaded = 0
    for partition, entries in by_partition.items():
        partition_downloaded = 0
        log(f"  分区 {partition}: {len(entries)} 个缺失文件")

        base_url = f"https://{server_ip}:{port}"
        pwd = urllib.parse.quote(auth_code)
        paths = [e[3] for e in entries]

        # ---- 批量请求 (最多 3 次连接级重试) ----
        batch_ok = False
        for batch_attempt in range(3):
            try:
                body = json.dumps({
                    "partition": partition,
                    "paths": paths,
                }, ensure_ascii=False).encode("utf-8-sig")

                req = urllib.request.Request(
                    f"{base_url}/batch_get?pwd={pwd}",
                    data=body,
                    headers={
                        "Content-Type": "application/json; charset=utf-8",
                        "Connection": "close",
                    },
                    method="POST",
                )
                resp = urllib.request.urlopen(req, timeout=60, context=_SSL_CTX)

                if resp.status != 200:
                    log(f"  [X] 批量重试 HTTPS {resp.status}")
                    break

                raw = resp.read()

                if len(raw) < 4:
                    log(f"  [X] 批量重试响应过短 ({len(raw)} 字节)")
                    break

                pos = 0
                file_count = _struct.unpack_from(">I", raw, pos)[0]
                pos += 4

                for i in range(file_count):
                    if pos + 4 > len(raw):
                        break
                    path_len = _struct.unpack_from(">I", raw, pos)[0]
                    pos += 4

                    if pos + path_len > len(raw):
                        break
                    rel_path = raw[pos:pos + path_len].decode("utf-8")
                    pos += path_len

                    if pos + 8 > len(raw):
                        break
                    data_len = _struct.unpack_from(">Q", raw, pos)[0]
                    pos += 8

                    if pos + data_len > len(raw):
                        break
                    file_data = raw[pos:pos + data_len]
                    pos += data_len

                    for entry in entries:
                        if entry[3] == rel_path:
                            target_path = entry[4]
                            target_dir = os.path.dirname(target_path)
                            if not os.path.isdir(target_dir):
                                try:
                                    os.makedirs(target_dir, exist_ok=True)
                                except OSError:
                                    log(f"  [X] 无法创建目录: {target_dir}")
                                    break
                            try:
                                with open(target_path, "wb") as fw:
                                    fw.write(file_data)
                                downloaded += 1
                                partition_downloaded += 1
                            except OSError:
                                log(f"  [X] 重试写入失败: {rel_path}")
                            break

                batch_ok = True
                log(f"  分区 {partition}: 批量重试完成，成功 {partition_downloaded} 文件")
                break

            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
                if batch_attempt < 2:
                    delay = 0.5 * (2 ** batch_attempt)
                    log(f"  [重试] 批量请求连接失败 (第{batch_attempt+1}次), {delay:.1f}s 后重试: {e}")
                    _time.sleep(delay)
                else:
                    log(f"  [X] 批量请求 {3} 次均失败: {e}")
            except Exception as e:
                log(f"  [X] 分区 {partition} 批量重试异常: {e}")
                break

        # 批量失败 → 回退逐个下载 (带重试)
        if not batch_ok:
            log(f"  回退: 逐个下载 {partition}")
            for entry in entries:
                _, _, drv, rp, tp, _ = entry
                if _download_one_file_with_retry(server_ip, port, drv, rp, tp, auth_code, log):
                    downloaded += 1

    log(f"重试下载完成: 成功恢复 {downloaded}/{len(missing_entries)} 个文件")
    return downloaded


def _download_one_file_with_retry(
    server_ip: str,
    port: int,
    partition: str,
    rel_path: str,
    target_path: str,
    auth_code: str = "",
    log_callback=None,
    max_retries: int = 3,
) -> bool:
    """从源设备下载单个文件（带连接重试 + 超时兜底）"""
    import time as _time
    for attempt in range(max_retries):
        try:
            encoded_path = urllib.parse.quote(rel_path.replace("\\", "/"), safe="")
            pwd = urllib.parse.quote(auth_code)
            url = f"https://{server_ip}:{port}/get?partition={partition}&path={encoded_path}&pwd={pwd}"
            req = urllib.request.urlopen(url, timeout=30, context=_SSL_CTX)
            target_dir = os.path.dirname(target_path)
            os.makedirs(target_dir, exist_ok=True)
            with open(target_path, "wb") as f:
                while True:
                    chunk = req.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
            return True
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            if attempt < max_retries - 1:
                delay = 0.5 * (2 ** attempt)
                if log_callback:
                    log_callback(f"  [重试] {rel_path} 连接失败 (第{attempt+1}次), {delay:.1f}s 后重试")
                _time.sleep(delay)
            else:
                if log_callback:
                    log_callback(f"  [X] 下载失败(已重试{max_retries}次): {rel_path} - {e}")
                return False
        except Exception:
            return False
    return False


def _download_one_file(
    server_ip: str,
    port: int,
    partition: str,
    rel_path: str,
    target_path: str,
    auth_code: str = "",
) -> bool:
    """从源设备下载单个文件（回退用，兼容旧接口, 无重试）"""
    return _download_one_file_with_retry(
        server_ip, port, partition, rel_path, target_path, auth_code,
        log_callback=None, max_retries=1,
    )


def verify_csv(
    csv_path: str,
    partition_map: dict,
    log_callback=None,
    max_workers: int = DEFAULT_VERIFY_WORKERS,
    stop_check=None,
    progress_callback=None,
) -> tuple:
    """
    校验 CSV 文件 (多线程) —— 纯校验，不做重试下载
    csv_path: FullFilelist_DEF.csv 的完整路径
    partition_map: {"D": "I:", "E": "J:", "F": "K:"}  正常盘符→PE盘符(目标设备)
    max_workers: 校验线程数 (默认 12)
    stop_check: callable, 返回 True 时中止校验
    progress_callback: callable(done, total), 每完成一个文件调用一次
    返回: (通过数, 失败数, 跳过数, 总文件数)
    """
    def log(msg):
        if log_callback:
            log_callback(msg)

    log(f"\n开始校验: {csv_path}")

    # 读取 CSV
    rows = []
    try:
        enc = _detect_csv_encoding(csv_path)
        with open(csv_path, "r", encoding=enc, newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header:
                log("CSV 文件为空或格式错误")
                return 0, 0, 0, 0

            # 查找各列索引 (按列名匹配，回退到位置)
            # CSV 格式: Drive | FullPath | FileName | SizeBytes
            # A 列: Drive
            try:
                col_a = header.index("Drive")
            except ValueError:
                col_a = 0
                log("警告: 未找到 'Drive' 列标题，使用第 1 列(A)")

            # B 列: FullPath
            try:
                col_b = header.index("FullPath")
            except ValueError:
                col_b = 1
                log("警告: 未找到 'FullPath' 列标题，使用第 2 列(B)")

            # D 列: SizeBytes
            try:
                col_d = header.index("SizeBytes")
            except ValueError:
                col_d = 3
                log("警告: 未找到 'SizeBytes' 列标题，使用第 4 列(D)")

            # E 列: VerifyResult (新增校验结果列)
            try:
                col_e = header.index("VerifyResult") if "VerifyResult" in header else 4
            except ValueError:
                col_e = 4
                log("警告: 未找到 'VerifyResult' 列标题，使用第 5 列(E)")

            for row in reader:
                if len(row) > col_b:
                    rows.append(row)

    except Exception as e:
        log(f"读取 CSV 失败: {e}")
        return 0, 0, 0, 0

    total = len(rows)
    log(f"共 {total} 个文件待校验，使用 {max_workers} 线程并行校验")

    passed = 0
    failed = 0
    skipped = 0
    stats_lock = threading.Lock()

    # 结果按原始顺序存储
    result_map = {}

    # 多线程校验
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for idx, row in enumerate(rows):
            future = executor.submit(
                _verify_single_row,
                row, idx, col_a, col_b, col_d, col_e, partition_map,
                log_callback,
            )
            futures[future] = idx

        completed_count = [0]
        for future in as_completed(futures):
            # 检查是否需要中止
            if stop_check and stop_check():
                log("校验收到中止信号，正在停止...")
                for f in futures:
                    f.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
                log(f"校验已中止: 已校验 {completed_count[0]}/{total}")
                return passed, failed, skipped, total

            try:
                idx, updated_row, result, log_msg = future.result()
                result_map[idx] = updated_row

                with stats_lock:
                    if result == "Y":
                        passed += 1
                    elif result == "S":
                        skipped += 1
                    else:
                        failed += 1
                    completed_count[0] += 1

                # 只输出失败的日志 (成功的不刷屏)
                if log_msg and log_callback:
                    log_callback(log_msg)

                # 每完成一个文件回调进度
                current = completed_count[0]
                if progress_callback:
                    progress_callback(current, total)

                # 每 500 个输出一次进度
                if current % 500 == 0:
                    log(f"校验进度: {current}/{total} (通过:{passed}, 失败:{failed}, 跳过:{skipped})")

            except Exception as e:
                with stats_lock:
                    failed += 1
                    completed_count[0] += 1
                log(f"  [N] 线程异常: {e}")

    # 按原始顺序重组结果
    updated_rows = [result_map[i] for i in range(len(rows)) if i in result_map]

    # 写回 CSV
    log(f"\n写入校验结果...")
    try:
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            full_header = list(header)
            while len(full_header) <= col_e:
                full_header.append("")
            if not full_header[col_e]:
                full_header[col_e] = "VerifyResult"
            writer.writerow(full_header)
            writer.writerows(updated_rows)
        log("校验结果已写入")
    except Exception as e:
        log(f"写入 CSV 失败: {e}")

    log(f"\n校验完成: 通过 {passed}, 失败 {failed}, 跳过 {skipped}, 总计 {total}")
    return passed, failed, skipped, total


# ==================== 公开入口 ====================

def run_verification(
    f_drive_pe: str,
    partition_map: dict,
    log_callback=None,
    max_workers: int = DEFAULT_VERIFY_WORKERS,
    stop_check=None,
    progress_callback=None,
    server_ip: str = "",
    gtmc_new_name: str = "",
    auth_code: str = "",
    winpe: bool | None = None,
    csv_path: str = "",
) -> tuple:
    """
    执行完整校验流程
    f_drive_pe: PE 下 F 盘的实际盘符 (用于定位 Appl 目录)
    partition_map: 目标设备的盘符映射
    max_workers: 校验线程数 (默认 12)
    stop_check: callable, 返回 True 时中止校验
    progress_callback: callable(done, total), 每完成一个文件调用一次
    server_ip: 源设备 IP，启用缺失文件重试下载 (可选)
    gtmc_new_name: 源端在 WinPE 下将 GTMC_User_Profiles 重命名后的新目录名 (可选)
    winpe: 是否运行在 WinPE 下。None 时自动检测。仅当 WinPE 且提供了新目录名，
           才将 CSV 中的 GTMC_User_Profiles 路径替换为重命名后的目录 (与源端重命名逻辑一致)。
           非 WinPE 环境源端不重命名，CSV 路径保持原样。
    csv_path: 接收端手动指定的 FullFilelist_DEF.csv 完整路径。非空且文件存在时
              直接采用，跳过 Appl 目录下最新文件夹的自动识别；为空则自动识别。
    流程: 校验 → 收集缺失文件 → 批量重试下载 → 二次校验
    返回: (成功?, 通过数, 失败数, 跳过数, 总文件数)
    """
    def log(msg):
        if log_callback:
            log_callback(msg)

    # 解析运行环境: 未显式指定则自动检测
    if winpe is None:
        winpe = is_running_in_winpe()

    try:
        if csv_path and os.path.isfile(csv_path):
            # 1) 接收端手动指定了 CSV: 直接采用
            log(f"使用手动指定的 CSV 文件: {csv_path}")
        else:
            # 2) 尝试从 systemconfig.ini 自动定位 (与步骤1导出配置联动)
            csv_path = _find_csv_from_ini(f_drive_pe)
            if csv_path:
                log(f"从 systemconfig.ini 定位到 CSV: {csv_path}")
            else:
                # 3) 回退: 搜索最新 Appl 文件夹
                folder = find_latest_appl_folder(f_drive_pe)
                log(f"找到最新 Appl 文件夹: {folder}")
                csv_path = find_csv_file(folder)
                log(f"找到 CSV 文件: {csv_path}")

        # 仅当运行在 WinPE 下 (源端会将 GTMC_User_Profiles 重命名为带日期后缀)
        # 且确实检测到新目录名时，才修改 CSV 文件中的路径；
        # 非 WinPE 环境源端未重命名，CSV 路径保持原样，无需替换
        if winpe and gtmc_new_name:
            log(f"CSV 路径替换 (WinPE 重命名): GTMC_User_Profiles → {gtmc_new_name}")
            _patch_csv_gtmc_paths(csv_path, gtmc_new_name)
        elif not winpe:
            log("非 WinPE 环境: 源端未重命名 GTMC_User_Profiles，CSV 路径保持原样")

        # ---- 第一轮: 纯校验 ----
        passed, failed, skipped, total = verify_csv(
            csv_path, partition_map, log_callback,
            max_workers=max_workers, stop_check=stop_check,
            progress_callback=progress_callback,
        )

        # ---- 第二轮: 如果有缺失文件且源设备可达，重试下载 ----
        if failed > 0 and server_ip:
            if stop_check and stop_check():
                log("重试下载已中止")
                return False, passed, failed, skipped, total

            # 解析 CSV 列 (与 verify_csv 内相同逻辑)
            header = []
            enc = _detect_csv_encoding(csv_path)
            with open(csv_path, "r", encoding=enc, newline="") as f:
                reader = csv.reader(f)
                header = next(reader, None)

            if header:
                try:
                    col_a = header.index("Drive")
                except ValueError:
                    col_a = 0

                try:
                    col_b = header.index("FullPath")
                except ValueError:
                    col_b = 1

                try:
                    col_d = header.index("SizeBytes")
                except ValueError:
                    col_d = 3

                try:
                    col_e = header.index("VerifyResult") if "VerifyResult" in header else 4
                except ValueError:
                    col_e = 4

                # 批量重试下载
                recovered = _retry_missing_files(
                    server_ip, TRANSFER_PORT,
                    partition_map, csv_path,
                    col_a, col_b, col_d, col_e,
                    log_callback=log,
                    auth_code=auth_code,
                )

                if recovered > 0:
                    log(f"\n========== 二次校验 (已重试 {recovered} 个文件) ==========")

                    if stop_check and stop_check():
                        log("二次校验已中止")
                        return False, passed, failed, skipped, total

                    # 只对恢复的文件做二次校验 (全量校验也可以，但避免重复扫描)
                    passed2, failed2, skipped2, total2 = verify_csv(
                        csv_path, partition_map, log_callback,
                        max_workers=max_workers,
                        stop_check=stop_check,
                        progress_callback=progress_callback,
                    )
                    passed, failed, skipped, total = passed2, failed2, skipped2, total2

        # ---- 添加文件类型列 (G 列), 帮助用户判断缺失文件重要性 ----
        if stop_check and stop_check():
            log("文件类型标记已中止")
            return True, passed, failed, skipped, total
        try:
            add_file_type_column(csv_path, log)
        except Exception as e:
            log(f"文件类型标记失败: {e}")

        return True, passed, failed, skipped, total

    except Exception as e:
        log(f"校验失败: {e}")
        return False, 0, 0, 0, 0
