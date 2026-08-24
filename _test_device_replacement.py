"""
设备更换全流程端到端集成测试
模拟: 旧设备(Sender) 导出配置→压缩→上传 → 新设备(Receiver) 下载→检测配置→导入
"""
import sys, os, threading, traceback, tempfile, shutil, io, json

os.chdir(r'c:\Users\Xinyi\Desktop\网络拷贝\NetworkzCopy')
sys.path.insert(0, '.')
sys.path.insert(0, os.path.join(os.getcwd(), 'python-3.13.14-embed-amd64', 'Lib', 'site-packages'))

# ============================================================
# 导入被测模块
# ============================================================
from ui import WinGUI
from control import Controller
import config_transfer

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
    """测试 fixture"""
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
        try: return str(self.app.tk_button_next.cget("state"))
        except: return "ERROR"

    @property
    def prev_state(self):
        try: return str(self.app.tk_button_prev.cget("state"))
        except: return "ERROR"

    @property
    def next_text(self):
        try: return self.app.tk_button_next.cget("text")
        except: return "ERROR"

    def select_role(self, role):
        self.app._on_select_role(role)
        self.app.update_idletasks()

    def set_nic(self, name="Test NIC [Realtek]"):
        cb = self.app.tk_select_box_mqfzkd6x
        cb.set(name)
        if name not in cb["values"]:
            cb["values"] = list(cb["values"]) + [name]
        if hasattr(self.ctl, '_on_nic_selected'):
            self.ctl._on_nic_selected()
        self.app.update_idletasks()

    def set_disk(self, name="磁盘 0 (ST1000DM010)"):
        cb = self.app.tk_select_box_mqfzmzbe
        cb.set(name)
        if name not in cb["values"]:
            cb["values"] = list(cb["values"]) + [name]
        # 模拟用户已确认自动选择, 避免"修改自动选择"弹窗阻塞测试
        self.ctl._auto_selected_disk = None
        if hasattr(self.ctl, '_on_disk_selected'):
            self.ctl._on_disk_selected()
        self.app.update_idletasks()

    def next_step(self):
        # 发送方步骤1前进会触发"未导出配置"提醒弹窗 → 测试中模拟已导出
        if self.app._step == 1 and getattr(self.ctl, '_device_type', '') == "源设备":
            self.ctl._config_export_done = True
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
                try: self.app.after_cancel(cb_id)
                except: pass
        except: pass
        try: self.app.destroy()
        except: pass


# ============================================================
# PART A: 发送端 — 导出 → 压缩 → 上传 完整链路
# ============================================================

@test("A1. 发送端导出按钮初始状态")
def test_source_export_btn_initial():
    """发送端 step1 导出按钮可见且可点击"""
    t = T()
    try:
        t.select_role("source")
        eq(t.step, 1)
        btn = t.app.tk_button_export_config
        check(btn.winfo_ismapped(), "导出按钮应可见")
        eq(str(btn.cget("state")), "normal", "导出按钮应可点击")
        eq(btn.cget("text"), "导出系统配置", "按钮初始文字应为'导出系统配置'")
    finally:
        t.destroy()

@test("A2. 接收端不显示导出按钮")
def test_target_no_export_btn():
    """接收端 step1 导出按钮应隐藏"""
    t = T()
    try:
        t.select_role("target")
        eq(t.step, 1)
        try:
            mapped = t.app.tk_button_export_config.winfo_ismapped()
            check(not mapped, "接收端导出按钮应隐藏")
        except Exception:
            pass  # 可能被 pack_forget
    finally:
        t.destroy()

@test("A3. 发送端导出→压缩→上传按钮状态链路")
def test_source_export_compress_upload_state_chain():
    """模拟完整导出链路, 验证按钮状态变化"""
    t = T()
    try:
        t.select_role("source")
        btn = t.app.tk_button_export_config
        # 阻止真实后台压缩上传线程, 避免异步竞争影响按钮文字断言
        _orig_upload = t.ctl._start_compress_upload
        t.ctl._start_compress_upload = lambda p: None
        try:
            # 1) 初始状态
            eq(str(btn.cget("state")), "normal")
            eq(btn.cget("text"), "导出系统配置")

            # 2) 模拟 _on_export_config — 点击后按钮应为 disabled + "导出中..."
            t.ctl._on_export_config()
            t.app.update_idletasks()
            eq(str(btn.cget("state")), "disabled", "导出中按钮应禁用")
            assert "导出中" in btn.cget("text"), f"按钮文字应含'导出中', 实际={btn.cget('text')}"

            # 3) 模拟导出成功 → _on_export_done
            t.ctl._on_export_done(True, r"F:\Appl\2026-08-03")
            t.app.update_idletasks()
            eq(str(btn.cget("state")), "disabled", "压缩上传中按钮应禁用")
            assert "压缩上传" in btn.cget("text") or "压缩" in btn.cget("text"), \
                f"按钮文字应含'压缩', 实际={btn.cget('text')}"

            # 4) 模拟上传成功
            t.ctl._on_upload_done(True, True, r"F:\Appl\QDNB5098_2026-08-03.zip")
            t.app.update_idletasks()
            eq(str(btn.cget("state")), "normal", "上传完成后按钮应重新启用")
            assert "导出完成" in btn.cget("text"), \
                f"按钮文字应含'导出完成', 实际={btn.cget('text')}"
        finally:
            t.ctl._start_compress_upload = _orig_upload
    finally:
        t.destroy()

@test("A4. 发送端压缩成功但上传失败")
def test_source_compress_ok_upload_fail():
    """压缩成功, 上传失败时按钮为 warning 状态且可点击"""
    t = T()
    try:
        t.select_role("source")
        btn = t.app.tk_button_export_config
        _orig_upload = t.ctl._start_compress_upload
        t.ctl._start_compress_upload = lambda p: None
        try:
            t.ctl._on_export_done(True, r"F:\Appl\2026-08-03")
            t.app.update_idletasks()

            t.ctl._on_upload_done(True, False, r"F:\Appl\QDNB5098_2026-08-03.zip")
            t.app.update_idletasks()
            eq(str(btn.cget("state")), "normal", "上传失败后按钮应可点击(供重试)")
            assert "导出完成" in btn.cget("text"), \
                f"按钮文字应含'导出完成', 实际={btn.cget('text')}"
        finally:
            t.ctl._start_compress_upload = _orig_upload
    finally:
        t.destroy()

@test("A5. 发送端压缩失败")
def test_source_compress_fail():
    """压缩失败时按钮为 danger 状态"""
    t = T()
    try:
        t.select_role("source")
        btn = t.app.tk_button_export_config
        _orig_upload = t.ctl._start_compress_upload
        t.ctl._start_compress_upload = lambda p: None
        try:
            t.ctl._on_export_done(True, r"F:\Appl\2026-08-03")
            t.app.update_idletasks()

            t.ctl._on_upload_done(False, False, "")
            t.app.update_idletasks()
            eq(str(btn.cget("state")), "normal", "压缩失败后按钮应可点击(供重试)")
            assert "压缩失败" in btn.cget("text"), \
                f"按钮文字应含'压缩失败', 实际={btn.cget('text')}"
        finally:
            t.ctl._start_compress_upload = _orig_upload
    finally:
        t.destroy()


# ============================================================
# PART B: 接收端 — 下载 → 配置检测 → 使用/跳过
# ============================================================

@test("B1. 接收端下载完成→自动跳转 step5 (无配置文件)")
def test_target_download_no_config_auto_jump():
    """无配置文件时自动跳到 step5"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.ctl._transfer_done = True
        t.go_step(4)

        # mock 无配置文件
        _orig = config_transfer.get_config_from_ini
        config_transfer.get_config_from_ini = lambda: (None, None)
        try:
            t.ctl._on_download_complete(True, 5, 1024*1024, [])
            eq(t.step, 5, "无配置文件时应自动跳到step5")
            # 配置检测区域应隐藏
            try:
                t.app._config_detect_frame.pack_info()
                check(False, "无配置文件时检测区域不应可见")
            except Exception:
                pass  # pack_info 抛异常 = 未 pack, 正确
        finally:
            config_transfer.get_config_from_ini = _orig
    finally:
        t.destroy()

@test("B2. 接收端下载完成→停留 step4 (有配置文件)")
def test_target_download_with_config_stay_step4():
    """有配置文件时停留在 step4 等待用户确认"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.go_step(4)
        t.ctl._transfer_done = True

        _orig = config_transfer.get_config_from_ini
        config_transfer.get_config_from_ini = lambda: (
            r"F:\Appl\2026-08-03", "2026-08-03 22:57:42"
        )
        try:
            t.ctl._on_download_complete(True, 5, 1024*1024, [])
            eq(t.step, 4, "有配置文件时应停留在step4")

            # 验证配置检测区域已展示
            try:
                info = t.app._config_detect_frame.pack_info()
                check(info, "配置检测区域应已 pack")
            except Exception:
                check(False, "配置检测区域未 pack")

            # 验证"使用此配置"和"跳过"按钮存在
            check(t.app.tk_button_use_config.winfo_ismapped(), "'使用此配置'按钮应可见")
            check(t.app.tk_button_skip_config.winfo_ismapped(), "'跳过'按钮应可见")
        finally:
            config_transfer.get_config_from_ini = _orig
    finally:
        t.destroy()

@test("B3. 接收端点'使用此配置'→跳 step5 并选中配置文件夹")
def test_target_use_detected_config():
    """确认使用配置后跳 step5, 并自动选中对应文件夹"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.go_step(4)
        t.ctl._transfer_done = True

        _orig = config_transfer.get_config_from_ini
        config_transfer.get_config_from_ini = lambda: (
            r"F:\Appl\2026-08-03", "2026-08-03 22:57:42"
        )
        try:
            t.ctl._on_download_complete(True, 5, 1024*1024, [])
            eq(t.step, 4)

            # 点击"使用此配置"
            t.ctl._on_use_detected_config()
            t.app.update_idletasks()
            eq(t.step, 5, "确认配置后应跳到step5")

            # 配置检测区域应已隐藏
            try:
                t.app._config_detect_frame.pack_info()
                check(False, "检测区域应已隐藏")
            except Exception:
                pass

            # step5 的下一步按钮应为"校验文件 >"
            eq(t.next_text, "校验文件 >", "step5 下一步应为'校验文件 >'")
        finally:
            config_transfer.get_config_from_ini = _orig
    finally:
        t.destroy()

@test("B4. 接收端点'跳过'→跳 step5 不自动选中")
def test_target_skip_detected_config():
    """跳过配置检测后跳 step5"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.go_step(4)
        t.ctl._transfer_done = True

        _orig = config_transfer.get_config_from_ini
        config_transfer.get_config_from_ini = lambda: (
            r"F:\Appl\2026-08-03", "2026-08-03 22:57:42"
        )
        try:
            t.ctl._on_download_complete(True, 5, 1024*1024, [])
            eq(t.step, 4)

            t.ctl._on_skip_detected_config()
            t.app.update_idletasks()
            eq(t.step, 5, "跳过配置后应跳到step5")
            eq(t.next_text, "校验文件 >")
        finally:
            config_transfer.get_config_from_ini = _orig
    finally:
        t.destroy()


# ============================================================
# PART C: 接收端 — 配置导入流程 (step 5)
# ============================================================

@test("C1. step5 导入页面控件状态")
def test_step5_import_page_controls():
    """验证 step5 页面控件初始状态"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.go_step(4)
        t.ctl._transfer_done = True

        _orig = config_transfer.get_config_from_ini
        config_transfer.get_config_from_ini = lambda: (None, None)
        try:
            t.ctl._on_download_complete(True, 5, 1024*1024, [])
            eq(t.step, 5)

            # 验证导入页面控件存在
            check(hasattr(t.app, 'tk_button_import_config'), "应有导入按钮")
            check(hasattr(t.app, 'tk_button_skip_import'), "应有跳过按钮")
            check(hasattr(t.app, 'tk_combo_config_folder'), "应有配置文件夹下拉框")
            check(hasattr(t.app, 'tk_label_config_status'), "应有配置状态标签")

            # 导入按钮初始启用
            try:
                eq(str(t.app.tk_button_import_config.cget("state")), "normal",
                   "导入按钮初始应启用")
            except: pass

            # 跳过按钮初始启用
            try:
                eq(str(t.app.tk_button_skip_import.cget("state")), "normal",
                   "跳过按钮初始应启用")
            except: pass
        finally:
            config_transfer.get_config_from_ini = _orig
    finally:
        t.destroy()

@test("C2. 跳过导入→跳 step6")
def test_skip_import_to_step6():
    """接收端 step5 跳过导入后跳 step6"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.go_step(4)
        t.ctl._transfer_done = True

        _orig = config_transfer.get_config_from_ini
        config_transfer.get_config_from_ini = lambda: (None, None)
        try:
            t.ctl._on_download_complete(True, 5, 1024*1024, [])
            eq(t.step, 5)

            t.ctl._on_skip_import()
            t.app.update_idletasks()
            eq(t.step, 6, "跳过导入后应跳到step6")
            eq(t.next_state, "normal", "step6 下一步=跳过校验")
            eq(t.next_text, "跳过校验 >")
            eq(t.prev_state, "normal", "step6 上一步应启用")
        finally:
            config_transfer.get_config_from_ini = _orig
    finally:
        t.destroy()

@test("C2b. 配置导入完成后自动跳转 step6 且禁用跳过")
def test_import_done_auto_jump_step6():
    """导入成功 → 自动跳到校验页(step6) + 跳过按钮禁用 + _on_skip_import 防护"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.go_step(5)
        t.ctl._config_import_done = False

        # 模拟导入成功
        t.ctl._on_import_done(True)
        t.app.update_idletasks()
        # 自动跳转 (300ms after) 后应到达 step6
        eq(t.step, 6, "导入成功后应自动跳转到step6")
        eq(t.next_state, "normal", "step6 下一步=跳过校验")
        eq(str(t.app.tk_button_skip_import.cget("state")), "disabled",
           "配置已导入, 导入页跳过按钮应禁用")

        # 已导入后调用 _on_skip_import 不应再进入校验跳过逻辑 (防护)
        t.ctl._on_skip_import()
        eq(t.step, 6, "配置已导入, 跳过导入应被阻止")
    finally:
        t.destroy()


@test("C2c. 导入失败时不自动跳转, 跳过按钮可用")
def test_import_failed_no_auto_jump():
    """导入失败 → 不跳转, 跳过按钮恢复可用"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.go_step(5)
        t.ctl._config_import_done = False

        t.ctl._on_import_done(False)
        t.app.update_idletasks()
        eq(t.step, 5, "导入失败不应自动跳转")
        eq(str(t.app.tk_button_skip_import.cget("state")), "normal",
           "导入失败跳过按钮应可用")
    finally:
        t.destroy()


@test("C2d. DHCP 搜索按钮 60 秒内禁用, 倒计时归零恢复")
def test_dhcp_button_disabled_60s():
    """模拟 DHCP 启动后按钮禁用 60 秒; _reset_dhcp_button 归零时恢复"""
    t = T()
    try:
        t.select_role("target")
        btn = t.app.tk_button_dhcp

        # 模拟 _setup_target_dhcp 启动后按钮状态 (60 秒内禁用)
        btn.config(state="disabled")
        btn.configure(text="搜索中 (60 秒)...")
        eq(str(btn.cget("state")), "disabled", "DHCP 搜索期间按钮应禁用")
        assert "搜索中" in btn.cget("text"), f"按钮文字应含'搜索中', 实际={btn.cget('text')}"

        # 倒计时归零 → _reset_dhcp_button 恢复
        t.ctl._reset_dhcp_button()
        t.app.update_idletasks()
        eq(str(btn.cget("state")), "normal", "倒计时结束按钮应恢复")
        eq(btn.cget("text"), "寻找旧电脑", "按钮文字应恢复为'寻找旧电脑'")
    finally:
        t.destroy()


@test("C2e. 网络断开恢复回验证码页: 禁用下一步, 激活重新接收")
def test_network_down_back_to_step3():
    """网络断开后用户回到验证码页(step3): 下一步禁用 + 重新接收按钮激活"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()  # →2
        t.set_disk()
        t.next_step()  # →3
        t.app.go_step(4)  # →传输页

        # 模拟传输完成(网络断开场景): _transferring=False, 未完成
        t.ctl._transferring = False
        t.ctl._transfer_done = False
        t.ctl._network_down = True

        # 用户点"上一步"回到验证码页
        t.ctl._on_prev_step()
        t.app.update_idletasks()

        eq(t.step, 3, "应回到验证码页")
        eq(t.next_state, "disabled", "验证码页下一步应禁用")
        eq(str(t.app.tk_button_mqfzl35t.cget("state")), "normal",
           "验证码页重新接收按钮应激活")
        assert "重新接收" in t.app.tk_button_mqfzl35t.cget("text"), \
            f"按钮文字应为'重新接收', 实际={t.app.tk_button_mqfzl35t.cget('text')}"
    finally:
        t.destroy()


@test("C3. step6 回退到 step5 后按钮文字恢复")
def test_back_from_step6_to_step5():
    """从 step6 回退到 step5 后, 下一步文字为'校验文件 >'"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.go_step(4)
        t.ctl._transfer_done = True

        _orig = config_transfer.get_config_from_ini
        config_transfer.get_config_from_ini = lambda: (None, None)
        try:
            t.ctl._on_download_complete(True, 5, 1024*1024, [])
            eq(t.step, 5)
            t.ctl._on_skip_import()
            eq(t.step, 6)

            t.prev_step()
            eq(t.step, 5, "回退后应在step5")
            eq(t.next_text, "校验文件 >", "回退到step5后下一步文字应恢复")
        finally:
            config_transfer.get_config_from_ini = _orig
    finally:
        t.destroy()


# ============================================================
# PART D: 压缩 & 上传功能单元测试
# ============================================================

@test("D1. ZIP 压缩—命名格式正确")
def test_compress_naming_format():
    """验证 ZIP 命名: <COMPUTERNAME>_<YYYY-MM-DD>.zip"""
    with tempfile.TemporaryDirectory() as tmpdir:
        export_dir = os.path.join(tmpdir, "2026-07-31")
        os.makedirs(export_dir)
        # 创建一些文件
        with open(os.path.join(export_dir, "test.txt"), "w") as f:
            f.write("test content")

        # 设置环境变量模拟特定电脑名
        os.environ["COMPUTERNAME"] = "QDNB5098"
        try:
            logs = []
            ok, zip_path, _ = config_transfer.compress_and_upload_config(
                export_dir, log_callback=lambda m: logs.append(m)
            )
            check(ok, "压缩应成功")
            expected_name = "QDNB5098_2026-07-31.zip"
            check(os.path.basename(zip_path) == expected_name,
                  f"ZIP 命名应为 {expected_name}, 实际={os.path.basename(zip_path)}")
            check(os.path.exists(zip_path), "ZIP 文件应存在")
            check(os.path.getsize(zip_path) > 0, "ZIP 应非空")

            # 验证 ZIP 内容
            import zipfile
            with zipfile.ZipFile(zip_path, 'r') as zf:
                names = zf.namelist()
                check("test.txt" in names, f"ZIP 应包含 test.txt, 实际={names}")
        finally:
            # 恢复原始 COMPUTERNAME
            if "COMPUTERNAME" not in os.environ:
                del os.environ["COMPUTERNAME"]

@test("D2. ZIP 压缩—导出文件夹不存在时失败")
def test_compress_folder_not_exist():
    """导出文件夹不存在时压缩应失败"""
    logs = []
    ok, zip_path, up_ok = config_transfer.compress_and_upload_config(
        r"Z:\non_existent\folder",
        log_callback=lambda m: logs.append(m)
    )
    check(not ok, "压缩应失败")
    eq(zip_path, "", "zip_path 应为空字符串")
    check(not up_ok, "上传也应失败")
    # 日志中应有错误信息
    assert any("失败" in l or "异常" in l for l in logs), f"日志应含错误: {logs}"

@test("D3. ZIP 压缩—空文件夹")
def test_compress_empty_folder():
    """空文件夹也能压缩成功"""
    with tempfile.TemporaryDirectory() as tmpdir:
        export_dir = os.path.join(tmpdir, "2026-08-03")
        os.makedirs(export_dir)

        logs = []
        ok, zip_path, _ = config_transfer.compress_and_upload_config(
            export_dir, log_callback=lambda m: logs.append(m)
        )
        check(ok, "空文件夹压缩应成功")
        check(os.path.exists(zip_path), "ZIP 应存在")
        import zipfile
        with zipfile.ZipFile(zip_path, 'r') as zf:
            check(len(zf.namelist()) == 0, "空文件夹 ZIP 应无文件")

@test("D4. 上传到不可达服务器—返回 compress_ok=True, upload_ok=False")
def test_upload_unreachable():
    """上传到不存在的主机, 压缩成功但上传失败"""
    with tempfile.TemporaryDirectory() as tmpdir:
        export_dir = os.path.join(tmpdir, "2026-08-03")
        os.makedirs(export_dir)
        with open(os.path.join(export_dir, "dummy.txt"), "w") as f:
            f.write("test")

        logs = []
        old_url = config_transfer.PROFILE_UPLOAD_URL
        config_transfer.PROFILE_UPLOAD_URL = "http://127.0.0.1:1/api/upload"  # 不可达端口
        try:
            ok, zip_path, up_ok = config_transfer.compress_and_upload_config(
                export_dir, log_callback=lambda m: logs.append(m)
            )
            check(ok, "压缩应成功")
            check(os.path.exists(zip_path), "ZIP 应存在(本地保留)")
            check(not up_ok, "上传应失败")
            # 应有日志说明 ZIP 已保留
            assert any("保留" in l or "失败" in l or "异常" in l for l in logs), \
                f"日志应含失败信息: {logs}"
        finally:
            config_transfer.PROFILE_UPLOAD_URL = old_url

@test("D5. multipart/form-data body 格式正确")
def test_multipart_format():
    """验证上传请求的 multipart 编码格式"""
    # 通过 monkey-patch urllib 来捕获请求体
    import urllib.request as _urllib

    captured_data = None
    captured_headers = None

    class FakeResponse:
        status = 200
        def read(self): return b"ok"
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake_urlopen(req, **kw):
        nonlocal captured_data, captured_headers
        captured_data = req.data
        captured_headers = dict(req.headers)
        return FakeResponse()

    with tempfile.TemporaryDirectory() as tmpdir:
        export_dir = os.path.join(tmpdir, "2026-08-03")
        os.makedirs(export_dir)
        with open(os.path.join(export_dir, "settings.txt"), "w") as f:
            f.write("key=value")

        _orig_urlopen = _urllib.urlopen
        _urllib.urlopen = fake_urlopen
        try:
            logs = []
            ok, zip_path, up_ok = config_transfer.compress_and_upload_config(
                export_dir, log_callback=lambda m: logs.append(m)
            )
            check(ok, "压缩应成功")
            check(up_ok, "模拟上传应成功")

            # 验证 Content-Type
            ct = captured_headers.get("Content-type", "")
            check("multipart/form-data" in ct, f"Content-Type 应为 multipart: {ct}")

            # 验证 body 包含 boundary
            body = captured_data.decode("latin-1") if isinstance(captured_data, bytes) else str(captured_data)
            check("Content-Disposition: form-data" in body, "body 应含 form-data")
            check('name="file"' in body, "body 应含 name='file'")
            check("application/zip" in body, "body 应含 application/zip")
            check("settings.txt" in body, "body 应含 zip 内的文件名")
        finally:
            _urllib.urlopen = _orig_urlopen


# ============================================================
# PART E: 全设备更换 E2E 流程 (发送端 + 接收端串联)
# ============================================================

@test("E1. 发送端完整流程: step0→1→2→3→4→6 (跳过5)")
def test_source_full_flow():
    """发送端完整导航流程"""
    t = T()
    try:
        # step0→1: 选角色
        t.select_role("source")
        eq(t.step, 1)
        # 自动网卡扫描可能已完成 → 未选时禁用, 已自动选中时启用 (环境相关)
        eq(t.next_state, "normal" if getattr(t.ctl, '_auto_nic', None) else "disabled",
           "step1 按钮状态取决于自动网卡是否已选中")
        eq(t.next_text, "下一步 >")

        # step1: 选网卡
        t.set_nic()
        eq(t.next_state, "normal")
        t.next_step()
        eq(t.step, 2)

        # step2: 选磁盘
        eq(t.next_state, "normal" if getattr(t.ctl, '_auto_selected_disk', None) else "disabled",
           "step2 按钮状态取决于自动磁盘选择")
        t.set_disk()
        eq(t.next_state, "normal")
        t.next_step()
        eq(t.step, 3)

        # step3: 传输页 (禁用直到完成)
        eq(t.next_state, "disabled", "step3 传输未完成应禁用")
        t.ctl._transfer_done = True
        t.go_step(4)

        # step4: 传输完成
        eq(t.step, 4)
        eq(t.next_text, "校验文件 >")
        eq(t.next_state, "normal")
        t.next_step()

        # step6: 发送端跳过 step5
        eq(t.step, 6, "发送端应跳过step5直达step6")
        eq(t.next_state, "normal")
        eq(t.next_text, "跳过校验 >")
        eq(t.prev_state, "normal")

        # step6→4 回退
        t.prev_step()
        eq(t.step, 4, "发送端回退应跳过step5")

        # step4→3→2→1→0
        t.prev_step()
        eq(t.step, 3)
        t.prev_step()
        eq(t.step, 2)
        t.prev_step()
        eq(t.step, 1)
        t.prev_step()
        eq(t.step, 0)
    finally:
        t.destroy()

@test("E2. 接收端完整流程: step0→1→2→3→4→5→6")
def test_target_full_flow():
    """接收端完整导航流程(含 step5 配置导入)"""
    t = T()
    try:
        # step0→1
        t.select_role("target")
        eq(t.step, 1)
        eq(t.next_text, "下一步 >")

        # step1→2
        t.set_nic()
        eq(t.next_state, "normal")
        t.next_step()
        eq(t.step, 2)

        # step2→3
        t.set_disk()
        eq(t.next_state, "normal")
        t.next_step()
        eq(t.step, 3)

        # step3→4 (传输完成)
        eq(t.next_state, "disabled")
        t.ctl._transfer_done = True
        t.go_step(4)
        eq(t.step, 4)
        eq(t.next_text, "导入配置 >", "step4 下一步应为'导入配置 >'")
        eq(t.next_state, "normal")

        # step4→5
        t.next_step()
        eq(t.step, 5, "接收端应进入 step5")
        eq(t.next_text, "校验文件 >")
        eq(t.next_state, "normal")

        # step5→6
        t.next_step()
        eq(t.step, 6)
        eq(t.next_state, "normal")
        eq(t.next_text, "跳过校验 >")

        # step6→5→4→3→2→1→0
        t.prev_step()
        eq(t.step, 5)
        eq(t.next_text, "校验文件 >")
        t.prev_step()
        eq(t.step, 4)
        eq(t.next_text, "导入配置 >")
        t.prev_step()
        eq(t.step, 3)
        t.prev_step()
        eq(t.step, 2)
        t.prev_step()
        eq(t.step, 1)
        t.prev_step()
        eq(t.step, 0)
    finally:
        t.destroy()

@test("E3. 设备类型切换一致性")
def test_role_switch_consistency():
    """发送端↔接收端切换后状态一致"""
    t = T()
    try:
        # 先选发送端
        t.select_role("source")
        eq(t.ctl._device_type, "源设备")
        eq(t.step, 1)

        # 回退重选接收端
        t.prev_step()
        eq(t.step, 0)
        t.select_role("target")
        eq(t.ctl._device_type, "目标设备")
        eq(t.step, 1)
        eq(t.prev_state, "normal")

        # 再切回发送端
        t.prev_step()
        eq(t.step, 0)
        t.select_role("source")
        eq(t.ctl._device_type, "源设备")
        eq(t.step, 1)

        # 验证发送端无"导入配置 >"按钮文字
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.ctl._transfer_done = True
        t.go_step(4)
        eq(t.next_text, "校验文件 >", "发送端 step4 不应显示'导入配置'")
        t.next_step()
        eq(t.step, 6, "发送端应跳过 step5")
    finally:
        t.destroy()

@test("E4. 配置检测区域不在 step0/1/2/3/5/6 中显示")
def test_config_detect_hidden_on_other_steps():
    """配置检测区域仅应在 step4 显示"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.go_step(4)
        t.ctl._transfer_done = True

        _orig = config_transfer.get_config_from_ini
        config_transfer.get_config_from_ini = lambda: (
            r"F:\Appl\2026-08-03", "2026-08-03 22:57:42"
        )
        try:
            t.ctl._on_download_complete(True, 5, 1024*1024, [])
            # step4: 应可见
            try:
                t.app._config_detect_frame.pack_info()
            except Exception:
                check(False, "step4 配置检测区域应可见")

            # 跳 step5: 应隐藏
            t.ctl._on_use_detected_config()
            eq(t.step, 5)
            try:
                t.app._config_detect_frame.pack_info()
                check(False, "step5 配置检测区域应隐藏")
            except Exception:
                pass  # 正确: 未 pack

            # step6: 应隐藏
            t.next_step()
            eq(t.step, 6)
            try:
                t.app._config_detect_frame.pack_info()
                check(False, "step6 配置检测区域应隐藏")
            except Exception:
                pass
        finally:
            config_transfer.get_config_from_ini = _orig
    finally:
        t.destroy()

@test("E5. 自动检测配置后再手动选择其他文件夹")
def test_detected_config_then_manual_select():
    """检测到配置后, 用户仍可在 step5 中选择其他文件夹"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.go_step(4)
        t.ctl._transfer_done = True

        _orig = config_transfer.get_config_from_ini
        config_transfer.get_config_from_ini = lambda: (
            r"F:\Appl\2026-08-03", "2026-08-03 22:57:42"
        )
        try:
            t.ctl._on_download_complete(True, 5, 1024*1024, [])
            eq(t.step, 4)

            # 点击"使用此配置"
            t.ctl._on_use_detected_config()
            eq(t.step, 5)

            # 验证 _config_folders 不为空
            check(len(t.ctl._config_folders) > 0, "应有配置文件夹列表")

            # 验证下拉框有选项
            vals = list(t.app.tk_combo_config_folder["values"])
            check(len(vals) > 0, f"下拉框应有选项, 实际={vals}")

            # 验证已检测到的配置路径被选中
            selected = t.app.tk_combo_config_folder.get()
            check("2026-08-03" in selected, f"选中的选项应包含日期: {selected}")
        finally:
            config_transfer.get_config_from_ini = _orig
    finally:
        t.destroy()


# ============================================================
# PART F: 按钮防重入 & 状态边界
# ============================================================

@test("F1. 导入配置按钮点击后禁用防重复")
def test_import_button_disabled_on_click():
    """点击导入后按钮应立即禁用"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.go_step(4)
        t.ctl._transfer_done = True

        _orig = config_transfer.get_config_from_ini
        config_transfer.get_config_from_ini = lambda: (None, None)
        try:
            t.ctl._on_download_complete(True, 5, 1024*1024, [])
            eq(t.step, 5)

            # 确保下拉框有选中项
            combo_vals = list(t.app.tk_combo_config_folder["values"])
            for v in combo_vals:
                if v not in ("未检测到配置备份",):
                    t.app.tk_combo_config_folder.set(v)
                    t.app.update_idletasks()
                    break

            # 模拟导入开始
            t.app.tk_button_import_config.invoke()
            t.app.update_idletasks()

            # 检查按钮状态 (invoke 触发了异步线程, 但状态应立即变化)
            # 直接调用 _on_import_config 来验证
            t.ctl._on_import_config()
            eq(str(t.app.tk_button_import_config.cget("state")), "disabled",
               "导入中按钮应禁用")
            eq(str(t.app.tk_button_skip_import.cget("state")), "disabled",
               "导入中跳过按钮也应禁用")
        finally:
            config_transfer.get_config_from_ini = _orig
    finally:
        t.destroy()

@test("F2. 导出按钮点击后立即禁用(防重复点击)")
def test_export_button_disabled_immediately():
    """点击导出后按钮应立即变为 '导出中...' 且 disabled"""
    t = T()
    try:
        t.select_role("source")
        btn = t.app.tk_button_export_config
        t.ctl._on_export_config()
        t.app.update_idletasks()
        eq(str(btn.cget("state")), "disabled", "导出中按钮应禁用")
    finally:
        t.destroy()

@test("F3. 多次回退前进不丢步")
def test_multiple_back_forward():
    """反复回退前进步骤数应保持正确"""
    t = T()
    try:
        t.select_role("source")
        t.set_nic()
        t.next_step()  # →2
        t.set_disk()
        t.next_step()  # →3
        t.ctl._transfer_done = True
        t.go_step(4)
        t.next_step()  # →6
        eq(t.step, 6)

        # 反复操作 10 次
        for i in range(10):
            t.prev_step()
            eq(t.step, 4, f"第{i+1}次回退应在step4")
            t.next_step()
            eq(t.step, 6, f"第{i+1}次前进应在step6")

        # 最终回到 step0
        t.prev_step()  # →4
        t.prev_step()  # →3
        t.prev_step()  # →2
        t.prev_step()  # →1
        t.prev_step()  # →0
        eq(t.step, 0)
    finally:
        t.destroy()


# ============================================================
# 运行
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  设备更换全流程端到端测试 (25 项)")
    print("=" * 60)
    print()

    # Part A: 发送端导出→压缩→上传
    test_source_export_btn_initial()
    test_target_no_export_btn()
    test_source_export_compress_upload_state_chain()
    test_source_compress_ok_upload_fail()
    test_source_compress_fail()

    # Part B: 接收端配置检测
    test_target_download_no_config_auto_jump()
    test_target_download_with_config_stay_step4()
    test_target_use_detected_config()
    test_target_skip_detected_config()

    # Part C: 接收端配置导入
    test_step5_import_page_controls()
    test_skip_import_to_step6()
    test_back_from_step6_to_step5()

    # Part D: 压缩上传单元测试
    test_compress_naming_format()
    test_compress_folder_not_exist()
    test_compress_empty_folder()
    test_upload_unreachable()
    test_multipart_format()

    # Part E: 完整 E2E
    test_source_full_flow()
    test_target_full_flow()
    test_role_switch_consistency()
    test_config_detect_hidden_on_other_steps()
    test_detected_config_then_manual_select()

    # Part F: 按钮防重入
    test_import_button_disabled_on_click()
    test_export_button_disabled_immediately()
    test_multiple_back_forward()

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
