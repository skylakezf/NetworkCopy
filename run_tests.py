# -*- coding: utf-8 -*-
"""磁盘拷贝工具 —— 唯一测试入口。

所有测试套件集中在 tests/ 目录, 本脚本自动发现并串行执行
(Tk 窗口与 9999 端口不能并发)。

用法:
  python run_tests.py                      # 运行全部套件
  python run_tests.py --fast               # 跳过耗时套件 (E2E)
  python run_tests.py --only ui            # 只跑名称/文件名含 ui 的套件
  python run_tests.py --only punctuation --only func
  python run_tests.py --list               # 列出所有套件
  python run_tests.py --stop-on-fail       # 首个失败即停止

新增套件: 在 tests/ 下放一个 test_xxx.py (内含 main() 或可直接执行) 即可,
本脚本会自动发现, 无需修改这里。
"""
import argparse
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS_DIR = os.path.join(HERE, "tests")

# (文件名, 显示名, 是否耗时) —— 同时决定默认执行顺序
SUITE_META = [
    ("test_punctuation.py", "标点(半角/全角)+0KB防护", False),
    ("test_func.py", "功能: 过滤/HTTPS端点/CSV修复", False),
    ("test_verify_e2e.py", "校验 E2E: 增量/清单/下载", False),
    ("test_thread_safe.py", "线程安全 (tkinter)", False),
    ("test_ui.py", "UI 全流程", False),
    ("test_ui_readability.py", "UI 可读性", False),
    ("test_button_states.py", "按钮状态矩阵", False),
    ("test_disconnect.py", "断线/验证码错误处理", False),
    ("test_user_cases.py", "用户场景用例", False),
    ("test_device_replacement.py", "设备更换 E2E", True),
    ("test_device_transfer_e2e.py", "传输 E2E (需证书)", True),
]


def discover():
    """发现 tests/ 下的套件: 按 SUITE_META 排序, 未登记的追加到末尾。"""
    if not os.path.isdir(TESTS_DIR):
        return []
    present = {f for f in os.listdir(TESTS_DIR)
               if f.startswith("test_") and f.endswith(".py")}

    suites = [(f, n, s) for f, n, s in SUITE_META if f in present]
    known = {f for f, _, _ in suites}
    for f in sorted(present - known):
        if f == "__init__.py":
            continue
        suites.append((f, f[:-3], False))
    return suites


def find_python():
    """优先嵌入式 Python (含 cryptography / ttkbootstrap), 否则用当前解释器。"""
    emb = os.path.join(HERE, "python-3.13.14-embed-amd64", "python.exe")
    if os.path.isfile(emb):
        return emb
    return sys.executable


def extract_counts(out):
    """尽力解析套件输出的 (总计, 通过, 失败); 解析不到返回 (None, None, None)。"""
    m = re.search(r"总计[:：]\s*(\d+)\s*通过[:：]\s*(\d+)\s*失败[:：]\s*(\d+)", out)
    if m:
        return tuple(int(x) for x in m.groups())
    m = re.search(r"PASS\s*(\d+)\s*/\s*FAIL\s*(\d+)", out)
    if m:
        p, f = int(m.group(1)), int(m.group(2))
        return (p + f, p, f)
    m = re.search(r"(\d+)\s*/\s*(\d+)\s*通过", out)
    if m:
        p, t = int(m.group(1)), int(m.group(2))
        return (t, p, t - p)
    m = re.search(r"通过[:：]\s*(\d+)\s*失败[:：]\s*(\d+)", out)
    if m:
        p, f = int(m.group(1)), int(m.group(2))
        return (p + f, p, f)
    return (None, None, None)


def is_failed(out, rc):
    if rc != 0:
        return True
    patterns = [
        r"Traceback \(most recent call last\)",
        r"(?m)^\s*FAIL\b",
        r"\bFAILED\b",
        r"AssertionError",
        r"失败[:：]\s*[1-9]",
    ]
    return any(re.search(p, out) for p in patterns)


def run_suite(script, py, timeout=900):
    path = os.path.join(TESTS_DIR, script)
    if not os.path.isfile(path):
        return 1, f"套件文件不存在: {script}", 0.0

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = HERE

    t0 = time.time()
    try:
        proc = subprocess.run(
            [py, os.path.join("tests", script)],
            cwd=HERE, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        out = proc.stdout.decode("utf-8", errors="replace")
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        out = f"[超时] 超过 {timeout} 秒未结束"
        rc = 124
    elapsed = time.time() - t0
    return rc, out, elapsed


def main():
    ap = argparse.ArgumentParser(description="磁盘拷贝工具 唯一测试入口")
    ap.add_argument("--only", action="append", default=[],
                    help="只运行名称/文件名包含该关键字的套件 (可多次指定)")
    ap.add_argument("--fast", action="store_true", help="跳过耗时套件")
    ap.add_argument("--list", action="store_true", help="列出套件后退出")
    ap.add_argument("--stop-on-fail", action="store_true", help="首个失败即停止")
    ap.add_argument("--timeout", type=int, default=900, help="单套件超时秒数")
    args = ap.parse_args()

    suites = discover()
    if args.fast:
        suites = [s for s in suites if not s[2]]
    if args.only:
        keys = [k.lower() for k in args.only]
        suites = [
            s for s in suites
            if any(k in s[0].lower() or k in s[1].lower() for k in keys)
        ]

    if args.list:
        print("可用套件 (tests/):")
        for script, name, slow in suites:
            print(f"  {script:32s} {name}  {'(耗时)' if slow else ''}")
        return 0

    if not suites:
        print("没有匹配的套件")
        return 1

    py = find_python()
    print("=" * 74)
    print(f"磁盘拷贝工具 —— 统一测试   (解释器: {py})")
    print(f"套件数: {len(suites)}   目录: tests/")
    print("=" * 74)

    results = []
    for script, name, slow in suites:
        print(f"\n==> {name}  [tests/{script}]" + ("  (耗时)" if slow else ""))
        rc, out, elapsed = run_suite(script, py, args.timeout)
        failed = is_failed(out, rc)
        total, passed, fail_n = extract_counts(out)

        if fail_n is None and not failed:
            fail_n = 0
        status = "FAIL" if failed else "OK"
        print(f"  结果: {status}   耗时 {elapsed:.1f}s"
              + (f"   用例 通过 {passed} / 失败 {fail_n} / 共 {total}"
                 if total else ""))

        if failed:
            keep = []
            for line in out.splitlines():
                s = line.strip()
                if (s.startswith("FAIL") or "Traceback" in s
                        or "AssertionError" in s or s.startswith("Error")
                        or "失败" in s):
                    keep.append("    " + s)
            if keep:
                print("  ---- 关键输出 ----")
                print("\n".join(keep[-25:]))
            else:
                tail = [l for l in out.splitlines() if l.strip()][-8:]
                print("  ---- 尾部输出 ----")
                print("\n".join("    " + l.strip() for l in tail))

        results.append((name, script, status, passed, fail_n, total, elapsed))
        if failed and args.stop_on_fail:
            print("\n[--stop-on-fail] 已停止后续套件")
            break

    print("\n" + "=" * 74)
    print("汇总")
    print("=" * 74)
    print(f"{'套件':<28}{'状态':<8}{'通过':>6}{'失败':>6}{'总计':>6}{'耗时':>9}")
    print("-" * 74)
    ok_count = 0
    sum_pass = sum_fail = 0
    for name, _script, status, passed, fail_n, total, elapsed in results:
        print(f"{name:<28}{status:<8}"
              f"{(passed if passed is not None else '-'):>6}"
              f"{(fail_n if fail_n is not None else '-'):>6}"
              f"{(total if total is not None else '-'):>6}"
              f"{elapsed:>8.1f}s")
        if status == "OK":
            ok_count += 1
        if passed:
            sum_pass += passed
        if fail_n:
            sum_fail += fail_n
    print("-" * 74)
    print(f"套件: {ok_count}/{len(results)} 通过"
          f"    用例: 通过 {sum_pass} / 失败 {sum_fail}")

    if ok_count != len(results):
        print("\n存在失败套件!")
        return 1
    print("\n全部套件通过!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
