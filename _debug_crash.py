"""验证: 后台线程调用 tkinter 接口 (after/log) 的实际行为"""
import os, sys, time, threading, traceback
os.environ["NETCOPY_SKIP_EULA_BROWSER"] = "1"
os.chdir(r'c:\Users\Xinyi\Desktop\网络拷贝\NetworkzCopy')
sys.path.insert(0, '.')
sys.path.insert(0, os.path.join(os.getcwd(), 'python-3.13.14-embed-amd64', 'Lib', 'site-packages'))
from ui import WinGUI
from control import Controller

app = WinGUI()
ctl = Controller()
ctl.init(app)
app.ctl = ctl

called = threading.Event()
def cb():
    called.set()
    print(">>> after 回调已执行 (主线程)")

def worker():
    try:
        app.after(0, cb)
        print(">>> [线程] app.after(0, cb) 调用返回 (无异常)")
    except Exception as e:
        print(">>> [线程] app.after 异常:", type(e).__name__, e)
    try:
        ctl._log("来自后台线程的日志")
        print(">>> [线程] ctl._log() 调用返回 (无异常)")
    except Exception as e:
        print(">>> [线程] ctl._log 异常:", type(e).__name__, e)

threading.Thread(target=worker, daemon=True).start()

deadline = time.time() + 5
while time.time() < deadline:
    app.update()
    time.sleep(0.03)

print(">>> 5 秒后 after 回调执行:", called.is_set())
app.destroy()
