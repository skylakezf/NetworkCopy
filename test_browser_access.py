"""复现浏览器访问场景: 模拟浏览器对 https://127.0.0.1:9999 的访问行为。

重点: 浏览器会校验证书 (hostname/SAN 匹配 + 受信 CA); 本工具自带的客户端
(make_client_ssl_context) 关闭了校验, 所以工具自己能传, 但浏览器打不开。
本脚本用 '默认 ssl 上下文' 模拟浏览器, 看它到底卡在哪。
"""
import os, sys, time, shutil, tempfile, threading, ssl, json, urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import file_transfer as ft
import tls_utils

PY = sys.executable
print("Python:", PY)

def cert_ok():
    try:
        import importlib.util
        return importlib.util.find_spec("cryptography") is not None
    except Exception:
        return False
print("cryptography available:", cert_ok())

src = tempfile.mkdtemp(prefix="src_")
open(os.path.join(src, "a.txt"), "w").write("hello")
os.makedirs(os.path.join(src, "sub"))
open(os.path.join(src, "sub", "b.txt"), "w").write("world")

# 模拟局域网 IP (证书 SAN 只含此 IP, 不含 127.0.0.1) —— 正是 control.py 的行为
CERT_IP = "192.168.56.1"
cert_paths = tls_utils.get_or_create_cert(CERT_IP)
print("cert for:", CERT_IP, "->", cert_paths)

server = ft.FileServer(
    partition_map={"D": src},
    log_callback=lambda m: None,
    auth_code="TEST",
    cert_paths=cert_paths,
)
server.start()
time.sleep(1.0)

PORT = ft.TRANSFER_PORT

def try_get(url, use_default_ctx, label):
    print(f"\n=== {label} ===")
    print("URL:", url)
    try:
        if use_default_ctx:
            ctx = ssl.create_default_context()  # 模拟浏览器: 校验证书
        else:
            ctx = tls_utils.make_client_ssl_context()  # 工具自带: 不校验
        with urllib.request.urlopen(url, timeout=8, context=ctx) as r:
            body = r.read().decode("utf-8", "replace")
            print(f"  HTTP {r.status}: {body[:200]}")
            return r.status
    except urllib.error.HTTPError as e:
        print(f"  HTTPError {e.code}: {e.read().decode('utf-8','replace')[:200]}")
        return e.code
    except Exception as e:
        print(f"  EXCEPTION: {type(e).__name__}: {e}")
        return None

print("\n########## 浏览器行为 (默认 ssl 上下文, 校验证书) ##########")
try_get(f"https://127.0.0.1:{PORT}/ping?pwd=TEST", True, "浏览器访问 /ping?pwd=TEST (SAN 不含 127.0.0.1)")
try_get(f"https://127.0.0.1:{PORT}/", True, "浏览器访问根路径 /")

print("\n########## 工具自带客户端行为 (CERT_NONE, 不校验) ##########")
try_get(f"https://127.0.0.1:{PORT}/ping?pwd=TEST", False, "客户端访问 /ping?pwd=TEST")
try_get(f"https://127.0.0.1:{PORT}/", False, "客户端访问根路径 /")
try_get(f"https://127.0.0.1:{PORT}/ping", False, "客户端访问 /ping (无 pwd -> 应 403)")
try_get(f"https://127.0.0.1:{PORT}/list?partition=D&pwd=TEST", False, "客户端访问 /list?partition=D&pwd=TEST")

server.stop() if hasattr(server, "stop") else None
shutil.rmtree(src, ignore_errors=True)
print("\n[done]")
