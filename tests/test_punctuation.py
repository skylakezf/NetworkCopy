# -*- coding: utf-8 -*-
"""专项测试: 文件名标点(半角/全角) + 0KB 垃圾文件不再生成。

覆盖 2026-09-09 修复的两个问题:
  1) 校验阶段不得改写 CSV 里的文件名标点 (无论真实文件名是半角还是全角)
  2) 重试下载时服务端返回空数据, 绝不能落 0KB 垃圾文件

运行:
  .\\python-3.13.14-embed-amd64\\python.exe _test_punctuation.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import csv as _csv
import os
import struct as _struct
import tempfile

import verifier

_TOTAL = [0]
_FAILED = [0]


def check(cond, msg):
    _TOTAL[0] += 1
    if cond:
        print(f"  PASS: {msg}")
    else:
        _FAILED[0] += 1
        print(f"  FAIL: {msg}")


def _write_csv(csv_path, rows, header=("Drive", "FullPath", "FileName",
                                       "SizeBytes", "VerifyResult")):
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = _csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def _read_col(csv_path, col=1):
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(_csv.reader(f))
    return [r[col] for r in rows[1:]]


FULLWIDTH = "前处理，ED整理表.xls"      # 全角逗号 U+FF0C
HALFWIDTH = "前处理,ED整理表.xls"       # 半角逗号 U+002C
DATA = b"x" * 100


def _make_disk(td, name):
    sub = os.path.join(td, "学习")
    os.makedirs(sub, exist_ok=True)
    with open(os.path.join(sub, name), "wb") as f:
        f.write(DATA)
    return sub


def _verify(td, csv_path, drive="D"):
    return verifier.verify_csv(
        csv_path, {drive: td}, log_callback=lambda m: None, max_workers=2)


# ============================================================
# 1. 校验不得改写文件名标点
# ============================================================

def t1_fullwidth_comma_on_disk():
    """磁盘真实文件名含【全角逗号】→ CSV 正确, 校验应通过且路径不变。"""
    with tempfile.TemporaryDirectory() as td:
        _make_disk(td, FULLWIDTH)
        csv_path = os.path.join(td, "FullFilelist_DEF.csv")
        _write_csv(csv_path, [
            ["D", fr"D:\学习\{FULLWIDTH}", FULLWIDTH, "100", ""],
        ])
        verifier._repair_csv_in_place(csv_path, partition_map={"D": td})
        check(_read_col(csv_path)[0] == fr"D:\学习\{FULLWIDTH}",
              "T1 全角逗号文件名: CSV 路径保持不变")
        passed, failed, skipped, total = _verify(td, csv_path)
        check((passed, failed) == (1, 0),
              f"T1 全角逗号文件名: 校验应通过 (实际 passed={passed} failed={failed})")


def t2_halfwidth_comma_on_disk():
    """磁盘真实文件名含【半角逗号】→ CSV 正确, 校验应通过且路径不变。"""
    with tempfile.TemporaryDirectory() as td:
        _make_disk(td, HALFWIDTH)
        csv_path = os.path.join(td, "FullFilelist_DEF.csv")
        _write_csv(csv_path, [
            ["D", fr"D:\学习\{HALFWIDTH}", HALFWIDTH, "100", ""],
        ])
        verifier._repair_csv_in_place(csv_path, partition_map={"D": td})
        check(_read_col(csv_path)[0] == fr"D:\学习\{HALFWIDTH}",
              "T2 半角逗号文件名: CSV 路径保持不变")
        passed, failed, _, _ = _verify(td, csv_path)
        check((passed, failed) == (1, 0),
              f"T2 半角逗号文件名: 校验应通过 (实际 passed={passed} failed={failed})")


def t3_zero_byte_twin_exists():
    """同目录已有 0KB 垃圾文件(标点变体)时, 真实文件校验仍应通过。"""
    with tempfile.TemporaryDirectory() as td:
        sub = _make_disk(td, FULLWIDTH)
        with open(os.path.join(sub, HALFWIDTH), "wb") as f:
            pass  # 0KB 垃圾 (半角变体)
        csv_path = os.path.join(td, "FullFilelist_DEF.csv")
        _write_csv(csv_path, [
            ["D", fr"D:\学习\{FULLWIDTH}", FULLWIDTH, "100", ""],
        ])
        verifier._repair_csv_in_place(csv_path, partition_map={"D": td})
        passed, failed, _, _ = _verify(td, csv_path)
        check((passed, failed) == (1, 0),
              "T3 存在0KB变体文件时: 真实文件校验仍通过")
        check(os.path.isfile(os.path.join(sub, HALFWIDTH)),
              "T3 校验不会误删已有文件 (清理交给 _cleanup_zero_byte_dupes.py)")


def t4_old_escape_csv_repair():
    """旧版 escape_csv 写坏的 CSV (磁盘半角, CSV 全角) → 应修复为半角并通过。"""
    with tempfile.TemporaryDirectory() as td:
        _make_disk(td, HALFWIDTH)
        csv_path = os.path.join(td, "FullFilelist_DEF.csv")
        _write_csv(csv_path, [
            ["D", fr"D:\学习\{FULLWIDTH}", FULLWIDTH, "100", ""],
        ])
        n = verifier._repair_csv_in_place(csv_path, partition_map={"D": td})
        check(n == 1, f"T4 旧版损坏CSV: 应修复 1 行 (实际 {n})")
        check(_read_col(csv_path)[0] == fr"D:\学习\{HALFWIDTH}",
              "T4 旧版损坏CSV: 应还原为半角逗号")
        passed, failed, _, _ = _verify(td, csv_path)
        check((passed, failed) == (1, 0), "T4 旧版损坏CSV: 校验应通过")


def t5_history_mistakenly_repaired():
    """历史被误改的 CSV (磁盘全角, CSV 半角) → 应反向修复为全角并通过。"""
    with tempfile.TemporaryDirectory() as td:
        _make_disk(td, FULLWIDTH)
        csv_path = os.path.join(td, "FullFilelist_DEF.csv")
        _write_csv(csv_path, [
            ["D", fr"D:\学习\{HALFWIDTH}", HALFWIDTH, "100", ""],
        ])
        n = verifier._repair_csv_in_place(csv_path, partition_map={"D": td})
        check(n == 1, f"T5 历史误改CSV: 应反向修复 1 行 (实际 {n})")
        check(_read_col(csv_path)[0] == fr"D:\学习\{FULLWIDTH}",
              "T5 历史误改CSV: 应还原为全角逗号")
        passed, failed, _, _ = _verify(td, csv_path)
        check((passed, failed) == (1, 0), "T5 历史误改CSV: 校验应通过")


# ============================================================
# 2. 重试下载不得产生 0KB 垃圾文件
# ============================================================

class _FakeResp:
    def __init__(self, raw, status=200):
        self._raw = raw
        self.status = status

    def read(self, size=-1):
        if size is None or size < 0:
            return self._raw
        chunk = self._raw[:size]
        self._raw = self._raw[len(chunk):]
        return chunk

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _batch_payload(items):
    """构造 /batch_get 响应体: [(rel_path, data_bytes), ...]"""
    out = _struct.pack(">I", len(items))
    for path, data in items:
        pb = path.encode("utf-8")
        out += _struct.pack(">I", len(pb)) + pb
        out += _struct.pack(">Q", len(data)) + data
    return out


def t6_batch_retry_empty_payload_no_file():
    """服务端返回空数据 → 批量重试不得落 0KB 文件。"""
    import urllib.request

    with tempfile.TemporaryDirectory() as td:
        rel = f"学习\\{FULLWIDTH}"
        csv_path = os.path.join(td, "FullFilelist_DEF.csv")
        _write_csv(csv_path, [
            ["D", fr"D:\学习\{FULLWIDTH}", FULLWIDTH, "100", "N"],
        ])
        target = os.path.join(td, rel)

        raw = _batch_payload([(rel, b"")])  # 服务端读不到 → 空占位
        orig = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: _FakeResp(raw)
        try:
            verifier._retry_missing_files(
                "127.0.0.1", 9999, {"D": td}, csv_path,
                0, 1, 3, 4, lambda m: None, "ABCD",
            )
        finally:
            urllib.request.urlopen = orig

        check(not os.path.exists(target),
              "T6 服务端空数据: 不得创建目标文件(0KB)")
        check(not os.path.exists(target + ".tmp"),
              "T6 服务端空数据: 不得残留 .tmp")


def t7_single_retry_empty_body_no_file():
    """单文件重试下载: 空响应体 → 不得创建 0KB 文件。"""
    import urllib.request

    with tempfile.TemporaryDirectory() as td:
        target = os.path.join(td, "sub", FULLWIDTH)
        orig = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: _FakeResp(b"")
        try:
            ok = verifier._download_one_file_with_retry(
                "127.0.0.1", 9999, "D", f"sub\\{FULLWIDTH}", target,
                "ABCD", lambda m: None, max_retries=1, expected_size=100,
            )
        finally:
            urllib.request.urlopen = orig

        check(ok is False, "T7 空响应: 应返回失败")
        check(not os.path.exists(target),
              "T7 空响应: 不得创建 0KB 文件")
        check(not os.path.exists(target + ".tmp"),
              "T7 空响应: 不得残留 .tmp")


def t8_batch_retry_size_mismatch_no_file():
    """服务端数据大小与 CSV 不符 → 不得落半截文件。"""
    import urllib.request

    with tempfile.TemporaryDirectory() as td:
        rel = f"学习\\{HALFWIDTH}"
        csv_path = os.path.join(td, "FullFilelist_DEF.csv")
        _write_csv(csv_path, [
            ["D", fr"D:\学习\{HALFWIDTH}", HALFWIDTH, "100", "N"],
        ])
        target = os.path.join(td, rel)
        raw = _batch_payload([(rel, b"short")])  # 10 字节, 期望 100

        orig = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: _FakeResp(raw)
        try:
            verifier._retry_missing_files(
                "127.0.0.1", 9999, {"D": td}, csv_path,
                0, 1, 3, 4, lambda m: None, "ABCD",
            )
        finally:
            urllib.request.urlopen = orig

        check(not os.path.exists(target),
              "T8 大小不符: 不得写入半截文件")


def t9_batch_retry_ok_writes_file():
    """正常数据 → 应当正常写入 (防止保护逻辑过度拦截)。"""
    import urllib.request

    with tempfile.TemporaryDirectory() as td:
        rel = f"学习\\{FULLWIDTH}"
        csv_path = os.path.join(td, "FullFilelist_DEF.csv")
        _write_csv(csv_path, [
            ["D", fr"D:\学习\{FULLWIDTH}", FULLWIDTH, "100", "N"],
        ])
        target = os.path.join(td, rel)
        raw = _batch_payload([(rel, DATA)])

        orig = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: _FakeResp(raw)
        try:
            n = verifier._retry_missing_files(
                "127.0.0.1", 9999, {"D": td}, csv_path,
                0, 1, 3, 4, lambda m: None, "ABCD",
            )
        finally:
            urllib.request.urlopen = orig

        check(os.path.isfile(target) and os.path.getsize(target) == 100,
              "T9 正常数据: 应正确写入 100 字节")
        check(n == 1, f"T9 正常数据: 应计数 1 个 (实际 {n})")


# ============================================================
# 3. 清理脚本能识别 0KB 垃圾
# ============================================================

def t10_cleanup_detects_junk():
    """_cleanup_zero_byte_dupes 应能识别 0KB 标点变体垃圾文件。"""
    import _cleanup_zero_byte_dupes as cleanup

    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, FULLWIDTH), "wb") as f:
            f.write(DATA)
        junk_path = os.path.join(td, HALFWIDTH)
        with open(junk_path, "wb") as f:
            pass  # 0KB 垃圾

        found = cleanup.scan_root(td)
        check(len(found) == 1, f"T10 清理脚本: 应识别 1 个垃圾 (实际 {len(found)})")
        if found:
            check(os.path.abspath(found[0][0]) == os.path.abspath(junk_path),
                  "T10 清理脚本: 命中的应是 0KB 的半角变体文件")

    # 只有 0KB 文件、没有非空变体时 → 不识别为垃圾
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "empty.txt"), "wb") as f:
            pass
        check(len(cleanup.scan_root(td)) == 0,
              "T10 清理脚本: 孤立的空文件不应被误判")


def t11_cleanup_variants():
    """变体生成应覆盖半角↔全角, 且不含自身。"""
    import _cleanup_zero_byte_dupes as cleanup

    vs = cleanup._variants("a,b.txt")
    check("a，b.txt" in vs, "T11 变体: 半角逗号应能生成全角变体")
    vs2 = cleanup._variants("a，b.txt")
    check("a,b.txt" in vs2, "T11 变体: 全角逗号应能生成半角变体")
    check("plain.txt" not in cleanup._variants("plain.txt"),
          "T11 变体: 不含标点时无变体")


def main():
    print("=" * 60)
    print("专项测试: 文件名标点(半角/全角) + 0KB 垃圾文件防护")
    print("=" * 60)

    t1_fullwidth_comma_on_disk()
    t2_halfwidth_comma_on_disk()
    t3_zero_byte_twin_exists()
    t4_old_escape_csv_repair()
    t5_history_mistakenly_repaired()
    t6_batch_retry_empty_payload_no_file()
    t7_single_retry_empty_body_no_file()
    t8_batch_retry_size_mismatch_no_file()
    t9_batch_retry_ok_writes_file()
    t10_cleanup_detects_junk()
    t11_cleanup_variants()

    print()
    print(f"总计: {_TOTAL[0]}  通过: {_TOTAL[0] - _FAILED[0]}  失败: {_FAILED[0]}")
    if _FAILED[0]:
        print("存在失败用例!")
        return 1
    print("全部通过!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
