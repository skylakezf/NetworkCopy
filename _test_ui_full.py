"""
全场景 UI 准确性测试
测试所有导航路径、按钮状态、步骤跳转逻辑
"""
import sys, os, threading, traceback, types

os.chdir(r'c:\Users\Xinyi\Desktop\网络拷贝\NetworkzCopy')
sys.path.insert(0, '.')
sys.path.insert(0, os.path.join(os.getcwd(), 'python-3.13.14-embed-amd64', 'Lib', 'site-packages'))

# ============================================================
# 导入被测模块
# ============================================================
from ui import WinGUI
from control import Controller

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
        # 抑制 bgerror, 防止前序测试的残留 after 回调污染当前实例
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

    def select_role(self, role):
        self.app._on_select_role(role)
        self.app.update_idletasks()

    def set_nic(self, name="Test NIC [Realtek]"):
        cb = self.app.tk_select_box_mqfzkd6x
        cb.set(name)
        if hasattr(self.ctl, '_on_nic_selected'):
            self.ctl._on_nic_selected()
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

    def prev_step(self):
        self.ctl._on_prev_step()
        self.app.update_idletasks()

    def destroy(self):
        try:
            # 取消所有 pending after 回调, 防止污染后续 Tk 实例
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

def make_app():
    return T()

# ============================================================
# 测试 1: 初始状态
# ============================================================

@test("1. 初始状态: step=0, 两个按钮均禁用")
def test_initial():
    t = make_app()
    try:
        eq(t.step, 0)
        eq(t.next_state, "disabled")
        eq(t.prev_state, "disabled")
    finally:
        t.destroy()

# ============================================================
# 测试 2: 发送端正向导航
# ============================================================

@test("2. 发送端: 0→1 选角色后 step=1, 上一步启用, 下一步禁用(未选网卡)")
def test_source_role_select():
    t = make_app()
    try:
        t.select_role("source")
        eq(t.step, 1)
        eq(t.prev_state, "normal")
        eq(t.next_state, "disabled")
    finally:
        t.destroy()

@test("3. 发送端: step1 选网卡后下一步启用")
def test_source_nic_select():
    t = make_app()
    try:
        t.select_role("source")
        t.set_nic()
        eq(t.next_state, "normal")
    finally:
        t.destroy()

@test("4. 发送端: 1→2 前进, step=2, 下一步禁用(未选磁盘)")
def test_source_step2():
    t = make_app()
    try:
        t.select_role("source")
        t.set_nic()
        t.next_step()
        eq(t.step, 2)
        eq(t.next_state, "disabled")
    finally:
        t.destroy()

@test("5. 发送端: step2 选磁盘后下一步启用")
def test_source_disk_select():
    t = make_app()
    try:
        t.select_role("source")
        t.set_nic()
        t.next_step()
        t.set_disk()
        eq(t.next_state, "normal")
    finally:
        t.destroy()

@test("6. 发送端: 2→3 前进, step=3, 下一步禁用")
def test_source_step3():
    t = make_app()
    try:
        t.select_role("source")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        eq(t.step, 3)
        eq(t.next_state, "disabled")
    finally:
        t.destroy()

@test("7. 发送端: 传输完成后 step4 下一步启用, 文字为'校验文件 >'")
def test_source_step4_done():
    t = make_app()
    try:
        t.select_role("source")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.ctl._transfer_done = True
        t.app.go_step(4)
        t.app.update_idletasks()
        eq(t.step, 4)
        eq(t.next_state, "normal")
        eq(t.next_text, "校验文件 >")
    finally:
        t.destroy()

@test("8. 发送端: 4→6 前进, 跳过步骤5, step=6, 下一步为'跳过校验 >'")
def test_source_step6():
    t = make_app()
    try:
        t.select_role("source")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.ctl._transfer_done = True
        t.app.go_step(4)
        t.app.update_idletasks()
        t.next_step()
        eq(t.step, 6)
        eq(t.next_state, "normal")
        eq(t.next_text, "跳过校验 >")
        eq(t.prev_state, "normal")
    finally:
        t.destroy()

# ============================================================
# 测试 3: 发送端反向导航
# ============================================================

@test("9. 发送端: 6→4 回退, 跳过步骤5, step=4")
def test_source_backward_skip5():
    t = make_app()
    try:
        t.select_role("source")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.ctl._transfer_done = True
        t.app.go_step(4)
        t.app.update_idletasks()
        t.next_step()  # →6
        t.prev_step()  # →4 (skip 5)
        eq(t.step, 4)
    finally:
        t.destroy()

@test("10. 发送端: 4→3→2→1→0 完整回退")
def test_source_full_backward():
    t = make_app()
    try:
        t.select_role("source")
        t.set_nic()
        t.next_step()  # →2
        t.set_disk()
        t.next_step()  # →3
        t.ctl._transfer_done = True
        t.app.go_step(4)
        t.app.update_idletasks()
        t.next_step()  # →6

        t.prev_step()  # →4
        eq(t.step, 4)
        t.prev_step()  # →3
        eq(t.step, 3)
        t.prev_step()  # →2
        eq(t.step, 2)
        t.prev_step()  # →1
        eq(t.step, 1)
        eq(t.next_state, "normal", "回退到step1时网卡仍选中, 下一步启用")
        t.prev_step()  # →0
        eq(t.step, 0)
        eq(t.next_state, "disabled")
        eq(t.prev_state, "disabled")
    finally:
        t.destroy()

# ============================================================
# 测试 4: 接收端正向导航
# ============================================================

@test("11. 接收端: 0→1 选角色后 step=1")
def test_target_role_select():
    t = make_app()
    try:
        t.select_role("target")
        eq(t.step, 1)
        eq(t.prev_state, "normal")
        eq(t.next_state, "disabled")
    finally:
        t.destroy()

@test("12. 接收端: step4 传输完成后下一步为'导入配置 >'")
def test_target_step4():
    t = make_app()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.ctl._transfer_done = True
        t.app.go_step(4)
        t.app.update_idletasks()
        eq(t.step, 4)
        eq(t.next_text, "导入配置 >")
        eq(t.next_state, "normal")
    finally:
        t.destroy()

@test("13. 接收端: 4→5 前进, step=5, 下一步为'校验文件 >'")
def test_target_step5():
    t = make_app()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.ctl._transfer_done = True
        t.app.go_step(4)
        t.app.update_idletasks()
        t.next_step()
        eq(t.step, 5)
        eq(t.next_text, "校验文件 >")
    finally:
        t.destroy()

@test("14. 接收端: 5→6 前进, step=6, 下一步为'跳过校验 >'")
def test_target_step6():
    t = make_app()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.ctl._transfer_done = True
        t.app.go_step(4)
        t.app.update_idletasks()
        t.next_step()  # →5
        t.next_step()  # →6
        eq(t.step, 6)
        eq(t.next_state, "normal")
        eq(t.next_text, "跳过校验 >")
        eq(t.prev_state, "normal")
    finally:
        t.destroy()

# ============================================================
# 测试 5: 接收端反向导航
# ============================================================

@test("15. 接收端: 6→5→4→3→2→1→0 完整回退")
def test_target_full_backward():
    t = make_app()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()  # →2
        t.set_disk()
        t.next_step()  # →3
        t.ctl._transfer_done = True
        t.app.go_step(4)
        t.app.update_idletasks()
        t.next_step()  # →5
        t.next_step()  # →6

        t.prev_step()  # →5
        eq(t.step, 5)
        eq(t.next_text, "校验文件 >")
        t.prev_step()  # →4
        eq(t.step, 4)
        eq(t.next_text, "导入配置 >")
        t.prev_step()  # →3
        eq(t.step, 3)
        t.prev_step()  # →2
        eq(t.step, 2)
        t.prev_step()  # →1
        eq(t.step, 1)
        eq(t.next_state, "normal")
        t.prev_step()  # →0
        eq(t.step, 0)
    finally:
        t.destroy()

# ============================================================
# 测试 6: 网卡状态检查
# ============================================================

@test("16. 未选网卡时下一步禁用(所有占位值)")
def test_nic_placeholders():
    t = make_app()
    try:
        t.select_role("source")
        eq(t.step, 1)

        t.app.tk_select_box_mqfzkd6x.set("扫描中...")
        eq(t.next_state, "disabled", "'扫描中...' 应禁用")

        t.app.tk_select_box_mqfzkd6x.set("未检测到网卡")
        eq(t.next_state, "disabled", "'未检测到网卡' 应禁用")

        t.app.tk_select_box_mqfzkd6x.set("")
        eq(t.next_state, "disabled", "'' 应禁用")

        t.app.tk_select_box_mqfzkd6x.set("网卡1")
        eq(t.next_state, "disabled", "'网卡1' 应禁用")
    finally:
        t.destroy()

@test("17. 真实网卡名启用下一步")
def test_nic_real():
    t = make_app()
    try:
        t.select_role("source")
        t.set_nic("Realtek PCIe GbE Family Controller")
        eq(t.next_state, "normal")
    finally:
        t.destroy()

# ============================================================
# 测试 7: 磁盘状态检查
# ============================================================

@test("18. 未选磁盘时下一步禁用(所有占位值)")
def test_disk_placeholders():
    t = make_app()
    try:
        t.select_role("source")
        t.set_nic()
        t.next_step()
        eq(t.step, 2)

        t.app.tk_select_box_mqfzmzbe.set("扫描中...")
        eq(t.next_state, "disabled")

        t.app.tk_select_box_mqfzmzbe.set("未检测到磁盘")
        eq(t.next_state, "disabled")

        t.app.tk_select_box_mqfzmzbe.set("请先选择设备类型")
        eq(t.next_state, "disabled")
    finally:
        t.destroy()

@test("19. 真实磁盘名启用下一步")
def test_disk_real():
    t = make_app()
    try:
        t.select_role("source")
        t.set_nic()
        t.next_step()
        t.set_disk("磁盘 0 (ST1000DM010-2EP102)")
        eq(t.next_state, "normal")
    finally:
        t.destroy()

# ============================================================
# 测试 8: 传输未完成时不可前进
# ============================================================

@test("20. 传输未完成 step4 下一步禁用, 完成后启用")
def test_transfer_not_done():
    t = make_app()
    try:
        t.select_role("source")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.app.go_step(4)
        t.app.update_idletasks()
        eq(t.step, 4)
        eq(t.next_state, "disabled", "传输未完成应禁用")

        t.ctl._transfer_done = True
        t.app.go_step(4)
        t.app.update_idletasks()
        eq(t.next_state, "normal", "传输完成后应启用")
    finally:
        t.destroy()

@test("20b. 步骤4 边传边校验状态行存在且可更新")
def test_verify_online_label():
    t = make_app()
    try:
        t.select_role("source")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.app.go_step(4)
        t.app.update_idletasks()
        eq(t.step, 4)
        check(hasattr(t.app, "tk_label_verify_online"),
              "步骤4 应存在边传边校验状态行 (tk_label_verify_online)")
        t.app.set_verify_online_status("边传边校验：已确认 3/5 个文件")
        t.app.update_idletasks()
        eq(t.app.tk_label_verify_online.cget("text"), "边传边校验：已确认 3/5 个文件",
           "set_verify_online_status 应更新标签文本")
        t.app.hide_transfer_error()
        t.app.update_idletasks()
        eq(t.app.tk_label_verify_online.cget("text"), "边传边校验：等待中",
           "hide_transfer_error 应重置状态行为等待中")
    finally:
        t.destroy()

# ============================================================
# 测试 9: 步骤间跳转保留网卡/磁盘选择
# ============================================================

@test("21. 回退到 step1 网卡选择保留")
def test_nic_preserved():
    t = make_app()
    try:
        t.select_role("source")
        t.set_nic("My NIC Adapter")
        t.next_step()  # →2
        t.prev_step()  # →1
        eq(t.app.tk_select_box_mqfzkd6x.get(), "My NIC Adapter")
        eq(t.next_state, "normal", "回退后网卡仍选中, 下一步启用")
    finally:
        t.destroy()

@test("22. 回退到 step2 磁盘选择保留")
def test_disk_preserved():
    t = make_app()
    try:
        t.select_role("source")
        t.set_nic()
        t.next_step()
        t.set_disk("磁盘 1 (WDC WD10EZEX)")
        t.next_step()  # →3
        t.prev_step()  # →2
        eq(t.app.tk_select_box_mqfzmzbe.get(), "磁盘 1 (WDC WD10EZEX)")
    finally:
        t.destroy()

# ============================================================
# 测试 10: 角色切换一致性
# ============================================================

@test("23. 角色切换后按钮状态一致性")
def test_role_switch():
    t = make_app()
    try:
        t.select_role("source")
        eq(t.ctl._device_type, "源设备")
        t.prev_step()  # →0

        t.select_role("target")
        eq(t.ctl._device_type, "目标设备")
        eq(t.step, 1)
        eq(t.prev_state, "normal")
        eq(t.next_state, "disabled")
    finally:
        t.destroy()

# ============================================================
# 测试 11: 发送端不应出现步骤5
# ============================================================

@test("24. 发送端永远不进入步骤5")
def test_source_no_step5():
    t = make_app()
    try:
        t.select_role("source")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.ctl._transfer_done = True
        t.app.go_step(4)
        t.app.update_idletasks()
        t.next_step()
        eq(t.step, 6, "发送端应跳到6, 不是5")
        t.prev_step()
        eq(t.step, 4, "发送端应回到4, 不是5")
    finally:
        t.destroy()

# ============================================================
# 测试 12: go_step 边界
# ============================================================

@test("25. go_step(0) 正确设置状态")
def test_gostep_0():
    t = make_app()
    try:
        t.select_role("source")
        t.set_nic()
        t.app.go_step(0)
        t.app.update_idletasks()
        eq(t.step, 0)
        eq(t.next_state, "disabled")
        eq(t.prev_state, "disabled")
    finally:
        t.destroy()

@test("26. go_step(1) 网卡已选时下一步启用")
def test_gostep_1():
    t = make_app()
    try:
        t.select_role("source")
        t.set_nic()
        t.app.go_step(1)
        eq(t.step, 1)
        eq(t.next_state, "normal")
    finally:
        t.destroy()

@test("27. go_step(1) 网卡未选时下一步禁用")
def test_gostep_1_no_nic():
    t = make_app()
    try:
        t.select_role("source")
        t.app.tk_select_box_mqfzkd6x.set("")
        t.app.go_step(1)
        eq(t.step, 1)
        eq(t.next_state, "disabled")
    finally:
        t.destroy()

# ============================================================
# 测试 13: 连续快速点击
# ============================================================

@test("28. 未选磁盘时连续点下一步不跳过步骤")
def test_rapid_click():
    t = make_app()
    try:
        t.select_role("source")
        t.set_nic()
        t.next_step()  # →2 (因为下一步在step1启用)
        eq(t.step, 2)
        # 连续点击 (下一步在step2禁用, 所以不会前进)
        t.next_step()
        eq(t.step, 2, "不应前进")
        t.next_step()
        eq(t.step, 2, "不应前进")
    finally:
        t.destroy()

# ============================================================
# 测试 14: 按钮文字变化
# ============================================================

@test("29. 发送端各步骤按钮文字")
def test_button_text_source():
    t = make_app()
    try:
        t.select_role("source")
        eq(t.next_text, "下一步 >", "step1文字")
        t.set_nic()
        t.next_step()  # →2
        eq(t.next_text, "下一步 >", "step2文字")
        t.set_disk()
        t.next_step()  # →3
        eq(t.next_text, "下一步 >", "step3文字(但禁用)")
    finally:
        t.destroy()

@test("30. 接收端各步骤按钮文字")
def test_button_text_target():
    t = make_app()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.ctl._transfer_done = True
        t.app.go_step(4)
        t.app.update_idletasks()
        eq(t.next_text, "导入配置 >", "接收端 step4 应为'导入配置 >'")
        t.next_step()
        eq(t.next_text, "校验文件 >", "step5 应为'校验文件 >'")
    finally:
        t.destroy()

# ============================================================
# 测试 15: 接收端跳过导入
# ============================================================

@test("31. 接收端跳过导入进入 step6")
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
        t.app.update_idletasks()
        t.next_step()  # →5
        eq(t.step, 5)
        t.ctl._on_skip_import()
        eq(t.step, 6)
        eq(t.next_state, "normal")
        eq(t.next_text, "跳过校验 >")
        eq(t.prev_state, "normal")
    finally:
        t.destroy()

# ============================================================
# 测试 16: 导出配置面板显隐
# ============================================================

@test("32. 发送端 step1 显示导出配置面板")
def test_export_visible_source():
    t = make_app()
    try:
        t.select_role("source")
        eq(t.step, 1)
        check(t.app._export_frame.winfo_ismapped(), "发送端应显示导出面板")
    finally:
        t.destroy()

@test("33. 接收端 step1 隐藏导出配置面板")
def test_export_hidden_target():
    t = make_app()
    try:
        t.select_role("target")
        eq(t.step, 1)
        check(not t.app._export_frame.winfo_ismapped(), "接收端应隐藏导出面板")
    finally:
        t.destroy()

# ============================================================
# 测试 17: step3 开始传输按钮状态
# ============================================================

@test("34. step3 开始传输按钮启用(网卡+设备已选)")
def test_transfer_button_enabled():
    t = make_app()
    try:
        t.select_role("source")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        eq(t.step, 3)
        eq(str(t.app.tk_button_mqfzl35t.cget("state")), "normal",
           "开始传输按钮应启用")
    finally:
        t.destroy()

# ============================================================
# 测试 18: _on_next_step 在 step1 无网卡时不前进
# ============================================================

@test("35. step1 未选网卡时 _on_next_step 不前进")
def test_next_blocked_no_nic():
    t = make_app()
    try:
        t.select_role("source")
        eq(t.step, 1)
        # 按钮禁用，但直接调用 _on_next_step 也应该被拦截
        # 实际上由按钮禁用保证，这里确认 step 不变
        saved_step = t.step
        t.next_step()
        eq(t.step, saved_step, "按钮禁用时不应前进")
    finally:
        t.destroy()

# ============================================================
# 测试 19: 接收端传输完成自动跳转 step5
# ============================================================

@test("36. 接收端 _on_download_complete 自动跳到 step5 (无配置文件时)")
def test_auto_jump_step5():
    t = make_app()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        t.ctl._transfer_done = True
        # 模拟无配置文件: monkey-patch get_config_from_ini 返回 None
        import config_transfer
        _orig = config_transfer.get_config_from_ini
        config_transfer.get_config_from_ini = lambda: (None, None)
        try:
            t.ctl._on_download_complete(True, 10, 1024*1024, [])
            eq(t.step, 5, "接收端传输完成应自动到step5")
        finally:
            config_transfer.get_config_from_ini = _orig
    finally:
        t.destroy()

@test("36b. 接收端 _on_download_complete 发现配置文件时停留在 step4")
def test_stay_step4_with_config():
    t = make_app()
    try:
        t.select_role("target")
        t.set_nic()
        t.next_step()
        t.set_disk()
        t.next_step()
        # 模拟传输已启动: 先跳到 step4 (传输页面)
        t.app.go_step(4)
        t.ctl._transfer_done = True
        import config_transfer
        _orig = config_transfer.get_config_from_ini
        config_transfer.get_config_from_ini = lambda: ("F:\\Appl\\2026-08-02", "2026-08-02 22:57:42")
        try:
            t.ctl._on_download_complete(True, 10, 1024*1024, [])
            eq(t.step, 4, "发现配置文件时应停留在step4等待用户确认")
            # 验证配置检测区域已 pack (使用 pack_info 替代 winfo_ismapped)
            try:
                t.app._config_detect_frame.pack_info()
            except Exception:
                assert False, "配置检测区域未 pack"
            # 点击"使用此配置"后应跳转到 step5
            t.ctl._on_use_detected_config()
            eq(t.step, 5, "确认配置后应跳到step5")
            # 返回 step4, 再测"跳过"按钮
            t.app.go_step(4)
            t.ctl._on_download_complete(True, 10, 1024*1024, [])
            eq(t.step, 4, "第二次也应停留在step4")
            t.ctl._on_skip_detected_config()
            eq(t.step, 5, "跳过配置后应跳到step5")
        finally:
            config_transfer.get_config_from_ini = _orig
    finally:
        t.destroy()

# ============================================================
#  新增测试 — 网卡自动选择 & 高级选项 (2026-08-02)
# ============================================================

def test_get_wired_adapters_struct():
    """get_wired_adapters() 返回正确的 6 元组结构"""
    from nic_scanner import get_wired_adapters
    wired = get_wired_adapters()
    assert isinstance(wired, list), "应返回列表"
    for item in wired:
        assert len(item) == 6, f"每项应为 6 元组, 实际 {len(item)}"
        assert isinstance(item[0], str), "display_name 应为 str"
        assert isinstance(item[1], str), "description 应为 str"
        assert isinstance(item[2], str), "adapter_name 应为 str"
        assert isinstance(item[3], str), "speed_str 应为 str"
        assert isinstance(item[4], int), "index 应为 int"
        assert isinstance(item[5], str), "ip_address 应为 str"
        assert item[4] > 0, f"网卡索引应 > 0, 实际 {item[4]}"
    print(f"  PASS test_get_wired_adapters_struct ({len(wired)} 个有线网卡)")

def test_get_wired_adapters_no_wifi():
    """get_wired_adapters() 不应包含 Wi-Fi(Type 71) 适配器"""
    from nic_scanner import get_wired_adapters
    wired = get_wired_adapters()
    # 检查返回的适配器不包含 802.11/Wi-Fi 关键词
    wifi_keywords = ["wireless", "wi-fi", "wifi", "802.11", "wlan"]
    for item in wired:
        lower_name = item[0].lower()
        lower_desc = item[1].lower()
        for kw in wifi_keywords:
            assert kw not in lower_name, f"Wi-Fi 适配器不应在结果中: {item[0]}"
            assert kw not in lower_desc, f"Wi-Fi 适配器不应在结果中: {item[1]}"
    print(f"  PASS test_get_wired_adapters_no_wifi")

def test_auto_nic_init_state():
    """Controller 初始化后 _auto_nic 和 _manual_nic 应为默认值"""
    app = T()
    try:
        eq(app.ctl._auto_nic, None, "初始化时 _auto_nic 应为 None")
        eq(app.ctl._manual_nic, False, "初始化时 _manual_nic 应为 False")
        print("  PASS test_auto_nic_init_state")
    finally:
        app.destroy()

def test_auto_select_wired_nic():
    """_auto_select_wired_nic() 在有有线网卡时设置 _auto_nic"""
    from nic_scanner import get_wired_adapters
    wired = get_wired_adapters()
    app = T()
    try:
        app.ctl._auto_select_wired_nic()
        if wired:
            eq(app.ctl._auto_nic is not None, True,
               f"有线网卡 {len(wired)} 个, _auto_nic 不应为 None")
            eq(len(app.ctl._auto_nic), 6, "_auto_nic 应为 6 元组")
        else:
            eq(app.ctl._auto_nic, None, "无有线网卡时 _auto_nic 应为 None")
        print(f"  PASS test_auto_select_wired_nic (wired={len(wired)})")
    finally:
        app.destroy()

def test_manual_nic_override():
    """手动选择网卡后 _manual_nic 置为 True"""
    app = T()
    try:
        # 等待扫描完成并用 update() 处理包含 after 回调的事件队列
        import time
        time.sleep(1.0)
        app.app.update()
        vals = list(app.app.tk_select_box_mqfzkd6x['values'])
        if not vals or vals[0] in ("扫描中...", "未检测到网卡"):
            print("  SKIP test_manual_nic_override (无可用网卡)")
            return
        cb = app.app.tk_select_box_mqfzkd6x
        cb.set(vals[0])
        app.ctl._on_nic_selected()
        eq(app.ctl._manual_nic, True, "手动选择后 _manual_nic 应为 True")
        print("  PASS test_manual_nic_override")
    finally:
        app.destroy()

def test_get_wired_nic_ips():
    """_get_wired_nic_ips() 返回 IP 地址列表"""
    app = T()
    try:
        ips = app.ctl._get_wired_nic_ips()
        assert isinstance(ips, list), "应返回列表"
        for ip in ips:
            assert "." in ip, f"IP 地址格式无效: {ip}"
            assert ip != "0.0.0.0", "不应包含 0.0.0.0"
        print(f"  PASS test_get_wired_nic_ips ({len(ips)} 个 IP)")
    finally:
        app.destroy()

def test_get_adapter_desc_from_auto():
    """_get_adapter_desc_from_auto() 返回自动网卡描述"""
    from nic_scanner import get_wired_adapters
    wired = get_wired_adapters()
    app = T()
    try:
        app.ctl._auto_select_wired_nic()
        desc = app.ctl._get_adapter_desc_from_auto()
        if wired:
            eq(desc, app.ctl._auto_nic[1], "应返回 _auto_nic 的 description")
        else:
            eq(desc, "", "无自动网卡时应返回空字符串")
        print("  PASS test_get_adapter_desc_from_auto")
    finally:
        app.destroy()

def test_auto_nic_display_update():
    """update_auto_nic_display() 正确更新标签文本"""
    app = T()
    try:
        app.app.update_auto_nic_display("Test NIC 1Gbps", "169.254.100.2",
                                        "1Gbps", 2)
        app.app.update_idletasks()

        # tk_label_auto_nic_detail 是高级选项内的详细网卡信息
        detail = app.app.tk_label_auto_nic_detail.cget("text")
        assert "Test NIC" in detail, f"详细标签应包含 NIC 名: {detail}"
        assert "169.254.100.2" in detail, f"详细标签应包含 IP: {detail}"
        assert "2 个" in detail, f"详细标签应包含数量: {detail}"

        # 无网卡情况
        app.app.update_auto_nic_display("", "", "", 0)
        app.app.update_idletasks()
        detail = app.app.tk_label_auto_nic_detail.cget("text")
        assert "未检测到" in detail, f"详细标签应显示'未检测到': {detail}"
        print("  PASS test_auto_nic_display_update")
    finally:
        app.destroy()

def test_advanced_panel_hidden_default():
    """高级选项面板默认隐藏"""
    app = T()
    try:
        assert not app.app._advanced_nic_frame.winfo_ismapped(), \
            "高级选项面板默认应隐藏"
        print("  PASS test_advanced_panel_hidden_default")
    finally:
        app.destroy()

def test_advanced_toggle_show_hide():
    """切换高级选项面板的显示/隐藏（需要先到步骤 1 页面）"""
    app = T()
    try:
        # 先选择角色进入步骤 1, 否则高级面板的父页面未 pack
        app.ctl._device_type = "源设备"
        app.select_role("source")
        eq(app.step, 1, "应在步骤 1")

        # 展开
        app.app._toggle_advanced_nic()
        app.app.update_idletasks()
        assert app.app._advanced_nic_frame.winfo_ismapped(), \
            "展开后面板应可见"
        assert "▾" in app.app._advanced_nic_toggle_btn.cget("text"), \
            "展开后按钮应为 ▾"

        # 折叠
        app.app._toggle_advanced_nic()
        app.app.update_idletasks()
        assert not app.app._advanced_nic_frame.winfo_ismapped(), \
            "折叠后面板应隐藏"
        assert "▸" in app.app._advanced_nic_toggle_btn.cget("text"), \
            "折叠后按钮应为 ▸"
        print("  PASS test_advanced_toggle_show_hide")
    finally:
        app.destroy()

def test_multisock_dhcp_init():
    """MiniDHCPServer 接受 out_ips 列表并创建多个 send socket"""
    from dhcp_server import MiniDHCPServer
    srv = MiniDHCPServer(out_ips=["169.254.100.1"])
    try:
        assert hasattr(srv, "out_ips"), "应有 out_ips 属性"
        assert not hasattr(srv, "out_ip"), "不应有旧的 out_ip 属性"
        eq(srv.out_ips, ["169.254.100.1"], "out_ips 应等于传入值")
        eq(srv._send_socks, [], "start 前 send_socks 应为空列表")
        print("  PASS test_multisock_dhcp_init")
    finally:
        try: srv.stop()
        except: pass

def test_multisock_dhcp_empty_ips():
    """MiniDHCPServer 无 out_ips 时仍然能启动（回退到监听 socket）"""
    from dhcp_server import MiniDHCPServer
    srv = MiniDHCPServer(out_ips=[])
    try:
        srv.start()
        import time
        time.sleep(0.2)
        eq(len(srv._send_socks), 0, "无 out_ips 时 send_socks 应为空")
        # 应使用监听 socket 本身发送（回退）
        srv.stop()
        print("  PASS test_multisock_dhcp_empty_ips")
    except Exception as e:
        srv.stop()
        raise e

def test_dhcp_no_out_ip_attribute():
    """MiniDHCPServer 不应有旧的 out_ip 属性（已重构为 out_ips）"""
    from dhcp_server import MiniDHCPServer
    srv = MiniDHCPServer(out_ips=["169.254.100.1"])
    try:
        assert hasattr(srv, "out_ips"), "应有 out_ips"
        assert not hasattr(srv, "out_ip"), "不应有 out_ip（已重构）"
        assert isinstance(srv._send_socks, list), "_send_socks 应为 list"
        print("  PASS test_dhcp_no_out_ip_attribute")
    finally:
        try: srv.stop()
        except: pass

def test_auto_nic_enables_next_button():
    """Bug #1 修复验证: 自动选择网卡后应启用「下一步」按钮"""
    from nic_scanner import get_wired_adapters
    wired = get_wired_adapters()
    app = T()
    try:
        # 等待扫描线程完成并处理所有 after 回调（模拟真实用户等待时间）
        import time
        time.sleep(1.0)
        app.app.update_idletasks()

        # 选角色 → 进入步骤 1
        app.select_role("source")
        app.app.update_idletasks()

        eq(app.step, 1, "应在步骤 1")
        if wired and app.ctl._auto_nic is not None:
            eq(app.next_state, "normal",
               "有有线网卡时下一步按钮应启用")
        print(f"  PASS test_auto_nic_enables_next_button (wired={len(wired)})")
    finally:
        app.destroy()

def test_no_auto_nic_disables_next():
    """无有线网卡时步骤 1 的下一步按钮应禁用"""
    from nic_scanner import get_wired_adapters
    wired = get_wired_adapters()
    app = T()
    try:
        app.ctl._device_type = "源设备"
        app.ctl._on_role_selected("source")
        app.app.update_idletasks()
        eq(app.step, 1)
        if not wired:
            # 无有线网卡 → 自动选择失败 → 按钮禁用
            eq(app.app.tk_button_next.cget("state"), "disabled",
               "无有线网卡时下一步按钮应禁用")
            print("  PASS test_no_auto_nic_disables_next")
        else:
            print("  SKIP test_no_auto_nic_disables_next (有有线网卡, 不适用)")
    finally:
        app.destroy()

def test_source_setup_network_no_arg():
    """_setup_source_network 不再需要 adapter_desc 参数 (新 API)"""
    app = T()
    try:
        # 直接调用验证不报 TypeError
        import time
        time.sleep(0.5)
        app.app.update()
        try:
            # 不应抛出参数错误
            app.ctl._setup_source_network()
            print("  PASS test_source_setup_network_no_arg")
        except TypeError as e:
            assert False, f"调用 _setup_source_network() 失败: {e}"
    finally:
        app.destroy()

def test_step1_has_auto_nic_card():
    """步骤 1 应有自动检测网卡信息卡片和高级选项按钮"""
    app = T()
    try:
        app.app.update_idletasks()
        assert hasattr(app.app, '_auto_nic_frame'), "应有 _auto_nic_frame"
        assert hasattr(app.app, 'tk_label_auto_nic_detail'), "应有 tk_label_auto_nic_detail"
        assert hasattr(app.app, '_advanced_nic_toggle_btn'), "应有 _advanced_nic_toggle_btn"
        assert hasattr(app.app, '_advanced_nic_frame'), "应有 _advanced_nic_frame"
        print("  PASS test_step1_has_auto_nic_card")
    finally:
        app.destroy()


# ============================================================
# 测试 20: 验证码横幅显示 (2026-08-11 修复)
# ============================================================

@test("37. 发送端选角色后传输页验证码横幅已 pack")
def test_auth_banner_packed_source():
    t = make_app()
    try:
        t.select_role("source")
        try:
            t.app._auth_banner_frame.pack_info()
        except Exception:
            check(False, "发送端选角色后横幅应已 pack")
    finally:
        t.destroy()

@test("38. 发送端 go_step(4) 横幅可见并显示验证码")
def test_auth_banner_visible_step4():
    t = make_app()
    try:
        t.select_role("source")
        t.set_nic()
        t.ctl._auth_code = "XY12"
        t.app.go_step(4)
        t.app.update_idletasks()
        eq(t.step, 4)
        check(t.app._auth_banner_frame.winfo_ismapped(), "step4 横幅应可见")
        eq(t.app.tk_label_transfer_auth_code.cget("text"), "XY12",
           "横幅应显示当前验证码")
    finally:
        t.destroy()

@test("39. 接收端选角色后验证码横幅未 pack")
def test_auth_banner_not_packed_target():
    t = make_app()
    try:
        t.select_role("target")
        try:
            t.app._auth_banner_frame.pack_info()
            check(False, "接收端横幅不应 pack")
        except Exception:
            pass
    finally:
        t.destroy()

@test("40. 发送端切到接收端后横幅 pack_forget")
def test_auth_banner_hidden_on_role_switch():
    t = make_app()
    try:
        t.select_role("source")
        t.prev_step()  # →0
        t.select_role("target")
        try:
            t.app._auth_banner_frame.pack_info()
            check(False, "切换到接收端后横幅应取消 pack")
        except Exception:
            pass
    finally:
        t.destroy()

# ============================================================
# 测试 21: 传输错误提示区
# ============================================================

@test("41. show_transfer_error 显示红色错误区, hide 后隐藏")
def test_show_transfer_error():
    t = make_app()
    try:
        t.app.show_transfer_error("测试错误消息")
        t.app.update_idletasks()
        try:
            t.app._transfer_error_frame.pack_info()
        except Exception:
            check(False, "错误区应已 pack")
        eq(t.app.tk_label_transfer_error.cget("text"), "测试错误消息")
        check("传输失败" in t.app.tk_label_transfer_status.cget("text"),
              "状态标签应显示传输失败")
        t.app.hide_transfer_error()
        t.app.update_idletasks()
        try:
            t.app._transfer_error_frame.pack_info()
            check(False, "隐藏后错误区不应 pack")
        except Exception:
            pass
    finally:
        t.destroy()

# ============================================================
# 测试 22: 网络断开检测 (2026-08-05 新增)
# ============================================================

@test("42. _on_download_complete 断网分支显示提示并复位标志")
def test_network_down_completion():
    t = make_app()
    try:
        t.select_role("target")
        t.ctl._network_down = True
        t.ctl._on_download_complete(False, 100, 0, [])
        t.app.update_idletasks()
        eq(t.ctl._network_down, False, "处理后应复位标志")
        check("网络连接已断开" in t.app.tk_label_transfer_error.cget("text"),
              "错误提示应含'网络连接已断开'")
        check("重新接收" in t.app.tk_button_mqfzl35t.cget("text"),
              "按钮应变为'重新接收'")
    finally:
        t.destroy()

@test("43. 网络监控线程生命周期 (本地回环不误判)")
def test_network_monitor_lifecycle():
    import time as _time
    t = make_app()
    try:
        t.ctl._start_network_monitor("127.0.0.1")
        _time.sleep(0.6)
        eq(t.ctl._network_down, False, "本地回环不应误判断网")
        t.ctl._stop_network_monitor()
        _time.sleep(0.3)
    finally:
        t.destroy()

@test("44. 断网时 download_files 的 stop_check 返回 'network_down'")
def test_stop_check_network_down():
    import control as _ctl_mod
    import time as _time
    t = make_app()
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
        # 手动 IP 直连, 避免 DHCP 依赖; 填入验证码
        t.app.tk_entry_code.delete(0, 'end')
        t.app.tk_entry_code.insert(0, "ABCD")
        t.ctl._start_target_download(manual_ip="127.0.0.1")
        # 等待下载线程调用 download_files
        deadline = _time.time() + 3
        while 'stop_check' not in captured and _time.time() < deadline:
            _time.sleep(0.05)
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

@test("45. 验证码输入自动转大写并限制4位")
def test_auth_code_entry():
    t = make_app()
    try:
        # 大写转换 (validate 允许 ≤4 位字母数字)
        t.app.tk_entry_code.delete(0, 'end')
        t.app.tk_entry_code.insert(0, "ab1d")
        t.ctl._on_auth_code_changed()
        eq(t.app.tk_entry_code.get(), "AB1D", "应自动转大写")
        # validatecommand 是实际输入拦截机制: 超4位/特殊字符被拒
        eq(t.app._auth_validate("ABCD"), True, "4位应允许")
        eq(t.app._auth_validate("ABCDE"), False, "5位应被拦截")
        eq(t.app._auth_validate("AB!D"), False, "含特殊字符应被拦截")
        # _on_auth_code_changed 对超长输入的兜底截断 (绕过 validate 直接注入)
        t.app.tk_entry_code.configure(validate="none")
        t.app.tk_entry_code.delete(0, 'end')
        t.app.tk_entry_code.insert(0, "ABCDE")
        t.ctl._on_auth_code_changed()
        eq(t.app.tk_entry_code.get(), "ABCD", "应截断为4位")
    finally:
        t.destroy()

# ============================================================
# 运行
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  UI 全场景导航测试")
    print("=" * 60)
    print()

    test_initial()
    test_source_role_select()
    test_source_nic_select()
    test_source_step2()
    test_source_disk_select()
    test_source_step3()
    test_source_step4_done()
    test_source_step6()
    test_source_backward_skip5()
    test_source_full_backward()
    test_target_role_select()
    test_target_step4()
    test_target_step5()
    test_target_step6()
    test_target_full_backward()
    test_nic_placeholders()
    test_nic_real()
    test_disk_placeholders()
    test_disk_real()
    test_transfer_not_done()
    test_verify_online_label()
    test_nic_preserved()
    test_disk_preserved()
    test_role_switch()
    test_source_no_step5()
    test_gostep_0()
    test_gostep_1()
    test_gostep_1_no_nic()
    test_rapid_click()
    test_button_text_source()
    test_button_text_target()
    test_skip_import()
    test_export_visible_source()
    test_export_hidden_target()
    test_transfer_button_enabled()
    test_next_blocked_no_nic()
    test_auto_jump_step5()
    test_stay_step4_with_config()

    # 新增 — 验证码横幅 / 传输错误 / 网络断开检测 (2026-08-11)
    test_auth_banner_packed_source()
    test_auth_banner_visible_step4()
    test_auth_banner_not_packed_target()
    test_auth_banner_hidden_on_role_switch()
    test_show_transfer_error()
    test_network_down_completion()
    test_network_monitor_lifecycle()
    test_stop_check_network_down()
    test_auth_code_entry()

    # 新增 — 自动网卡 & 高级选项 (2026-08-02)
    test_get_wired_adapters_struct()
    test_get_wired_adapters_no_wifi()
    test_auto_nic_init_state()
    test_auto_select_wired_nic()
    test_manual_nic_override()
    test_get_wired_nic_ips()
    test_get_adapter_desc_from_auto()
    test_auto_nic_display_update()
    test_advanced_panel_hidden_default()
    test_advanced_toggle_show_hide()
    test_multisock_dhcp_init()
    test_multisock_dhcp_empty_ips()
    test_dhcp_no_out_ip_attribute()
    test_auto_nic_enables_next_button()
    test_no_auto_nic_disables_next()
    test_source_setup_network_no_arg()
    test_step1_has_auto_nic_card()

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
