# -*- coding: utf-8 -*-
"""校验相关端到端测试 (合并自原 _test_preok / _test_filelist_e2e / _test_download_verify)

三个小节:
  A. 边传边校验增量确认 (pre_verified.txt 跳过磁盘校验)
  B. 全盘清单 /filelist 端点 E2E
  C. 下载 + 边传边校验 E2E (含断点续传)
"""
import csv
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import file_transfer as ft  # noqa: E402
import tls_utils  # noqa: E402
import verifier  # noqa: E402

_TOTAL = [0]
_FAILED = [0]


def check(cond, msg):
    _TOTAL[0] += 1
    if cond:
        print(f"  PASS: {msg}")
    else:
        _FAILED[0] += 1
        print(f"  FAIL: {msg}")


# ============================================================
# A. 边传边校验增量确认
# ============================================================

def suite_preok():
    tmp = tempfile.mkdtemp(prefix="preok_test_")
    # a.txt 存在且大小正确(3字节); b.txt 存在但大小不符(3 vs 5); c.txt 不存在
    with open(os.path.join(tmp, "a.txt"), "wb") as f:
        f.write(b"abc")
    with open(os.path.join(tmp, "b.txt"), "wb") as f:
        f.write(b"abc")

    csv_path = os.path.join(tmp, "FullFilelist_DEF.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Drive", "FullPath", "FileName", "SizeBytes"])
        w.writerow(["D", "D:\\a.txt", "a.txt", "3"])
        w.writerow(["D", "D:\\b.txt", "b.txt", "5"])
        w.writerow(["D", "D:\\c.txt", "c.txt", "3"])

    partition_map = {"D": tmp + "\\"}
    logs = []

    # 场景1: pre_ok 含 a.txt → a 直接标 Y, 其余正常校验
    p1, f1, _s1, t1 = verifier.verify_csv(
        csv_path, partition_map, log_callback=logs.append,
        max_workers=2, pre_ok_paths={"D:\\a.txt"},
    )
    check((p1, f1, t1) == (1, 2, 3),
          f"A1 pre_ok 命中应跳过磁盘校验 (passed=1 failed=2 total=3, 实际 {p1}/{f1}/{t1})")

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    hdr = rows[0]
    e_idx = hdr.index("VerifyResult") if "VerifyResult" in hdr else None
    check(e_idx is not None, "A2 CSV 应新增 VerifyResult 列")
    if e_idx is None:
        return
    by_path = {r[1]: r for r in rows[1:]}
    check(by_path["D:\\a.txt"][e_idx] == "Y", "A3 a.txt 应被 pre_ok 标记为 Y")
    check(by_path["D:\\b.txt"][e_idx] == "N", "A4 b.txt 大小不符应标记 N")
    check(by_path["D:\\c.txt"][e_idx] == "N", "A5 c.txt 不存在应标记 N")

    # 场景2: 不传 pre_ok → 全量校验 (回归)
    p2, f2, _s2, t2 = verifier.verify_csv(
        csv_path, partition_map, log_callback=logs.append, max_workers=2,
    )
    check((p2, f2, t2) == (1, 2, 3),
          f"A6 不传 pre_ok 时全量校验 (实际 {p2}/{f2}/{t2})")

    # 场景3: 二次校验 (target_indices) 不应用 pre_ok
    _p3, f3, _s3, _t3 = verifier.verify_csv(
        csv_path, partition_map, log_callback=logs.append, max_workers=2,
        target_indices={1}, pre_ok_paths={"D:\\a.txt", "D:\\b.txt", "D:\\c.txt"},
    )
    check(f3 == 1, f"A7 target_indices 模式应忽略 pre_ok (实际 failed={f3})")


# ============================================================
# B. 全盘清单 /filelist 端点 E2E
# ============================================================

def suite_filelist():
    ft.TRANSFER_PORT = 18777

    src_f = tempfile.mkdtemp(prefix="src_f_")
    appl = os.path.join(src_f, "Appl", "2026-08-25")
    os.makedirs(appl)
    with open(os.path.join(appl, "FullFilelist_DEF.csv"), "w", encoding="utf-8-sig") as f:
        f.write('Drive,FullPath,FileName,SizeBytes\r\nD,"D:\\a.txt",a.txt,3\r\n')

    cert_path, key_path = tls_utils.get_or_create_fixed_cert()
    srv_logs = []
    server = ft.FileServer(
        {"D": "D:", "E": "E:", "F": src_f},
        log_callback=srv_logs.append, auth_code="abcd",
        cert_paths=(cert_path, key_path),
    )
    threading.Thread(target=server.start, daemon=True).start()
    time.sleep(1.2)

    try:
        dst_f = tempfile.mkdtemp(prefix="dst_f_")
        logs = []
        csv_path = ft._download_filelist(
            f"https://127.0.0.1:{ft.TRANSFER_PORT}", "abcd", {"F": dst_f}, logs.append)
        check(bool(csv_path) and os.path.isfile(csv_path), "B1 清单应被下载保存")
        if not csv_path:
            return
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            content = f.read()
        check("a.txt" in content, "B2 清单内容应包含 a.txt")
        check(os.path.basename(csv_path) == "FullFilelist_DEF.csv",
              "B3 保存文件名应为 FullFilelist_DEF.csv")
        check(os.path.basename(os.path.dirname(csv_path)) == "2026-08-25",
              "B4 应保存到源端日期目录")

        # 错误鉴权应失败
        bad = ft._download_filelist(
            f"https://127.0.0.1:{ft.TRANSFER_PORT}", "wrong", {"F": dst_f},
            lambda m: None)
        check(bad == "", "B5 错误验证码应返回空字符串")

        # 源端无 Appl 目录应优雅返回空
        server.stop()
        time.sleep(0.5)
        src_f2 = tempfile.mkdtemp(prefix="src_f2_")
        server2 = ft.FileServer(
            {"D": "D:", "E": "E:", "F": src_f2},
            log_callback=srv_logs.append, auth_code="abcd",
            cert_paths=(cert_path, key_path),
        )
        threading.Thread(target=server2.start, daemon=True).start()
        time.sleep(1.2)
        empty = ft._download_filelist(
            f"https://127.0.0.1:{ft.TRANSFER_PORT}", "abcd", {"F": dst_f},
            lambda m: None)
        check(empty == "", "B6 源端无 Appl 目录应返回空字符串")
        server2.stop()
    finally:
        try:
            server.stop()
        except Exception:
            pass


# ============================================================
# C. 下载 + 边传边校验 E2E (含断点续传)
# ============================================================

def suite_download_verify():
    ft.TRANSFER_PORT = 18778

    src_d = tempfile.mkdtemp(prefix="src_d_")
    src_f = tempfile.mkdtemp(prefix="src_f_")
    os.makedirs(os.path.join(src_d, "sub"))
    with open(os.path.join(src_d, "a.txt"), "wb") as f:
        f.write(b"hello a")
    with open(os.path.join(src_d, "sub", "b.bin"), "wb") as f:
        f.write(os.urandom(1000))
    with open(os.path.join(src_d, "empty.txt"), "wb") as f:
        pass  # 空文件

    cert_path, key_path = tls_utils.get_or_create_fixed_cert()
    srv_logs = []
    server = ft.FileServer(
        {"D": src_d, "F": src_f}, log_callback=srv_logs.append,
        auth_code="abcd", cert_paths=(cert_path, key_path),
    )
    threading.Thread(target=server.start, daemon=True).start()
    time.sleep(1.2)

    try:
        dst_d = tempfile.mkdtemp(prefix="dst_d_")
        dst_f = tempfile.mkdtemp(prefix="dst_f_")
        logs = []
        pre_verified_out = [None]
        verify_prog = []
        success, done_files, _done_bytes, _errors = ft.download_files(
            server_ip="127.0.0.1",
            partition_map={"D": dst_d, "F": dst_f},
            log_callback=logs.append, max_workers=2, partition_count=0,
            auth_code="abcd", verify_after_transfer=True,
            pre_verified_out=pre_verified_out,
            verify_progress_callback=lambda ok, fail, total: verify_prog.append(
                (ok, fail, total)),
        )

        check(success, "C1 传输应成功")
        check(done_files == 3, f"C2 应传输 3 个文件 (实际 {done_files})")
        check(os.path.isfile(os.path.join(dst_d, "a.txt")), "C3 a.txt 应存在")
        check(os.path.isfile(os.path.join(dst_d, "sub", "b.bin")), "C4 sub/b.bin 应存在")
        check(os.path.isfile(os.path.join(dst_d, "empty.txt")), "C5 empty.txt 应存在")

        check(bool(pre_verified_out[0]) and os.path.isfile(pre_verified_out[0]),
              "C6 pre_verified 确认清单应生成")
        if not pre_verified_out[0]:
            return
        with open(pre_verified_out[0], "r", encoding="utf-8") as f:
            content = f.read().strip()
        paths, sizes = set(), {}
        for ln in content.splitlines():
            if "|" in ln:
                p, _, s = ln.rpartition("|")
                paths.add(p)
                sizes[p] = s
            else:
                paths.add(ln)  # 兼容旧格式(纯路径)
        check(len(paths) == 3, f"C7 确认清单应有 3 个文件 (实际 {len(paths)})")
        check(sizes.get("D:\\a.txt") == "7", "C8 清单应记录 a.txt 大小 7")
        check(sizes.get("D:\\sub\\b.bin") == "1000", "C9 清单应记录 b.bin 大小 1000")
        check(sizes.get("D:\\empty.txt") == "0", "C10 清单应记录空文件大小 0")

        check(bool(verify_prog), "C11 verify_progress_callback 应被调用")
        if verify_prog:
            last_ok, last_fail, last_total = verify_prog[-1]
            check((last_ok, last_fail, last_total) == (3, 0, 3),
                  f"C12 回调终值应为 ok=3 fail=0 total=3 (实际 {last_ok}/{last_fail}/{last_total})")
            ok_seq = [o for o, _, _ in verify_prog]
            check(ok_seq == sorted(ok_seq), "C13 ok 计数应为非递减序列")
            check(len(verify_prog) >= 3, "C14 每个文件至少回调一次")

        # 二次传输 (断点续传)
        logs_first = len(logs)
        pre_verified_out2 = [None]
        success2, _f2, _b2, _e2 = ft.download_files(
            server_ip="127.0.0.1",
            partition_map={"D": dst_d, "F": dst_f},
            log_callback=logs.append, max_workers=2, partition_count=0,
            auth_code="abcd", verify_after_transfer=True,
            pre_verified_out=pre_verified_out2,
        )
        second_logs = "\n".join(logs[logs_first:])
        check(success2, "C15 二次传输应成功")
        check("断点续传: 跳过 3 个已存在文件" in second_logs,
              "C16 二次传输应全部由断点续传跳过")
        check("所有文件已存在，无需下载" in second_logs,
              "C17 全量跳过应走 early return")
        check(bool(pre_verified_out2[0]) and os.path.isfile(pre_verified_out2[0]),
              "C18 early return 也应保存确认清单")
        if pre_verified_out2[0]:
            with open(pre_verified_out2[0], "r", encoding="utf-8") as f:
                paths2 = {ln.rpartition("|")[0] if "|" in ln else ln
                          for ln in f.read().strip().splitlines()}
            check("D:\\a.txt" in paths2 and "D:\\sub\\b.bin" in paths2,
                  "C19 断点续传跳过文件也应记入确认清单")
    finally:
        try:
            server.stop()
        except Exception:
            pass
        time.sleep(0.3)


def _run(name, fn):
    print(f"\n--- {name} ---")
    try:
        fn()
    except Exception as e:
        check(False, f"{name} 执行异常: {type(e).__name__}: {e}")


def main():
    print("=" * 60)
    print("校验相关端到端测试 (增量确认 / 全盘清单 / 下载+校验)")
    print("=" * 60)

    _run("A. 边传边校验增量确认", suite_preok)
    _run("B. 全盘清单 /filelist E2E", suite_filelist)
    _run("C. 下载+校验 E2E (含断点续传)", suite_download_verify)

    print()
    print(f"总计: {_TOTAL[0]}  通过: {_TOTAL[0] - _FAILED[0]}  失败: {_FAILED[0]}")
    if _FAILED[0]:
        print("存在失败用例!")
        return 1
    print("全部通过!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
