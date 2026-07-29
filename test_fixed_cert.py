"""验证固定证书方案:
1) 浏览器等价校验 (信任该自签名证书 + 校验主机名): 连 https://127.0.0.1 与 https://localhost 都应 200
   -> 证明 SAN 含 127.0.0.1/localhost, 浏览器点击'继续访问'后页面可加载
2) 接收端行为 (CERT_NONE, 忽略证书错误): 传输仍正常
"""
import os, sys, time, shutil, tempfile, threading, ssl, json, urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import file_transfer as ft
import tls_utils

src = tempfile.mkdtemp(prefix="src_")
open(os.path.join(src, "a.txt"), "w").write("hello")
os.makedirs(os.path.join(src, "sub"))
open(os.path.join(src, "sub", "b.txt"), "w").write("world")

cert_paths = tls_utils.get_or_create_fixed_cert()
print("固定证书:", cert_paths[0])
print("SAN:", tls_utils.cert_san_info(cert_paths[0]))

server = ft.FileServer(
    partition_map={"D": src},
    log_callback=lambda m: print("[SRV]", m),
    auth_code="TEST",
    cert_paths=cert_paths,
)
server.start()
time.sleep(1.0)
PORT = ft.TRANSFER_PORT

def try_get(url, ctx, label):
    print(f"\n=== {label} ===\n  {url}")
    try:
        with urllib.request.urlopen(url, timeout=8, context=ctx) as r:
            print(f"  HTTP {r.status}: {r.read().decode('utf-8','replace')[:160]}")
            return r.status
    except urllib.error.HTTPError as e:
        print(f"  HTTPError {e.code}: {e.read().decode('utf-8','replace')[:160]}")
        return e.code
    except Exception as e:
        print(f"  EXCEPTION: {type(e).__name__}: {e}")
        return None

# 1) 浏览器等价: 把自签名证书加入信任库 + 校验主机名
browse_ctx = ssl.create_default_context()
browse_ctx.load_verify_locations(cert_paths[0])  # 信任该证书 (浏览器需手动点'继续访问')
try_get(f"https://127.0.0.1:{PORT}/ping?pwd=TEST", browse_ctx, "浏览器等价-127.0.0.1")
try_get(f"https://localhost:{PORT}/ping?pwd=TEST", browse_ctx, "浏览器等价-localhost")

# 2) 接收端: CERT_NONE 忽略证书错误
recv_ctx = tls_utils.make_client_ssl_context()
try_get(f"https://127.0.0.1:{PORT}/list?partition=D&pwd=TEST", recv_ctx, "接收端(CERT_NONE)-list")
try_get(f"https://127.0.0.1:{PORT}/ping", recv_ctx, "接收端-无pwd(应403)")

server.stop() if hasattr(server, "stop") else None
shutil.rmtree(src, ignore_errors=True)
print("\n[done]")
