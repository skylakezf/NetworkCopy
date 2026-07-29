"""
本代码由[Tkinter布局助手]生成
官网:https://www.pytk.net
QQ交流群:905019785
在线反馈:https://support.qq.com/product/618914
"""
import atexit
# 导入布局文件
from ui import Win as MainWin
# 导入窗口控制器
from control import Controller as MainUIController
# 将窗口控制器 传递给UI
app = MainWin(MainUIController())


def _on_close():
    """窗口关闭按钮 → 清理所有后台进程后退出"""
    try:
        app.ctl.shutdown()
    except Exception:
        pass
    try:
        app.destroy()
    except Exception:
        pass


# 注册窗口关闭协议 (点击 X 按钮)
app.protocol("WM_DELETE_WINDOW", _on_close)


@atexit.register
def _atexit_cleanup():
    """atexit 兜底: 即使未走 _on_close (如 os._exit / 异常传播), 也尝试清理"""
    try:
        app.ctl.shutdown()
    except Exception:
        pass


if __name__ == "__main__":
    # 启动
    app.mainloop()