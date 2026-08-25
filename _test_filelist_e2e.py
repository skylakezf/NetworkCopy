# -*- coding: utf-8 -*-
"""端到端测试: 源端 /filelist 端点 → 目标端 _download_filelist 提前下载清单"""
import os
import sys
import time
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import file_transfer as ft
import tls_utils

PORT = 18777

def main():
    ft.TRANSFER_PORT = PORT

    # ---- 模拟源端 F 盘 (含 Appl/<日期>/FullFilelist_DEF.csv) ----
    src_f = tempfile.mkdtemp(prefix="src_f_")
    appl = os.path.join(src_f, "Appl", "2026-08-25")
    os.makedirs(appl)
    csv_content = 'Drive,FullPath,FileName,SizeBytes\r\nD,"D:\\a.txt",a.txt,3\r\n'
    with open(os.path.join(appl, "FullFilelist_DEF.csv"), "w", encoding="utf-8-sig") as f:
        f.write(csv_content)

    # ---- 源端服务器 (模拟 FileServer, 直接走 handler 类) ----
    cert_path, key_path = tls_utils.get_or_create_fixed_cert()
    srv_logs = []
    server = ft.FileServer(
        {"D": "D:", "E": "E:", "F": src_f},
        log_callback=srv_logs.append,
        auth_code="abcd",
        cert_paths=(cert_path, key_path),
    )
    t = threading.Thread(target=server.start, daemon=True)
    t.start()
    time.sleep(1.2)

    # ---- 目标端: 调用 _download_filelist ----
    dst_f = tempfile.mkdtemp(prefix="dst_f_")
    logs = []
    csv_path = ft._download_filelist(f"https://127.0.0.1:{PORT}", "abcd", {"F": dst_f}, logs.append)
    print("csv_path:", csv_path)
    print("目标日志:")
    for ln in logs:
        print("  |", ln)
    print("服务器日志:")
    for ln in srv_logs:
        print("  |", ln)
    assert csv_path and os.path.isfile(csv_path), "CSV 应被下载保存"
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        content = f.read()
    assert "a.txt" in content, "CSV 内容应包含 a.txt"
    # 校验保存目录: 目标 F 盘 Appl/<日期>/FullFilelist_DEF.csv
    assert os.path.basename(csv_path) == "FullFilelist_DEF.csv"
    assert os.path.basename(os.path.dirname(csv_path)) == "2026-08-25", "应保存到源端日期目录"
    print("保存内容:", content.strip())
    print("目标日志:")
    for ln in logs:
        print("  |", ln)

    # ---- 错误鉴权: 应失败 ----
    logs2 = []
    bad = ft._download_filelist(f"https://127.0.0.1:{PORT}", "wrong", {"F": dst_f}, logs2.append)
    assert bad == "", "错误验证码应返回空字符串"
    print("鉴权失败测试 OK")

    # ---- 源端无 Appl 目录: 应优雅返回空 ----
    src_f2 = tempfile.mkdtemp(prefix="src_f2_")
    server2 = ft.FileServer(
        {"D": "D:", "E": "E:", "F": src_f2},
        log_callback=srv_logs.append, auth_code="abcd",
        cert_paths=(cert_path, key_path),
    )
    # 复用同一端口需先停 server
    server.stop()
    time.sleep(0.5)
    t2 = threading.Thread(target=server2.start, daemon=True)
    t2.start()
    time.sleep(1.2)
    logs3 = []
    empty = ft._download_filelist(f"https://127.0.0.1:{PORT}", "abcd", {"F": dst_f}, logs3.append)
    assert empty == "", "源端无 Appl 应返回空字符串"
    print("无 Appl 目录测试 OK")
    server2.stop()
    time.sleep(0.3)

    print("E2E FILELIST TEST PASSED")

if __name__ == "__main__":
    main()
