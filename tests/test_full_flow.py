# -*- coding: utf-8 -*-
"""端到端主流程测试: 程序能否按期望目标运行。

覆盖"用户真实场景"的完整链路:
  源端 HTTPS 服务 → /list 扫描 → 并发下载(批次+单文件) → 目录/空目录/时间戳还原
  → 边传边校验确认清单 → 按 FullFilelist_DEF.csv 校验

重点验证文件名中的标点(半角/全角逗号、括号、顿号、空格)全程不失真,
且目标端不会多出 0KB 垃圾文件。
"""
import csv
import hashlib
import os
import shutil
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import file_transfer as ft  # noqa: E402
import tls_utils  # noqa: E402
import verifier  # noqa: E402
import _cleanup_zero_byte_dupes as cleanup  # noqa: E402

PORT = 18790

_TOTAL = [0]
_FAILED = [0]


def check(cond, msg):
    _TOTAL[0] += 1
    if cond:
        print(f"  PASS: {msg}")
    else:
        _FAILED[0] += 1
        print(f"  FAIL: {msg}")


def md5_file(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(root):
    """返回 (文件相对路径集合, {相对路径: md5}, 目录相对路径集合)"""
    files, digests, dirs = set(), {}, set()
    for dp, _dn, fn in os.walk(root):
        rel_dir = os.path.relpath(dp, root).replace("\\", "/")
        if rel_dir != ".":
            dirs.add(rel_dir)
        for name in fn:
            rel = (name if rel_dir == "." else f"{rel_dir}/{name}")
            files.add(rel)
            digests[rel] = md5_file(os.path.join(dp, name))
    return files, digests, dirs


# ============================================================
# 源端数据: 覆盖各种标点与结构
# ============================================================

SRC_FILES = {
    "10,无表调测试报价.xls": b"x" * 4096,                       # 半角逗号
    "工事申请资料点检表(自用，无需提交）.xlsx": b"y" * 2048,      # 全角逗号 + 混用括号
    "QSF123_車両塗装,防錆品質基準.pdf": b"z" * 1024,             # 半角逗号 + 日文汉字
    "吹扫、擦拭机器人/导入说明.txt": "擦拭机器人".encode("utf-8"),  # 顿号
    "色差管理/885的3T3色差/报告/3T3TCB 随膜厚变化,色差检讨.xlsx": b"c" * 512,
    "普通文件.txt": "普通内容".encode("utf-8"),
    "带 空格 的 文件.bin": os.urandom(300),
    "空文件.txt": b"",                                          # 0 字节文件
    "深层目录/a/b/c/最深文件.dat": os.urandom(1024),
}
EMPTY_DIRS = ["空文件夹", "深层目录/只有目录/再深一层"]
BIG_FILE = ("大文件/1.5MB.bin", 1572864)  # >1MB 走单文件通道


def build_source(root):
    if os.path.isdir(root):
        shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root, exist_ok=True)
    for rel, data in SRC_FILES.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
    for d in EMPTY_DIRS:
        os.makedirs(os.path.join(root, d), exist_ok=True)
    # 大文件单独生成 (随机内容)
    p = os.path.join(root, BIG_FILE[0])
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(os.urandom(BIG_FILE[1]))


def wait_port(port, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket()
        s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            time.sleep(0.2)
        finally:
            s.close()
    return False


def main():
    print("=" * 66)
    print("端到端主流程测试: 源设备 → 目标设备 完整拷贝 + 校验")
    print("=" * 66)

    ft.TRANSFER_PORT = PORT
    src_d = tempfile.mkdtemp(prefix="flow_src_")
    dst_d = tempfile.mkdtemp(prefix="flow_dst_")
    build_source(src_d)

    src_files, src_md5, src_dirs = snapshot(src_d)
    print(f"  源端: {len(src_files)} 个文件, {len(src_dirs)} 个目录")

    cert_path, key_path = tls_utils.get_or_create_fixed_cert()
    srv_logs = []
    server = ft.FileServer(
        {"D": src_d}, log_callback=srv_logs.append,
        auth_code="abcd", cert_paths=(cert_path, key_path),
    )
    threading.Thread(target=server.start, daemon=True).start()
    check(wait_port(PORT), "F1 源端 HTTPS 服务应就绪")

    try:
        logs = []
        pre_verified_out = [None]
        success, done_files, done_bytes, errors = ft.download_files(
            server_ip="127.0.0.1",
            partition_map={"D": dst_d},
            log_callback=logs.append,
            max_workers=4,
            partition_count=0,
            auth_code="abcd",
            verify_after_transfer=True,
            pre_verified_out=pre_verified_out,
        )

        check(success, f"F2 传输应成功 (errors={errors})")
        check(done_files == len(src_files),
              f"F3 应传输 {len(src_files)} 个文件 (实际 {done_files})")
        check(done_bytes == sum(len(v) for v in SRC_FILES.values()) + BIG_FILE[1],
              "F4 传输字节数应与源端一致")

        # ---- 目录树一致性 (核心: 标点不失真 + 无多余文件) ----
        dst_files, dst_md5, dst_dirs = snapshot(dst_d)
        missing = sorted(src_files - dst_files)
        extra = sorted(dst_files - src_files)
        check(not missing, f"F5 不应有缺失文件 (缺失: {missing[:5]})")
        check(not extra, f"F6 不应有多余文件 (多余: {extra[:5]})")

        diff = [r for r in src_files & dst_files if src_md5[r] != dst_md5[r]]
        check(not diff, f"F7 所有文件内容应一致 (不一致: {diff[:5]})")

        miss_dirs = sorted(src_dirs - dst_dirs)
        check(not miss_dirs, f"F8 空目录也应被创建 (缺失: {miss_dirs[:5]})")

        # ---- 标点专项: 半角/全角都必须在目标端原样存在 ----
        for rel in ("10,无表调测试报价.xls",
                    "工事申请资料点检表(自用，无需提交）.xlsx",
                    "QSF123_車両塗装,防錆品質基準.pdf"):
            check(os.path.isfile(os.path.join(dst_d, rel)),
                  f"F9 标点文件名应原样落盘: {rel}")

        # ---- 不得出现 0KB 标点变体垃圾 ----
        junk = cleanup.scan_root(dst_d)
        check(not junk, f"F10 目标端不应有 0KB 标点变体垃圾 (发现 {len(junk)} 个)")

        # ---- 边传边校验确认清单 ----
        check(bool(pre_verified_out[0]) and os.path.isfile(pre_verified_out[0]),
              "F11 边传边校正确认清单应生成")
        if pre_verified_out[0]:
            with open(pre_verified_out[0], "r", encoding="utf-8") as f:
                pre_content = f.read()
            check("10,无表调测试报价.xls" in pre_content,
                  "F12 确认清单应包含半角逗号文件")
            check("工事申请资料点检表(自用，无需提交）.xlsx" in pre_content,
                  "F13 确认清单应包含全角逗号文件")

        # ---- 按 FullFilelist_DEF.csv 做正式校验 ----
        csv_path = os.path.join(dst_d, "_verify_input.csv")
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Drive", "FullPath", "FileName", "SizeBytes"])
            for rel in sorted(src_files):
                p = os.path.join(src_d, rel)
                w.writerow(["D", f"D:\\{rel}", os.path.basename(rel),
                            os.path.getsize(p)])
        passed, failed, skipped, total = verifier.verify_csv(
            csv_path, {"D": dst_d + "\\"}, log_callback=logs.append, max_workers=4)
        check((passed, failed) == (len(src_files), 0),
              f"F14 校验应全部通过 (passed={passed} failed={failed} total={total})")
    finally:
        try:
            server.stop()
        except Exception:
            pass
        time.sleep(0.3)

    print()
    print(f"总计: {_TOTAL[0]}  通过: {_TOTAL[0] - _FAILED[0]}  失败: {_FAILED[0]}")
    if _FAILED[0]:
        print("存在失败用例!")
        return 1
    print("全部通过!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
