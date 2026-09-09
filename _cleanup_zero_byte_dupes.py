# -*- coding: utf-8 -*-
"""清理校验重试下载产生的 0KB 垃圾文件。

背景 (2026-09-09):
  旧版 verifier 会把 CSV 里的全角逗号「，」无条件替换成半角「,」，
  导致文件名含全角逗号的文件被误判缺失 → 按错误路径重试下载 →
  服务端读不到返回空数据 → 客户端照写 → 目标盘留下 0KB、
  且标点与真实文件不一致的垃圾文件。

判定规则 (缺一不可):
  1) 该文件大小为 0 字节
  2) 同一目录下存在"标点变体"文件名的文件, 且该文件大小 > 0
     变体 = ASCII 标点 ↔ 全角标点 互换 (如 "a,x.txt" ↔ "a，x.txt")

安全:
  - 默认 dry-run, 只列出不删除; 加 --delete 才真正删除
  - 每次删除前都会重新校验上述两个条件
  - 跳过系统/回收站/程序目录

用法:
  python _cleanup_zero_byte_dupes.py                      # 扫描 D:\\ E:\\ F:\\ (仅列出)
  python _cleanup_zero_byte_dupes.py --roots E:\\1L       # 指定目录
  python _cleanup_zero_byte_dupes.py --delete             # 真正删除
  python _cleanup_zero_byte_dupes.py --delete --log out.csv
"""
import argparse
import csv
import os
import sys

# ASCII 标点 → 全角标点 (双向映射用于生成变体)
ASCII_TO_FULL = {
    ",": "，",
    "!": "！",
    "?": "？",
    "(": "（",
    ")": "）",
    ":": "：",
    ";": "；",
    "~": "～",
    "-": "－",
    "_": "＿",
    ".": "．",   # 仅作为变体候选, 实际极少见
}
FULL_TO_ASCII = {v: k for k, v in ASCII_TO_FULL.items()}

SKIP_DIR_NAMES = {
    "$recycle.bin", "system volume information", "$windows.~ws",
    "$windows.~bt", "windows", "program files", "program files (x86)",
    "programdata", "appdata", "recovery", "perflogs", "msocache",
}


def _variants(name):
    """生成文件名的标点变体 (去重, 不含自身)。"""
    out = []

    def add(v):
        if v != name and v not in out:
            out.append(v)

    add("".join(ASCII_TO_FULL.get(ch, ch) for ch in name))
    add("".join(FULL_TO_ASCII.get(ch, ch) for ch in name))
    if "," in name:
        add(name.replace(",", "，"))
    if "，" in name:
        add(name.replace("，", ","))
    return out


def _extended(path):
    """为长路径 (>260) 添加 \\\\?\\ 前缀。"""
    if path.startswith("\\\\?\\"):
        return path
    return "\\\\?\\" + os.path.abspath(path)


def scan_root(root):
    """扫描一个根目录, 返回垃圾文件列表 [(显示路径, 真实路径, 对应真实文件)]。"""
    junk = []
    real_root = _extended(root)
    if not os.path.isdir(real_root):
        print(f"[跳过] 目录不存在或不可访问: {root}")
        return junk

    for dirpath, dirnames, filenames in os.walk(real_root):
        # 过滤系统目录
        dirnames[:] = [
            d for d in dirnames
            if d.lower() not in SKIP_DIR_NAMES and not d.startswith("$")
        ]

        try:
            entries = {}
            with os.scandir(dirpath) as it:
                for e in it:
                    try:
                        if e.is_file():
                            entries[e.name] = e.stat().st_size
                    except OSError:
                        continue
        except OSError:
            continue

        if not entries:
            continue

        for name, size in entries.items():
            if size != 0:
                continue
            for var in _variants(name):
                vsize = entries.get(var)
                if vsize and vsize > 0:
                    full = os.path.join(dirpath, name)
                    junk.append((full.replace("\\\\?\\", ""), full, var, vsize))
                    break

    return junk


def main():
    ap = argparse.ArgumentParser(
        description="清理校验重试下载产生的 0KB 垃圾文件 (默认 dry-run)")
    ap.add_argument("--roots", nargs="*", default=None,
                    help="要扫描的根目录, 默认 D:\\ E:\\ F:\\")
    ap.add_argument("--delete", action="store_true",
                    help="真正删除 (默认只列出)")
    ap.add_argument("--log", default="", help="把结果写入 CSV 日志")
    args = ap.parse_args()

    roots = args.roots or [d + "\\" for d in ("D:", "E:", "F:")
                           if os.path.isdir(d + "\\")]
    if not roots:
        print("未找到任何可扫描的根目录, 请用 --roots 指定")
        return 1

    print("=" * 72)
    print("0KB 垃圾文件清理" + ("  [模式: 删除]" if args.delete else "  [模式: 仅列出]"))
    print("扫描目录: " + ", ".join(roots))
    print("=" * 72)

    all_junk = []
    for root in roots:
        print(f"\n正在扫描 {root} ...")
        found = scan_root(root)
        print(f"  发现 {len(found)} 个 0KB 垃圾文件")
        all_junk.extend(found)

    print("\n" + "-" * 72)
    if not all_junk:
        print("未发现符合条件的 0KB 垃圾文件。")
        return 0

    deleted = 0
    failed = 0
    rows = []
    for display, real, twin, twin_size in all_junk:
        # 二次确认: 仍为 0 字节 + 变体仍存在且非空
        try:
            ok = os.path.getsize(real) == 0
        except OSError:
            ok = False
        twin_path = os.path.join(os.path.dirname(real), twin)
        try:
            ok = ok and os.path.getsize(twin_path) > 0
        except OSError:
            ok = False

        if not ok:
            rows.append((display, "跳过(条件已不满足)", twin, twin_size))
            continue

        if args.delete:
            try:
                os.remove(real)
                deleted += 1
                rows.append((display, "已删除", twin, twin_size))
                print(f"  [删除] {display}")
                print(f"          对应真实文件: {twin} ({twin_size} 字节)")
            except OSError as e:
                failed += 1
                rows.append((display, f"删除失败: {e}", twin, twin_size))
                print(f"  [失败] {display} - {e}")
        else:
            rows.append((display, "待删除", twin, twin_size))
            print(f"  [待删] {display}")
            print(f"          对应真实文件: {twin} ({twin_size} 字节)")

    print("-" * 72)
    print(f"合计: {len(all_junk)} 个"
          + (f", 已删除 {deleted} 个, 失败 {failed} 个" if args.delete else
             " (dry-run, 未删除; 确认无误后加 --delete 执行)"))

    if args.log:
        try:
            with open(args.log, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["0KB文件路径", "处理结果", "对应真实文件名", "真实文件大小"])
                w.writerows(rows)
            print(f"日志已写入: {args.log}")
        except OSError as e:
            print(f"[警告] 日志写入失败: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
