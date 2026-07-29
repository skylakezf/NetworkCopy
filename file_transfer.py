"""
文件传输模块
Phase 3: 源设备启动 HTTP 文件服务器，目标设备通过 HTTP 下载
保持完整目录结构，日志实时回传
PE 下自动使用 APIPA (169.254.x.x)，目标设备扫描发现源设备
"""
import ctypes as _ctypes
import os
import json
import socket
import ssl
import threading
import time
import secrets
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from concurrent.futures import ThreadPoolExecutor, as_completed

import tls_utils

# Windows API 常量: 阻止系统休眠/锁屏
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002


def _prevent_sleep():
    """阻止系统自动休眠和关闭显示器 (Windows API SetThreadExecutionState)"""
    try:
        _ctypes.windll.kernel32.SetThreadExecutionState(
            _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED
        )
    except Exception:
        pass  # 非 Windows 或权限不足时静默忽略


def _allow_sleep():
    """恢复系统正常休眠策略"""
    try:
        _ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
    except Exception:
        pass

# 服务端口 (高位非特权端口: 无需 SYSTEM/管理员即可绑定, 避免 WinError 10013)
TRANSFER_PORT = 9999

# 客户端 SSL 上下文 (自签名证书不校验, 由验证码保证鉴权)
CLIENT_SSL_CTX = tls_utils.make_client_ssl_context()

# 需要跳过的文件夹
SKIP_DIRS = {"AppData", "System Volume Information", "WeChat Files"}
SKIP_PREFIXES = ("$",)  # $RECYCLE.BIN 等
# 需要跳过的系统文件 (根目录级别)
SKIP_FILES = {"pagefile.sys", "hiberfil.sys", "swapfile.sys", "DumpStack.log.tmp"}


# ===================== 文件服务器 (源设备) =====================

class FileServerHandler(BaseHTTPRequestHandler):
    """
    HTTP 文件服务处理器
    端点:
      GET /list?partition=D  → 返回分区文件列表 JSON
      GET /get?partition=D&path=xxx → 返回文件内容
    注意: partition 是 PE 下的实际盘符
    """
    protocol_version = "HTTP/1.1"  # 启用 Keep-Alive，避免每请求关闭连接→客户端端口耗尽

    # 类变量，由外部设置
    partition_map = {}   # {"D": "I:", "E": "J:", "F": "K:"}  正常盘符→PE盘符
    log_callback = None  # 日志回调函数
    suppress_access_log = False  # 批量传输时禁用逐条 HTTP 日志
    auth_code = ""       # 随机验证码: 所有请求必须携带 ?pwd= 且匹配

    @classmethod
    def _is_authorized(cls, params: dict) -> bool:
        """校验请求中的 pwd 参数是否匹配验证码 (常量时间比较)"""
        if not cls.auth_code:
            return False
        pwd = params.get("pwd", "")
        return secrets.compare_digest(pwd, cls.auth_code)

    @staticmethod
    def _parse_query(path: str):
        """从请求路径解析 (path, params_dict)"""
        p = path.split("?")[0]
        params = {}
        if "?" in path:
            qs = path.split("?", 1)[1]
            for pair in qs.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    params[k] = urllib.parse.unquote(v)
        return p, params

    def log_message(self, format, *args):
        """重定向 HTTP 日志 (批量传输时抑制逐条日志，避免 after(0) 洪水)"""
        if FileServerHandler.suppress_access_log:
            return
        if FileServerHandler.log_callback:
            FileServerHandler.log_callback(f"[HTTP] {args[0]}")

    def _send_json(self, data, status=200):
        # 必须携带 Content-Length: HTTP/1.1 keep-alive 下客户端依赖它判断 body 结束,
        # 否则 resp.read() 会阻塞直到超时 (P0 Bug 修复)
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, filepath, file_info=""):
        if not os.path.isfile(filepath):
            self._send_json({"error": "文件不存在"}, 404)
            return

        file_size = os.path.getsize(filepath)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(file_size))
        self.end_headers()

        # 源设备日志: 显示正在发送的文件
        if FileServerHandler.log_callback:
            display = file_info or os.path.basename(filepath)
            FileServerHandler.log_callback(
                f"[HTTP] 正在发送: {display} ({_fmt_size(file_size)})"
            )

        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(4 * 1024 * 1024)  # 4MB chunks，减少循环和 SSL 记录开销
                if not chunk:
                    break
                self.wfile.write(chunk)
        # 确保缓冲数据立即推送 (wbufsize=1MB 时，最后不到 1MB 的数据可能未自动 flush)
        try:
            self.wfile.flush()
        except Exception:
            pass

    def _resolve_path(self, normal_partition: str, relative_path: str = ""):
        """
        将正常盘符 + 相对路径 转换为 PE 下的绝对路径
        normal_partition: "D", "E", "F"
        relative_path: "folder/sub/file.txt"
        """
        pe_drive = FileServerHandler.partition_map.get(normal_partition)
        if not pe_drive:
            return None
        base = pe_drive.rstrip("\\") + "\\"
        if relative_path:
            # 安全检查：防止路径穿越
            safe_path = os.path.normpath(relative_path).lstrip("\\/")
            return os.path.join(base, safe_path)
        return base

    def do_GET(self):
        try:
            # 解析路径和参数
            path, params = self._parse_query(self.path)

            # ---- 鉴权: 必须携带正确的 pwd ----
            if not FileServerHandler._is_authorized(params):
                if FileServerHandler.log_callback:
                    FileServerHandler.log_callback(
                        f"[诊断] 拒绝未授权请求: path={path} (缺少或错误的 ?pwd= 验证码)")
                self._send_json({"error": "未授权: 验证码错误"}, 403)
                return

            if path == "/list":
                self._handle_list(params)
            elif path == "/get":
                self._handle_get(params)
            elif path == "/ping":
                self._send_json({"status": "ok", "partitions": list(FileServerHandler.partition_map.keys())})
            else:
                self._send_json({"error": "未知端点"}, 404)
        except Exception as e:
            try:
                self._send_json({"error": str(e)}, 500)
            except:
                pass

    def _handle_list(self, params):
        partition = params.get("partition", "").upper()
        if partition not in ("D", "E", "F"):
            self._send_json({"error": f"无效分区: {partition}"}, 400)
            return

        base = self._resolve_path(partition)
        if not base or not os.path.isdir(base):
            self._send_json({"error": f"分区 {partition} 未映射或不可访问"}, 404)
            return

        files = []
        total_size = 0
        try:
            for root, dirs, filenames in os.walk(base):
                # 过滤: 跳过 AppData / $前缀文件夹 / System Volume Information
                dirs[:] = [
                    d for d in dirs
                    if not (
                        d in SKIP_DIRS
                        or d.startswith(SKIP_PREFIXES)
                    )
                ]
                # 额外: 跳过路径中包含 AppData 的文件 (AppData 下的内容)
                rel_root = os.path.relpath(root, base)
                if "AppData" in rel_root.replace("\\", "/").split("/"):
                    continue

                for fname in filenames:
                    # 跳过系统文件 (仅根目录)
                    if os.path.normpath(root) == os.path.normpath(base):
                        if fname in SKIP_FILES:
                            continue
                    full = os.path.join(root, fname)
                    try:
                        st = os.stat(full)
                        fsize = st.st_size
                        f_mtime = st.st_mtime  # 保留原始修改时间，传输后还原
                    except OSError:
                        fsize = 0
                        f_mtime = 0
                    rel = os.path.relpath(full, base)
                    files.append({
                        "path": rel.replace("\\", "/"),
                        "size": fsize,
                        "mtime": f_mtime,
                    })
                    total_size += fsize

            self._send_json({
                "partition": partition,
                "file_count": len(files),
                "total_size": total_size,
                "files": files,
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_get(self, params):
        partition = params.get("partition", "").upper()
        rel_path = params.get("path", "")
        if partition not in ("D", "E", "F") or not rel_path:
            self._send_json({"error": "参数错误"}, 400)
            return

        full_path = self._resolve_path(partition, rel_path)
        if not full_path:
            self._send_json({"error": "路径解析失败"}, 400)
            return

        # 构建可读的文件信息供日志使用
        file_info = f"{partition}:\\{rel_path.replace('/', '\\\\')}"
        self._send_file(full_path, file_info=file_info)

    # ---- 小文件批量下载 ----
    def do_POST(self):
        try:
            path, params = self._parse_query(self.path)
            # ---- 鉴权: 必须携带正确的 pwd ----
            if not FileServerHandler._is_authorized(params):
                if FileServerHandler.log_callback:
                    FileServerHandler.log_callback(
                        f"[诊断] 拒绝未授权请求: path={path} (缺少或错误的 ?pwd= 验证码)")
                self._send_json({"error": "未授权: 验证码错误"}, 403)
                return
            if path == "/batch_get":
                self._handle_batch_get()
            else:
                self._send_json({"error": "未知端点"}, 404)
        except Exception as e:
            try:
                self._send_json({"error": str(e)}, 500)
            except:
                pass

    def _handle_batch_get(self):
        """一次 POST 请求下发多个小文件，减少小文件的 HTTP 往返开销"""
        import struct as _struct

        content_len = int(self.headers.get("Content-Length", 0))
        if content_len == 0 or content_len > 2 * 1024 * 1024:
            self._send_json({"error": "请求体为空或过大"}, 400)
            return

        body = self.rfile.read(content_len)
        try:
            req_data = json.loads(body.decode("utf-8-sig"))
        except Exception:
            self._send_json({"error": "JSON 解析失败"}, 400)
            return

        partition = req_data.get("partition", "").upper()
        paths = req_data.get("paths", [])
        if partition not in ("D", "E", "F") or not paths:
            self._send_json({"error": "参数错误"}, 400)
            return

        # 在内存中读取所有请求的文件
        file_entries = []  # [(path_bytes, data_bytes), ...]
        total_body_size = 4  # 开头 4 字节存文件数量
        for rel_path in paths:
            full_path = self._resolve_path(partition, rel_path)
            if not full_path or not os.path.isfile(full_path):
                continue
            try:
                with open(full_path, "rb") as f:
                    data = f.read()
            except OSError:
                continue
            path_bytes = rel_path.encode("utf-8")
            file_entries.append((path_bytes, data))
            total_body_size += 4 + len(path_bytes) + 8 + len(data)

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(total_body_size))
        self.end_headers()

        self.wfile.write(_struct.pack(">I", len(file_entries)))
        for path_bytes, data in file_entries:
            self.wfile.write(_struct.pack(">I", len(path_bytes)))
            self.wfile.write(path_bytes)
            self.wfile.write(_struct.pack(">Q", len(data)))
            self.wfile.write(data)


class ThreadingFileServer(ThreadingMixIn, HTTPServer):
    """多线程 HTTPS 服务器，支持并发处理多个下载请求。

    关键设计: _requests_semaphore 限制并发处理线程数 (默认 16)。
    ThreadingMixIn 原生无上限 — 每次请求 spawn 新线程, 客户端 8 worker × 上万文件
    会导致服务端创建/销毁数万个线程, 最终资源耗尽 (WinError 10054→10060)。
    """
    daemon_threads = True
    allow_reuse_address = True
    ssl_ctx = None  # 服务器 SSL 上下文 (由 FileServer.start 设置)

    # 输出缓冲区: 1MB，合并多次小写为一个 socket.sendall，减少 SSL/TLS 记录开销
    # 默认 wbufsize=0 意味着每个 write 都直接走 sendall，产生大量 TLS 片 → 严重拖慢吞吐量
    wbufsize = 1024 * 1024

    # 最大并发处理线程数 (防止 ThreadingMixIn 无限创建线程耗尽 OS 资源)
    MAX_CONCURRENT_HANDLERS = 16
    _requests_semaphore = None  # 类级别信号量, 首次 process_request 时惰性初始化

    def process_request(self, request, client_address):
        """限制并发线程数: 超过上限则阻塞等待, 避免无限创建线程"""
        if ThreadingFileServer._requests_semaphore is None:
            ThreadingFileServer._requests_semaphore = threading.BoundedSemaphore(
                ThreadingFileServer.MAX_CONCURRENT_HANDLERS
            )
        ThreadingFileServer._requests_semaphore.acquire()
        try:
            super().process_request(request, client_address)
        finally:
            ThreadingFileServer._requests_semaphore.release()

    def server_bind(self):
        """绑定前设置 socket 选项"""
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # TCP_NODELAY: 禁用 Nagle 算法，小文件立即发送不等待合并
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # SO_SNDBUF: 增大发送缓冲区 (2MB)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2 * 1024 * 1024)
        super().server_bind()

    def get_request(self):
        """接受连接后设置客户端 socket 选项, 并强制 TLS: 仅当首字节为 TLS ClientHello
        (0x16 0x03) 才升级为加密连接; 其余一律视为非法明文请求直接关闭。
        按安全策略 (指令 C) 禁止明文 HTTP, 无证书 (ssl_ctx 为 None) 时同样拒绝服务。

        关键: 所有拒绝路径都抛 OSError 子类 (ConnectionAbortedError)。
        原因: 基类 BaseServer.handle_request 只捕获 `except OSError` 来跳过异常连接,
        若抛 RuntimeError 会导致异常逃逸出 serve_forever, 使整个服务器线程崩溃 ——
        这正是"浏览器一发明文请求服务器就死、后续 https 也连不上"的根因。
        改为抛 OSError 后, 非法/握手失败的连接只会被丢弃, 服务器继续存活。
        """
        conn, addr = super().get_request()
        if FileServerHandler.log_callback:
            FileServerHandler.log_callback(f"[诊断] 收到新连接来自 {addr}")
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2 * 1024 * 1024)
        conn.settimeout(30)  # 30s 超时，防止僵死连接
        if self.ssl_ctx is None:
            # 无证书则无法提供 TLS; 按指令 C 禁止明文, 直接拒绝
            if FileServerHandler.log_callback:
                FileServerHandler.log_callback(f"[诊断] 拒绝连接 {addr}: 未配置 TLS 证书 (明文 HTTP 已禁用)")
            try:
                conn.close()
            except Exception:
                pass
            raise ConnectionAbortedError("未配置 TLS 证书, 已禁用明文 HTTP (指令 C)")
        # 稳健读取 TLS 握手头: 至少需要 2 字节 (0x16 0x03)。
        # 跨机网络下 recv(2, MSG_PEEK) 可能先只到达 1 字节, 必须用循环补齐,
        # 否则会误判为"明文 HTTP"而直接关闭连接, 导致传输失败。
        try:
            peek = b""
            while len(peek) < 2:
                chunk = conn.recv(2 - len(peek), socket.MSG_PEEK)
                if not chunk:
                    raise ConnectionAbortedError("客户端未发送数据 (连接已关闭)")
                peek += chunk
        except Exception as e:
            try:
                conn.close()
            except Exception:
                pass
            if FileServerHandler.log_callback:
                FileServerHandler.log_callback(
                    f"[诊断] 拒绝连接 {addr}: 读取握手头失败/超时: {e} (连接已关闭)")
            # 抛 OSError 子类, 让服务器在拒绝该连接后继续存活 (而非崩溃)
            raise ConnectionAbortedError("明文 HTTP 已被禁用 (指令 C)")
        if not (peek[:1] == b"\x16" and peek[1:2] == b"\x03"):
            # 非 TLS 请求 (明文 HTTP) → 禁止, 关闭连接
            try:
                conn.close()
            except Exception:
                pass
            if FileServerHandler.log_callback:
                FileServerHandler.log_callback(f"[诊断] 拒绝连接 {addr}: 非 TLS 握手 (明文 HTTP 已被禁用)")
            # 抛 OSError 子类, 让服务器在拒绝该连接后继续存活 (而非崩溃)
            raise ConnectionAbortedError("明文 HTTP 已被禁用 (指令 C)")
        try:
            conn = self.ssl_ctx.wrap_socket(conn, server_side=True)
        except Exception as e:
            try:
                conn.close()
            except Exception:
                pass
            if FileServerHandler.log_callback:
                FileServerHandler.log_callback(f"[诊断] 拒绝连接 {addr}: TLS 握手失败: {e}")
            # TLS 握手失败 (非 TLS 客户端/证书问题) → 抛 OSError, 服务器继续存活
            raise ConnectionAbortedError("TLS 握手失败, 已断开连接")
        if FileServerHandler.log_callback:
            FileServerHandler.log_callback(f"[诊断] 连接 {addr}: TLS 握手成功, 进入加密通道")
        # 握手成功后取消 30s 握手超时, 改为阻塞, 避免大文件传输中因空闲间隙被误断
        conn.settimeout(None)
        return conn, addr


# (HTTP→HTTPS 重定向服务已移除; 本服务器为纯 TLS, 明文 HTTP 已被禁用, 见指令 C)


class FileServer:
    """文件服务器包装类 (纯 TLS, 明文 HTTP 已禁用)"""

    def __init__(self, partition_map: dict, log_callback=None,
                 auth_code: str = "", cert_paths: tuple = None, redirect_ip: str = ""):
        """
        partition_map: {"D": "I:", "E": "J:", "F": "K:"} 正常盘符→PE盘符
        auth_code:     随机验证码, 客户端请求必须携带
        cert_paths:    (cert_path, key_path) 自签名证书
        redirect_ip:   保留参数 (原明文重定向目标, 现已禁用明文, 不再使用)
        """
        self.partition_map = partition_map
        self.log_callback = log_callback
        self.auth_code = auth_code
        self.cert_paths = cert_paths
        self.redirect_ip = redirect_ip
        self._server = None
        self._thread = None
        self._redirect_server = None
        self._redirect_thread = None

    def start(self):
        """启动服务器（阻塞线程）"""
        FileServerHandler.partition_map = self.partition_map
        FileServerHandler.log_callback = self.log_callback
        FileServerHandler.suppress_access_log = True  # 批量传输时禁止逐条 HTTP 访问日志
        FileServerHandler.auth_code = self.auth_code

        self._server = ThreadingFileServer(("0.0.0.0", TRANSFER_PORT), FileServerHandler)
        if self.cert_paths:
            try:
                self._server.ssl_ctx = tls_utils.make_server_ssl_context(*self.cert_paths)
                if self.log_callback:
                    self.log_callback("HTTPS (TLS) 已启用")
                    self.log_callback(f"[诊断] TLS 证书文件: {self.cert_paths[0]}")
                    self.log_callback(f"[诊断] TLS 证书 SAN: {tls_utils.cert_san_info(self.cert_paths[0])}")
                    self.log_callback(f"[诊断] 鉴权验证码已设置: {'是' if self.auth_code else '否(将拒绝所有请求)'}")
                    self.log_callback(f"[诊断] 已映射盘符: {list(self.partition_map.keys())}")
            except Exception as e:
                if self.log_callback:
                    self.log_callback(f"SSL 上下文创建失败: {e}")
        else:
            if self.log_callback:
                self.log_callback("[诊断] 未提供证书路径, ssl_ctx 为 None, 将拒绝所有连接")
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        _prevent_sleep()  # 源端开始传输后阻止系统休眠/锁屏
        if self.log_callback:
            self.log_callback(f"文件服务器已启动 (纯 TLS), 监听端口 {TRANSFER_PORT}")

        # 说明: 纯 TLS 服务器, 明文 HTTP 已禁用 (指令 C)。鉴权由 pwd 验证码保证。
        # 端口为高位非特权端口 9999, 无需 SYSTEM/管理员即可绑定, 避免 WinError 10013。

    def _serve(self):
        try:
            self._server.serve_forever()
        except Exception as e:
            if self.log_callback:
                self.log_callback(f"服务器异常: {e}")

    def _redirect_serve(self):
        try:
            self._redirect_server.serve_forever()
        except Exception:
            pass

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._redirect_server:
            try:
                self._redirect_server.shutdown()
                self._redirect_server.server_close()
            except Exception:
                pass
        _allow_sleep()  # 传输结束, 恢复系统正常休眠策略
        if self.log_callback:
            self.log_callback("文件服务器已停止")


# ===================== 文件下载客户端 (目标设备) =====================

# 多线程下载并发数（有线网络通常 4-8 线程最佳）
# 降低至 4, 减少对服务端的并发连接压力, 避免 ThreadingMixIn 线程爆炸
DEFAULT_DOWNLOAD_WORKERS = 4

# 小文件批量传输配置
BATCH_SIZE_THRESHOLD = 1 * 1024 * 1024   # < 1MB 归入批次
BATCH_MAX_FILES = 200                     # 每批次最多文件数
BATCH_MAX_TOTAL = 20 * 1024 * 1024        # 每批次最大总字节数 (20MB)

# 线程本地 HTTP 连接池: 每个线程持有一个持久连接，复用避免 TCP 握手开销
# 关键设计: 连接断开后只标记为死，不立即重建 → 避免 Windows 临时端口耗尽
import http.client as _http_client
_tl_connections = threading.local()


def _urlopen_https(url: str, timeout: int = 5, context=CLIENT_SSL_CTX, log_callback=None, **kwargs):
    """仅使用 HTTPS (TLS)。按安全策略 (指令 C) 禁止回退到明文 HTTP:
    TLS 握手失败即抛出异常, 由调用方记录并中止, 绝不降级为明文传输。
    链路加密由 TLS 提供, 鉴权仍由 pwd 验证码保证。

    证书校验已被关闭 (CERT_NONE), 故证书/主机名错误不会在此抛出,
    连接失败通常是网络层问题 (主机不可达/超时/被拒绝)。诊断信息会明确区分。
    """
    try:
        return urllib.request.urlopen(url, timeout=timeout, context=context, **kwargs)
    except Exception as e:
        # 把底层原因展开, 便于定位"连不上"的真实原因
        reason = getattr(e, "reason", None)
        errno = getattr(reason, "errno", None)
        import ssl as _ssl
        kind = "未知"
        if isinstance(e, _ssl.SSLError):
            kind = "TLS握手/证书错误"
        elif isinstance(e, TimeoutError) or (errno in (11001, 10060, 60, 110)):
            kind = "连接超时"
        elif errno in (10061, 111, 61):
            kind = "连接被拒绝(目标端口无服务)"
        elif errno in (11001, 10051, 101, 51):
            kind = "主机不可达/网络不可达"
        elif reason is not None:
            kind = f"底层错误({reason})"
        detail = f"[{kind}] {type(e).__name__}: {e}"
        if log_callback:
            log_callback(f"[诊断] HTTPS 请求失败 {url} -> {detail}")
        # 重新抛出, 让调用方 (download_files/verifier) 继续处理
        raise


def _get_thread_connection(host: str, port: int):
    """获取或惰性建立当前线程的持久 TLS 连接 (纯 HTTPS)。
    按安全策略 (指令 C) 禁止明文回退: 若 TLS 握手失败直接抛出异常,
    由下载函数捕获并记录错误, 绝不降级为明文 HTTP。
    """
    key = f"{host}:{port}"
    conns = getattr(_tl_connections, "conns", None)
    if conns is None:
        conns = {}
        _tl_connections.conns = conns

    conn = conns.get(key)
    if conn is None:
        conn = _http_client.HTTPSConnection(
            host, port, timeout=120, context=CLIENT_SSL_CTX, blocksize=1024 * 1024
        )
        conn.connect()
        # 客户端性能优化: TCP_NODELAY 禁用 Nagle(防止 ACK 延迟拖慢发送端),
        # SO_RCVBUF 增大接收窗口 (匹配服务端 SO_SNDBUF=2MB)
        try:
            conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2 * 1024 * 1024)
        except Exception:
            pass
        conns[key] = conn
    return conn


def _invalidate_thread_connection(host: str, port: int):
    """标记连接为死: 不立即重建，下次请求时惰性重建 (速率由文件下载本身限制)"""
    key = f"{host}:{port}"
    conns = getattr(_tl_connections, "conns", None)
    if conns and key in conns:
        try:
            conns[key].close()
        except Exception:
            pass
        del conns[key]


def _close_all_thread_connections():
    """关闭当前线程持有的所有持久连接 (分区间调用, 避免复用僵死连接)"""
    conns = getattr(_tl_connections, "conns", None)
    if conns:
        for key in list(conns.keys()):
            try:
                conns[key].close()
            except Exception:
                pass
        conns.clear()


def _download_single_file(
    base_url: str,
    normal_partition: str,
    rel_path: str,
    fsize: int,
    target_path: str,
    mtime: float = 0,
    auth_code: str = "",
    stats_lock: threading.Lock = None,
    completed_files_list: list = None,
    completed_bytes_list: list = None,
    errors: list = None,
    log_callback=None,
    file_progress_callback=None,
    overwrite: bool = False,
):
    """下载单个文件（在线程池中执行，写 .tmp 后重命名防断点文件损坏）
    overwrite=True 时覆盖已存在文件 (用户选择覆盖); 否则断点续传跳过已存在文件。
    """
    import urllib.parse as _urlparse

    def log(msg):
        if log_callback:
            log_callback(msg)

    # 已存在文件处理: 覆盖模式重下; 否则断点续传跳过 (大小正确时)
    if os.path.isfile(target_path):
        existing_size = os.path.getsize(target_path)
        if existing_size == fsize and not overwrite:
            if mtime:
                try:
                    os.utime(target_path, (mtime, mtime))
                except OSError:
                    pass
            log(f"  [✓] 跳过(已存在): {rel_path}")
            with stats_lock:
                completed_files_list[0] += 1
                completed_bytes_list[0] += fsize
            return
        elif not overwrite:
            log(f"  [_] 大小不匹配, 重新下载: {rel_path} (已有{existing_size}, 期望{fsize})")
        else:
            log(f"  [_] 覆盖模式, 重新下载: {rel_path}")
        # 覆盖/大小不匹配: 由下方 tmp+rename 过程覆盖, 无需预删除

    # 清理上一轮残留的 .tmp 文件
    tmp_path = target_path + ".tmp"
    if os.path.isfile(tmp_path):
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    parsed = _urlparse.urlparse(base_url)
    host = parsed.hostname
    port = parsed.port or TRANSFER_PORT

    try:
        conn = _get_thread_connection(host, port)
        encoded_path = _urlparse.quote(rel_path, safe="")
        url_path = f"/get?partition={normal_partition}&path={encoded_path}&pwd={urllib.parse.quote(auth_code)}"
        conn.request("GET", url_path, headers={"Connection": "keep-alive"})
        resp = conn.getresponse()

        if resp.status != 200:
            body = resp.read().decode("utf-8-sig", errors="replace")
            log(f"  [X] 下载失败 HTTP {resp.status}: {rel_path} - {body}")
            with stats_lock:
                errors.append(f"分区 {normal_partition}: {rel_path} HTTP {resp.status}")
            return

        # 大文件: 记录开始传输
        if fsize >= BATCH_SIZE_THRESHOLD:
            log(f"  [→] 正在拷贝: {rel_path} ({_fmt_size(fsize)})")

        # 写入 .tmp 文件（断点保护: 只有完整下载后才重命名）
        with open(tmp_path, "wb") as f:
            file_done = 0
            while True:
                chunk = resp.read(4 * 1024 * 1024)  # 4MB chunks，匹配服务端发送块大小
                if not chunk:
                    break
                f.write(chunk)
                file_done += len(chunk)
                with stats_lock:
                    completed_bytes_list[0] += len(chunk)
                if file_progress_callback and fsize > 0:
                    file_progress_callback(rel_path, file_done, fsize)

        # 验证大小
        actual_size = os.path.getsize(tmp_path)
        if actual_size != fsize:
            log(f"  [!] 大小不匹配: {rel_path} (期望{fsize}, 实际{actual_size})")
            with stats_lock:
                errors.append(f"分区 {normal_partition}: {rel_path} 大小不匹配")
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return

        # 验证通过 → 重命名 .tmp 为目标文件
        try:
            if os.path.isfile(target_path):
                os.remove(target_path)
            os.rename(tmp_path, target_path)
        except OSError as e:
            log(f"  [!] 重命名失败: {rel_path} - {e}")
            with stats_lock:
                errors.append(f"分区 {normal_partition}: {rel_path} 重命名失败")
            return

        # 还原原始修改时间 (HTTP 传输默认会变为传输时间)
        if mtime:
            try:
                os.utime(target_path, (mtime, mtime))
            except OSError:
                pass

        if fsize >= BATCH_SIZE_THRESHOLD:
            log(f"  [OK] {rel_path}")

        with stats_lock:
            completed_files_list[0] += 1

    except (ConnectionError, TimeoutError, OSError,
            ConnectionAbortedError, ConnectionResetError,
            BrokenPipeError) as e:
        _invalidate_thread_connection(host, port)
        _handle_tmp_failure(log, tmp_path, rel_path)
        log(f"  [X] 下载失败(连接): {rel_path} - {e}")
        with stats_lock:
            errors.append(f"分区 {normal_partition}: {rel_path} 下载失败 - {e}")
        raise  # 重新抛出连接错误, 供上层重试逻辑处理

    except Exception as e:
        _handle_tmp_failure(log, tmp_path, rel_path)
        log(f"  [X] 下载失败: {rel_path} - {e}")
        with stats_lock:
            errors.append(f"分区 {normal_partition}: {rel_path} 下载失败 - {e}")


def _download_single_file_with_retry(
    base_url: str,
    normal_partition: str,
    rel_path: str,
    fsize: int,
    target_path: str,
    mtime: float = 0,
    auth_code: str = "",
    stats_lock: threading.Lock = None,
    completed_files_list: list = None,
    completed_bytes_list: list = None,
    errors: list = None,
    log_callback=None,
    file_progress_callback=None,
    overwrite: bool = False,
    max_retries: int = 3,
):
    """带重试的单文件下载: 连接错误时指数退避重试 (最多 3 次)。
    服务端线程爆炸时连接被拒绝 → 等一会再试通常恢复。
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            return _download_single_file(
                base_url, normal_partition, rel_path, fsize, target_path,
                mtime, auth_code, stats_lock, completed_files_list,
                completed_bytes_list, errors, log_callback,
                file_progress_callback, overwrite,
            )
        except (ConnectionError, TimeoutError, OSError,
                ConnectionAbortedError, ConnectionResetError,
                BrokenPipeError) as e:
            last_error = e
            if attempt < max_retries - 1:
                delay = 0.5 * (2 ** attempt)  # 0.5s, 1s, 2s
                if log_callback:
                    log_callback(f"  [_] 重试 {attempt+1}/{max_retries}: {rel_path} ({delay:.1f}s 后退)")
                time.sleep(delay)
        except Exception:
            # 非连接错误不重试
            break

    if last_error and log_callback:
        log_callback(f"  [X] 下载失败(已重试{max_retries}次): {rel_path} - {last_error}")


def _handle_tmp_failure(log, tmp_path: str, rel_path: str):
    """下载失败时清理残留 .tmp"""
    if tmp_path and os.path.isfile(tmp_path):
        try:
            os.remove(tmp_path)
            log(f"  [_] 已清理残留: {rel_path}.tmp")
        except OSError:
            pass


def _download_batch(
    base_url: str,
    normal_partition: str,
    batch_tasks: list,  # [(normal_partition, rel_path, fsize, target_path, mtime), ...]
    auth_code: str = "",
    stats_lock: threading.Lock = None,
    completed_files_list: list = None,
    completed_bytes_list: list = None,
    errors: list = None,
    log_callback=None,
    overwrite: bool = False,
):
    """批量下载小文件：一次 POST 请求获取多个文件，写 .tmp 后重命名防断点损坏
    overwrite=True 时覆盖已存在文件; 否则断点续传跳过已存在且大小正确的文件。
    """
    import struct as _struct
    import urllib.parse as _urlparse

    def log(msg):
        if log_callback:
            log_callback(msg)

    parsed = _urlparse.urlparse(base_url)
    host = parsed.hostname
    port = parsed.port or TRANSFER_PORT

    # 断点续传: 跳过已存在且大小正确的文件 (覆盖模式则全部重新下载)
    remaining_tasks = []  # [(rel_path, fsize, target_path), ...]
    for item in batch_tasks:
        _, rel_path, fsize, target_path, mtime = item
        if os.path.isfile(target_path):
            existing_size = os.path.getsize(target_path)
            if existing_size == fsize and not overwrite:
                if mtime:
                    try:
                        os.utime(target_path, (mtime, mtime))
                    except OSError:
                        pass
                log(f"  [✓] 跳过(已存在): {rel_path}")
                with stats_lock:
                    completed_files_list[0] += 1
                    completed_bytes_list[0] += fsize
                continue
            elif not overwrite:
                log(f"  [_] 大小不匹配, 重新下载: {rel_path}")
            else:
                log(f"  [_] 覆盖模式, 重新下载: {rel_path}")
            # 覆盖/大小不匹配: 交由下方 tmp+rename 覆盖
        remaining_tasks.append((rel_path, fsize, target_path, mtime))

    if not remaining_tasks:
        return  # 本批次全部已存在

    # 构建请求体：文件路径列表
    paths = [t[0] for t in remaining_tasks]  # rel_path
    expected = {t[0]: t[1] for t in remaining_tasks}  # {path: fsize}

    try:
        conn = _get_thread_connection(host, port)
        body = json.dumps({
            "partition": normal_partition,
            "paths": paths,
        }, ensure_ascii=False).encode("utf-8-sig")

        conn.request("POST", f"/batch_get?pwd={urllib.parse.quote(auth_code)}", body=body, headers={
            "Content-Type": "application/json; charset=utf-8",
            "Connection": "keep-alive",
        })
        resp = conn.getresponse()

        if resp.status != 200:
            body_text = resp.read().decode("utf-8-sig", errors="replace")
            log(f"  [X] 批次下载失败 HTTP {resp.status}: {body_text}")
            with stats_lock:
                for rel_path, fsize, target_path, _mt in remaining_tasks:
                    errors.append(
                        f"分区 {normal_partition}: {rel_path} 批次HTTP {resp.status}"
                    )
            return

        # 解析二进制响应
        raw = resp.read()
        if len(raw) < 4:
            log(f"  [X] 批次响应过短: {len(raw)} 字节")
            with stats_lock:
                for rel_path, fsize, target_path, _mt in remaining_tasks:
                    errors.append(f"分区 {normal_partition}: {rel_path} 批次响应异常")
            return

        pos = 0
        file_count = _struct.unpack_from(">I", raw, pos)[0]
        pos += 4

        received_files = 0
        returned_paths = set()

        for i in range(file_count):
            if pos + 4 > len(raw):
                break
            path_len = _struct.unpack_from(">I", raw, pos)[0]
            pos += 4

            if pos + path_len > len(raw):
                break
            rel_path = raw[pos:pos + path_len].decode("utf-8")
            pos += path_len

            if pos + 8 > len(raw):
                break
            data_len = _struct.unpack_from(">Q", raw, pos)[0]
            pos += 8

            if pos + data_len > len(raw):
                break
            file_data = raw[pos:pos + data_len]
            pos += data_len
            returned_paths.add(rel_path)

            # 查找对应任务
            target_path = ""
            exp_size = 0
            exp_mtime = 0
            for bp, bsize, tp, bt in remaining_tasks:
                if bp == rel_path:
                    target_path = tp
                    exp_size = bsize
                    exp_mtime = bt
                    break

            if not target_path:
                log(f"  [_] 批次中未知文件: {rel_path}，跳过")
                continue

            # 写入 .tmp 文件
            tmp_path = target_path + ".tmp"
            try:
                with open(tmp_path, "wb") as f:
                    f.write(file_data)
            except OSError as e:
                log(f"  [X] 写入失败: {rel_path} - {e}")
                with stats_lock:
                    errors.append(f"分区 {normal_partition}: {rel_path} 写入失败")
                continue

            # 大小校验
            actual_size = os.path.getsize(tmp_path)
            if actual_size != exp_size:
                log(f"  [!] 批次文件大小不匹配: {rel_path} (期望{exp_size}, 实际{actual_size})")
                with stats_lock:
                    errors.append(f"分区 {normal_partition}: {rel_path} 大小不匹配")
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                continue

            # 校验通过 → 重命名 .tmp
            try:
                if os.path.isfile(target_path):
                    os.remove(target_path)
                os.rename(tmp_path, target_path)
            except OSError as e:
                log(f"  [!] 重命名失败: {rel_path} - {e}")
                with stats_lock:
                    errors.append(f"分区 {normal_partition}: {rel_path} 重命名失败")
                continue

            # 还原原始修改时间
            if exp_mtime:
                try:
                    os.utime(target_path, (exp_mtime, exp_mtime))
                except OSError:
                    pass

            with stats_lock:
                completed_files_list[0] += 1
                completed_bytes_list[0] += actual_size
            received_files += 1

        # 检查是否有本批次请求了但未返回的文件
        for bp, bsize, btp, _bt in remaining_tasks:
            if bp in returned_paths:
                continue
            if os.path.isfile(btp):
                continue  # 文件可能在之前已存在
            log(f"  [X] 批次缺失: {bp} (服务端未返回)")
            with stats_lock:
                errors.append(f"分区 {normal_partition}: {bp} 批次缺失")

    except (ConnectionError, TimeoutError, OSError,
            ConnectionAbortedError, ConnectionResetError,
            BrokenPipeError) as e:
        _invalidate_thread_connection(host, port)
        log(f"  [X] 批次下载失败(连接): {e}")
        with stats_lock:
            for rel_path, fsize, target_path, _mt in remaining_tasks:
                errors.append(f"分区 {normal_partition}: {rel_path} 批次连接失败")
        raise  # 重新抛出连接错误, 供上层重试逻辑处理

    except Exception as e:
        log(f"  [X] 批次下载失败: {e}")
        with stats_lock:
            for rel_path, fsize, target_path, _mt in remaining_tasks:
                errors.append(f"分区 {normal_partition}: {rel_path} 批次异常")


def _download_batch_with_retry(
    base_url: str,
    normal_partition: str,
    batch_tasks: list,
    auth_code: str = "",
    stats_lock: threading.Lock = None,
    completed_files_list: list = None,
    completed_bytes_list: list = None,
    errors: list = None,
    log_callback=None,
    overwrite: bool = False,
    max_retries: int = 3,
):
    """带重试的批量下载: 连接错误时指数退避重试 (最多 3 次)"""
    last_error = None
    for attempt in range(max_retries):
        try:
            return _download_batch(
                base_url, normal_partition, batch_tasks,
                auth_code, stats_lock, completed_files_list,
                completed_bytes_list, errors, log_callback, overwrite,
            )
        except (ConnectionError, TimeoutError, OSError,
                ConnectionAbortedError, ConnectionResetError,
                BrokenPipeError) as e:
            last_error = e
            if attempt < max_retries - 1:
                delay = 0.5 * (2 ** attempt)
                if log_callback:
                    log_callback(
                        f"  [_] 批次重试 {attempt+1}/{max_retries} "
                        f"({len(batch_tasks)} 个文件) ({delay:.1f}s 后退)"
                    )
                time.sleep(delay)
        except Exception:
            break

    if last_error and log_callback:
        log_callback(
            f"  [X] 批次下载失败(已重试{max_retries}次): "
            f"{len(batch_tasks)} 个文件 - {last_error}"
        )


def download_files(
    server_ip: str,
    partition_map: dict,
    log_callback=None,
    progress_callback=None,
    partition_progress_callback=None,
    max_workers: int = DEFAULT_DOWNLOAD_WORKERS,
    partition_count: int = 0,
    auth_code: str = "",
    overwrite: bool = False,
    stop_check=None,
    conflict_callback=None,
):
    """
    从源设备多线程并行下载 D/E/F 分区数据 (HTTPS + 验证码鉴权)
    partition_map: {"D": "I:", "E": "J:", "F": "K:"}  正常盘符→目标PE盘符
    server_ip: 源设备 IP
    auth_code: 源设备显示的验证码 (pwd)
    max_workers: 并行下载线程数 (默认 6)
    partition_count: NTFS 分区数 (2/3/4) — 2 分区时 D 盘仅下载 User 文件夹
    conflict_callback: 同名文件冲突回调 conflicts → set of paths to skip
                       (参数: list[dict], log_function → set[str])
    """
    import urllib.request
    import urllib.error

    base_url = f"https://{server_ip}:{TRANSFER_PORT}"
    pwd = urllib.parse.quote(auth_code)
    local_partition_map = partition_map

    _prevent_sleep()  # 接收端开始传输后阻止系统休眠/锁屏
    try:
        success, done_files, done_bytes, errors = _download_files_inner(
            base_url, pwd, local_partition_map, log_callback,
            progress_callback, partition_progress_callback,
            max_workers, partition_count, auth_code, overwrite,
            stop_check, conflict_callback,
        )
    finally:
        _allow_sleep()  # 传输结束, 恢复系统正常休眠策略
    return success, done_files, done_bytes, errors


def _download_files_inner(
    base_url: str,
    pwd: str,
    local_partition_map: dict,
    log_callback=None,
    progress_callback=None,
    partition_progress_callback=None,
    max_workers: int = DEFAULT_DOWNLOAD_WORKERS,
    partition_count: int = 0,
    auth_code: str = "",
    overwrite: bool = False,
    stop_check=None,
    conflict_callback=None,
):
    # 使用可变列表包装，避免闭包中赋值问题
    completed_files = [0]
    completed_bytes = [0]
    total_files = 0
    total_bytes = 0
    errors = []

    def log(msg):
        if log_callback:
            log_callback(msg)

    def progress():
        if progress_callback:
            progress_callback(completed_files[0], total_files, completed_bytes[0], total_bytes)

    # 先 ping 确认服务器在线
    log(f"正在连接源设备 {base_url} (HTTPS)...")
    log(f"[诊断] 目标地址 base_url={base_url}, 验证码已携带={'是' if pwd else '否'}")
    log(f"[诊断] 客户端 TLS 上下文: check_hostname={getattr(CLIENT_SSL_CTX,'check_hostname',None)}, "
        f"verify_mode={getattr(CLIENT_SSL_CTX,'verify_mode',None)} (CERT_NONE=0 表示已忽略证书错误)")
    connected = False
    for attempt in range(5):
        try:
            req = _urlopen_https(
                f"{base_url}/ping?pwd={pwd}", timeout=5, log_callback=log
            )
            data = json.loads(req.read().decode("utf-8-sig"))
            if data.get("status") == "ok":
                log(f"已连接到源设备，可用分区: {data.get('partitions', [])}")
                connected = True
                break
            else:
                log(f"[诊断] 服务器已连通但返回非 ok: {data}")
        except Exception as e:
            log(f"[诊断] 第 {attempt+1}/5 次连接失败: {type(e).__name__}: {e}")
            if attempt == 4:
                log(f"无法连接到源设备 ({base_url}): {e}")
                log("排查建议: 1) 源端是否已点'源设备'启动服务器; 2) 两端网线/网卡已连接; "
                    "3) 接收端实际 IP 与源端是否同网段; 4) 防火墙是否放行 9999; "
                    "5) 验证码是否输入正确 (证书错误已被忽略, 此问题不是证书)")
                return False, 0, 0, errors
            time.sleep(2)
    if not connected:
        return False, 0, 0, errors

    # 第一阶段: 收集所有分区的文件列表
    all_download_tasks = []  # [(normal_partition, rel_path, fsize, target_path), ...]

    for normal_partition in ("D", "E", "F"):
        target_drive = local_partition_map.get(normal_partition, "").rstrip("\\") + "\\"
        if not target_drive.strip("\\"):
            log(f"分区 {normal_partition} 未配置映射，跳过")
            continue

        if not os.path.isdir(target_drive):
            log(f"目标分区 {target_drive} 不存在，跳过 {normal_partition}")
            errors.append(f"分区 {normal_partition}: 目标盘符 {target_drive} 不可用")
            continue

        log(f"\n{'='*40}")
        log(f"正在获取 {normal_partition} 盘文件列表...")
        try:
            req = _urlopen_https(
                f"{base_url}/list?partition={normal_partition}&pwd={pwd}", timeout=30,
            )
            list_data = json.loads(req.read().decode("utf-8-sig"))
        except Exception as e:
            log(f"获取 {normal_partition} 文件列表失败: {e}")
            errors.append(f"分区 {normal_partition}: 获取列表失败 - {e}")
            continue

        if "error" in list_data:
            log(f"{normal_partition}: {list_data['error']}")
            errors.append(f"分区 {normal_partition}: {list_data['error']}")
            continue

        files = list_data.get("files", [])
        partition_total = list_data.get("total_size", 0)

        # 2 分区模式: D 盘只下载 User 文件夹
        if partition_count == 2 and normal_partition == "D":
            filtered = []
            filtered_size = 0
            for f_info in files:
                rel = f_info["path"]
                if rel.replace("\\", "/").startswith("User/"):
                    filtered.append(f_info)
                    filtered_size += f_info["size"]
            log(f"{normal_partition} 盘: {len(filtered)} 个文件 (仅 User), 共 {_fmt_size(filtered_size)}")
            files = filtered
            partition_total = filtered_size
        else:
            log(f"{normal_partition} 盘: {len(files)} 个文件, 共 {_fmt_size(partition_total)}")
        total_files += len(files)
        total_bytes += partition_total

        for f_info in files:
            rel_path = f_info["path"]
            fsize = f_info["size"]
            mtime = f_info.get("mtime", 0)
            target_path = os.path.join(target_drive, rel_path.replace("/", "\\"))
            all_download_tasks.append((normal_partition, rel_path, fsize, target_path, mtime))

    # 断点续传: 清理所有残留 .tmp 文件 (上一轮传输中断留下的)
    log("清理上一轮残留的 .tmp 文件...")
    tmp_cleaned = 0
    for task in all_download_tasks:
        tmp_path = task[3] + ".tmp"
        if os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
                tmp_cleaned += 1
            except OSError:
                pass
    if tmp_cleaned > 0:
        log(f"已清理 {tmp_cleaned} 个残留 .tmp 文件")

    # 断点续传/覆盖: 统计已存在且大小正确的文件
    skipped_files = 0
    skipped_bytes = 0
    remaining_tasks = []
    if overwrite:
        # 覆盖模式: 不排除任何已存在文件, 全部重新下载
        remaining_tasks = list(all_download_tasks)
        log("覆盖模式: 将重新下载所有文件 (含已存在的)")
    else:
        # 断点续传: 跳过已存在且大小正确的文件
        for task in all_download_tasks:
            _, _, fsize, target_path, _mtime = task
            if os.path.isfile(target_path):
                existing_size = os.path.getsize(target_path)
                if existing_size == fsize:
                    # 修正已存在文件的原始修改时间 (上一轮可能丢失)
                    if _mtime:
                        try:
                            os.utime(target_path, (_mtime, _mtime))
                        except OSError:
                            pass
                    skipped_files += 1
                    skipped_bytes += fsize
                    continue
            remaining_tasks.append(task)
        if skipped_files > 0:
            log(f"断点续传: 跳过 {skipped_files} 个已存在文件 ({_fmt_size(skipped_bytes)})")
    all_download_tasks = remaining_tasks

    # 冲突检测: 同名文件已存在但大小不同 → 提示用户决定保留/覆盖
    if not overwrite and conflict_callback and all_download_tasks:
        conflicts = []
        conflict_seen = set()  # 同路径只记录一次
        for task in all_download_tasks:
            _, rel_path, fsize, target_path, mtime = task
            if target_path in conflict_seen:
                continue
            if os.path.isfile(target_path):
                existing_size = os.path.getsize(target_path)
                if existing_size != fsize:
                    try:
                        existing_mtime = os.path.getmtime(target_path)
                    except OSError:
                        existing_mtime = 0
                    conflicts.append({
                        "rel_path": rel_path,
                        "target_path": target_path,
                        "src_size": fsize,
                        "dst_size": existing_size,
                        "src_mtime": mtime,
                        "dst_mtime": existing_mtime,
                    })
                    conflict_seen.add(target_path)

        if conflicts:
            log(f"\n检测到 {len(conflicts)} 个同名文件冲突 (源端/目标端大小不同), 等待用户决定...")
            skip_paths = conflict_callback(conflicts, log)
            if skip_paths:
                # 用户选择保留目标端文件 → 过滤掉这些任务
                filtered_tasks = []
                for task in all_download_tasks:
                    if task[3] in skip_paths:
                        skipped_files += 1
                        skipped_bytes += task[2]
                        _mtime = task[4]
                        if _mtime:
                            try:
                                os.utime(task[3], (_mtime, _mtime))
                            except OSError:
                                pass
                    else:
                        filtered_tasks.append(task)
                all_download_tasks = filtered_tasks
                log(f"用户选择保留 {len(skip_paths)} 个已有文件")

    # 初始化进度: 已跳过的文件计入已完成
    completed_files[0] = skipped_files
    completed_bytes[0] = skipped_bytes
    if skipped_files > 0:
        progress()

    if not all_download_tasks:
        log("所有文件已存在，无需下载！")
        progress()
        return True, total_files, total_bytes, []
    # 按分区分组 (仅处理剩余任务)
    partition_task_map = {}
    for task in all_download_tasks:
        p = task[0]
        if p not in partition_task_map:
            partition_task_map[p] = []
        partition_task_map[p].append(task)

    log(f"\n总计 {total_files} 个文件, {_fmt_size(total_bytes)}")
    if skipped_files > 0:
        log(f"断点续传: 跳过 {skipped_files} 个已存在文件 ({_fmt_size(skipped_bytes)})")
    log(f"需下载: {len(all_download_tasks)} 个文件")
    log(f"开始分区串行下载 (分区内并行 {max_workers} 线程)...")

    # 总进度上报线程
    stats_lock = threading.Lock()

    def progress_reporter():
        """后台线程: 每 1 秒上报一次总进度。

        同时按"文件数"和"字节数"判断推进: 传输大文件时文件数长时间不变,
        但字节数在持续增长, 必须也按字节刷新, 否则进度条会"卡住"假死。
        """
        last_files = [completed_files[0]]
        last_bytes = [completed_bytes[0]]
        while completed_files[0] < total_files:
            time.sleep(1)
            with stats_lock:
                cur_files = completed_files[0]
                cur_bytes = completed_bytes[0]
            if cur_files != last_files[0] or cur_bytes != last_bytes[0]:
                progress()
                last_files[0] = cur_files
                last_bytes[0] = cur_bytes

    reporter_thread = threading.Thread(target=progress_reporter, daemon=True)
    reporter_thread.start()

    # 暂停/取消信号: stop_check() 返回 ("cancel",) 终止传输; 返回 ("pause",) 阻塞等待恢复
    _pause_event = threading.Event()
    _pause_event.set()

    def _check_stop():
        """返回 True 表示已取消; 暂停时阻塞直到恢复"""
        if stop_check is None:
            return False
        sig = stop_check()
        if sig == "cancel":
            return True
        if sig == "pause":
            _pause_event.clear()
            _pause_event.wait()  # 等待 control 层调用 set() 恢复
            return False
        return False

    # 逐分区处理 (D → E → F)，避免跨分区并发 IO 导致崩溃
    cancelled = False
    for normal_partition in ("D", "E", "F"):
        partition_tasks = partition_task_map.get(normal_partition, [])
        if not partition_tasks:
            continue

        # 进入新分区前检查暂停/取消
        if _check_stop():
            cancelled = True
            log(f"{normal_partition} 盘: 传输已取消 (用户中止)")
            break

        partition_total = len(partition_tasks)
        log(f"\n{'='*40}")
        log(f"开始传输 {normal_partition} 盘: {partition_total} 个文件")

        # 为该分区创建/验证目录（跳过无法写入或路径冲突的目录）
        _created_dirs = set()
        _skipped_dirs = set()
        for task in partition_tasks:
            target_dir = os.path.dirname(task[3])
            if target_dir not in _created_dirs and target_dir not in _skipped_dirs:
                _created_dirs.add(target_dir)

        for d in sorted(_created_dirs):
            # 预检1: 路径已存在为文件 (而非目录) → 无法创建同名目录
            if os.path.isfile(d):
                log(f"  [跳过] 路径被文件占用，无法创建目录: {d}")
                _skipped_dirs.add(d)
                continue
            # 预检2: 路径已存在为目录 → 检查是否可写
            if os.path.isdir(d):
                if os.access(d, os.W_OK):
                    continue  # 目录已存在且可写，无需创建
                else:
                    log(f"  [跳过] 目录已存在但无写入权限: {d}")
                    _skipped_dirs.add(d)
                    continue
            # 预检3: 路径不存在 → 尝试创建目录
            try:
                os.makedirs(d, exist_ok=True)
            except (PermissionError, FileExistsError, OSError) as e:
                log(f"  [跳过] 无法创建目录: {d} ({e})")
                _skipped_dirs.add(d)

        for d in _skipped_dirs:
            _created_dirs.discard(d)

        # 分区内: 按大小分组 (小文件批次 / 大文件单独)
        partition_batches = []   # [batch_tasks, ...]
        partition_singles = []   # [(normal_partition, rel_path, fsize, target_path), ...]

        skipped_count = 0
        current_batch = []
        current_batch_bytes = 0
        for task in partition_tasks:
            _, rel_path, fsize, target_path, _mtime = task
            if os.path.dirname(target_path) in _skipped_dirs:
                skipped_count += 1
                continue
            if fsize < BATCH_SIZE_THRESHOLD:
                current_batch.append(task)
                current_batch_bytes += fsize
                if len(current_batch) >= BATCH_MAX_FILES or current_batch_bytes >= BATCH_MAX_TOTAL:
                    partition_batches.append(current_batch)
                    current_batch = []
                    current_batch_bytes = 0
            else:
                partition_singles.append(task)
        if current_batch:
            partition_batches.append(current_batch)

        if skipped_count > 0:
            log(f"  因目录无写入权限，跳过 {skipped_count} 个文件")
            partition_total -= skipped_count

        if partition_progress_callback:
            partition_progress_callback(normal_partition, 0, partition_total)

        small_count = sum(len(b) for b in partition_batches)
        large_count = len(partition_singles)
        if small_count > 0:
            log(f"  小文件: {small_count} 个打包为 {len(partition_batches)} 批次")
        if large_count > 0:
            log(f"  大文件: {large_count} 个单独传输")

        # 记录该分区开始前的基准值，用于计算分区内进度
        baseline_files = completed_files[0]

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []

            # 提交批次任务 (带重试: 连接错误重试最多3次)
            for batch in partition_batches:
                future = executor.submit(
                    _download_batch_with_retry,
                    base_url,
                    normal_partition,
                    batch,
                    auth_code,
                    stats_lock,
                    completed_files,
                    completed_bytes,
                    errors,
                    log_callback,
                    overwrite,
                )
                futures.append(future)

            # 提交单文件任务 (带重试: 连接错误重试最多3次)
            for task in partition_singles:
                np, rp, fs, tp, mt = task
                future = executor.submit(
                    _download_single_file_with_retry,
                    base_url,
                    np,
                    rp,
                    fs,
                    tp,
                    mt,
                    auth_code,
                    stats_lock,
                    completed_files,
                    completed_bytes,
                    errors,
                    log_callback,
                    None,  # file_progress_callback 不再使用 (简化进度显示)
                    overwrite,
                )
                futures.append(future)

            # 等待该分区所有任务完成，同时更新分区进度
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    log(f"  [X] 线程异常: {e}")
                    errors.append(f"线程异常: {e}")

                # 每完成一个文件检查暂停/取消 (取消则取消剩余任务并跳出)
                if _check_stop():
                    cancelled = True
                    for f in futures:
                        f.cancel()
                    log(f"{normal_partition} 盘: 传输已取消 (用户中止)")
                    break

                # 更新分区进度条
                if partition_progress_callback:
                    partition_done = completed_files[0] - baseline_files
                    partition_progress_callback(
                        normal_partition,
                        min(partition_done, partition_total),
                        partition_total,
                    )

        # 确保分区进度条到 100%
        if partition_progress_callback:
            partition_progress_callback(normal_partition, partition_total, partition_total)

        log(f"{normal_partition} 盘传输完成")

        # 分区完成后关闭本线程持有的持久连接, 防止僵死连接跨分区复用
        _close_all_thread_connections()

    # 最终总进度
    progress()
    success = len(errors) == 0 and not cancelled
    if cancelled:
        log("传输已被用户取消 (已下载的文件保留在磁盘, 可重新传输以续传)")
    log(f"\n{'='*40}")
    log(f"传输完成: {completed_files[0]}/{total_files} 文件, "
        + f"{_fmt_size(completed_bytes[0])}/{_fmt_size(total_bytes)}")
    if errors:
        log(f"错误数: {len(errors)}")
        for err in errors[:10]:
            log(f"  - {err}")
        if len(errors) > 10:
            log(f"  ... 共 {len(errors)} 个错误")

    return success, completed_files[0], completed_bytes[0], errors


def _fmt_size(size: int) -> str:
    """格式化文件大小"""
    if size < 1024:
        return f"{size}B"
    elif size < 1024 * 1024:
        return f"{size/1024:.1f}KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size/(1024*1024):.1f}MB"
    else:
        return f"{size/(1024*1024*1024):.2f}GB"


def get_local_ip() -> str:
    """获取本机 IP"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def _try_connect(ip: str, port: int, timeout: float = 0.8) -> bool:
    """尝试 TCP 连接到指定 IP:Port，成功返回 True"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def _verify_source(ip: str, timeout: float = 2.0, auth_code: str = "") -> bool:
    """验证目标 IP 是否是源设备 (发送 /ping 请求, HTTPS)"""
    import urllib.request
    import urllib.error
    try:
        pwd = urllib.parse.quote(auth_code)
        req = _urlopen_https(
            f"https://{ip}:{TRANSFER_PORT}/ping?pwd={pwd}", timeout=timeout,
        )
        data = json.loads(req.read().decode("utf-8-sig"))
        return data.get("status") == "ok"
    except Exception:
        return False


def scan_source_device(log_callback=None, auth_code: str = "") -> str:
    """
    扫描局域网找到源设备 (端口开放, TLS 或明文均可)
    策略: 优先扫描本机所在 /24，逐级扩大，绝不扫整个 /16
    返回源设备 IP，找不到返回空字符串
    """
    local_ip = get_local_ip()
    if log_callback:
        log_callback(f"本机 IP: {local_ip}")

    # 生成扫描批次: [本机/24, 相邻/24, ...]
    parts = local_ip.split(".")
    batches = []
    if len(parts) == 4:
        local_c = int(parts[2])
        local_b = int(parts[1])
        # 第 1 批: 本机 /24
        batches.append([f"{parts[0]}.{parts[1]}.{local_c}.{i}" for i in range(1, 255)])
        # 第 2-4 批: 相邻 /24 子网 (如果本机是 169.254.x.x)
        if parts[0] == "169" and parts[1] == "254":
            for offset in [1, -1, 2, -2, 3, -3]:
                c = (local_c + offset) % 256
                if c != local_c:
                    batches.append([f"169.254.{c}.{i}" for i in range(1, 255)])
    else:
        batches.append([f"169.254.{a}.{b}" for a in range(256) for b in range(1, 255)])

    for batch_idx, ip_range in enumerate(batches):
        batch_label = f"批次 {batch_idx + 1}"
        if batch_idx == 0:
            batch_label = "本机 /24 子网"
        if log_callback:
            log_callback(f"\n扫描 {batch_label}: {len(ip_range)} 个地址...")

        # 第一阶段: 快速 TCP 端口扫描 (并行)
        candidates = []
        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = {executor.submit(_try_connect, ip, TRANSFER_PORT, 0.3): ip for ip in ip_range}
            for future in as_completed(futures):
                ip = futures[future]
                try:
                    if future.result():
                        candidates.append(ip)
                        if log_callback:
                            log_callback(f"  发现开放端口: {ip}:{TRANSFER_PORT}")
                except Exception:
                    pass

        if not candidates:
            continue

        # 第二阶段: 验证
        for ip in candidates:
            if _verify_source(ip, auth_code=auth_code):
                return ip

    if log_callback:
        log_callback("未发现源设备")
    return ""
