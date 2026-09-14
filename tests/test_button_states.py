# -*- coding: utf-8 -*-
"""
按钮状态矩阵 + 断网 / 验证码错误 综合测试脚本
================================================

覆盖三个主题:

  [套件1] 每个页面的按钮启用/禁用/文字/可见性准确性 (A1-A9)
      0=角色选择  1=高级设置  2=连接  3=传输  4=传输总结(接收端)  5=完成页(发送端)

  [套件2] 传输过程中断网的表现 (B1-B2)
      B1. 接收端: 下载完成回调带断网标志 → 提示中断, 不进入完成/配置导入
      B2. 发送端: 接收端长时间无请求且无 done → 提示连接中断, 停服务器, 不进完成页

  [套件3] 验证码输入错误 / 未输入正确验证码的表现 (C1-C3)
      C1. 接收端: files==0 (验证码错误) → 引导返回修改验证码, 可重试
      C2. 发送端: 验证码错误 → 提示, 保持服务器运行, 修正验证码后恢复正常
      C3. HTTP 层: 未携带/错误验证码 → 403 + auth_failed_flag; 正确 → 200 并清除

运行方式 (项目根目录):
    python-3.13.14-embed-amd64\\python.exe _test_button_states_full.py

退出码: 0=全部通过  1=存在失败
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import sys, os, threading, traceback, time, json
import urllib.request, urllib.error, ssl

os.environ["NETCOPY_SKIP_EULA_BROWSER"] = "1"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_ROOT)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, 'python-3.13.14-embed-amd64',
                                'Lib', 'site-packages'))

from ui import WinGUI
from control import Controller, SOURCE_IDLE_TIMEOUT
from file_transfer import FileServer, FileServerHandler, TRANSFER_PORT

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

# ------------------------------------------------------------
# 测试 fixture
# ------------------------------------------------------------
class T:
    def __init__(self):
        self.app = WinGUI()
        self.app.report_callback_exception = lambda *a: None
        self.ctl = Controller()
        self.ctl.init(self.app)
        self.app.ctl = self.ctl
        self.app._eula_var.set(True)
        self.app._on_eula_toggle()
        self.app.update_idletasks()

    @property
    def step(self):
        return self.app._step

    @property
    def next_state(self):
        try:
            return str(self.app.tk_button_next.cget("state"))
        except Exception:
            return "ERROR"

    @property
    def prev_state(self):
        try:
            return str(self.app.tk_button_prev.cget("state"))
        except Exception:
            return "ERROR"

    @property
    def next_text(self):
        try:
            return str(self.app.tk_button_next.cget("text"))
        except Exception:
            return "ERROR"

    @property
    def prev_text(self):
        try:
            return str(self.app.tk_button_prev.cget("text"))
        except Exception:
            return "ERROR"

    @property
    def transfer_btn_state(self):
        try:
            return str(self.app.tk_button_mqfzl35t.cget("state"))
        except Exception:
            return "ERROR"

    @property
    def transfer_btn_text(self):
        try:
            return str(self.app.tk_button_mqfzl35t.cget("text"))
        except Exception:
            return "ERROR"

    @property
    def transfer_btn_visible(self):
        try:
            return self.app.tk_button_mqfzl35t.winfo_ismapped()
        except Exception:
            return False

    def select_role(self, role):
        self.app._on_select_role(role)
        self.app.update_idletasks()

    def destroy(self):
        try:
            self.app.destroy()
        except Exception:
            pass


def reset_server_flags():
    FileServerHandler.transfer_done_flag = False
    FileServerHandler.ever_connected = False
    FileServerHandler.last_activity = 0.0
    FileServerHandler.active_requests = 0
    FileServerHandler.auth_failed_flag = False


def _http_status(url):
    ctx = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(url, timeout=10, context=ctx) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return -1


class FakeFileServer:
    """替代 FileServer: 记录 stop 是否被调用, is_transfer_done 恒 False"""
    def __init__(self):
        self.stopped = False
        self.is_done = False

    def is_transfer_done(self):
        return self.is_done

    def stop(self):
        self.stopped = True


# ============================================================
# 套件1: 页面按钮状态矩阵
# ============================================================

@test("A1. step0 角色选择页: prev/next 禁用, 开始传输隐藏, 角色按钮随 EULA 启停")
def test_step0_buttons():
    t = T()
    try:
        eq(t.step, 0, "初始应停留在角色选择页")
        eq(t.prev_state, "disabled", "step0 上一步应禁用")
        eq(t.next_state, "disabled", "step0 下一步应禁用")
        check(not t.transfer_btn_visible, "step0 不应显示开始传输按钮")
        eq(str(t.app.tk_btn_source.cget("state")), "normal", "勾选协议后发送方按钮应启用")
        eq(str(t.app.tk_btn_target.cget("state")), "normal", "勾选协议后接收方按钮应启用")
        # 取消勾选 → 角色按钮重新禁用
        t.app._eula_var.set(False)
        t.app._on_eula_toggle()
        t.app.update_idletasks()
        eq(str(t.app.tk_btn_source.cget("state")), "disabled", "取消勾选后发送方按钮应禁用")
        eq(str(t.app.tk_btn_target.cget("state")), "disabled", "取消勾选后接收方按钮应禁用")
    finally:
        t.destroy()


@test("A2. step1 高级设置页: 发送端显示导出配置, 接收端隐藏; prev/next 启用")
def test_step1_buttons():
    for role, export_visible in (("source", True), ("target", False)):
        t = T()
        try:
            t.select_role(role)
            eq(t.step, 1, f"{role} 选角色后应进入 step1")
            eq(t.prev_state, "normal", "step1 上一步应启用")
            eq(t.next_state, "normal", "step1 下一步应启用 (网卡全自动)")
            check(not t.transfer_btn_visible, "step1 不应显示开始传输按钮")
            got = t.app._export_frame.winfo_ismapped()
            eq(got, export_visible,
               f"step1 导出配置面板可见性 期望={export_visible} 实际={got}")
        finally:
            t.destroy()


@test("A3. step2 连接页: 开始传输显示且启用, next 禁用, prev 启用")
def test_step2_buttons():
    for role in ("source", "target"):
        t = T()
        try:
            t.select_role(role)
            t.app.go_step(2)
            t.app.update_idletasks()
            eq(t.step, 2, f"{role} 应处于 step2")
            check(t.transfer_btn_visible, "step2 应显示开始传输按钮")
            eq(t.transfer_btn_state, "normal", "step2 开始传输按钮应启用")
            eq(t.transfer_btn_text, "开始传输", "step2 开始传输按钮文字应为[开始传输]")
            eq(t.next_state, "disabled", "step2 下一步应禁用 (通过开始传输进入传输)")
            eq(t.prev_state, "normal", "step2 上一步应启用")
        finally:
            t.destroy()


@test("A4. step3 传输页(未完成): 发送端显示验证码横幅, next 禁用, prev 启用")
def test_step3_transferring():
    t = T()
    try:
        t.select_role("source")
        t.ctl._auth_code = "TEST"
        t.app.go_step(3)
        t.app.update_idletasks()
        eq(t.step, 3)
        check(t.app._auth_banner_frame.winfo_ismapped(), "发送端传输页应显示验证码横幅")
        eq(str(t.app.tk_label_transfer_auth_code.cget("text")), "TEST",
           "验证码横幅应显示当前验证码")
        eq(t.next_state, "disabled", "传输未完成时下一步应禁用")
        eq(t.prev_state, "normal", "传输页上一步应启用")
        check(not t.transfer_btn_visible, "传输页不应显示开始传输按钮")
    finally:
        t.destroy()


@test("A5. step3 传输完成: 发送端 next 禁用(即结束), 接收端 next=[查看总结 >]")
def test_step3_done():
    # 发送端
    t = T()
    try:
        t.select_role("source")
        t.ctl._transfer_done = True
        t.app.go_step(3)
        t.app.update_idletasks()
        eq(t.next_state, "disabled", "发送端传输完成即结束, 下一步应禁用")
        eq(t.prev_state, "normal", "发送端完成页上一步仍可返回")
    finally:
        t.destroy()
    # 接收端
    t = T()
    try:
        t.select_role("target")
        t.ctl._transfer_done = True
        t.app.go_step(3)
        t.app.update_idletasks()
        eq(t.next_state, "normal", "接收端传输完成后下一步应启用")
        eq(t.next_text, "查看总结 >", "接收端传输完成后下一步文字应为[查看总结 >]")
    finally:
        t.destroy()


@test("A6. step4 传输总结页(接收端): next=[完成], 查看校验报告禁用, 完成并退出启用")
def test_step4_summary():
    t = T()
    try:
        t.select_role("target")
        t.ctl._transfer_done = True
        t.app.go_step(4)
        t.app.update_idletasks()
        eq(t.step, 4)
        eq(t.next_state, "normal", "总结页下一步应启用")
        eq(t.next_text, "完成", "总结页下一步文字应为[完成]")
        eq(t.prev_state, "normal", "总结页上一步应启用")
        eq(str(t.app.tk_button_view_report.cget("state")), "disabled",
           "校验未完成时[查看校验报告]应禁用")
        eq(str(t.app.tk_button_summary_done.cget("state")), "normal",
           "[完成并退出]按钮应启用")
        eq(str(t.app.tk_button_summary_done.cget("text")), "完成并退出",
           "总结页底部按钮文字应为[完成并退出]")
    finally:
        t.destroy()


@test("A7. step5 完成页(发送端): prev 禁用, next=[完成], 完成并关闭启用")
def test_step5_done():
    t = T()
    try:
        t.select_role("source")
        t.app.go_step(5)
        t.app.update_idletasks()
        eq(t.step, 5)
        eq(t.prev_state, "disabled", "完成页上一步应禁用")
        eq(t.next_state, "normal", "完成页下一步应启用")
        eq(t.next_text, "完成", "完成页下一步文字应为[完成]")
        eq(str(t.app.tk_button_source_done.cget("state")), "normal",
           "[完成并关闭]按钮应启用")
        eq(str(t.app.tk_button_source_done.cget("text")), "完成并关闭",
           "完成页按钮文字应为[完成并关闭]")
    finally:
        t.destroy()


@test("A8. 传输进行中: 开始传输按钮禁用 (防重复点击)")
def test_transferring_btn_disabled():
    t = T()
    try:
        t.select_role("target")
        t.app.go_step(2)
        t.app.update_idletasks()
        t.ctl._transferring = True
        t.app.go_step(2)
        t.app.update_idletasks()
        eq(t.transfer_btn_state, "disabled", "传输进行中开始传输按钮应禁用")
        t.ctl._transferring = False
    finally:
        t.destroy()


@test("A9. 接收端未完成时回退到 step2: 开始传输按钮文字恢复为[重新接收]")
def test_retry_btn_on_back():
    t = T()
    try:
        t.select_role("target")
        t.app.go_step(3)
        t.app.update_idletasks()
        t.ctl._on_prev_step()  # 3 → 2
        t.app.update_idletasks()
        eq(t.step, 2, "回退应回到连接页")
        eq(t.transfer_btn_text, "重新接收", "接收端未完成回退连接页, 按钮应为[重新接收]")
        eq(t.transfer_btn_state, "normal", "重新接收按钮应启用")
    finally:
        t.destroy()


# ============================================================
# 套件2: 传输过程中断网的表现
# ============================================================

@test("B1. 接收端断网: 提示[网络连接已中断], 不完成/不发 done/不进入配置导入")
def test_target_network_down():
    reset_server_flags()
    t = T()
    try:
        t.select_role("target")
        t.app.go_step(3)
        t.app.update_idletasks()
        calls = []
        verified = [False]
        t.ctl._report_to_source = lambda payload: calls.append(payload)
        t.ctl._start_auto_verification = lambda: verified.__setitem__(0, True)
        t.ctl._network_down = True
        step_before = t.step
        t.ctl._on_download_complete(False, 100, 0, [])
        t.app.update_idletasks()

        eq(t.ctl._network_down, False, "处理后断网标志应复位")
        eq(t.ctl._transfer_done, False, "断网不得标记为传输完成")
        eq(len(calls), 0, "断网不得向源端上报 done")
        eq(verified[0], False, "断网不得启动校验")
        eq(t.step, step_before, "断网不得跳转页面 (不进入配置导入/总结页)")
        eq(getattr(t.ctl, "_auto_import_active", False), False, "断网不得触发自动导入")
        eq(t.next_state, "disabled", "断网后下一步应禁用")
        eq(t.prev_text, "< 返回", "断网后上一步文字应为[< 返回]")
        eq(t.transfer_btn_text, "重新接收", "断网后开始传输按钮应为[重新接收]")
        eq(t.transfer_btn_state, "normal", "断网后重新接收按钮应启用")
        txt = str(t.app.tk_label_transfer_error.cget("text"))
        check("网络连接已中断" in txt, f"应提示网络连接已中断, 实际: {txt}")
    finally:
        t.destroy()


@test("B2. 发送端: 接收端长时间无请求且无 done → 提示连接中断, 停服务器, 不进完成页")
def test_source_interrupted():
    reset_server_flags()
    t = T()
    try:
        t.select_role("source")
        t.app.go_step(3)
        t.app.update_idletasks()
        fake = FakeFileServer()
        t.ctl._file_server = fake
        t.ctl._source_done_after = None
        FileServerHandler.ever_connected = True
        FileServerHandler.transfer_done_flag = False
        FileServerHandler.last_activity = time.time() - SOURCE_IDLE_TIMEOUT - 1
        t.ctl._poll_source_done()
        t.app.update_idletasks()

        check(getattr(t.ctl, "_source_interrupted_marked", False),
              "空闲超时无 done 应标记连接中断")
        check(not getattr(t.ctl, "_source_done_marked", False),
              "中断不得进入完成页")
        eq(fake.stopped, True, "中断后应停止文件服务器")
        eq(t.transfer_btn_text, "重新启动传输", "中断后开始传输按钮应为[重新启动传输]")
        eq(t.transfer_btn_state, "normal", "中断后重新启动传输按钮应启用")
        txt = str(t.app.tk_label_transfer_error.cget("text"))
        check("连接已中断" in txt, f"应提示连接已中断, 实际: {txt}")
    finally:
        t.destroy()


# ============================================================
# 套件3: 验证码错误 / 未输入正确验证码的表现
# ============================================================

@test("C1. 接收端验证码错误(files==0): 返回修改验证码, 重试按钮, 不完成/不发 done")
def test_target_auth_failed():
    reset_server_flags()
    t = T()
    try:
        t.select_role("target")
        t.app.go_step(3)
        t.app.update_idletasks()
        calls = []
        verified = [False]
        t.ctl._report_to_source = lambda payload: calls.append(payload)
        t.ctl._start_auto_verification = lambda: verified.__setitem__(0, True)
        step_before = t.step
        t.ctl._on_download_complete(False, 0, 0, [])
        t.app.update_idletasks()

        eq(t.ctl._transfer_done, False, "验证码错误不得标记为传输完成")
        eq(len(calls), 0, "验证码错误不得向源端上报 done")
        eq(verified[0], False, "验证码错误不得启动校验")
        eq(t.step, step_before, "验证码错误不得跳转页面")
        eq(t.next_state, "disabled", "验证码错误后下一步应禁用")
        eq(t.prev_text, "< 返回修改验证码", "验证码错误后上一步应引导返回修改验证码")
        eq(t.transfer_btn_text, "重试接收", "验证码错误后开始传输按钮应为[重试接收]")
        eq(t.transfer_btn_state, "normal", "重试接收按钮应启用")
        txt = str(t.app.tk_label_transfer_error.cget("text"))
        check("验证码错误" in txt, f"应提示验证码错误, 实际: {txt}")
    finally:
        t.destroy()


@test("C2. 发送端: 验证码错误 → 提示但不误判完成/中断, 保持服务器运行, 修正后恢复")
def test_source_auth_failed():
    reset_server_flags()
    t = T()
    try:
        t.select_role("source")
        t.app.go_step(3)
        t.app.update_idletasks()
        fake = FakeFileServer()
        t.ctl._file_server = fake
        t.ctl._source_done_after = None
        FileServerHandler.ever_connected = True
        FileServerHandler.transfer_done_flag = False
        FileServerHandler.auth_failed_flag = True
        FileServerHandler.last_activity = time.time() - SOURCE_IDLE_TIMEOUT - 1
        t.ctl._poll_source_done()
        t.app.update_idletasks()

        check(getattr(t.ctl, "_source_auth_notified", False), "应提示验证码有误")
        check(not getattr(t.ctl, "_source_done_marked", False), "验证码错误不得进入完成页")
        check(not getattr(t.ctl, "_source_interrupted_marked", False),
              "验证码错误不得判定为网络中断")
        eq(fake.stopped, False, "验证码错误时不得停止文件服务器 (等待重试)")
        check(t.ctl._source_done_after is not None, "验证码错误后应继续轮询等待重试")
        txt = str(t.app.tk_label_transfer_error.cget("text"))
        check("验证码有误" in txt, f"应提示验证码有误, 实际: {txt}")

        # 接收端修正验证码后重试成功 → 鉴权通过清除 auth_failed_flag → 恢复正常轮询
        t.ctl._source_auth_notified = False
        FileServerHandler.auth_failed_flag = False
        FileServerHandler.last_activity = time.time()  # 有请求活动, 未超时
        t.ctl._source_done_after = None
        t.ctl._poll_source_done()
        check(not getattr(t.ctl, "_source_done_marked", False),
              "鉴权恢复但未收到 done 前不得进入完成页")
        check(not getattr(t.ctl, "_source_interrupted_marked", False),
              "有活动请求时不得判定为中断")
        check(t.ctl._source_done_after is not None, "恢复正常后应继续轮询")
    finally:
        t.destroy()


@test("C3. HTTP 层: 未输入/错误验证码 → 403 + auth_failed_flag; 正确 → 200 并清除")
def test_http_auth():
    import tls_utils
    import tempfile
    reset_server_flags()
    src = tempfile.mkdtemp(prefix="auth_src_")
    srv = FileServer(partition_map={"D": src}, auth_code="TEST",
                     cert_paths=tls_utils.get_or_create_fixed_cert())
    srv.start()
    time.sleep(0.8)
    base = f"https://127.0.0.1:{TRANSFER_PORT}"
    try:
        # 未输入验证码 → 403
        eq(_http_status(f"{base}/ping"), 403, "缺少验证码应 403")
        eq(FileServerHandler.auth_failed_flag, True, "缺少验证码应置 auth_failed_flag")
        # 错误验证码 → 403
        eq(_http_status(f"{base}/ping?pwd=WRNG"), 403, "错误验证码应 403")
        eq(FileServerHandler.auth_failed_flag, True, "错误验证码应置 auth_failed_flag")
        # 未输入验证码的 /report (接收端完成上报) → 同样 403 且置标志
        body = json.dumps({"done": True}).encode("utf-8")
        ctx = ssl._create_unverified_context()
        try:
            req = urllib.request.Request(f"{base}/report", data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10, context=ctx):
                pass
            raise AssertionError("/report 缺少验证码应 403")
        except urllib.error.HTTPError as e:
            eq(e.code, 403, "/report 缺少验证码应 403")
        eq(FileServerHandler.auth_failed_flag, True, "/report 缺少验证码应置标志")
        # 正确验证码 → 200 且清除 auth_failed_flag
        eq(_http_status(f"{base}/ping?pwd=TEST"), 200, "正确验证码应 200")
        eq(FileServerHandler.auth_failed_flag, False, "鉴权成功后应清除 auth_failed_flag")
    finally:
        srv.stop()
        try:
            import shutil
            shutil.rmtree(src, ignore_errors=True)
        except Exception:
            pass


# ============================================================
# 测试步骤总结 (人工可执行步骤)
# ============================================================
STEPS_SUMMARY = """
============================== 测试步骤总结 (可人工复现) ==============================
【前置】两台设备接好网线; 各自启动磁盘拷贝应用; 勾选"我已阅读并同意《用户协议》"。

---- 套件1: 页面按钮状态检查 ----
S1 角色选择页(步骤0): 上一步/下一步均灰色禁用; 未勾选协议时"选择发送方/选择接收方"灰色,
   勾选后变亮; 无"开始传输"按钮。
S2 高级设置页(步骤1): 发送端显示"导出系统配置"按钮, 接收端不显示; 上一步/下一步均可点。
S3 连接页(步骤2): "开始传输"绿色按钮显示且可点; 下一步禁用(必须用开始传输进入传输);
   上一步可点。接收端此处有"寻找旧电脑"。
S4 传输页(步骤3, 未完成): 发送端顶部显示红色验证码横幅(与接收端输入一致); 下一步禁用;
   上一步可点; 无"开始传输"按钮。
S5 传输完成: 发送端下一步保持禁用(传输完成即结束); 接收端下一步变为"查看总结 >"且可点。
S6 总结页(步骤4, 接收端): 下一步为"完成"; "查看校验报告"在校验完成前灰色;
   "完成并退出"绿色可点。
S7 完成页(步骤5, 发送端): 上一步禁用; 下一步为"完成"; "完成并关闭"可点。
S8 传输进行中: 开始传输按钮禁用(防止重复点击)。
S9 接收端未完成时点"上一步"回连接页: "开始传输"文字变为"重新接收"。

---- 套件2: 传输中断网 ----
S10 拷贝进行中直接拔掉网线:
   - 接收端: 约 30-60 秒内提示"网络连接已中断", 上一步变为"< 返回", 按钮变"重新接收",
     停留传输页, 不自动进入配置导入/总结页。
   - 发送端: 约 45 秒后提示"与接收端的连接已中断", 按钮变"重新启动传输",
     不进入"传输完成"页。
S11 恢复网线后, 接收端点"重新接收"重新开始传输, 发送端点"重新启动传输"重新等待。

---- 套件3: 验证码错误 ----
S12 接收端输入错误验证码或留空后点"开始传输":
   - 接收端: 显示红色"验证码错误 — 请输入正确的验证码后重新连接", 上一步变
     "< 返回修改验证码", 按钮变"重试接收", 不进入任何完成/导入流程。
   - 发送端: 约 45 秒后提示"接收端输入的验证码有误", 服务器保持运行等待重试,
     不显示"传输完成"、也不误报"网络中断"。
S13 接收端点"< 返回修改验证码"修正为正确验证码后"重试接收":
   - 传输正常开始; 完成后发送端进入"传输完成"页、接收端进入总结/自动导入流程。
S14 HTTP 层自检: 不带验证码或错误验证码访问发送端接口一律 403 并记录"验证码错误"标志;
   携带正确验证码后恢复 200。
=====================================================================================
"""

print("===== 按钮状态 + 断网/验证码 综合测试 =====")
for fn in [test_step0_buttons, test_step1_buttons, test_step2_buttons,
           test_step3_transferring, test_step3_done, test_step4_summary,
           test_step5_done, test_transferring_btn_disabled, test_retry_btn_on_back,
           test_target_network_down, test_source_interrupted,
           test_target_auth_failed, test_source_auth_failed, test_http_auth]:
    fn()
print("\n".join(results))
print(f"\n总计: {len(results)}  通过: {len(results) - len(errors)}  失败: {len(errors)}")
if errors:
    print("存在失败!")
    print(STEPS_SUMMARY)
    sys.exit(1)
print("全部通过!")
print(STEPS_SUMMARY)
