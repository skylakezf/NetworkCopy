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

# 客户端 SSL 上下文: 不校验自签名证书 (与 file_transfer.py 一致)
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# 与 file_transfer.py 保持一致
SKIP_DIRS = {"AppData", "System Volume Information", "WeChat Files"}
SKIP_PREFIXES = ("$",)
TRANSFER_PORT = 9999

# 校验线程数 (文件多时 I/O 是瓶颈，多线程可大幅加速)
DEFAULT_VERIFY_WORKERS = 12


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


def _patch_csv_gtmc_paths(csv_path: str, gtmc_new_name: str) -> None:
    """
    直接修改 CSV 文件内容: 将 GTMC_User_Profiles 替换为 GTMC_User_ProfilesYYMMDD
    修改后 CSV 中的路径与实际文件系统一致，后续校验无需额外处理
    """
    old_name = "GTMC_User_Profiles"
    new_name = gtmc_new_name

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
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
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
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
                    log(f"  [X] 批量重试 HTTP {resp.status}")
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
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
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
            # 接收端手动指定了 CSV: 直接采用，跳过文件夹自动识别
            log(f"使用手动指定的 CSV 文件: {csv_path}")
        else:
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
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
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

        return True, passed, failed, skipped, total

    except Exception as e:
        log(f"校验失败: {e}")
        return False, 0, 0, 0, 0
