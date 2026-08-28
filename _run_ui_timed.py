# -*- coding: utf-8 -*-
"""带超时堆栈转储运行 _test_ui_full.py: 若 20 秒内未完成则 dump 全部线程堆栈后退出"""
import faulthandler
import sys

faulthandler.dump_traceback_later(280, exit=True)
src = open("_test_ui_full.py", encoding="utf-8").read()
exec(compile(src, "_test_ui_full.py", "exec"))
print(">>> _test_ui_full.py 正常执行完毕 <<<")
