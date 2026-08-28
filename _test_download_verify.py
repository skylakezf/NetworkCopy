# -*- coding: utf-8 -*-
"""完整测试: download_files 启用边传边校验 → pre_verified.txt 确认清单生成"""
import os
import sys
import time
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import file_transfer as ft
import tls_utils

PORT = 18778

def main():
    ft.TRANSFER_PORT = PORT

    # ---- 源端 D 盘 (含几个文件, 其中 sub 目录) ----
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
    t = threading.Thread(target=server.start, daemon=True)
    t.start()
    time.sleep(1.2)

    # ---- 接收端: 传输到临时 D 盘 ----
    dst_d = tempfile.mkdtemp(prefix="dst_d_")
    dst_f = tempfile.mkdtemp(prefix="dst_f_")
    logs = []
    pre_verified_out = [None]
    verify_prog = []  # 收集 (ok, fail, total) 回调序列
    success, done_files, done_bytes, errors = ft.download_files(
        server_ip="127.0.0.1",
        partition_map={"D": dst_d, "F": dst_f},
        log_callback=logs.append,
        max_workers=2,
        partition_count=0,
        auth_code="abcd",
        verify_after_transfer=True,
        pre_verified_out=pre_verified_out,
        verify_progress_callback=lambda ok, fail, total: verify_prog.append((ok, fail, total)),
    )
    print("success:", success, "done_files:", done_files, "done_bytes:", done_bytes, "errors:", errors)
    for ln in logs:
        print("  |", ln)

    # ---- 断言 ----
    assert success, "传输应成功"
    assert done_files == 3, f"应传输 3 个文件, 实际 {done_files}"
    assert os.path.isfile(os.path.join(dst_d, "a.txt"))
    assert os.path.isfile(os.path.join(dst_d, "sub", "b.bin"))
    assert os.path.isfile(os.path.join(dst_d, "empty.txt"))
    assert pre_verified_out[0] and os.path.isfile(pre_verified_out[0]), "pre_verified 文件应生成"
    with open(pre_verified_out[0], "r", encoding="utf-8") as f:
        content = f.read().strip()
    print("pre_verified 内容:")
    print(content)
    paths = set()
    sizes = {}
    for ln in content.splitlines():
        if "|" in ln:
            p, _, s = ln.rpartition("|")
            paths.add(p)
            sizes[p] = s
        else:
            paths.add(ln)  # 兼容旧格式(纯路径)
    assert "D:\\a.txt" in paths, "a.txt 应记入确认清单"
    assert "D:\\sub\\b.bin" in paths, "b.bin 应记入确认清单"
    assert "D:\\empty.txt" in paths, "空文件也应记入确认清单"
    assert len(paths) == 3, f"应有 3 个确认文件, 实际 {len(paths)}"
    # 确认清单应携带文件大小 (供校验阶段大小复核)
    assert sizes.get("D:\\a.txt") == "7", "确认清单应记录 a.txt 的大小(7字节)"
    assert sizes.get("D:\\sub\\b.bin") == "1000", f"确认清单应记录 b.bin 的大小(1000字节), 实际 {sizes.get('D:\\sub\\b.bin')}"
    assert sizes.get("D:\\empty.txt") == "0", "确认清单应记录空文件大小 0"

    # ---- verify_progress_callback 回调断言 ----
    assert verify_prog, "verify_progress_callback 应被调用"
    last_ok, last_fail, last_total = verify_prog[-1]
    assert last_total == 3, f"回调 total 应为 3, 实际 {last_total}"
    assert last_ok == 3, f"回调最终 ok 应为 3, 实际 {last_ok}"
    assert last_fail == 0, f"回调最终 fail 应为 0, 实际 {last_fail}"
    # 回调应为递增序列 (每个文件确认一次)
    ok_seq = [o for o, _, _ in verify_prog]
    assert ok_seq == sorted(ok_seq), "ok 计数应为非递减序列"
    assert len(verify_prog) >= 3, f"每个文件至少回调一次, 实际 {len(verify_prog)} 次"
    print(f"verify_progress_callback 回调序列: {verify_prog}")

    # ---- 再次传输 (断点续传): 跳过的文件也应记入确认清单 ----
    logs_first = len(logs)
    pre_verified_out2 = [None]
    success2, done_files2, done_bytes2, errors2 = ft.download_files(
        server_ip="127.0.0.1",
        partition_map={"D": dst_d, "F": dst_f},
        log_callback=logs.append,
        max_workers=2,
        partition_count=0,
        auth_code="abcd",
        verify_after_transfer=True,
        pre_verified_out=pre_verified_out2,
    )
    print("二次传输 success:", success2, "done_files:", done_files2)
    # done_files 计数含断点续传跳过的文件, 关键看日志是否"跳过"而非重新下载
    second_logs = "\n".join(logs[logs_first:])
    assert success2, "二次传输应成功"
    assert "断点续传: 跳过 3 个已存在文件" in second_logs, "二次传输应全部由断点续传跳过"
    assert "所有文件已存在，无需下载" in second_logs, "全量跳过应走 early return"
    assert pre_verified_out2[0] and os.path.isfile(pre_verified_out2[0]), "early return 也应保存确认清单"
    with open(pre_verified_out2[0], "r", encoding="utf-8") as f:
        paths2 = set()
        for ln in f.read().strip().splitlines():
            if "|" in ln:
                paths2.add(ln.rpartition("|")[0])
            else:
                paths2.add(ln)
    assert "D:\\a.txt" in paths2 and "D:\\sub\\b.bin" in paths2, "断点续传跳过文件也应记入确认清单"
    print("断点续传跳过文件记入确认清单 OK")

    server.stop()
    time.sleep(0.3)
    print("DOWNLOAD+VERIFY E2E TEST PASSED")

if __name__ == "__main__":
    main()
