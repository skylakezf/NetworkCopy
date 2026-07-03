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
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# 与 file_transfer.py 保持一致的跳过规则
SKIP_DIRS = {"AppData", "System Volume Information","WeChat Files"}
SKIP_PREFIXES = ("$",)  # $RECYCLE.BIN 等

# 校验线程数 (文件多时 I/O 是瓶颈，多线程可大幅加速)
DEFAULT_VERIFY_WORKERS = 12


def _parse_date_from_folder(folder_name: str):
    """
    解析 YYYY/MM/DD 或 YYYY-MM-DD 格式的日期
    返回 datetime 对象或 None
    """
    # 尝试 YYYY/MM/DD 格式（单层目录名如 2024/01/15 不能直接作为文件夹名，更可能是 2024-01-15）
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(folder_name, fmt)
        except ValueError:
            continue

    # 尝试正则
    m = re.match(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", folder_name)
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
    校验单行 (在线程池中执行)
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
            pe_drive = partition_map[drive_letter].rstrip("\\")
        elif len(full_path) >= 2 and full_path[1] == ":":
            # 回退: 从 B 列路径解析盘符
            src_drive = full_path[0].upper()
            pe_drive = partition_map.get(src_drive, "").rstrip("\\")
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


def verify_csv(
    csv_path: str,
    partition_map: dict,
    log_callback=None,
    max_workers: int = DEFAULT_VERIFY_WORKERS,
    stop_check=None,
    progress_callback=None,
) -> tuple:
    """
    校验 CSV 文件 (多线程)
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
                row, idx, col_a, col_b, col_d, col_e, partition_map, log_callback,
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
) -> tuple:
    """
    执行完整校验流程
    f_drive_pe: PE 下 F 盘的实际盘符 (用于定位 Appl 目录)
    partition_map: 目标设备的盘符映射
    max_workers: 校验线程数 (默认 12)
    stop_check: callable, 返回 True 时中止校验
    progress_callback: callable(done, total), 每完成一个文件调用一次
    返回: (成功?, 通过数, 失败数, 跳过数, 总文件数)
    """
    try:
        folder = find_latest_appl_folder(f_drive_pe)
        if log_callback:
            log_callback(f"找到最新 Appl 文件夹: {folder}")

        csv_path = find_csv_file(folder)
        if log_callback:
            log_callback(f"找到 CSV 文件: {csv_path}")

        passed, failed, skipped, total = verify_csv(
            csv_path, partition_map, log_callback,
            max_workers=max_workers, stop_check=stop_check,
            progress_callback=progress_callback,
        )
        return True, passed, failed, skipped, total

    except Exception as e:
        if log_callback:
            log_callback(f"校验失败: {e}")
        return False, 0, 0, 0, 0
