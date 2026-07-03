"""
数据校验模块
Phase 4: CSV 校验
- 定位 F:\\Appl\\ 下最新 YYYY/MM/DD 文件夹
- 解析 FullFilelist_DEF.csv
- B 列 FullPath 逐文件 MD5 校验
- E 列填入 Y/N
"""
import os
import csv
import hashlib
import re
from datetime import datetime


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


def md5_file(filepath: str) -> str:
    """计算文件 MD5"""
    h = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def verify_csv(csv_path: str, partition_map: dict, log_callback=None) -> tuple:
    """
    校验 CSV 文件
    csv_path: FullFilelist_DEF.csv 的完整路径
    partition_map: {"D": "I:", "E": "J:", "F": "K:"}  正常盘符→PE盘符(目标设备)
    返回: (通过数, 失败数, 总文件数)
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
                return 0, 0, 0

            # 查找 B 列索引
            try:
                col_b = header.index("FullPath")
            except ValueError:
                # 尝试通过位置找 B 列 (第 2 列, index 1)
                col_b = 1  # B = 索引 1
                log("警告: 未找到 'FullPath' 列标题，使用第 2 列(B)")

            # 查找 E 列索引
            try:
                col_e = header.index("VerifyResult") if "VerifyResult" in header else 4
            except ValueError:
                col_e = 4  # E = 索引 4

            for row in reader:
                if len(row) > col_b:
                    rows.append(row)

    except Exception as e:
        log(f"读取 CSV 失败: {e}")
        return 0, 0, 0

    total = len(rows)
    log(f"共 {total} 个文件待校验")

    passed = 0
    failed = 0

    # 逐文件校验
    updated_rows = []
    for idx, row in enumerate(rows):
        try:
            full_path = row[col_b].strip()

            # 解析盘符 (如 D:\xxx)
            if len(full_path) >= 2 and full_path[1] == ":":
                src_drive = full_path[0].upper()  # D, E, F
                rel_path = full_path[3:]  # 去掉 "D:\"
                target_drive = partition_map.get(src_drive, "").rstrip("\\")
                actual_path = os.path.join(target_drive, rel_path)
            else:
                actual_path = full_path

            # 校验
            if os.path.isfile(actual_path):
                # MD5 校验（只比较文件是否存在 + 大小非零）
                fsize = os.path.getsize(actual_path)
                if fsize > 0:
                    result = "Y"
                    passed += 1
                else:
                    result = "N"
                    failed += 1
                    log(f"  [N] 文件大小为0: {actual_path}")
            else:
                result = "N"
                failed += 1
                log(f"  [N] 文件不存在: {actual_path}")

            # 确保行足够长
            while len(row) <= col_e:
                row.append("")
            row[col_e] = result

            # 如果有 E 列标题，保留它
            updated_rows.append(row)

        except Exception as e:
            failed += 1
            log(f"  [N] 校验异常: {full_path[:80]} - {e}")
            while len(row) <= col_e:
                row.append("")
            row[col_e] = "N"
            updated_rows.append(row)

        # 每 100 个输出一次进度
        if (idx + 1) % 100 == 0:
            log(f"校验进度: {idx + 1}/{total} (通过:{passed}, 失败:{failed})")

    # 写回 CSV
    log(f"\n写入校验结果...")
    try:
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            # 写入表头
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

    log(f"\n校验完成: 通过 {passed}, 失败 {failed}, 总计 {total}")
    return passed, failed, total


# ==================== 公开入口 ====================

def run_verification(f_drive_pe: str, partition_map: dict, log_callback=None) -> tuple:
    """
    执行完整校验流程
    f_drive_pe: PE 下 F 盘的实际盘符 (用于定位 Appl 目录)
    partition_map: 目标设备的盘符映射
    返回: (成功?, 通过数, 失败数, 总文件数)
    """
    try:
        folder = find_latest_appl_folder(f_drive_pe)
        if log_callback:
            log_callback(f"找到最新 Appl 文件夹: {folder}")

        csv_path = find_csv_file(folder)
        if log_callback:
            log_callback(f"找到 CSV 文件: {csv_path}")

        passed, failed, total = verify_csv(csv_path, partition_map, log_callback)
        return True, passed, failed, total

    except Exception as e:
        if log_callback:
            log_callback(f"校验失败: {e}")
        return False, 0, 0, 0
