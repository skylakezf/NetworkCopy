# -*- coding: utf-8 -*-
"""
新设备与旧设备传输测试 (2026-08-26)
=====================================
模拟「旧设备(发送端/源) → 新设备(接收端/目标)」完整拷贝链路:
  1. 旧设备: 生成验证码 → 启动 HTTPS 文件服务器 (端口 9999, TLS + 验证码鉴权)
  2. 新设备: 输入验证码 → download_files 并行下载 D/E/F 数据
  3. 事后校验: MD5 对比源/目标全部文件
  4. 鉴权: 错误验证码被拒绝
  5. 幂等: 二次接收 (overwrite=False) 已存在文件跳过
  6. 边传边校验: 源端无清单时自动降级, 传输仍成功

运行 (需 cryptography):
    python-3.13.14-embed-amd64\\python.exe _test_device_transfer_e2e.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import hashlib
import os
import shutil
import socket
import sys
import tempfile
import time
import urllib.request

# 测试环境: 关闭"启动时用默认浏览器打开 EULA 页面"
os.environ["NETCOPY_SKIP_EULA_BROWSER"] = "1"
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
os.chdir(BASE)

from file_transfer import FileServer, download_files, TRANSFER_PORT

CERT = os.path.join(BASE, "certs", "server.pem")
KEY = os.path.join(BASE, "certs", "server.key")

PASS = 0
FAIL = 0


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS: {msg}")
    else:
        FAIL += 1
        print(f"  FAIL: {msg}")


def eq(a, b, msg):
    check(a == b, f"{msg} (got {a!r}, want {b!r})")


# ---------------- 工具函数 ----------------

def md5_file(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_md5(root):
    """返回 {相对路径: md5} 映射"""
    res = {}
    if not os.path.isdir(root):
        return res
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            fp = os.path.join(dp, f)
            rel = os.path.relpath(fp, root).replace("\\", "/")
            res[rel] = md5_file(fp)
    return res


def make_src_partition(root):
    """构造模拟「旧设备 D/E/F 盘」的源目录。
    注意: 目录名避开工具内置过滤规则 (Program Files / User/非当前用户 等会被跳过)。"""
    if os.path.isdir(root):
        shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root, exist_ok=True)

    def w(rel, data):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)

    w("系统文件.txt", "旧设备系统配置文件内容\n".encode("utf-8"))
    w("用户文档/桌面/文档.docx", b"dummy docx content " * 100)
    w("应用软件/App1/app.exe", os.urandom(64 * 1024))           # 64KB 随机二进制
    w("数据备份/大文件.dat", os.urandom(512 * 1024))            # 512KB 大文件
    w("中文文件名/测试报告 2026.pdf", "PDF 内容".encode("utf-8"))
    # 空目录 (无文件)
    os.makedirs(os.path.join(root, "空文件夹"), exist_ok=True)


def port_in_use(port=TRANSFER_PORT):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def wait_server_ready(auth_code, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(
                f"https://127.0.0.1:{TRANSFER_PORT}/ping?pwd={auth_code}",
                timeout=1,
                context=__import__("ssl")._create_unverified_context(),
            )
            return True
        except Exception:
            time.sleep(0.2)
    return False


def start_old_device(partition_map, auth_code, cert_paths=(CERT, KEY)):
    """旧设备 (源): 启动 HTTPS 文件服务器"""
    srv = FileServer(partition_map, log_callback=None,
                     auth_code=auth_code, cert_paths=cert_paths)
    srv.start()
    return srv


# ---------------- 测试用例 ----------------

def test_old_to_new_full_transfer():
    """场景 1: 旧设备 → 新设备 完整传输 (含中文/空目录/二进制/大文件)"""
    print("\n[场景1] 旧设备→新设备 完整传输 + MD5 校验")
    auth = "A1B2"
    src_d = tempfile.mkdtemp(prefix="old_d_")
    src_e = tempfile.mkdtemp(prefix="old_e_")
    src_f = tempfile.mkdtemp(prefix="old_f_")
    dst_d = tempfile.mkdtemp(prefix="new_d_")
    dst_e = tempfile.mkdtemp(prefix="new_e_")
    dst_f = tempfile.mkdtemp(prefix="new_f_")
    srv = None
    try:
        make_src_partition(src_d)
        make_src_partition(src_e)
        os.makedirs(os.path.join(src_f, "Appl"), exist_ok=True)

        # 旧设备启动服务器
        srv = start_old_device({"D": src_d, "E": src_e, "F": src_f}, auth)
        check(wait_server_ready(auth), "旧设备文件服务器已就绪")

        # 新设备下载
        ok, done, done_bytes, errs = download_files(
            server_ip="127.0.0.1",
            partition_map={"D": dst_d, "E": dst_e, "F": dst_f},
            log_callback=lambda m: None,
            max_workers=4,
            partition_count=3,
            auth_code=auth,
        )
        eq(ok, True, "新设备下载应成功")
        eq(len(errs), 0, f"不应有错误 (got {len(errs)})")

        # 逐分区 MD5 校验
        for part, src, dst in (("D", src_d, dst_d), ("E", src_e, dst_e), ("F", src_f, dst_f)):
            src_map = collect_md5(src)
            dst_map = collect_md5(dst)
            # 目标端可能额外包含 Appl 清单目录 (边传边校验关闭时不产生), 只比对源存在的文件
            common = set(src_map)
            missing = common - set(dst_map)
            eq(len(missing), 0, f"分区{part} 不应有缺失文件 {missing}")
            mismatch = [r for r in common if src_map[r] != dst_map.get(r)]
            eq(len(mismatch), 0, f"分区{part} MD5 应一致 (got {mismatch})")
        print(f"  传输统计: 文件数={done}, 字节数={done_bytes}")
    finally:
        if srv:
            srv.stop()
        for p in (src_d, src_e, src_f, dst_d, dst_e, dst_f):
            shutil.rmtree(p, ignore_errors=True)


def test_wrong_auth_rejected():
    """场景 2: 错误验证码 → 下载被拒绝"""
    print("\n[场景2] 错误验证码被拒绝")
    auth = "Z9X8"
    src_d = tempfile.mkdtemp(prefix="old_d_")
    src_f = tempfile.mkdtemp(prefix="old_f_")
    dst_d = tempfile.mkdtemp(prefix="new_d_")
    dst_f = tempfile.mkdtemp(prefix="new_f_")
    srv = None
    try:
        make_src_partition(src_d)
        srv = start_old_device({"D": src_d, "F": src_f}, auth)
        check(wait_server_ready(auth), "旧设备文件服务器已就绪")

        ok, _done, _db, errs = download_files(
            server_ip="127.0.0.1",
            partition_map={"D": dst_d, "F": dst_f},
            log_callback=lambda m: None,
            max_workers=2,
            partition_count=3,
            auth_code="WRONG",
        )
        eq(ok, False, "错误验证码应返回失败")
        eq(os.listdir(dst_d), [], "错误验证码时目标盘不应有下载文件")
    finally:
        if srv:
            srv.stop()
        for p in (src_d, src_f, dst_d, dst_f):
            shutil.rmtree(p, ignore_errors=True)


def test_resume_second_download_skips_existing():
    """场景 3: 二次接收 (overwrite=False) → 已存在文件被跳过, MD5 仍一致"""
    print("\n[场景3] 二次接收跳过已存在文件 (断点续传)")
    auth = "C3D4"
    src_d = tempfile.mkdtemp(prefix="old_d_")
    src_f = tempfile.mkdtemp(prefix="old_f_")
    dst_d = tempfile.mkdtemp(prefix="new_d_")
    dst_f = tempfile.mkdtemp(prefix="new_f_")
    srv = None
    try:
        make_src_partition(src_d)
        srv = start_old_device({"D": src_d, "F": src_f}, auth)
        check(wait_server_ready(auth), "旧设备文件服务器已就绪")

        # 第一次完整下载
        ok1, done1, _b1, _e1 = download_files(
            server_ip="127.0.0.1",
            partition_map={"D": dst_d, "F": dst_f},
            log_callback=lambda m: None,
            max_workers=2,
            partition_count=3,
            auth_code=auth,
        )
        eq(ok1, True, "第一次下载应成功")
        eq(done1, len(collect_md5(src_d)), "第一次应下载全部文件")

        # 第二次下载: 所有文件已存在 → 跳过 (不重复传输)
        ok2, done2, _b2, _e2 = download_files(
            server_ip="127.0.0.1",
            partition_map={"D": dst_d, "F": dst_f},
            log_callback=lambda m: None,
            max_workers=2,
            partition_count=3,
            auth_code=auth,
            overwrite=False,
        )
        eq(ok2, True, "第二次下载应成功")
        # completed_files 统计包含断点续传"跳过(已存在)"的文件:
        # 5 个文件全部已存在且大小正确 → 无任何新下载, 全部走跳过分支
        eq(done2, len(collect_md5(src_d)),
           "第二次应全部命中断点续传跳过 (无新下载, 完成数=源文件数)")

        # 校验 MD5 仍一致
        src_map = collect_md5(src_d)
        dst_map = collect_md5(dst_d)
        eq(len(set(src_map) - set(dst_map)), 0, "二次接收后不应有缺失文件")
        eq(sum(1 for r in src_map if src_map[r] != dst_map.get(r)), 0,
           "二次接收后 MD5 应仍一致")
    finally:
        if srv:
            srv.stop()
        for p in (src_d, src_f, dst_d, dst_f):
            shutil.rmtree(p, ignore_errors=True)


def test_verify_after_transfer_degraded():
    """场景 4: 边传边校验 (源端无清单) → 自动降级, 传输仍成功"""
    print("\n[场景4] 边传边校验: 源端无 FullFilelist 时降级仍成功")
    auth = "E5F6"
    src_d = tempfile.mkdtemp(prefix="old_d_")
    src_f = tempfile.mkdtemp(prefix="old_f_")   # 无 Appl 目录 → 无清单
    dst_d = tempfile.mkdtemp(prefix="new_d_")
    dst_f = tempfile.mkdtemp(prefix="new_f_")
    srv = None
    try:
        make_src_partition(src_d)
        srv = start_old_device({"D": src_d, "F": src_f}, auth)
        check(wait_server_ready(auth), "旧设备文件服务器已就绪")

        pre_verified = [None]
        ok, done, _db, errs = download_files(
            server_ip="127.0.0.1",
            partition_map={"D": dst_d, "F": dst_f},
            log_callback=lambda m: None,
            max_workers=3,
            partition_count=3,
            auth_code=auth,
            verify_after_transfer=True,
            pre_verified_out=pre_verified,
        )
        eq(ok, True, "边传边校验模式下传输应成功")
        eq(len(errs), 0, f"不应有错误 (got {len(errs)})")
        # 源端无清单 → 边传边校验降级为按下载结果确认 (存在+大小)。
        # 已确认文件仍会写入 pre_verified 清单 (若有), 供校验阶段增量跳过, 属正常设计
        if pre_verified[0]:
            check(os.path.isfile(pre_verified[0]),
                  "pre_verified 确认清单应存在 (如已生成)")

        src_map = collect_md5(src_d)
        dst_map = collect_md5(dst_d)
        eq(sum(1 for r in src_map if src_map[r] != dst_map.get(r)), 0,
           "降级传输后 MD5 应仍一致")
        print(f"  传输统计: 文件数={done}")
    finally:
        if srv:
            srv.stop()
        for p in (src_d, src_f, dst_d, dst_f):
            shutil.rmtree(p, ignore_errors=True)


def test_auto_verify_after_transfer():
    """场景 6: 传输完成后自动校验 + 报告生成 + 自动上传 (校验页已取消)"""
    print("\n[场景6] 传输完成自动校验 → 生成报告 → 自动上传")
    import ui as ui_mod
    import control as ctl_mod
    import verifier as v_mod
    import config_transfer as ct_mod

    auth = "G7H8"
    src_d = tempfile.mkdtemp(prefix="old_d_")
    src_f = tempfile.mkdtemp(prefix="old_f_")
    dst_d = tempfile.mkdtemp(prefix="new_d_")
    dst_f = tempfile.mkdtemp(prefix="new_f_")
    srv = None
    app = None
    try:
        make_src_partition(src_d)
        srv = start_old_device({"D": src_d, "F": src_f}, auth)
        check(wait_server_ready(auth), "旧设备文件服务器已就绪")

        # 真实下载链路
        ok, done, _db, _errs = download_files(
            server_ip="127.0.0.1",
            partition_map={"D": dst_d, "F": dst_f},
            log_callback=lambda m: None,
            max_workers=3,
            partition_count=3,
            auth_code=auth,
        )
        eq(ok, True, "下载应成功")

        # 模拟新设备控制层「传输完成」回调
        app = ui_mod.WinGUI()
        app.report_callback_exception = lambda *a: None
        ctl = ctl_mod.Controller()
        ctl.init(app)
        app.ctl = ctl
        ctl._partition_map = {"D": dst_d, "F": dst_f}
        ctl._last_source_ip = "127.0.0.1"
        ctl._auth_code = auth
        ctl._device_type = "目标设备"
        ctl._pre_verified_file = None  # 简化: 不依赖边传边校验清单

        calls = {"verify": 0, "upload": 0}
        # 注意: control.py 通过模块级导入绑定 run_verification, 必须 patch control 模块全局
        _orig_verify = ctl_mod.run_verification
        _orig_upload = ct_mod.upload_zip_to_server
        try:
            def _fake_verify(**kw):
                calls["verify"] += 1
                # 真实 run_verification 会把校验报告 ZIP 路径 append 进 report_zip_out 列表
                rzo = kw.get("report_zip_out")
                if rzo is not None:
                    rzo.append("unverifi_report.zip")
                return True, 5, 0, 0, 5  # ok, passed, failed, skipped, total

            def _fake_upload(zip_path, log_callback=None, **kw):
                calls["upload"] += 1
                calls["zip"] = zip_path
                return True, None

            ctl_mod.run_verification = _fake_verify
            ct_mod.upload_zip_to_server = _fake_upload

            # 传输完成 → 应自动启动校验线程
            ctl._on_download_complete(True, done, _db, [])
            app.update()
            check(ctl._verify_thread is not None, "传输完成后应自动启动校验线程")

            # 等待校验完成 + 报告上传
            deadline = time.time() + 10
            while time.time() < deadline:
                app.update()
                time.sleep(0.05)
                if ctl._verify_done and calls["upload"] >= 1:
                    break
            eq(ctl._verify_done, True, "校验应自动完成")
            check(calls["verify"] >= 1, "run_verification 应被自动调用")
            check(calls["upload"] >= 1, "校验报告应自动上传到服务器")
            check(ctl._verify_report_path is not None, "校验报告路径应已记录")
            eq(ctl._transfer_done, True, "传输完成标记应置位")
        finally:
            ctl_mod.run_verification = _orig_verify
            ct_mod.upload_zip_to_server = _orig_upload
    finally:
        if app:
            app.destroy()
        if srv:
            srv.stop()
        for p in (src_d, src_f, dst_d, dst_f):
            shutil.rmtree(p, ignore_errors=True)


def test_dhcp_client_nic_independent():
    """场景 5: 新设备「寻找旧电脑」在无手动网卡时不会崩溃 (自动网卡逻辑)"""
    print("\n[场景5] 接收端 DHCP 按钮不依赖手动网卡选择")
    # 直接验证 control 层在 _auto_nic=None (无网卡环境) 下的容错
    import control as ctl_mod
    try:
        from nic_scanner import get_wired_adapters
        wired = get_wired_adapters()
        # 无论是否有网卡, _get_adapter_desc_from_auto 都不应抛异常
        app_ctl = ctl_mod.Controller.__new__(ctl_mod.Controller)
        app_ctl._auto_nic = wired[0] if wired else None
        desc = app_ctl._get_adapter_desc_from_auto()
        if wired:
            check(desc == wired[0][1], "有网卡时 _get_adapter_desc_from_auto 应返回其描述")
        else:
            check(desc == "", "无网卡时 _get_adapter_desc_from_auto 应返回空串")
        print("  PASS: 无手动网卡环境下接收端流程可容错")
    except Exception as e:
        check(False, f"控制层网卡容错异常: {e}")


# ---------------- main ----------------

def main():
    global PASS, FAIL
    print("=" * 60)
    print("新设备与旧设备传输测试 (NetworkzCopy)")
    print("=" * 60)

    if not (os.path.isfile(CERT) and os.path.isfile(KEY)):
        print(f"SKIP: 证书不存在 ({CERT}) — 请先运行 build.ps1 或生成证书")
        return 1
    if port_in_use():
        print(f"SKIP: 端口 {TRANSFER_PORT} 已被占用, 请关闭占用进程后重试")
        return 1

    test_old_to_new_full_transfer()
    test_wrong_auth_rejected()
    test_resume_second_download_skips_existing()
    test_verify_after_transfer_degraded()
    test_auto_verify_after_transfer()
    test_dhcp_client_nic_independent()

    print("-" * 60)
    print(f"结果: PASS {PASS} / FAIL {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
