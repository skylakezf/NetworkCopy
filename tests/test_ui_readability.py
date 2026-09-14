"""UI 可读性验证 (2026-08-28):
1. 接收端设备发现页: 「手动导入系统配置」与「高级: 手动输入 IP 地址」同 Y 轴 (并排)
2. 总结页 (step4): 「完成并退出」按钮在窗口可视范围内 (不被内容顶出)
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import os, sys, time
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
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, detail)

app = WinGUI()
ctl = Controller()
ctl.init(app)
app.ctl = ctl

# 勾选 EULA 后进入接收端 (target) 角色 → 步骤 1 设备发现页
app._eula_var.set(True)
app._on_eula_toggle()
app._on_select_role("target")
app.update()
time.sleep(0.3)
app.update()

# ---- 验证 1: 两个复选框同 Y 轴 (并排) ----
opt_row = getattr(app, "_target_opt_row", None)
check("选项行存在", opt_row is not None)
if opt_row is not None:
    row_mapped = opt_row.winfo_ismapped()
    check("选项行已显示 (接收端)", row_mapped)
    kids = opt_row.winfo_children()
    check("选项行含 2 个复选框", len(kids) == 2)
    if len(kids) == 2:
        y0, y1 = kids[0].winfo_rooty(), kids[1].winfo_rooty()
        check("两复选框 Y 轴一致 (并排)", abs(y0 - y1) <= 2, f"y0={y0}, y1={y1}")
        x0, x1 = kids[0].winfo_rootx(), kids[1].winfo_rootx()
        check("两复选框 X 轴不同 (水平排列)", x1 > x0, f"x0={x0}, x1={x1}")

# ---- 验证 2: 总结页「完成并退出」按钮可见 ----
ctl.ui.go_step(4)
app.update()
time.sleep(0.2)
app.update()

btn = getattr(app, "tk_button_summary_done", None)
check("总结页存在完成按钮", btn is not None)
if btn is not None:
    btn_mapped = btn.winfo_ismapped()
    check("完成按钮已显示", btn_mapped)
    text = btn.cget("text")
    check("按钮文字为「完成并退出」", text == "完成并退出", f"text={text!r}")
    win_top = app.winfo_rooty()
    win_bottom = win_top + app.winfo_height()
    btn_top = btn.winfo_rooty()
    btn_bottom = btn_top + btn.winfo_height()
    check("完成按钮在窗口可视范围内",
          btn_mapped and btn_top >= win_top - 1 and btn_bottom <= win_bottom + 1,
          f"btn=[{btn_top},{btn_bottom}], win=[{win_top},{win_bottom}]")

    # ---- 验证 3: 导入日志已隐藏 ----
    log_text = getattr(app, "tk_text_import_log", None)
    check("导入日志控件存在 (属性保留)", log_text is not None)
    if log_text is not None:
        check("导入日志未显示 (已隐藏)", not log_text.winfo_ismapped())

fails = [n for n, ok in results if not ok]
print("\n===== 结果:", f"{len(results)-len(fails)}/{len(results)} 通过", "=====")
app.destroy()
sys.exit(1 if fails else 0)
