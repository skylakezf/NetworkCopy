"""
普通用户场景测试 — 对应 测试用例.md 中的 TC 用例
参照 _test_ui_full.py 的框架, 将人工验收用例转化为可执行的 UI 自动化测试。

重点覆盖:
  TC-02 不导出配置直接传输
  TC-03 传输中断网后恢复 (重新接收)
  TC-04 验证码输错重连
  TC-05 网络不通提示
  TC-06 同名文件冲突 (保留/覆盖对话框)
  TC-07 跳过导入配置
  TC-08 跳过校验 (空包上传)
  TC-10 关闭程序干净退出 (未校验时发送跳过信息)

运行: python-3.13.14-embed-amd64/python.exe _test_user_cases.py
"""
import sys, os, threading, traceback, time as _time

os.chdir(r'c:\Users\Xinyi\Desktop\网络拷贝\NetworkzCopy')
sys.path.insert(0, '.')
sys.path.insert(0, os.path.join(os.getcwd(), 'python-3.13.14-embed-amd64', 'Lib', 'site-packages'))

# ============================================================
# 导入被测模块
# ============================================================
from ui import WinGUI
from control import Controller
import control as ctl_mod
import tkinter.messagebox

# ============================================================
# 测试框架
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
# 测试辅助
# ============================================================

class T:
    """测试 fixture (与 _test_ui_full.py 一致)"""
    def __init__(self):
        self.app = WinGUI()
        self.app.report_callback_exception = lambda *a: None
        self.ctl = Controller()
        self.ctl.init(self.app)
        self.app.ctl = self.ctl
        self.app.update_idletasks()

    @property
    def step(self):
        return self.app._step

    @property
    def next_state(self):
        try:
            return str(self.app.tk_button_next.cget("state"))
        except:
            return "ERROR"

    @property
    def prev_state(self):
        try:
            return str(self.app.tk_button_prev.cget("state"))
        except:
            return "ERROR"

    @property
    def next_text(self):
        try:
            return self.app.tk_button_next.cget("text")
        except:
            return "ERROR"

    @property
    def prev_text(self):
        try:
            return self.app.tk_button_prev.cget("text")
        except:
            return "ERROR"

    @property
    def start_text(self):
        """开始传输/接收按钮文字"""
        try:
            return self.app.tk_button_mqfzl35t.cget("text")
        except:
            return "ERROR"

    @property
    def start_state(self):
        try:
            return str(self.app.tk_button_mqfzl35t.cget("state"))
        except:
            return "ERROR"

    def select_role(self, role):
        self.app._on_select_role(role)
        self.app.update_idletasks()

    def set_nic(self, name="Test NIC [Realtek]"):
        # 网卡已改为全自动检测 (不再有手动选择下拉框)。
        # 此方法仅用于测试中模拟自动网卡选择结果。
        self.ctl._auto_nic = (name, "Realtek PCIe GbE Family Controller",
                              "ethernet0", "1Gbps", 1, "169.254.100.2")
        self.ctl._nic_list = [self.ctl._auto_nic]
        self.app.update_idletasks()

    def set_disk(self, name="磁盘 0 (ST1000DM010)"):
        cb = self.app.tk_select_box_mqfzmzbe
        cb.set(name)
        if hasattr(self.ctl, '_on_disk_selected'):
            self.ctl._on_disk_selected()
        self.app.update_idletasks()

    def next_step(self):
        # 发送方步骤1前进会触发"未导出配置"提醒弹窗 → 测试中模拟已导出
        if self.app._step == 1 and getattr(self.ctl, '_device_type', '') == "源设备":
            self.ctl._config_export_done = True
        self.ctl._on_next_step()
        self.app.update_idletasks()

    def raw_next_step(self):
        """不做任何模拟, 原样调用 _on_next_step (用于测试未导出配置弹窗)"""
        self.ctl._on_next_step()
        self.app.update_idletasks()

    def prev_step(self):
        self.ctl._on_prev_step()
        self.app.update_idletasks()

    def go_step(self, s):
        self.app.go_step(s)
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
        except:
            pass

    # ---- 用于捕获 ui.after 的辅助 (无 mainloop 时驱动后台线程的 after 回调) ----
    def capture_after(self):
        """把 app.after 替换为收集回调, 返回 (pending, restore)。"""
        pending = []
        orig = self.app.after
        def _fake_after(delay, cb, *a, **kw):
            pending.append((cb, a, kw))
            return "x"
        self.app.after = _fake_after
        def restore():
            self.app.after = orig
        return pending, restore

    def pump_after(self, pending):
        for cb, a, kw in list(pending):
            try:
                cb(*a, **kw)
            except Exception:
                pass


def make_app():
    return T()

# ============================================================
# TC-02 不导出配置直接传输
# ============================================================

@test("TC-02a. 未导出配置点'下一步'弹窗, 选'否'不前进")
def test_no_export_ask_no():
    t = make_app()
    _orig = tkinter.messagebox.askyesno
    try:
        t.select_role("source")
        t.set_nic()  # 使下一步可用
        eq(t.next_state, "normal")
        tkinter.messagebox.askyesno = lambda *a, **kw: False
        t.raw_next_step()
        eq(t.step, 1, "选'否'应停留在步骤1")
        eq(t.ctl._config_export_done, False, "未导出标记不应被设置")
    finally:
        tkinter.messagebox.askyesno = _orig
        t.destroy()

@test("TC-02b. 未导出配置点'下一步'弹窗, 选'是'继续前进")
def test_no_export_ask_yes():
    t = make_app()
    _orig = tkinter.messagebox.askyesno
    try:
        t.select_role("source")
        t.set_nic()
        tkinter.messagebox.askyesno = lambda *a, **kw: True
        t.raw_next_step()
        eq(t.step, 2, "选'是'应进入步骤2")
    finally:
        tkinter.messagebox.askyesno = _orig
        t.destroy()

@test("TC-02c. 已导出配置后点'下一步'不再弹窗")
def test_export_done_no_prompt():
    t = make_app()
    called = {"n": 0}
    _orig = tkinter.messagebox.askyesno
    try:
        t.select_role("source")
        t.set_nic()
        t.ctl._config_export_done = True  # 模拟已导出
        tkinter.messagebox.askyesno = lambda *a, **kw: called.__setitem__("n", called["n"] + 1) or True
        t.raw_next_step()
        eq(t.step, 2, "应正常进入步骤2")
        eq(called["n"], 0, "已导出时不应弹窗")
    finally:
        tkinter.messagebox.askyesno = _orig
        t.destroy()

@test("TC-02d. 接收端传输完成无配置 → 自动到导入页并显示'未检测到配置备份'")
def test_no_config_auto_step5():
    t = make_app()
    import config_transfer
    _orig_ini = config_transfer.get_config_from_ini
    _orig_find = config_transfer.find_config_folders
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.ctl._transfer_done = True
        t.app.go_step(4)
        # 模拟无配置: systemconfig.ini 无记录 + 未检测到备份文件夹
        config_transfer.get_config_from_ini = lambda: (None, None)
        config_transfer.find_config_folders = lambda: []
        t.ctl._on_download_complete(True, 10, 1024 * 1024, [])
        eq(t.step, 5, "无配置时应自动跳转到步骤5导入页")
        values = list(t.app.tk_combo_config_folder["values"])
        check("未检测到配置备份" in values, f"下拉应显示'未检测到配置备份', 实际={values}")
        check("未在 F:\\Appl\\" in t.app.tk_label_config_status.cget("text"),
              "状态文字应提示未检测到配置备份")
    finally:
        config_transfer.get_config_from_ini = _orig_ini
        config_transfer.find_config_folders = _orig_find
        t.destroy()

# ============================================================
# TC-03 传输中断网后恢复
# ============================================================

@test("TC-03a. 断网立即提示 + '重新接收'按钮激活")
def test_network_lost_ui():
    t = make_app()
    try:
        t.select_role("target")
        t.ctl._on_network_lost_ui()
        t.app.update_idletasks()
        check("网络连接已断开" in t.app.tk_label_transfer_error.cget("text"),
              "错误条应含'网络连接已断开'")
        eq(t.start_text, "重新接收", "按钮应变为'重新接收'")
        eq(t.start_state, "normal", "重新接收按钮应可点击")
    finally:
        t.destroy()

@test("TC-03b. 断网后回到步骤3: 下一步禁用 + 重新接收激活")
def test_network_down_back_to_step3():
    t = make_app()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        # 模拟断网失败后的状态: 不在传输中, 未完成
        t.ctl._transferring = False
        t.ctl._transfer_done = False
        t.app.go_step(4)
        t.prev_step()  # → step3
        eq(t.step, 3, "应回到步骤3连接页")
        eq(t.next_state, "disabled", "连接页'下一步'应禁用")
        eq(t.start_text, "重新接收", "按钮应为'重新接收'")
        eq(t.start_state, "normal", "'重新接收'应可点击")
    finally:
        t.destroy()

@test("TC-03c. 断网恢复后重新接收: 验证码输入框有值则直接复用, 无需再弹窗")
def test_retry_reuse_auth_code():
    import control as _c
    t = make_app()
    captured = {}
    _orig_dl = _c.download_files
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        # 预填验证码输入框
        t.app.tk_entry_code.delete(0, "end")
        t.app.tk_entry_code.insert(0, "ABCD")
        # 阻止真实网络调用
        _c.download_files = lambda **kw: (True, 5, 1024, [])
        t.app.after = lambda *a, **kw: None  # 后台线程的 after 回调 no-op
        t.ctl._use_dhcp = True
        # 伪造 DHCP 服务器运行中, 避免真实 UDP
        fake_dhcp = type("FakeDHCP", (), {"is_running": lambda self: True, "stop": lambda self: None})()
        t.ctl._dhcp_server = fake_dhcp
        t.ctl._start_target_download()
        # _start_target_download 中会立即走 _auth_code = 输入框值, 无需弹窗
        eq(t.ctl._auth_code, "ABCD", "应复用输入框验证码")
        eq(t.ctl._transferring, True, "传输应已开始")
    finally:
        t.ctl._stop_transfer = True
        _c.download_files = _orig_dl
        t.destroy()

# ============================================================
# TC-04 / TC-05 验证码错误 & 网络不通
# ============================================================

@test("TC-04. 验证码错误: 红色提示 + '重试接收' + '< 返回修改验证码'")
def test_wrong_auth_code():
    t = make_app()
    try:
        t.select_role("target")
        t.ctl._on_download_complete(False, 0, 0, [])
        t.app.update_idletasks()
        check("验证码错误" in t.app.tk_label_transfer_error.cget("text"),
              "错误条应含'验证码错误'")
        eq(t.prev_text, "< 返回修改验证码", "上一步按钮文字应引导修改验证码")
        eq(t.start_text, "重试接收", "按钮应变为'重试接收'")
        eq(t.start_state, "normal", "'重试接收'应可点击")
    finally:
        t.destroy()

@test("TC-05. 网络不通: 发送端连接失败提示")
def test_conn_fail_source():
    t = make_app()
    try:
        t.select_role("source")
        t.ctl._on_download_complete(False, 0, 0, [])
        t.app.update_idletasks()
        check("连接失败" in t.app.tk_label_transfer_error.cget("text"),
              "错误条应含'连接失败'")
        eq(t.start_text, "重试接收", "发送端按钮也应可重试")
    finally:
        t.destroy()

# ============================================================
# TC-06 同名文件冲突对话框
# ============================================================

@test("TC-06a. 无冲突时 _resolve_conflicts 直接返回空集")
def test_conflict_empty():
    t = make_app()
    try:
        t.select_role("target")
        result = t.ctl._resolve_conflicts([], lambda m: None)
        eq(result, set(), "无冲突应返回空集")
    finally:
        t.destroy()

@test("TC-06b. 冲突对话框: 点击'保留已有文件'返回保留集合")
def test_conflict_dialog_keep():
    t = make_app()
    # 用假控件替换 tkinter 控件类, 避免真实弹窗阻塞
    fake_kw_capture = {"buttons": []}

    class _FakeWidget:
        def __init__(self, master=None, **kw):
            self._kw = kw
            self.command = kw.get("command")
            if kw.get("text"):
                fake_kw_capture["buttons"].append(self)
        def pack(self, *a, **kw): pass
        def pack_forget(self): pass
        def config(self, **kw): pass
        def configure(self, **kw): pass
        def cget(self, name):
            return self._kw.get(name, "")
        def bind(self, *a, **kw): pass
        def bind_all(self, *a, **kw): pass
        def unbind_all(self, *a, **kw): pass
        def destroy(self): pass
        def title(self, *a): pass
        def geometry(self, *a): pass
        def resizable(self, *a): pass
        def transient(self, *a): pass
        def grab_set(self): pass
        def protocol(self, *a): pass
        def after(self, *a, **kw): return 1
        def yview(self, *a): pass
        def yview_scroll(self, *a, **kw): pass
        def set(self, *a): pass
        def create_window(self, *a, **kw): return 1
        def bbox(self, *a): return (0, 0, 1, 1)
        def winfo_ismapped(self): return False
        def invoke(self):
            if self.command:
                self.command()

    saved = {}
    try:
        for name in ("Toplevel", "Label", "Frame", "Canvas", "Scrollbar", "Button"):
            saved[name] = getattr(ctl_mod.tk, name)
            setattr(ctl_mod.tk, name, _FakeWidget)

        conflicts = [
            {"rel_path": "test\\a.txt", "target_path": "D:\\test\\a.txt",
             "src_size": 100, "dst_size": 200},
            {"rel_path": "test\\b.docx", "target_path": "D:\\test\\b.docx",
             "src_size": 500, "dst_size": 400},
        ]
        pending, restore = t.capture_after()
        result_box = {}
        def worker():
            result_box["r"] = t.ctl._resolve_conflicts(conflicts, lambda m: None)
        th = threading.Thread(target=worker, daemon=True)
        th.start()
        # 无 mainloop: 手动驱动 after 回调 (创建对话框)
        _time.sleep(0.1)
        t.pump_after(pending)
        # 找到"保留已有文件"按钮并模拟点击
        keep_btn = None
        for b in fake_kw_capture["buttons"]:
            if "保留已有文件" in str(b.cget("text")):
                keep_btn = b
        check(keep_btn is not None, "应有'保留已有文件'按钮")
        if keep_btn:
            keep_btn.invoke()
        th.join(timeout=2)
        restore()
        check("r" in result_box, "worker 线程应返回")
        eq(result_box["r"], {"D:\\test\\a.txt", "D:\\test\\b.docx"},
           "选择保留应返回全部 target_path 集合")
    finally:
        for name, orig in saved.items():
            setattr(ctl_mod.tk, name, orig)
        t.destroy()

@test("TC-06c. 冲突对话框: 点击'覆盖重新下载'返回空集")
def test_conflict_dialog_overwrite():
    t = make_app()
    fake_kw_capture = {"buttons": []}

    class _FakeWidget:
        def __init__(self, master=None, **kw):
            self._kw = kw
            self.command = kw.get("command")
            if kw.get("text"):
                fake_kw_capture["buttons"].append(self)
        def pack(self, *a, **kw): pass
        def pack_forget(self): pass
        def config(self, **kw): pass
        def configure(self, **kw): pass
        def cget(self, name):
            return self._kw.get(name, "")
        def bind(self, *a, **kw): pass
        def bind_all(self, *a, **kw): pass
        def unbind_all(self, *a, **kw): pass
        def destroy(self): pass
        def title(self, *a): pass
        def geometry(self, *a): pass
        def resizable(self, *a): pass
        def transient(self, *a): pass
        def grab_set(self): pass
        def protocol(self, *a): pass
        def after(self, *a, **kw): return 1
        def yview(self, *a): pass
        def yview_scroll(self, *a, **kw): pass
        def set(self, *a): pass
        def create_window(self, *a, **kw): return 1
        def bbox(self, *a): return (0, 0, 1, 1)
        def winfo_ismapped(self): return False
        def invoke(self):
            if self.command:
                self.command()

    saved = {}
    try:
        for name in ("Toplevel", "Label", "Frame", "Canvas", "Scrollbar", "Button"):
            saved[name] = getattr(ctl_mod.tk, name)
            setattr(ctl_mod.tk, name, _FakeWidget)

        conflicts = [
            {"rel_path": "test\\a.txt", "target_path": "D:\\test\\a.txt",
             "src_size": 100, "dst_size": 200},
        ]
        pending, restore = t.capture_after()
        result_box = {}
        def worker():
            result_box["r"] = t.ctl._resolve_conflicts(conflicts, lambda m: None)
        th = threading.Thread(target=worker, daemon=True)
        th.start()
        _time.sleep(0.1)
        t.pump_after(pending)
        overwrite_btn = None
        for b in fake_kw_capture["buttons"]:
            if "覆盖重新下载" in str(b.cget("text")):
                overwrite_btn = b
        check(overwrite_btn is not None, "应有'覆盖重新下载'按钮")
        if overwrite_btn:
            overwrite_btn.invoke()
        th.join(timeout=2)
        restore()
        check("r" in result_box, "worker 线程应返回")
        eq(result_box["r"], set(), "选择覆盖应返回空集(全部重新下载)")
    finally:
        for name, orig in saved.items():
            setattr(ctl_mod.tk, name, orig)
        t.destroy()

# ============================================================
# TC-07 跳过导入配置
# ============================================================

@test("TC-07. 跳过导入 → 进入步骤6, 下一步为'跳过校验 >'")
def test_skip_import():
    t = make_app()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.ctl._transfer_done = True
        t.app.go_step(4)
        t.next_step()  # →5
        eq(t.step, 5)
        t.ctl._on_skip_import()
        eq(t.step, 6, "跳过导入应进入步骤6校验页")
        eq(t.next_text, "跳过校验 >")
        eq(t.next_state, "normal")
    finally:
        t.destroy()

@test("TC-07b. 导入成功后'跳过'被禁用 (导入完成防护)")
def test_import_done_skip_disabled():
    t = make_app()
    try:
        t.select_role("target")
        t.ctl._on_import_done(True)  # 模拟导入成功
        eq(t.ctl._config_import_done, True, "导入成功标记应置位")
        eq(str(t.app.tk_button_skip_import.cget("state")), "disabled",
           "导入成功后'跳过'按钮应禁用")
        # 兜底防护: 即使强行调用也不跳
        step_before = t.step
        t.ctl._on_skip_import()
        eq(t.step, step_before, "导入完成后不应再跳过")
    finally:
        t.destroy()

@test("TC-07c. 导入失败后'跳过'恢复可用")
def test_import_fail_skip_normal():
    t = make_app()
    try:
        t.select_role("target")
        t.ctl._on_import_done(False)  # 模拟导入失败
        eq(str(t.app.tk_button_skip_import.cget("state")), "normal",
           "导入失败后'跳过'按钮应恢复可用")
    finally:
        t.destroy()

# ============================================================
# TC-08 跳过校验
# ============================================================

@test("TC-08. 跳过校验: 创建空 Unverifi 包并上传")
def test_skip_verify():
    import verifier as _v
    import config_transfer as _ct
    t = make_app()
    calls = {"create": False, "upload": False}
    _orig_create = _v.create_unverifi_zip
    _orig_upload = _ct.upload_zip_to_server
    try:
        t.select_role("target")
        t.ctl._partition_map = {"F": "F:\\"}
        def _fake_create(log_callback=None, save_dir=""):
            calls["create"] = True
            calls["save_dir"] = save_dir
            return True, "F:\\Appl\\TESTPC_Unverifi.zip"
        def _fake_upload(zip_path, log_callback=None):
            calls["upload"] = True
            calls["zip"] = zip_path
            return True, None
        _v.create_unverifi_zip = _fake_create
        _ct.upload_zip_to_server = _fake_upload

        pending, restore = t.capture_after()
        t.ctl._on_skip_verify()
        # 后台线程执行, 主线程驱动 after 回调
        deadline = _time.time() + 3
        while (not calls["upload"]) and _time.time() < deadline:
            _time.sleep(0.05)
            t.pump_after(pending)
        check(calls["create"], "应创建空校验报告")
        check(calls["upload"], "应上传空校验报告")
        check("Unverifi" in calls.get("zip", ""), f"上传应为 Unverifi 包, 实际={calls.get('zip')}")
        check("Appl" in calls.get("save_dir", ""), f"空包保存目录应为 F:\\Appl, 实际={calls.get('save_dir')}")
        eq(str(t.app.tk_button_next.cget("state")), "disabled", "跳过校验后'下一步'应禁用")
        restore()
    finally:
        _v.create_unverifi_zip = _orig_create
        _ct.upload_zip_to_server = _orig_upload
        t.destroy()

@test("TC-08b. 校验进行中不允许跳过")
def test_skip_verify_busy():
    import verifier as _v
    t = make_app()
    _orig_create = _v.create_unverifi_zip
    called = {"n": 0}
    try:
        t.select_role("target")
        class _AliveThread:
            def is_alive(self):
                return True
        t.ctl._verify_thread = _AliveThread()
        def _fake_create(log_callback=None, save_dir=""):
            called["n"] += 1
            return True, "x.zip"
        _v.create_unverifi_zip = _fake_create
        t.ctl._on_skip_verify()
        eq(called["n"], 0, "校验进行中不应创建空包")
    finally:
        _v.create_unverifi_zip = _orig_create
        t.destroy()

# ============================================================
# TC-10 关闭程序干净退出
# ============================================================

@test("TC-10a. shutdown 幂等, 无异常")
def test_shutdown_idempotent():
    t = make_app()
    try:
        t.select_role("target")
        t.ctl.shutdown()  # 首次
        t.ctl.shutdown()  # 再次, 应幂等
        eq(t.ctl._stop_transfer, True, "停止标志应置位")
    finally:
        t.destroy()

@test("TC-10b. 校验页未完成校验直接关闭 → 发送跳过校验信息")
def test_send_skip_on_close():
    import verifier as _v
    import config_transfer as _ct
    t = make_app()
    calls = {"create": False, "upload": False}
    _orig_create = _v.create_unverifi_zip
    _orig_upload = _ct.upload_zip_to_server
    try:
        t.select_role("target")
        t.ctl._partition_map = {"F": "F:\\"}
        t.app.go_step(6)  # 处于校验页
        t.ctl._verify_done = False
        t.ctl._send_skip_verify_done = False
        def _fake_create(log_callback=None, save_dir=""):
            calls["create"] = True
            return True, "F:\\Appl\\TESTPC_Unverifi.zip"
        def _fake_upload(zip_path, log_callback=None):
            calls["upload"] = True
            return True, None
        _v.create_unverifi_zip = _fake_create
        _ct.upload_zip_to_server = _fake_upload
        t.ctl._send_skip_verify_on_close()
        check(calls["create"], "关闭时应创建空校验报告")
        check(calls["upload"], "关闭时应上传空校验报告")
        eq(t.ctl._send_skip_verify_done, True, "跳过信息发送标记应置位")
    finally:
        _v.create_unverifi_zip = _orig_create
        _ct.upload_zip_to_server = _orig_upload
        t.destroy()

@test("TC-10c. 非校验页关闭不发送跳过信息")
def test_send_skip_on_close_guard():
    import verifier as _v
    t = make_app()
    _orig_create = _v.create_unverifi_zip
    called = {"n": 0}
    try:
        t.select_role("target")
        t.app.go_step(3)  # 不在校验页
        def _fake_create(log_callback=None, save_dir=""):
            called["n"] += 1
            return True, "x.zip"
        _v.create_unverifi_zip = _fake_create
        t.ctl._send_skip_verify_on_close()
        eq(called["n"], 0, "非校验页关闭不应创建空包")
    finally:
        _v.create_unverifi_zip = _orig_create
        t.destroy()

@test("TC-10d. 已正常完成校验后关闭不再发送")
def test_send_skip_on_close_verify_done():
    import verifier as _v
    t = make_app()
    _orig_create = _v.create_unverifi_zip
    called = {"n": 0}
    try:
        t.select_role("target")
        t.app.go_step(6)
        t.ctl._verify_done = True  # 已校验完成
        def _fake_create(log_callback=None, save_dir=""):
            called["n"] += 1
            return True, "x.zip"
        _v.create_unverifi_zip = _fake_create
        t.ctl._send_skip_verify_on_close()
        eq(called["n"], 0, "校验已完成时不应再创建空包")
    finally:
        _v.create_unverifi_zip = _orig_create
        t.destroy()

# ============================================================
# 运行
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  普通用户场景测试 (测试用例.md 的 TC 用例)")
    print("=" * 60)
    print()

    test_no_export_ask_no()
    test_no_export_ask_yes()
    test_export_done_no_prompt()
    test_no_config_auto_step5()
    test_network_lost_ui()
    test_network_down_back_to_step3()
    test_retry_reuse_auth_code()
    test_wrong_auth_code()
    test_conn_fail_source()
    test_conflict_empty()
    test_conflict_dialog_keep()
    test_conflict_dialog_overwrite()
    test_skip_import()
    test_import_done_skip_disabled()
    test_import_fail_skip_normal()
    test_skip_verify()
    test_skip_verify_busy()
    test_shutdown_idempotent()
    test_send_skip_on_close()
    test_send_skip_on_close_guard()
    test_send_skip_on_close_verify_done()

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
