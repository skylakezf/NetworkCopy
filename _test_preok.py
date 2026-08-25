# -*- coding: utf-8 -*-
"""验证 verify_csv 的 pre_ok_paths 增量校验逻辑 (边传边校验确认清单跳过磁盘校验)"""
import os
import sys
import tempfile
import csv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verifier

def main():
    tmp = tempfile.mkdtemp(prefix="preok_test_")
    # 构造文件: a.txt 存在且大小正确(3字节); b.txt 存在但大小不符(3 vs 5); c.txt 不存在
    with open(os.path.join(tmp, "a.txt"), "wb") as f:
        f.write(b"abc")
    with open(os.path.join(tmp, "b.txt"), "wb") as f:
        f.write(b"abc")

    csv_path = os.path.join(tmp, "FullFilelist_DEF.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Drive", "FullPath", "FileName", "SizeBytes"])
        w.writerow(["D", "D:\\a.txt", "a.txt", "3"])
        w.writerow(["D", "D:\\b.txt", "b.txt", "5"])
        w.writerow(["D", "D:\\c.txt", "c.txt", "3"])

    partition_map = {"D": tmp + "\\"}

    logs = []
    # 场景1: pre_ok 包含 a.txt (边传边校验已确认) → a 直接标 Y, 其余正常校验
    passed, failed, skipped, total = verifier.verify_csv(
        csv_path, partition_map, log_callback=logs.append,
        max_workers=2, pre_ok_paths={"D:\\a.txt"},
    )
    print(f"场景1: passed={passed} failed={failed} skipped={skipped} total={total} (期望 passed=1 failed=2)")
    assert passed == 1, f"passed 期望 1 实际 {passed}"
    assert failed == 2, f"failed 期望 2 实际 {failed}"
    assert total == 3

    # 检查 CSV 结果列
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    hdr = rows[0]
    e_idx = hdr.index("VerifyResult") if "VerifyResult" in hdr else None
    assert e_idx is not None, "CSV 应新增 VerifyResult 列"
    by_path = {r[1]: r for r in rows[1:]}
    assert by_path["D:\\a.txt"][e_idx] == "Y", "a.txt 应被 pre_ok 标记为 Y"
    assert by_path["D:\\b.txt"][e_idx] == "N", "b.txt 大小不符应标记 N"
    assert by_path["D:\\c.txt"][e_idx] == "N", "c.txt 不存在应标记 N"

    # 场景2: 不传 pre_ok → 全量校验 (回归)
    passed2, failed2, skipped2, total2 = verifier.verify_csv(
        csv_path, partition_map, log_callback=logs.append, max_workers=2,
    )
    print(f"场景2: passed={passed2} failed={failed2} total={total2} (期望 passed=1 failed=2)")
    assert passed2 == 1 and failed2 == 2 and total2 == 3

    # 场景3: 二次校验 (target_indices) 不应用 pre_ok
    passed3, failed3, skipped3, total3 = verifier.verify_csv(
        csv_path, partition_map, log_callback=logs.append, max_workers=2,
        target_indices={1}, pre_ok_paths={"D:\\a.txt", "D:\\b.txt", "D:\\c.txt"},
    )
    print(f"场景3: passed={passed3} failed={failed3} total={total3} (target_indices 应忽略 pre_ok)")
    assert failed3 == 1, "b.txt (索引1) 大小不符应失败, pre_ok 不应生效"

    print("ALL PREOK TESTS PASSED")
    for ln in logs:
        print("  |", ln)

if __name__ == "__main__":
    main()
