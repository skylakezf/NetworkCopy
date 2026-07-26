import os, marshal, glob

base = r"c:\Users\Xinyi\Desktop\网络拷贝\_extracted"
files = glob.glob(os.path.join(base, "pyz", "*.pyc")) + [os.path.join(base, "main.pyc")]
for fp in sorted(files):
    with open(fp, "rb") as f:
        data = f.read()
    head = data[:16]
    body = data[16:]
    try:
        co = marshal.loads(body)
        ok = hasattr(co, "co_code") and hasattr(co, "co_name")
        print("%-40s head=%s code_ok=%s name=%s consts=%d" % (
            os.path.basename(fp), head[:4].hex(), ok,
            getattr(co, "co_name", "?"), len(getattr(co, "co_consts", []))))
    except Exception as e:
        print("%-40s FAIL: %s" % (os.path.basename(fp), e))
