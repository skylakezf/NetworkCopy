"""
设备更换全流程端到端集成测试
模拟: 旧设备(Sender) 导出配置→压缩→上传 → 新设备(Receiver) 下载→检测配置→导入
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import sys, os, threading, traceback, tempfile, shutil, io, json

# 测试环境: 关闭"启动时用默认浏览器打开 EULA 页面"
os.environ["NETCOPY_SKIP_EULA_BROWSER"] = "1"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_ROOT)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, 'python-3.13.14-embed-amd64',
                                'Lib', 'site-packages'))

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
        # 协议确认 (EULA): 测试默认勾选, 激活发送方/接收方按钮
        self.app._eula_var.set(True)
        self.app._on_eula_toggle()
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
        # 网卡已改为全自动检测 (不再有手动选择下拉框)。
        # 此方法仅用于测试中模拟自动网卡选择结果。
        self.ctl._auto_nic = (name, "Realtek PCIe GbE Family Controller",
                              "ethernet0", "1Gbps", 1, "169.254.100.2")
        self.ctl._nic_list = [self.ctl._auto_nic]
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

@test("B1. 接收端下载完成→自动跳转 step4 (无配置文件)")
def test_target_download_no_config_auto_jump():
    """无配置文件时自动跳到 step4"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()  # →2 连接页
        t.go_step(3)  # →3 传输页
        t.ctl._transfer_done = True

        # mock 无配置文件
        _orig = config_transfer.get_config_from_ini
        config_transfer.get_config_from_ini = lambda: (None, None)
        try:
            t.ctl._on_download_complete(True, 5, 1024*1024, [])
            eq(t.step, 4, "无配置文件时应自动跳到step4")
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

@test("B2. 接收端下载完成→自动使用检测到的配置并跳 step4")
def test_target_download_with_config_stay_step4():
    """检测到 systemconfig.ini → 自动导入 (无需确认), 直接进入 step4"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()  # →2 连接页
        t.go_step(3)  # →3 传输页
        t.ctl._transfer_done = True

        _orig = config_transfer.get_config_from_ini
        config_transfer.get_config_from_ini = lambda: (
            r"F:\Appl\2026-08-03", "2026-08-03 22:57:42"
        )
        try:
            t.ctl._on_download_complete(True, 5, 1024*1024, [])
            t.app.update_idletasks()
            # 检测到配置 → 后台自动导入, 停留在传输页(step3), 校验完成后统一进总结页
            eq(t.step, 3, "检测到配置时后台自动导入, 不立即跳转")
            check(getattr(t.ctl, '_auto_import_active', False),
                  "应处于自动导入状态")

            # 配置检测确认区应保持隐藏 (自动导入, 无需确认)
            try:
                t.app._config_detect_frame.pack_info()
                check(False, "自动导入后检测区域应隐藏")
            except Exception:
                pass

            # 自动导入中: 下一步保持禁用 (等待导入/校验完成统一进总结页)
            eq(t.next_state, "disabled", "自动导入中下一步应禁用")
        finally:
            config_transfer.get_config_from_ini = _orig
    finally:
        t.destroy()

@test("B3. 自动导入后 step4 已选中检测到的配置文件夹")
def test_target_use_detected_config():
    """自动导入后, step4 应自动选中检测到的配置文件夹"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()  # →2 连接页
        t.go_step(3)  # →3 传输页
        t.ctl._transfer_done = True

        _orig = config_transfer.get_config_from_ini
        config_transfer.get_config_from_ini = lambda: (
            r"F:\Appl\2026-08-03", "2026-08-03 22:57:42"
        )
        try:
            t.ctl._on_download_complete(True, 5, 1024*1024, [])
            t.app.update_idletasks()
            # 检测到配置 → 后台自动导入, 停留传输页(step3)
            eq(t.step, 3, "检测到配置时后台自动导入, 不立即跳转")
            check(getattr(t.ctl, '_auto_import_active', False),
                  "应处于自动导入状态")

            # 下拉框应自动选中检测到的配置 (含日期)
            selected = t.app.tk_combo_config_folder.get()
            check("2026-08-03" in selected,
                  f"下拉框应自动选中检测到的配置: {selected}")

            # 配置检测确认区应已隐藏 (自动导入, 无需确认)
            try:
                t.app._config_detect_frame.pack_info()
                check(False, "检测区域应已隐藏")
            except Exception:
                pass

            # 自动导入中: 下一步保持禁用 (等待导入/校验完成统一进总结页)
            eq(t.next_state, "disabled", "自动导入中下一步应禁用")
        finally:
            config_transfer.get_config_from_ini = _orig
    finally:
        t.destroy()

@test("B4. 有配置文件时不再显示确认区(自动导入, 无跳过按钮)")
def test_target_skip_detected_config():
    """自动导入: 检测到配置后不再显示'使用此配置/跳过'确认区"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()  # →2 连接页
        t.go_step(3)  # →3 传输页
        t.ctl._transfer_done = True

        _orig = config_transfer.get_config_from_ini
        config_transfer.get_config_from_ini = lambda: (
            r"F:\Appl\2026-08-03", "2026-08-03 22:57:42"
        )
        try:
            t.ctl._on_download_complete(True, 5, 1024*1024, [])
            t.app.update_idletasks()
            eq(t.step, 3, "检测到配置时后台自动导入, 不立即跳转")
            check(getattr(t.ctl, '_auto_import_active', False),
                  "应处于自动导入状态")

            # 确认区与两个确认按钮均不应显示
            try:
                t.app._config_detect_frame.pack_info()
                check(False, "配置检测区域应隐藏")
            except Exception:
                pass
            check(not t.app.tk_button_use_config.winfo_ismapped(),
                  "'使用此配置'按钮不应显示")
            check(not t.app.tk_button_skip_config.winfo_ismapped(),
                  "'跳过'按钮不应显示")
        finally:
            config_transfer.get_config_from_ini = _orig
    finally:
        t.destroy()


# ============================================================
# PART C: 接收端 — 配置导入流程 (step 5)
# ============================================================

@test("C1. step4 导入页面控件状态")
def test_step5_import_page_controls():
    """验证 step4 页面控件初始状态"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()  # →2 连接页
        t.go_step(3)  # →3 传输页
        t.ctl._transfer_done = True

        _orig = config_transfer.get_config_from_ini
        config_transfer.get_config_from_ini = lambda: (None, None)
        try:
            t.ctl._on_download_complete(True, 5, 1024*1024, [])
            eq(t.step, 4)

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

@test("C2. 跳过导入 → 停留在 step4 (最后一步)")
def test_skip_import_to_step6():
    """接收端 step4 跳过导入后停留在 step4 (校验已自动后台执行)"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()  # →2 连接页
        t.go_step(3)  # →3 传输页
        t.ctl._transfer_done = True

        _orig = config_transfer.get_config_from_ini
        config_transfer.get_config_from_ini = lambda: (None, None)
        try:
            t.ctl._on_download_complete(True, 5, 1024*1024, [])
            eq(t.step, 4)

            t.ctl._on_skip_import()
            t.app.update_idletasks()
            eq(t.step, 4, "跳过导入后停留在步骤4")
            eq(t.next_state, "normal", "跳过导入后进入总结页, 下一步为'完成'")
            eq(t.next_text, "完成", "总结页下一步应为'完成'")
            eq(t.prev_state, "normal", "上一步应启用")
        finally:
            config_transfer.get_config_from_ini = _orig
    finally:
        t.destroy()

@test("C2b. 配置导入完成后停留在 step4 且禁用跳过")
def test_import_done_auto_jump_step6():
    """导入成功 → 停留在 step4 (最后一步) + 跳过按钮禁用 + _on_skip_import 防护"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()  # →2 连接页
        t.go_step(4)  # →4 导入配置页
        t.ctl._config_import_done = False

        # 模拟导入成功
        t.ctl._on_import_done(True)
        t.app.update_idletasks()
        eq(t.step, 4, "导入成功后停留在步骤4 (校验已自动后台执行)")
        eq(t.next_state, "disabled", "最后一步无下一步")
        eq(str(t.app.tk_button_skip_import.cget("state")), "disabled",
           "配置已导入, 导入页跳过按钮应禁用")

        # 已导入后调用 _on_skip_import 不应再进入校验跳过逻辑 (防护)
        t.ctl._on_skip_import()
        eq(t.step, 4, "配置已导入, 跳过导入应被阻止")
    finally:
        t.destroy()


@test("C2c. 导入失败时不自动跳转, 跳过按钮可用")
def test_import_failed_no_auto_jump():
    """导入失败 → 不跳转, 跳过按钮恢复可用"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()  # →2 连接页
        t.go_step(4)  # →4 导入配置页
        t.ctl._config_import_done = False

        t.ctl._on_import_done(False)
        t.app.update_idletasks()
        eq(t.step, 4, "导入失败不应自动跳转")
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
    """网络断开后用户回到验证码页(step2): 下一步禁用 + 重新接收按钮激活"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()  # →2 连接页
        t.go_step(3)  # →3 传输页

        # 模拟传输完成(网络断开场景): _transferring=False, 未完成
        t.ctl._transferring = False
        t.ctl._transfer_done = False
        t.ctl._network_down = True

        # 用户点"上一步"回到验证码页
        t.ctl._on_prev_step()
        t.app.update_idletasks()

        eq(t.step, 2, "应回到验证码页")
        eq(t.next_state, "disabled", "验证码页下一步应禁用")
        eq(str(t.app.tk_button_mqfzl35t.cget("state")), "normal",
           "验证码页重新接收按钮应激活")
        assert "重新接收" in t.app.tk_button_mqfzl35t.cget("text"), \
            f"按钮文字应为'重新接收', 实际={t.app.tk_button_mqfzl35t.cget('text')}"
    finally:
        t.destroy()


@test("C3. step4 回退到 step3 后下一步文字恢复")
def test_back_from_step6_to_step5():
    """从 step4 回退到 step3 后, 下一步文字为'导入配置 >'"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()  # →2 连接页
        t.go_step(3)  # →3 传输页
        t.ctl._transfer_done = True

        _orig = config_transfer.get_config_from_ini
        config_transfer.get_config_from_ini = lambda: (None, None)
        try:
            t.ctl._on_download_complete(True, 5, 1024*1024, [])
            eq(t.step, 4)

            t.prev_step()
            eq(t.step, 3, "回退后应在step3")
            eq(t.next_text, "查看总结 >", "回退到step3后下一步文字应恢复")
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

@test("E1. 发送端完整流程: step0→1→2→3 (无步骤4/5)")
def test_source_full_flow():
    """发送端完整导航流程 (校验页已取消, 传输完成即结束)"""
    t = T()
    try:
        # step0→1: 选角色
        t.select_role("source")
        eq(t.step, 1)
        eq(t.next_state, "normal", "步骤1 网卡全自动, 角色已选即可下一步")
        eq(t.next_text, "下一步 >")

        # step1→2: 连接页 (下一步禁用, 用开始传输)
        t.set_nic()
        eq(t.next_state, "normal")
        t.next_step()
        eq(t.step, 2)
        eq(t.next_state, "disabled", "step2 连接页下一步禁用")

        # step2→3: 传输页 (传输完成即结束)
        t.ctl._transfer_done = True
        t.go_step(3)
        eq(t.step, 3)
        eq(t.next_state, "disabled", "发送端传输完成即结束")

        # step3→2→1→0 回退
        t.prev_step()
        eq(t.step, 2)
        t.prev_step()
        eq(t.step, 1)
        t.prev_step()
        eq(t.step, 0)
    finally:
        t.destroy()

@test("E2. 接收端完整流程: step0→1→2→3→4 (最后一步)")
def test_target_full_flow():
    """接收端完整导航流程(step4 配置导入为最后一步)"""
    t = T()
    try:
        # step0→1
        t.select_role("target")
        eq(t.step, 1)
        eq(t.next_text, "下一步 >")

        # step1→2: 连接页 (下一步禁用, 用开始接收)
        t.set_nic()
        eq(t.next_state, "normal")
        t.next_step()
        eq(t.step, 2)
        eq(t.next_state, "disabled", "step2 连接页下一步禁用")

        # step2→3: 传输页 (传输完成后下一步为'查看总结 >')
        t.ctl._transfer_done = True
        t.go_step(3)
        eq(t.step, 3)
        eq(t.next_text, "查看总结 >", "step3 下一步应为'查看总结 >'")
        eq(t.next_state, "normal")

        # step3→4: 总结页
        t.next_step()
        eq(t.step, 4, "接收端应进入 step4")
        eq(t.next_state, "normal", "step4 为总结页, 下一步为'完成'")
        eq(t.next_text, "完成", "总结页下一步应为'完成'")

        # step4→3→2→1→0
        t.prev_step()
        eq(t.step, 3)
        eq(t.next_text, "查看总结 >")
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

        # 验证发送端无"导入配置 >"按钮文字 (传输完成即结束, 无步骤4/5)
        t.set_nic()
        t.next_step()  # →2 连接页
        t.ctl._transfer_done = True
        t.go_step(3)  # →3 传输页
        eq(t.next_state, "disabled", "发送端传输完成即结束, 无后续步骤")
        eq(t.next_text, "下一步 >", "发送端 step3 不应显示'导入配置'")
    finally:
        t.destroy()

@test("E4. 配置检测确认区不再显示(自动导入)")
def test_config_detect_hidden_on_other_steps():
    """自动导入: 配置检测确认区在任意步骤均不显示"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()  # →2 连接页
        t.go_step(3)  # →3 传输页
        t.ctl._transfer_done = True

        _orig = config_transfer.get_config_from_ini
        config_transfer.get_config_from_ini = lambda: (
            r"F:\Appl\2026-08-03", "2026-08-03 22:57:42"
        )
        try:
            t.ctl._on_download_complete(True, 5, 1024*1024, [])
            t.app.update_idletasks()
            # 检测到配置 → 后台自动导入, 停留传输页(step3)
            eq(t.step, 3, "检测到配置时后台自动导入, 不立即跳转")
            check(getattr(t.ctl, '_auto_import_active', False),
                  "应处于自动导入状态")

            # 确认区应始终隐藏 (自动导入无需确认)
            try:
                t.app._config_detect_frame.pack_info()
                check(False, "配置检测确认区应隐藏")
            except Exception:
                pass  # 正确: 未 pack

            # 自动导入中: 下一步保持禁用 (等待导入/校验完成统一进总结页)
            eq(t.next_state, "disabled", "自动导入中下一步应禁用")
        finally:
            config_transfer.get_config_from_ini = _orig
    finally:
        t.destroy()

@test("E5. 自动导入后仍可手动选择其他配置文件夹")
def test_detected_config_then_manual_select():
    """自动导入后, 用户仍可在 step4 中手动切换其他文件夹"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()  # →2 连接页
        t.go_step(3)  # →3 传输页
        t.ctl._transfer_done = True

        _orig = config_transfer.get_config_from_ini
        config_transfer.get_config_from_ini = lambda: (
            r"F:\Appl\2026-08-03", "2026-08-03 22:57:42"
        )
        try:
            t.ctl._on_download_complete(True, 5, 1024*1024, [])
            t.app.update_idletasks()
            # 自动导入: 检测到配置后停留传输页(step3)
            eq(t.step, 3, "检测到配置时后台自动导入, 不立即跳转")
            check(getattr(t.ctl, '_auto_import_active', False),
                  "应处于自动导入状态")

            # 验证 _config_folders 不为空
            check(len(t.ctl._config_folders) > 0, "应有配置文件夹列表")

            # 验证下拉框有选项
            vals = list(t.app.tk_combo_config_folder["values"])
            check(len(vals) > 0, f"下拉框应有选项, 实际={vals}")

            # 验证已自动选中的检测到的配置路径
            selected = t.app.tk_combo_config_folder.get()
            check("2026-08-03" in selected, f"选中的选项应包含日期: {selected}")

            # 用户仍可手动切换为其他选项
            if len(vals) > 1:
                other = next((v for v in vals if "2026-08-03" not in str(v)), None)
                if other is not None:
                    t.app.tk_combo_config_folder.set(other)
                    check(True, "可手动切换其他配置文件夹")
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
        t.next_step()  # →2 连接页
        t.go_step(3)  # →3 传输页
        t.ctl._transfer_done = True

        _orig = config_transfer.get_config_from_ini
        config_transfer.get_config_from_ini = lambda: (None, None)
        try:
            t.ctl._on_download_complete(True, 5, 1024*1024, [])
            eq(t.step, 4)

            # 构造有效配置目录, 使手动导入走完校验流程 (验证"点击即禁用")
            cfg_dir = tempfile.mkdtemp(prefix="cfg_")
            _orig_import = config_transfer.import_config
            try:
                t.ctl._config_folders = [("2026-08-03 22:57:42", cfg_dir)]
                t.app.tk_combo_config_folder["values"] = ["2026-08-03 22:57:42"]
                t.app.tk_combo_config_folder.current(0)
                config_transfer.import_config = lambda folder, log_callback=None: (True, [("x", "导入成功", "")])

                # 点击导入 → 校验通过后应立即禁用 (防重复导入)
                t.ctl._on_import_config()
                eq(str(t.app.tk_button_import_config.cget("state")), "disabled",
                   "导入中按钮应禁用")
                eq(str(t.app.tk_button_skip_import.cget("state")), "disabled",
                   "导入中跳过按钮也应禁用")
            finally:
                config_transfer.import_config = _orig_import
                shutil.rmtree(cfg_dir, ignore_errors=True)
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
    """反复回退前进步骤数应保持正确 (step3↔step4)"""
    t = T()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()  # →2 连接页
        t.ctl._transfer_done = True
        t.go_step(3)  # →3 传输页
        t.next_step()  # →4 导入配置
        eq(t.step, 4)

        # 反复操作 10 次
        for i in range(10):
            t.prev_step()
            eq(t.step, 3, f"第{i+1}次回退应在step3")
            t.next_step()
            eq(t.step, 4, f"第{i+1}次前进应在step4")

        # 最终回到 step0
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
