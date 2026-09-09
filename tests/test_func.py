"""
磁盘拷贝工具 — 完整功能测试
覆盖:
  A. file_transfer 文件过滤规则 (微信 .dat 修复 / ntuser.dat 精确跳过)
  B. FileServer HTTPS 端点 (ping / list / get / batch_get / 鉴权 403)
  C. download_files 端到端传输 (过滤 + 内容一致性)
  D. verifier CSV 路径修复 (全角逗号 → ASCII 逗号)
  E. calc_allocation_migration 过滤规则 & 分配单元计算
  F. tls_utils 验证码 & 自签名证书
  G. config_transfer ZIP 压缩命名
  H. controller 网络断开检测 (2026-08-05 新增)
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import sys, os, threading, time, traceback, tempfile, shutil
import json, ssl, struct
import urllib.request, urllib.error

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_ROOT)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, 'python-3.13.14-embed-amd64',
                                'Lib', 'site-packages'))

# ============================================================
# 测试框架 (与 UI 测试脚本一致)
# ============================================================

results = []
errors = []

def test(name):
    def decorator(fn):
        def wrapper():
            try:
                fn()
                results.append(f"  PASS: {name}")
            except AssertionError as e:
                msg = f"  FAIL: {name} — {e}"
                results.append(msg)
                errors.append(msg)
            except Exception as e:
                msg = f"  ERROR: {name} — {e}\n{traceback.format_exc()}"
                results.append(msg)
                errors.append(msg)
        return wrapper
    return decorator

def check(cond, msg=""):
    if not cond:
        raise AssertionError(msg)

def eq(a, b, msg=""):
    if a != b:
        raise AssertionError(f"{msg} 期望={b!r}, 实际={a!r}")

# ============================================================
# HTTP 客户端辅助 (HTTPS + 不校验自签名证书)
# ============================================================

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

def _http_get(url, timeout=10):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        return r.status, r.read()

def _http_post(url, body, timeout=10):
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        return r.status, r.read()

def _http_status(url):
    try:
        with urllib.request.urlopen(url, timeout=10, context=_CTX) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code

# ============================================================
# FileServer 测试夹具: 临时源目录 + 启动服务器
# ============================================================

class ServerFixture:
    """在临时目录上启动 FileServer (partition D), 端口 9999"""
    def __init__(self):
        from file_transfer import FileServer, TRANSFER_PORT
        import tls_utils
        self.port = TRANSFER_PORT
        self.auth = "TEST"
        self.src = tempfile.mkdtemp(prefix="ft_src_")
        self._mkfiles()
        certs = tls_utils.get_or_create_fixed_cert()
        self.srv = FileServer(
            partition_map={"D": self.src},
            auth_code=self.auth,
            cert_paths=certs,
        )
        self.srv.start()
        time.sleep(0.6)

    def _mkfiles(self):
        # 应被传输的文件
        with open(os.path.join(self.src, "normal.txt"), "w") as f:
            f.write("hello")
        with open(os.path.join(self.src, "data.dat"), "wb") as f:
            f.write(b"\x00\x01\x02" * 100)  # 微信风格 .dat 数据
        sub = os.path.join(self.src, "sub")
        os.makedirs(sub)
        with open(os.path.join(sub, "nested.dat"), "w") as f:
            f.write("nested dat")
        # 应被跳过的文件
        with open(os.path.join(self.src, "NTUSER.DAT"), "wb") as f:
            f.write(b"reg")            # SKIP_FILENAMES (大小写不敏感)
        with open(os.path.join(self.src, "temp.tmp"), "w") as f:
            f.write("t")               # SKIP_FILE_SUFFIXES .tmp
        with open(os.path.join(self.src, "app.log"), "w") as f:
            f.write("l")               # SKIP_FILE_SUFFIXES .log
        with open(os.path.join(self.src, "NTUSER.DAT.LOG1"), "w") as f:
            f.write("log")             # SKIP_FILE_CONTAINS .dat.log
        with open(os.path.join(self.src, "~$draft.docx"), "w") as f:
            f.write("tmp")             # SKIP_FILE_PREFIXES ~$
        with open(os.path.join(self.src, "pagefile.sys"), "w") as f:
            f.write("pf")              # SKIP_FILES

    def url(self, path, pwd=None):
        p = pwd if pwd is not None else self.auth
        sep = "&" if "?" in path else "?"
        return f"https://127.0.0.1:{self.port}{path}{sep}pwd={p}"

    def close(self):
        try:
            self.srv.stop()
        except Exception:
            pass
        shutil.rmtree(self.src, ignore_errors=True)


# ============================================================
# A. file_transfer 文件过滤规则
# ============================================================

@test("A1. 过滤常量: .dat 已从后缀过滤移除")
def test_dat_not_in_suffixes():
    from file_transfer import SKIP_FILE_SUFFIXES, SKIP_FILENAMES
    check(".dat" not in SKIP_FILE_SUFFIXES,
          f".dat 不应在 SKIP_FILE_SUFFIXES: {SKIP_FILE_SUFFIXES}")
    check("ntuser.dat" in SKIP_FILENAMES, "ntuser.dat 应精确跳过")

@test("A2. 过滤常量与 calc_allocation_migration 对齐")
def test_filter_constants_aligned():
    import file_transfer as ft
    import systemconfig.calc_allocation_migration as cam
    eq(ft.SKIP_FILE_SUFFIXES, cam.SKIP_FILE_SUFFIXES,
       "SKIP_FILE_SUFFIXES 应一致")
    eq(ft.SKIP_FILENAMES, cam.SKIP_FILENAMES, "SKIP_FILENAMES 应一致")

# ============================================================
# B. FileServer HTTPS 端点
# ============================================================

@test("B1. /ping 鉴权成功返回 ok 与分区列表")
def test_ping_ok():
    srv = ServerFixture()
    try:
        status, body = _http_get(srv.url("/ping"))
        eq(status, 200)
        data = json.loads(body)
        eq(data.get("status"), "ok")
        eq(data.get("partitions"), ["D"])
    finally:
        srv.close()

@test("B2. /ping 缺少 pwd 返回 403")
def test_ping_no_pwd():
    srv = ServerFixture()
    try:
        status = _http_status(f"https://127.0.0.1:{srv.port}/ping")
        eq(status, 403, "缺少验证码应 403")
    finally:
        srv.close()

@test("B3. /ping 错误 pwd 返回 403")
def test_ping_wrong_pwd():
    srv = ServerFixture()
    try:
        status = _http_status(srv.url("/ping", pwd="WRNG"))
        eq(status, 403, "错误验证码应 403")
    finally:
        srv.close()

@test("B4. /list 过滤规则正确 (dat 保留, 锁定/临时文件剔除)")
def test_list_filter():
    srv = ServerFixture()
    try:
        status, body = _http_get(srv.url("/list?partition=D"))
        eq(status, 200)
        data = json.loads(body)
        paths = {f["path"] for f in data["files"]}
        # 应包含
        check("normal.txt" in paths, f"缺少 normal.txt: {sorted(paths)}")
        check("data.dat" in paths, f"缺少 data.dat (微信 .dat 应保留)")
        check("sub/nested.dat" in paths, f"缺少 sub/nested.dat")
        # 应排除
        for name in ("NTUSER.DAT", "temp.tmp", "app.log",
                     "NTUSER.DAT.LOG1", "~$draft.docx", "pagefile.sys"):
            check(name not in paths, f"{name} 不应出现在列表")
    finally:
        srv.close()

@test("B5. /get 下载文件内容一致")
def test_get_file():
    srv = ServerFixture()
    try:
        status, body = _http_get(srv.url("/get?partition=D&path=normal.txt"))
        eq(status, 200)
        eq(body, b"hello")
    finally:
        srv.close()

@test("B6. /get 请求不存在文件返回 404")
def test_get_missing():
    srv = ServerFixture()
    try:
        status = _http_status(srv.url("/get?partition=D&path=no_such.txt"))
        eq(status, 404, "不存在文件应 404")
    finally:
        srv.close()

@test("B7. /list 无效分区返回 400")
def test_list_invalid_partition():
    srv = ServerFixture()
    try:
        status = _http_status(srv.url("/list?partition=Z"))
        eq(status, 400, "无效分区应 400")
    finally:
        srv.close()

@test("B8. /batch_get 批量下载二进制格式正确")
def test_batch_get():
    srv = ServerFixture()
    try:
        body = json.dumps({"partition": "D",
                           "paths": ["normal.txt", "data.dat"]},
                          ensure_ascii=False).encode("utf-8")
        status, raw = _http_post(srv.url("/batch_get"), body)
        eq(status, 200)
        count = struct.unpack(">I", raw[:4])[0]
        eq(count, 2)
        pos = 4
        entries = {}
        for _ in range(count):
            plen = struct.unpack(">I", raw[pos:pos + 4])[0]; pos += 4
            p = raw[pos:pos + plen].decode("utf-8"); pos += plen
            dlen = struct.unpack(">Q", raw[pos:pos + 8])[0]; pos += 8
            d = raw[pos:pos + dlen]; pos += dlen
            entries[p] = d
        eq(entries.get("normal.txt"), b"hello")
        eq(entries.get("data.dat"), b"\x00\x01\x02" * 100)
    finally:
        srv.close()

@test("B9. 未知端点返回 404")
def test_unknown_endpoint():
    srv = ServerFixture()
    try:
        status = _http_status(srv.url("/nope"))
        eq(status, 404)
    finally:
        srv.close()

# ============================================================
# C. download_files 端到端传输
# ============================================================

@test("C1. download_files 端到端: 过滤 + 内容一致")
def test_download_files_e2e():
    from file_transfer import download_files
    srv = ServerFixture()
    target = tempfile.mkdtemp(prefix="ft_tgt_")
    logs = []
    try:
        success, files, bytes_done, errors = download_files(
            server_ip="127.0.0.1",
            partition_map={"D": target},
            log_callback=lambda m: logs.append(m),
            auth_code=srv.auth,
        )
        check(success, f"传输应成功, errors={errors}")
        # 应存在
        for name in ("normal.txt", "data.dat"):
            check(os.path.isfile(os.path.join(target, name)),
                  f"目标端缺少 {name}")
        check(os.path.isfile(os.path.join(target, "sub", "nested.dat")),
              "目标端缺少 sub/nested.dat")
        # 应不存在
        for name in ("NTUSER.DAT", "temp.tmp", "app.log",
                     "NTUSER.DAT.LOG1", "~$draft.docx", "pagefile.sys"):
            check(not os.path.exists(os.path.join(target, name)),
                  f"{name} 不应被复制")
        # 内容一致
        with open(os.path.join(target, "normal.txt"), "rb") as f:
            eq(f.read(), b"hello")
        with open(os.path.join(target, "data.dat"), "rb") as f:
            eq(f.read(), b"\x00\x01\x02" * 100)
    finally:
        srv.close()
        shutil.rmtree(target, ignore_errors=True)

# ============================================================
# D. verifier CSV 路径修复 (全角逗号)
# ============================================================

@test("D1. _normalize_csv_path 全角逗号还原为 ASCII")
def test_normalize_csv_path():
    from verifier import _normalize_csv_path
    eq(_normalize_csv_path(r"F:\学习\前处理，ED整理表.xls"),
       r"F:\学习\前处理,ED整理表.xls")
    eq(_normalize_csv_path(r"F:\a\b.txt"), r"F:\a\b.txt")
    eq(_normalize_csv_path(""), "")

@test("D2. _repair_csv_in_place 修复旧版 escape_csv 写坏的 CSV")
def test_repair_csv_in_place():
    import csv as _csv
    from verifier import _repair_csv_in_place
    with tempfile.TemporaryDirectory() as td:
        # 磁盘上真实存在的是半角逗号文件, CSV 里被旧 escape_csv 写成了全角
        sub = os.path.join(td, "学习")
        os.makedirs(sub, exist_ok=True)
        with open(os.path.join(sub, "前处理,ED整理表.xls"), "wb") as f:
            f.write(b"x" * 100)
        csv_path = os.path.join(td, "FullFilelist_DEF.csv")
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            f.write("FullPath,FileName,Size\n")
            f.write(r"F:\学习\前处理，ED整理表.xls,前处理，ED整理表.xls,100" + "\n")
            f.write(r"F:\正常\文件.txt,文件.txt,50" + "\n")
        n = _repair_csv_in_place(csv_path, partition_map={"F": td})
        eq(n, 1, "应修复 1 行 FullPath")
        with open(csv_path, encoding="utf-8-sig") as f:
            rows = list(_csv.reader(f))
        eq(rows[1][0], r"F:\学习\前处理,ED整理表.xls", "FullPath 应还原为半角逗号")
        eq(rows[2][0], r"F:\正常\文件.txt", "无逗号路径不受影响")


@test("D3. 文件名本身含全角逗号时绝不能被改写 (2026-09-09 回归)")
def test_repair_csv_keeps_fullwidth_comma():
    """回归: 磁盘真实文件名就是全角逗号, CSV 记录正确 —— 不得改成半角,
    否则校验会误判缺失并在重试下载时写出 0KB 垃圾文件。"""
    import csv as _csv
    from verifier import _repair_csv_in_place
    with tempfile.TemporaryDirectory() as td:
        sub = os.path.join(td, "学习")
        os.makedirs(sub, exist_ok=True)
        with open(os.path.join(sub, "前处理，ED整理表.xls"), "wb") as f:
            f.write(b"x" * 100)
        csv_path = os.path.join(td, "FullFilelist_DEF.csv")
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            f.write("FullPath,FileName,Size\n")
            f.write(r"F:\学习\前处理，ED整理表.xls,前处理，ED整理表.xls,100" + "\n")
        n = _repair_csv_in_place(csv_path, partition_map={"F": td})
        eq(n, 0, "全角逗号是真实文件名, 不应修改")
        with open(csv_path, encoding="utf-8-sig") as f:
            rows = list(_csv.reader(f))
        eq(rows[1][0], r"F:\学习\前处理，ED整理表.xls", "FullPath 应保持全角逗号")


@test("D4. 无盘符映射时不修改 CSV (安全兜底)")
def test_repair_csv_without_partition_map():
    import csv as _csv
    from verifier import _repair_csv_in_place
    with tempfile.TemporaryDirectory() as td:
        csv_path = os.path.join(td, "FullFilelist_DEF.csv")
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            f.write("FullPath,FileName,Size\n")
            f.write(r"F:\学习\前处理，ED整理表.xls,前处理，ED整理表.xls,100" + "\n")
        n = _repair_csv_in_place(csv_path)
        eq(n, 0, "无法验证磁盘存在性时不应修改")
        with open(csv_path, encoding="utf-8-sig") as f:
            rows = list(_csv.reader(f))
        eq(rows[1][0], r"F:\学习\前处理，ED整理表.xls", "CSV 应保持原样")

# ============================================================
# E. calc_allocation_migration 过滤 & 计算
# ============================================================

@test("E1. should_skip_file 规则 (含 ntuser.dat 大小写不敏感)")
def test_skip_file_rules():
    import systemconfig.calc_allocation_migration as cam
    eq(cam.should_skip_file("NTUSER.DAT"), True)
    eq(cam.should_skip_file("ntuser.dat"), True)
    eq(cam.should_skip_file("data.dat"), False, "微信 .dat 不应跳过")
    eq(cam.should_skip_file("x.tmp"), True)
    eq(cam.should_skip_file("x.log"), True)
    eq(cam.should_skip_file("~$a.docx"), True)
    eq(cam.should_skip_file("pagefile.sys"), True)
    eq(cam.should_skip_file("hiberfil.sys"), True)
    eq(cam.should_skip_file("normal.xlsx"), False)

@test("E2. should_skip_dir 规则")
def test_skip_dir_rules():
    import systemconfig.calc_allocation_migration as cam
    eq(cam.should_skip_dir("AppData"), True)
    eq(cam.should_skip_dir("$RECYCLE.BIN"), True)
    eq(cam.should_skip_dir("WeChat Files"), True)
    eq(cam.should_skip_dir("System Volume Information"), True)
    eq(cam.should_skip_dir("正常目录"), False)

@test("E3. escape_csv 不再替换逗号 (校验器路径一致)")
def test_escape_csv():
    import systemconfig.calc_allocation_migration as cam
    eq(cam.escape_csv("a,b"), "a,b", "逗号应保留")
    # 斜杠标准化 + 去尾部反斜杠; 文件名内部空格与尾部空格都保留
    # (尾部空格可能是真实文件名的一部分, 去掉会导致 CSV 路径与磁盘不一致)
    eq(cam.escape_csv(r"F:\a\b/ c "), r"F:\a\b\ c ",
       "斜杠标准化, 内部/尾部空格均保留")
    eq(cam.escape_csv("F:\\a\\b\\"), r"F:\a\b", "仅去除尾部反斜杠")

@test("E4. 分配单元对齐计算 (KB140365)")
def test_aligned_size():
    import systemconfig.calc_allocation_migration as cam
    eq(cam.aligned_size(100, 4096), 4096)
    eq(cam.aligned_size(4096, 4096), 4096)
    eq(cam.aligned_size(4097, 4096), 8192)
    eq(cam.aligned_size(0, 4096), 0)

# ============================================================
# F. tls_utils 验证码 & 证书
# ============================================================

@test("F1. 验证码生成: 4位且仅含安全字符集")
def test_auth_code():
    import tls_utils
    charset = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    for _ in range(20):
        c = tls_utils.generate_auth_code()
        eq(len(c), 4, "验证码应为 4 位")
        check(all(ch in charset for ch in c), f"验证码含非法字符: {c}")

@test("F2. 自签名证书生成")
def test_cert():
    import tls_utils
    cert, key = tls_utils.get_or_create_fixed_cert()
    check(os.path.isfile(cert), "证书文件应存在")
    check(os.path.isfile(key), "私钥文件应存在")
    info = tls_utils.cert_san_info(cert)
    check("127.0.0.1" in info or "localhost" in info or "SAN" in info,
          f"SAN 信息异常: {info}")

# ============================================================
# G. config_transfer ZIP 压缩
# ============================================================

@test("G1. 压缩命名格式 <COMPUTERNAME>_<日期>.zip")
def test_compress_naming():
    import config_transfer
    with tempfile.TemporaryDirectory() as td:
        export = os.path.join(td, "2026-08-11")
        os.makedirs(export)
        with open(os.path.join(export, "a.txt"), "w") as f:
            f.write("hi")
        old = os.environ.get("COMPUTERNAME")
        os.environ["COMPUTERNAME"] = "TESTPC"
        try:
            ok, zpath, _ = config_transfer.compress_and_upload_config(
                export, log_callback=lambda m: None)
            check(ok, "压缩应成功")
            check("TESTPC_2026-08-11.zip" in zpath,
                  f"ZIP 命名错误: {zpath}")
            check(os.path.isfile(zpath), "ZIP 应存在")
        finally:
            if old is None:
                os.environ.pop("COMPUTERNAME", None)
            else:
                os.environ["COMPUTERNAME"] = old

# ============================================================
# H. controller 网络断开检测 (2026-08-05 新增)
# ============================================================

class T:
    """controller 测试夹具 (需 UI 才能 init)"""
    def __init__(self):
        from ui import WinGUI
        from control import Controller
        self.app = WinGUI()
        self.app.report_callback_exception = lambda *a: None
        self.ctl = Controller()
        self.ctl.init(self.app)
        self.app.ctl = self.ctl
        self.app.update_idletasks()

    def destroy(self):
        try:
            for cb_id in self.app.tk.call('after', 'info'):
                try:
                    self.app.after_cancel(cb_id)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self.app.destroy()
        except Exception:
            pass

@test("H1. 网络监控线程生命周期 (本地回环不误判)")
def test_net_monitor_lifecycle():
    t = T()
    try:
        t.ctl._start_network_monitor("127.0.0.1")
        time.sleep(0.6)
        eq(t.ctl._network_down, False, "本地回环不应误判断网")
        t.ctl._stop_network_monitor()
        time.sleep(0.3)
    finally:
        t.destroy()

@test("H2. 断网时 download_files 的 stop_check 返回 'network_down'")
def test_stop_check_network_down():
    import control as _ctl_mod
    t = T()
    captured = {}
    orig_dl = _ctl_mod.download_files

    def fake_download_files(**kwargs):
        captured['stop_check'] = kwargs.get('stop_check')
        return True, 0, 0, []

    # control.py 是 `from file_transfer import download_files`, 需 patch control 模块内的引用
    _ctl_mod.download_files = fake_download_files
    try:
        # 测试无 mainloop: 后台线程的 after/_log 访问 Tk 控件会阻塞 → 替换为安全 no-op
        t.app.after = lambda *a, **kw: None
        t.ctl._log = lambda m: None
        t.app.tk_entry_code.delete(0, 'end')
        t.app.tk_entry_code.insert(0, "ABCD")
        t.ctl._start_target_download(manual_ip="127.0.0.1")
        deadline = time.time() + 3
        while 'stop_check' not in captured and time.time() < deadline:
            time.sleep(0.05)
            t.app.update_idletasks()
        sc = captured.get('stop_check')
        assert sc is not None, "stop_check 应被传入 download_files"
        eq(sc(), None, "正常状态返回 None")
        t.ctl._stop_transfer = True
        eq(sc(), "cancel", "取消时返回 cancel")
        t.ctl._stop_transfer = False
        t.ctl._network_down = True
        eq(sc(), "network_down", "断网时返回 network_down")
    finally:
        t.ctl._stop_transfer = False
        t.ctl._network_down = False
        _ctl_mod.download_files = orig_dl
        t.destroy()

@test("H3. _on_download_complete 断网分支显示错误提示")
def test_network_down_completion():
    t = T()
    try:
        t.ctl._network_down = True
        t.ctl._on_download_complete(False, 100, 0, [])
        t.app.update_idletasks()
        eq(t.ctl._network_down, False, "处理后应复位标志")
        check("网络连接已中断" in t.app.tk_label_transfer_error.cget("text"),
              "错误提示应含'网络连接已中断'")
    finally:
        t.destroy()

@test("H4. _sleep_interruptible 可被 stop_check 立即中断")
def test_sleep_interruptible():
    import file_transfer as ft
    t0 = time.time()
    try:
        ft._sleep_interruptible(5.0, stop_check=lambda: True)
        check(False, "应抛出 _TransferCancelled")
    except ft._TransferCancelled:
        pass
    elapsed = time.time() - t0
    check(elapsed < 1.0, f"应快速中断, 实际耗时 {elapsed:.2f}s")

@test("H5. 单文件重试: 断网时根本不再尝试下载")
def test_single_retry_stop_immediately():
    import file_transfer as ft
    calls = []

    def fake_download(*a, **kw):
        calls.append(1)
        raise ConnectionResetError("模拟断网")

    orig = ft._download_single_file
    ft._download_single_file = fake_download
    try:
        try:
            ft._download_single_file_with_retry(
                "https://127.0.0.1:9999", "D", "x.txt", 1,
                os.path.join(tempfile.gettempdir(), "x.txt"),
                stop_check=lambda: "network_down",
            )
            check(False, "应抛出 _TransferCancelled")
        except ft._TransferCancelled:
            pass
        eq(len(calls), 0, "断网时应根本不再尝试下载")
    finally:
        ft._download_single_file = orig

@test("H6. 单文件重试: 后退等待期间断网立即中止 (跳过 0.5s 后退)")
def test_single_retry_interrupt_during_backoff():
    import file_transfer as ft
    calls = []

    def fake_download(*a, **kw):
        calls.append(1)
        raise ConnectionResetError("模拟断网")

    orig = ft._download_single_file
    ft._download_single_file = fake_download
    state = {"n": 0}

    def sc():
        state["n"] += 1
        return "network_down" if state["n"] >= 3 else None

    try:
        t0 = time.time()
        try:
            ft._download_single_file_with_retry(
                "https://127.0.0.1:9999", "D", "x.txt", 1,
                os.path.join(tempfile.gettempdir(), "x.txt"),
                stop_check=sc, max_retries=5,
            )
            check(False, "应抛出 _TransferCancelled")
        except ft._TransferCancelled:
            pass
        elapsed = time.time() - t0
        eq(len(calls), 1, "应只尝试 1 次下载")
        check(elapsed < 1.0,
              f"断网时应跳过后退立即中止, 实际耗时 {elapsed:.2f}s")
    finally:
        ft._download_single_file = orig

@test("H7. 批次重试: 断网时根本不再尝试下载")
def test_batch_retry_stop_immediately():
    import file_transfer as ft
    calls = []

    def fake_batch(*a, **kw):
        calls.append(1)
        raise ConnectionResetError("模拟断网")

    orig = ft._download_batch
    ft._download_batch = fake_batch
    try:
        try:
            ft._download_batch_with_retry(
                "https://127.0.0.1:9999", "D",
                [("D", "a.txt", 1, os.path.join(tempfile.gettempdir(), "a.txt"), 0)],
                stop_check=lambda: "network_down",
            )
            check(False, "应抛出 _TransferCancelled")
        except ft._TransferCancelled:
            pass
        eq(len(calls), 0, "断网时应根本不再尝试下载")
    finally:
        ft._download_batch = orig

@test("H8. _host_reachable: 纯 TCP 探测, TCP 通即判定可达")
def test_host_reachable_tcp_ok():
    import socket as _s
    t = T()
    orig_conn = _s.create_connection

    class FakeSock:
        def close(self):
            pass

    # 模拟 TCP 9999 可连接 (防火墙已放行传输端口, 不依赖 ICMP)
    _s.create_connection = lambda *a, **kw: FakeSock()
    try:
        r = t.ctl._host_reachable("169.254.91.39")
        check(r is True, "TCP 通时应判定可达")
    finally:
        _s.create_connection = orig_conn
        t.destroy()

@test("H9. _host_reachable: TCP 连接失败即判定不可达")
def test_host_reachable_tcp_fail():
    import socket as _s
    t = T()
    orig_conn = _s.create_connection

    def refuse_conn(*a, **kw):
        raise ConnectionRefusedError("无服务器监听")

    _s.create_connection = refuse_conn
    try:
        r = t.ctl._host_reachable("169.254.91.39")
        check(r is False, "TCP 连不上应判定不可达")
    finally:
        _s.create_connection = orig_conn
        t.destroy()


# ============================================================
# 运行
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  功能测试全套 (30 项)")
    print("=" * 60)
    print()

    # A. 过滤规则
    test_dat_not_in_suffixes()
    test_filter_constants_aligned()

    # B. HTTP 端点
    test_ping_ok()
    test_ping_no_pwd()
    test_ping_wrong_pwd()
    test_list_filter()
    test_get_file()
    test_get_missing()
    test_list_invalid_partition()
    test_batch_get()
    test_unknown_endpoint()

    # C. 端到端
    test_download_files_e2e()

    # D. verifier
    test_normalize_csv_path()
    test_repair_csv_in_place()
    test_repair_csv_keeps_fullwidth_comma()
    test_repair_csv_without_partition_map()

    # E. calc_allocation_migration
    test_skip_file_rules()
    test_skip_dir_rules()
    test_escape_csv()
    test_aligned_size()

    # F. tls_utils
    test_auth_code()
    test_cert()

    # G. config_transfer
    test_compress_naming()

    # H. 网络断开检测 (含断网立即中止重试)
    test_net_monitor_lifecycle()
    test_stop_check_network_down()
    test_network_down_completion()
    test_sleep_interruptible()
    test_single_retry_stop_immediately()
    test_single_retry_interrupt_during_backoff()
    test_batch_retry_stop_immediately()
    test_host_reachable_tcp_ok()
    test_host_reachable_tcp_fail()

    print()
    print("=" * 60)
    print("  结果汇总")
    print("=" * 60)
    for r in results:
        print(r)

    total = len(results)
    failed = len(errors)
    passed = total - failed
    print()
    print(f"  总计: {total}  通过: {passed}  失败: {failed}")
    if failed:
        print()
        print("  失败详情:")
        for e in errors:
            print(f"    {e}")
        sys.exit(1)
    else:
        print("  全部通过!")
    print()
