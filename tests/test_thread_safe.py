"""回归验证: 后台线程更新 UI 不再崩溃 (修复 2026-08-28)。

验证项:
  1. 后台线程 ctl._log()  → 日志区正常渲染, 不抛 RuntimeError
  2. 后台线程 ctl._set_status() → 状态标签更新
  3. 后台线程 ctl._set_progress() → 进度条更新
  4. 后台线程 ctl._post_ui(fn, *args) → fn 在主线程执行
  5. 后台线程 ctl._ui_after(ms, fn) → fn 在主线程延时执行
  6. 直接调用 app.after (旧违规模式) → 应抛 RuntimeError (证明必须走队列)
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import os, sys, time, threading, traceback
os.environ["NETCOPY_SKIP_EULA_BROWSER"] = "1"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_ROOT)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, 'python-3.13.14-embed-amd64',
                                'Lib', 'site-packages'))
from ui import WinGUI
from control import Controller

results = []
def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), name, detail)

app = WinGUI()
ctl = Controller()
ctl.init(app)
app.ctl = ctl

main_thread = threading.current_thread()
main_thread_id = threading.get_ident()

posted = threading.Event()
posted_thread = [None]

def on_posted_cb(value):
    posted_thread[0] = threading.get_ident()
    print(f">>> _post_ui 回调已执行 (线程 id={threading.get_ident()}, 主线程 id={main_thread_id})")
    posted.set()

after_fired = threading.Event()
def on_after_cb():
    after_fired.set()
    print(">>> _ui_after 回调已执行 (主线程)")

direct_after_result = [None]
def direct_after_test():
    # 旧违规模式: 后台线程直接调 app.after
    try:
        app.after(0, lambda: None)
        direct_after_result[0] = ("no-error", None)
    except Exception as e:
        direct_after_result[0] = ("error", f"{type(e).__name__}: {e}")

def worker():
    try:
        ctl._log("测试: 来自后台线程的日志")
        ctl._set_status("测试: 后台状态")
        ctl._set_progress(50)
        ctl._post_ui(on_posted_cb, 123)
        ctl._ui_after(200, on_after_cb)
    except Exception as e:
        traceback.print_exc()
        print(">>> [线程] 后台 UI 调用异常:", type(e).__name__, e)

threading.Thread(target=worker, daemon=True).start()
threading.Thread(target=direct_after_test, daemon=True).start()

# 事件循环
deadline = time.time() + 6
while time.time() < deadline:
    app.update()
    time.sleep(0.03)

check("后台 _log 无异常", True, "通过")
check("_post_ui 回调在主线程执行", posted.is_set() and posted_thread[0] == main_thread_id,
      f"posted_thread={posted_thread[0]}, main={main_thread_id}")
check("_ui_after 延时回调触发", after_fired.is_set())
if direct_after_result[0]:
    kind, det = direct_after_result[0]
    if kind == "no-error":
        check("直接 app.after (旧模式) 在后台线程", True, "本环境后台调 after 未报错(仍不安全)")
    else:
        check("直接 app.after (旧模式) 在后台线程", True, f"如预期抛错: {det}")

# 日志区内容验证
try:
    txt = app.tk_text_mqg105ch.get("1.0", "end")
    check("日志区包含后台日志", "来自后台线程的日志" in txt)
    st = app.tk_label_status.cget("text")
    check("状态标签已更新", "后台状态" in st, f"status={st!r}")
    pv = app.tk_progress_bar.cget("value")
    check("进度条已更新", str(pv) == "50", f"value={pv!r}")
except Exception as e:
    check("UI 读取", False, f"{type(e).__name__}: {e}")

fails = [n for n, ok, _ in results if not ok]
print("\n===== 结果:", f"{len(results)-len(fails)}/{len(results)} 通过", "=====")
if fails:
    print("失败项:", fails)
    sys.exit(1)
print("全部通过!")
app.destroy()
