# ============================================================
# calc_allocation_migration.py
# 计算从 512Byte 分配单元迁移到 4096Byte 分配单元后的磁盘占用
# 目标分区: D:, E:, F:
# 跳过目录/文件规则与 file_transfer.py 保持一致
# 输出目录: F:\Appl\<YYYY-MM-DD> (与 out.cmd 一致)
#
# 微软 KB140365 官方公式:
#   磁盘占用 = ceil(FileSize / ClusterSize) × ClusterSize
# 本脚本对同一批文件分别按 512B / 4096B 对齐计算，确保对比公平。
# ============================================================

import os
import sys
import math
import csv
import time
import shutil
import argparse
import ctypes
from collections import deque
from datetime import datetime
from typing import Tuple, List, Optional

# ============================================================
# 跳过规则 (与 file_transfer.py 保持一致)
# ============================================================
SKIP_DIRS = {
    "AppData", "System Volume Information", "WeChat Files",
    "Application Data",  # NTFS 符号链接/交接点, 递归会导致死循环
}
SKIP_PREFIXES = ("$",)  # $RECYCLE.BIN 等
SKIP_FILE_SUFFIXES = (".tmp",)  # 跳过临时文件
SKIP_FILE_PREFIXES = ("~$",)  # 跳过 Office 自动保存文件
SKIP_FILES = {"pagefile.sys", "hiberfil.sys", "swapfile.sys", "DumpStack.log.tmp"}

# ============================================================
# 常量
# ============================================================
OLD_CLUSTER = 512
NEW_CLUSTER = 4096
SMALL_FILE_THRESHOLD = 4096  # 4KB
DEFAULT_DRIVES = ["D", "E", "F"]
THRESHOLDS = {"D": 180, "E": 180, "F": 29}  # GB 阈值
PROGRESS_INTERVAL = 50000  # 每 N 个文件报告一次进度


def should_skip_dir(dirname: str) -> bool:
    """判断目录是否应跳过（按名称匹配）。"""
    if dirname in SKIP_DIRS:
        return True
    if dirname.startswith(SKIP_PREFIXES):
        return True
    return False


def should_skip_file(filename: str) -> bool:
    """判断文件是否应跳过。"""
    if filename in SKIP_FILES:
        return True
    if filename.startswith(SKIP_FILE_PREFIXES):
        return True
    if filename.endswith(SKIP_FILE_SUFFIXES):
        return True
    return False


def is_junction(entry: os.DirEntry) -> bool:
    """检查目录是否为 NTFS 交接点/重解析点（避免死循环）。"""
    try:
        # FILE_ATTRIBUTE_REPARSE_POINT = 0x400
        return bool(entry.stat(follow_symlinks=False).st_file_attributes & 0x400)
    except OSError:
        return False


def aligned_size(file_size: int, cluster_size: int) -> int:
    """ceil(FileSize / ClusterSize) × ClusterSize"""
    return math.ceil(file_size / cluster_size) * cluster_size


def get_drive_used(drive_letter: str) -> int:
    """获取分区已用空间（字节）。"""
    drive_path = f"{drive_letter}:\\"
    try:
        usage = shutil.disk_usage(drive_path)
        return usage.total - usage.free
    except OSError:
        return 0


def format_bytes_gb(b: int) -> float:
    """字节转 GB，保留 2 位小数。"""
    return round(b / (1024 ** 3), 2)


def escape_csv(val: str) -> str:
    """CSV 中逗号替换为中文逗号。"""
    return val.replace(",", "，")


class DriveScanResult:
    """单分区扫描结果。"""
    def __init__(self):
        self.drive = ""
        self.file_count = 0
        self.small_count = 0
        self.error_count = 0
        self.drive_used_bytes = 0
        self.old_aligned_bytes = 0
        self.new_aligned_bytes = 0
        self.elapsed_sec = 0.0

    @property
    def diff_bytes(self) -> int:
        return self.new_aligned_bytes - self.old_aligned_bytes

    @property
    def drive_used_gb(self) -> float:
        return format_bytes_gb(self.drive_used_bytes)

    @property
    def old_aligned_gb(self) -> float:
        return format_bytes_gb(self.old_aligned_bytes)

    @property
    def new_aligned_gb(self) -> float:
        return format_bytes_gb(self.new_aligned_bytes)

    @property
    def diff_gb(self) -> float:
        return format_bytes_gb(self.diff_bytes)


def scan_drive(
    drive: str,
    full_writer: Optional[csv.writer] = None,
    small_writer: Optional[csv.writer] = None,
    old_cluster: int = OLD_CLUSTER,
    new_cluster: int = NEW_CLUSTER,
    small_threshold: int = SMALL_FILE_THRESHOLD,
) -> DriveScanResult:
    """
    扫描单个分区，统计文件数、计算 512B/4K 对齐后占用。
    使用 BFS 队列 + os.scandir()（惰性枚举，高性能）。
    """
    result = DriveScanResult()
    result.drive = drive
    drive_path = f"{drive}:\\"

    if not os.path.exists(drive_path):
        print(f"  [{drive}:] 分区不存在，跳过。")
        return result

    print(f"正在扫描 {drive_path} ...")

    # 获取分区实际已用空间
    result.drive_used_bytes = get_drive_used(drive)

    start_time = time.monotonic()
    dir_queue = deque([drive_path])

    while dir_queue:
        current_dir = dir_queue.popleft()

        try:
            entries = list(os.scandir(current_dir))
        except OSError:
            result.error_count += 1
            continue

        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    # 目录：排除跳过目录 + 交接点
                    name = entry.name
                    if not should_skip_dir(name) and not is_junction(entry):
                        dir_queue.append(entry.path)
                else:
                    # 文件
                    name = entry.name
                    if should_skip_file(name):
                        continue

                    try:
                        size = entry.stat().st_size
                    except OSError:
                        result.error_count += 1
                        continue

                    # CSV 输出（逗号替换为中文逗号）
                    csv_path = escape_csv(entry.path)
                    csv_name = escape_csv(name)

                    if full_writer is not None:
                        full_writer.writerow([drive, csv_path, csv_name, size])

                    result.file_count += 1

                    # 按两种簇大小对齐计算
                    result.old_aligned_bytes += aligned_size(size, old_cluster)
                    result.new_aligned_bytes += aligned_size(size, new_cluster)

                    # 小文件统计
                    if size < small_threshold:
                        result.small_count += 1
                        if small_writer is not None:
                            small_writer.writerow([drive, csv_path, csv_name, size])

                    # 进度报告
                    if result.file_count % PROGRESS_INTERVAL == 0:
                        elapsed = time.monotonic() - start_time
                        print(
                            f"  已处理 {result.file_count} 文件, "
                            f"耗时 {elapsed:.1f}s ..."
                        )

            except OSError:
                result.error_count += 1

    result.elapsed_sec = round(time.monotonic() - start_time, 1)

    # 打印分区结果
    print(
        f"  文件总数: {result.file_count} | "
        f"<4KB: {result.small_count} | "
        f"错误: {result.error_count} | "
        f"耗时: {result.elapsed_sec}s"
    )
    print(
        f"  当前文件占用(512B对齐): {result.old_aligned_gb}GB -> "
        f"迁移后(4K对齐): {result.new_aligned_gb}GB "
        f"(+{result.diff_gb}GB)"
    )
    print(
        f"  磁盘实际已用(disk_usage): {result.drive_used_gb}GB "
        f"(含NTFS元数据)"
    )
    print()

    return result


def check_thresholds(results: List[DriveScanResult]) -> Optional[List[str]]:
    """检查是否超过分区空间阈值。"""
    warnings = []
    for r in results:
        if r.drive in THRESHOLDS and r.new_aligned_gb > THRESHOLDS[r.drive]:
            warnings.append(
                f"{r.drive}: 预估 {r.new_aligned_gb}GB > 阈值 {THRESHOLDS[r.drive]}GB"
            )
    if warnings:
        drive_names = "、".join(w[0] for w in warnings)
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                f"磁盘 {drive_names} 超出了新硬盘的最大空间利用值，需要注意磁盘分区空间大小。",
                "磁盘空间警告",
                0x30,  # MB_ICONWARNING
            )
        except Exception:
            print(f"  [警告] 磁盘 {drive_names} 超出了新硬盘的最大空间利用值！")
    return warnings if warnings else None


def print_summary(results: List[DriveScanResult]) -> None:
    """打印汇总表格。"""
    print("=" * 60)
    print(" 汇总结果 (文件内容占用对比，同一批文件，两种对齐方式)")
    print("=" * 60)
    print()
    print(f"{'分区':<6} {'文件总数':>10} {'<4KB数':>12} {'512B对齐':>10} {'4096B对齐':>10} {'增加':>10}")
    print(f"{'----':<6} {'--------':>10} {'------':>12} {'-------':>10} {'--------':>10} {'----':>10}")

    orig_parts = []
    new_parts = []
    for r in results:
        print(
            f"{r.drive}:{'':<4} "
            f"{r.file_count:>10,} "
            f"{r.small_count:>12,} "
            f"{r.old_aligned_gb:>8}GB "
            f"{r.new_aligned_gb:>8}GB "
            f"{r.diff_gb:>8}GB"
        )
        orig_parts.append(f"{r.drive}:{r.old_aligned_gb}GB")
        new_parts.append(f"{r.drive}:{r.new_aligned_gb}GB")

    print()
    print(f"原占用磁盘空间(512B对齐):   {' '.join(orig_parts)}")
    print(f"预估迁移至新磁盘占用空间(4K对齐): {' '.join(new_parts)}")

    total_old = format_bytes_gb(sum(r.old_aligned_bytes for r in results))
    total_new = format_bytes_gb(sum(r.new_aligned_bytes for r in results))
    total_diff = format_bytes_gb(sum(r.diff_bytes for r in results))
    total_used = format_bytes_gb(sum(r.drive_used_bytes for r in results))

    print()
    print(
        f"总计: 512B对齐 {total_old}GB -> 4K对齐 {total_new}GB "
        f"(增加 {total_diff}GB)"
    )
    print(
        f"      磁盘管理器中显示已用: {total_used}GB "
        f"(含NTFS元数据、权限拒绝的目录等)"
    )


def run_estimate(
    output_dir: str,
    drives: List[str] = None,
    old_cluster: int = OLD_CLUSTER,
    new_cluster: int = NEW_CLUSTER,
    small_threshold: int = SMALL_FILE_THRESHOLD,
    no_csv: bool = False,
    log_callback=None,
) -> Tuple[bool, List[DriveScanResult]]:
    """供外部直接调用的核心入口。
    返回 (success, results)。

    - 不依赖 argparse / sys.exit，可在 frozen exe 中安全导入。
    - log_callback 为可选的回调函数 log_callback(msg)，用于写入日志；
      若为 None 则直接 print。
    """
    if drives is None:
        drives = DEFAULT_DRIVES

    if log_callback is None:
        log_callback = print

    os.makedirs(output_dir, exist_ok=True)

    small_csv_path = os.path.join(output_dir, "SmallFiles_Under4KB.csv")
    full_csv_path = os.path.join(output_dir, "FullFilelist_DEF.csv")

    log_callback("=" * 60)
    log_callback(f" 磁盘分配单元迁移空间估算 ({old_cluster}B -> {new_cluster}B)")
    log_callback(f" 输出目录: {output_dir}")
    log_callback(f" ceil(FileSize / ClusterSize) x ClusterSize ")
    log_callback("=" * 60)
    log_callback("")

    # 打开 CSV 写入器
    small_file = None
    full_file = None
    small_writer = None
    full_writer = None

    if not no_csv:
        try:
            # 清理旧文件
            for p in (small_csv_path, full_csv_path):
                try:
                    os.remove(p)
                except FileNotFoundError:
                    pass
            time.sleep(0.2)

            small_file = open(
                small_csv_path, "w", newline="", encoding="utf-8-sig"
            )
            small_writer = csv.writer(small_file)
            small_writer.writerow(["Drive", "FullPath", "FileName", "SizeBytes"])

            full_file = open(
                full_csv_path, "w", newline="", encoding="utf-8-sig"
            )
            full_writer = csv.writer(full_file)
            full_writer.writerow(["Drive", "FullPath", "FileName", "SizeBytes"])
        except OSError as e:
            log_callback(f"无法创建 CSV 文件: {e}")

    # 扫描各分区
    results = []
    total_small = 0
    for drive in drives:
        drive = drive.rstrip(":").upper()
        result = scan_drive(
            drive, full_writer, small_writer,
            old_cluster=old_cluster,
            new_cluster=new_cluster,
            small_threshold=small_threshold,
        )
        results.append(result)
        total_small += result.small_count

    # 关闭 CSV 文件
    for f in (small_file, full_file):
        if f:
            try:
                f.close()
            except OSError:
                pass

    # 汇总输出
    print_summary(results)

    # 阈值检测
    check_thresholds(results)

    # 输出文件路径
    log_callback("")
    log_callback("输出文件:")
    log_callback(f"  全量文件清单:    {full_csv_path}")
    log_callback(f"  <4KB 小文件清单:  {small_csv_path} ({total_small} 个)")
    log_callback("")
    log_callback("=" * 60)
    log_callback(" 计算完成")
    log_callback("=" * 60)

    return True, results


def main():
    """命令行入口（独立运行）。"""
    parser = argparse.ArgumentParser(
        description="磁盘分配单元迁移空间估算 (512B -> 4096B)"
    )
    parser.add_argument(
        "-d", "--drives",
        nargs="+",
        default=DEFAULT_DRIVES,
        help=f"要扫描的分区盘符 (默认: {' '.join(DEFAULT_DRIVES)})",
    )
    parser.add_argument(
        "-o", "--output-dir",
        default=None,
        help="输出目录 (默认: F:\\Appl\\<YYYY-MM-DD>)",
    )
    parser.add_argument(
        "--old-cluster",
        type=int,
        default=OLD_CLUSTER,
        help=f"旧分配单元大小(字节) (默认: {OLD_CLUSTER})",
    )
    parser.add_argument(
        "--new-cluster",
        type=int,
        default=NEW_CLUSTER,
        help=f"新分配单元大小(字节) (默认: {NEW_CLUSTER})",
    )
    parser.add_argument(
        "--small-threshold",
        type=int,
        default=SMALL_FILE_THRESHOLD,
        help=f"小文件阈值(字节) (默认: {SMALL_FILE_THRESHOLD})",
    )
    parser.add_argument(
        "--no-csv",
        action="store_true",
        help="不输出 CSV 文件",
    )
    args = parser.parse_args()

    if args.output_dir:
        output_dir = args.output_dir
    else:
        date_folder = datetime.now().strftime("%Y-%m-%d")
        appl_root = os.environ.get("APPL_ROOT", r"F:\Appl")
        output_dir = os.path.join(appl_root, date_folder)

    run_estimate(
        output_dir=output_dir,
        drives=args.drives,
        old_cluster=args.old_cluster,
        new_cluster=args.new_cluster,
        small_threshold=args.small_threshold,
        no_csv=args.no_csv,
    )


if __name__ == "__main__":
    main()
