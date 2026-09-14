"""
断线处理专项测试: 修复"拷贝过程中网络断开, 发送方误判完成 / 接收方误入配置导入"

覆盖:
  A. 源端 is_transfer_done: 空闲超时 + 无 done → 不得判为"完成"
  B. 源端 is_transfer_done: 显式 /report done → 判为"完成"
  C. 接收端 _on_download_complete(部分文件失败) → 不进入完成流程/配置导入, 不发 done
  D. 接收端 _on_download_complete(成功) → 上报 done, 进入完成流程
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import sys, os, threading, traceback, time

os.environ["NETCOPY_SKIP_EULA_BROWSER"] = "1"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_ROOT)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, 'python-3.13.14-embed-amd64',
                                'Lib', 'site-packages'))

from ui import WinGUI
from control import Controller, SOURCE_IDLE_TIMEOUT
from file_transfer import FileServer, FileServerHandler

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
        # 模拟 _connect_and_download 会设置的属性
        self.ctl._pre_verified_file = None

    def select_role(self, role):
        self.app._on_select_role(role)
        self.app.update_idletasks()

    @property
    def step(self):
        return self.app._step


def reset_server_flags():
    FileServerHandler.transfer_done_flag = False
    FileServerHandler.ever_connected = False
    FileServerHandler.last_activity = 0.0
    FileServerHandler.active_requests = 0


@test("A. 源端: 接收端曾连接但空闲超时且无 done → 不判为完成 (断网不得误判完成)")
def test_source_idle_not_done():
    reset_server_flags()
    fs = FileServer.__new__(FileServer)
    FileServerHandler.ever_connected = True
    FileServerHandler.last_activity = time.time() - 60  # 空闲 60 秒
    eq(fs.is_transfer_done(), False, "空闲超时且未收到 /report done 应视为传输未完成")


@test("B. 源端: 收到显式 /report done → 判为完成")
def test_source_done_flag():
    reset_server_flags()
    fs = FileServer.__new__(FileServer)
    FileServerHandler.transfer_done_flag = True
    eq(fs.is_transfer_done(), True, "显式 /report done 应判为完成")


@test("C. 接收端: 部分文件失败(非断网标志) → 不进入完成/配置导入流程, 不发 done")
def test_target_partial_failure():
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
        t.ctl._on_download_complete(False, 5, 1024, ["传输中断"])

        eq(t.ctl._transfer_done, False, "传输失败不得标记为已传输完成")
        eq(len(calls), 0, "传输失败不得向源端上报 done")
        eq(verified[0], False, "传输失败不得启动校验")
        eq(t.step, step_before, "传输失败不得跳转到总结/配置导入页")
        eq(str(t.app.tk_button_mqfzl35t.cget("text")), "重新接收",
           "传输失败按钮应恢复为[重新接收]")
        txt = str(t.app.tk_label_transfer_error.cget("text"))
        check("传输未完成" in txt, f"应显示传输未完成提示, 实际: {txt}")
    finally:
        t.app.destroy()


@test("D. 接收端: 传输成功 → 上报 done, 进入完成流程")
def test_target_success():
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

        t.ctl._on_download_complete(True, 10, 2048, [])

        eq(t.ctl._transfer_done, True, "传输成功应标记为已传输完成")
        eq(calls, [{"done": True}], "传输成功应向源端上报 done")
        eq(verified[0], True, "传输成功应启动校验")
    finally:
        t.app.destroy()


@test("E. 源端轮询: 空闲超时且无 done → 提示中断, 不进入完成页")
def test_poll_source_interrupt():
    reset_server_flags()
    t = T()
    try:
        t.select_role("source")
        FileServerHandler.ever_connected = True
        FileServerHandler.transfer_done_flag = False
        FileServerHandler.last_activity = time.time() - SOURCE_IDLE_TIMEOUT - 1
        t.ctl._file_server = FileServer.__new__(FileServer)
        t.ctl._source_done_after = None
        t.ctl._poll_source_done()
        check(getattr(t.ctl, "_source_interrupted_marked", False),
              "长时间无请求且无 done 应标记为连接中断")
        check(not getattr(t.ctl, "_source_done_marked", False),
              "不得进入完成页标记")
    finally:
        t.app.destroy()


@test("F. 源端轮询: 收到 done → 正常进入完成页")
def test_poll_source_done():
    reset_server_flags()
    t = T()
    try:
        t.select_role("source")
        FileServerHandler.transfer_done_flag = True
        FileServerHandler.ever_connected = True
        FileServerHandler.last_activity = time.time()
        t.ctl._file_server = FileServer.__new__(FileServer)
        t.ctl._source_done_after = None
        t.ctl._poll_source_done()
        check(getattr(t.ctl, "_source_done_marked", False),
              "收到 done 应标记为完成")
        check(not getattr(t.ctl, "_source_interrupted_marked", False),
              "收到 done 不得标记为中断")
    finally:
        t.app.destroy()


@test("G. 源端轮询: 接收端验证码错误 → 提示验证码错误, 不判中断/完成")
def test_poll_source_auth_failed():
    reset_server_flags()
    t = T()
    try:
        t.select_role("source")
        FileServerHandler.ever_connected = True
        FileServerHandler.transfer_done_flag = False
        FileServerHandler.auth_failed_flag = True
        FileServerHandler.last_activity = time.time() - SOURCE_IDLE_TIMEOUT - 1
        t.ctl._file_server = FileServer.__new__(FileServer)
        t.ctl._source_done_after = None
        t.ctl._poll_source_done()
        check(getattr(t.ctl, "_source_auth_notified", False),
              "验证码错误应标记为已提示")
        check(not getattr(t.ctl, "_source_done_marked", False),
              "验证码错误不得进入完成页")
        check(not getattr(t.ctl, "_source_interrupted_marked", False),
              "验证码错误不得判定为网络中断")
        # 接收端修正验证码重试成功 → auth_failed_flag 清除 → 后续不再按验证码错误提示
        FileServerHandler.auth_failed_flag = False
        FileServerHandler.last_activity = time.time()
        t.ctl._source_auth_notified = False
        t.ctl._poll_source_done()
        check(getattr(t.ctl, "_source_done_marked", False) is False,
              "鉴权成功后未完成前不得进入完成页")
    finally:
        t.app.destroy()


print("===== 断线处理专项测试 =====")
for fn in [test_source_idle_not_done, test_source_done_flag,
           test_target_partial_failure, test_target_success,
           test_poll_source_interrupt, test_poll_source_done,
           test_poll_source_auth_failed]:
    fn()
print("\n".join(results))
print(f"\n总计: {len(results)}  通过: {len(results) - len(errors)}  失败: {len(errors)}")
if errors:
    print("存在失败!")
    sys.exit(1)
print("全部通过!")
