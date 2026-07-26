"""
文件传输模块
Phase 3: 源设备启动 HTTP 文件服务器，目标设备通过 HTTP 下载
保持完整目录结构，日志实时回传
PE 下自动使用 APIPA (169.254.x.x)，目标设备扫描发现源设备
"""
import os
import json
import socket
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from concurrent.futures import ThreadPoolExecutor, as_completed

# 服务端口
TRANSFER_PORT = 9999

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

    def log_message(self, format, *args):
        """重定向 HTTP 日志 (批量传输时抑制逐条日志，避免 after(0) 洪水)"""
        if FileServerHandler.suppress_access_log:
            return
        if FileServerHandler.log_callback:
            FileServerHandler.log_callback(f"[HTTP] {args[0]}")

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8-sig"))

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
                chunk = f.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                self.wfile.write(chunk)

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
            path = self.path.split("?")[0]
            params = {}
            if "?" in self.path:
                qs = self.path.split("?", 1)[1]
                for pair in qs.split("&"):
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        # URL 解码参数值 (中文文件名等)
                        from urllib.parse import unquote
                        params[k] = unquote(v)

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
                        fsize = os.path.getsize(full)
                    except OSError:
                        fsize = 0
                    rel = os.path.relpath(full, base)
                    files.append({
                        "path": rel.replace("\\", "/"),
                        "size": fsize,
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
            path = self.path.split("?")[0]
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
    """多线程 HTTP 服务器，支持并发处理多个下载请求"""
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self):
        """绑定前设置 socket 选项"""
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # TCP_NODELAY: 禁用 Nagle 算法，小文件立即发送不等待合并
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # SO_SNDBUF: 增大发送缓冲区 (2MB)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2 * 1024 * 1024)
        super().server_bind()

    def get_request(self):
        """接受连接后设置客户端 socket 选项"""
        conn, addr = super().get_request()
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2 * 1024 * 1024)
        conn.settimeout(30)  # 30s 超时，防止僵死连接
        return conn, addr


class FileServer:
    """文件服务器包装类"""

    def __init__(self, partition_map: dict, log_callback=None):
        """
        partition_map: {"D": "I:", "E": "J:", "F": "K:"} 正常盘符→PE盘符
        """
        self.partition_map = partition_map
        self.log_callback = log_callback
        self._server = None
        self._thread = None

    def start(self):
        """启动服务器（阻塞线程）"""
        FileServerHandler.partition_map = self.partition_map
        FileServerHandler.log_callback = self.log_callback
        FileServerHandler.suppress_access_log = True  # 批量传输时禁止逐条 HTTP 访问日志

        self._server = ThreadingFileServer(("0.0.0.0", TRANSFER_PORT), FileServerHandler)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        if self.log_callback:
            self.log_callback(f"文件服务器已启动，监听端口 {TRANSFER_PORT}")

    def _serve(self):
        try:
            self._server.serve_forever()
        except Exception as e:
            if self.log_callback:
                self.log_callback(f"服务器异常: {e}")

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self.log_callback:
            self.log_callback("文件服务器已停止")


# ===================== 文件下载客户端 (目标设备) =====================

# 多线程下载并发数（有线网络通常 4-8 线程最佳）
DEFAULT_DOWNLOAD_WORKERS = 8

# 小文件批量传输配置
BATCH_SIZE_THRESHOLD = 1 * 1024 * 1024   # < 1MB 归入批次
BATCH_MAX_FILES = 200                     # 每批次最多文件数
BATCH_MAX_TOTAL = 20 * 1024 * 1024        # 每批次最大总字节数 (20MB)

# 线程本地 HTTP 连接池: 每个线程持有一个持久连接，复用避免 TCP 握手开销
# 关键设计: 连接断开后只标记为死，不立即重建 → 避免 Windows 临时端口耗尽
import http.client as _http_client
_tl_connections = threading.local()


def _get_thread_connection(host: str, port: int):
    """获取或惰性重建当前线程的持久 HTTP 连接"""
    key = f"{host}:{port}"
    conns = getattr(_tl_connections, "conns", None)
    if conns is None:
        conns = {}
        _tl_connections.conns = conns

    conn = conns.get(key)
    if conn is None:
        conn = _http_client.HTTPConnection(host, port, timeout=120, blocksize=1024 * 1024)
        conn.connect()
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


def _download_single_file(
    base_url: str,
    normal_partition: str,
    rel_path: str,
    fsize: int,
    target_path: str,
    stats_lock: threading.Lock,
    completed_files_list: list,
    completed_bytes_list: list,
    errors: list,
    log_callback=None,
    file_progress_callback=None,
):
    """下载单个文件（在线程池中执行，写 .tmp 后重命名防断点文件损坏）"""
    import urllib.parse as _urlparse

    def log(msg):
        if log_callback:
            log_callback(msg)

    # 断点续传: 如果目标文件已存在且大小正确，直接跳过
    if os.path.isfile(target_path):
        existing_size = os.path.getsize(target_path)
        if existing_size == fsize:
            log(f"  [✓] 跳过(已存在): {rel_path}")
            with stats_lock:
                completed_files_list[0] += 1
                completed_bytes_list[0] += fsize
            return
        else:
            log(f"  [_] 大小不匹配, 重新下载: {rel_path} (已有{existing_size}, 期望{fsize})")
            try:
                os.remove(target_path)
            except OSError:
                pass

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
        url_path = f"/get?partition={normal_partition}&path={encoded_path}"
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
                chunk = resp.read(1024 * 1024)
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

    except Exception as e:
        _handle_tmp_failure(log, tmp_path, rel_path)
        log(f"  [X] 下载失败: {rel_path} - {e}")
        with stats_lock:
            errors.append(f"分区 {normal_partition}: {rel_path} 下载失败 - {e}")


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
    batch_tasks: list,  # [(normal_partition, rel_path, fsize, target_path), ...]
    stats_lock: threading.Lock,
    completed_files_list: list,
    completed_bytes_list: list,
    errors: list,
    log_callback=None,
):
    """批量下载小文件：一次 POST 请求获取多个文件，写 .tmp 后重命名防断点损坏"""
    import struct as _struct
    import urllib.parse as _urlparse

    def log(msg):
        if log_callback:
            log_callback(msg)

    parsed = _urlparse.urlparse(base_url)
    host = parsed.hostname
    port = parsed.port or TRANSFER_PORT

    # 断点续传: 跳过已存在且大小正确的文件
    remaining_tasks = []  # [(rel_path, fsize, target_path), ...]
    for item in batch_tasks:
        _, rel_path, fsize, target_path = item
        if os.path.isfile(target_path):
            existing_size = os.path.getsize(target_path)
            if existing_size == fsize:
                log(f"  [✓] 跳过(已存在): {rel_path}")
                with stats_lock:
                    completed_files_list[0] += 1
                    completed_bytes_list[0] += fsize
                continue
            else:
                log(f"  [_] 大小不匹配, 重新下载: {rel_path}")
                try:
                    os.remove(target_path)
                except OSError:
                    pass
        remaining_tasks.append((rel_path, fsize, target_path))

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

        conn.request("POST", "/batch_get", body=body, headers={
            "Content-Type": "application/json; charset=utf-8",
            "Connection": "keep-alive",
        })
        resp = conn.getresponse()

        if resp.status != 200:
            body_text = resp.read().decode("utf-8-sig", errors="replace")
            log(f"  [X] 批次下载失败 HTTP {resp.status}: {body_text}")
            with stats_lock:
                for rel_path, fsize, target_path in remaining_tasks:
                    errors.append(
                        f"分区 {normal_partition}: {rel_path} 批次HTTP {resp.status}"
                    )
            return

        # 解析二进制响应
        raw = resp.read()
        if len(raw) < 4:
            log(f"  [X] 批次响应过短: {len(raw)} 字节")
            with stats_lock:
                for rel_path, fsize, target_path in remaining_tasks:
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
            for bp, bsize, tp in remaining_tasks:
                if bp == rel_path:
                    target_path = tp
                    exp_size = bsize
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

            with stats_lock:
                completed_files_list[0] += 1
                completed_bytes_list[0] += actual_size
            received_files += 1

        # 检查是否有本批次请求了但未返回的文件
        for bp, bsize, btp in remaining_tasks:
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
            for rel_path, fsize, target_path in remaining_tasks:
                errors.append(f"分区 {normal_partition}: {rel_path} 批次连接失败")

    except Exception as e:
        log(f"  [X] 批次下载失败: {e}")
        with stats_lock:
            for rel_path, fsize, target_path in remaining_tasks:
                errors.append(f"分区 {normal_partition}: {rel_path} 批次异常")


def download_files(
    server_ip: str,
    partition_map: dict,
    log_callback=None,
    progress_callback=None,
    partition_progress_callback=None,
    max_workers: int = DEFAULT_DOWNLOAD_WORKERS,
    partition_count: int = 0,
):
    """
    从源设备多线程并行下载 D/E/F 分区数据
    partition_map: {"D": "I:", "E": "J:", "F": "K:"}  正常盘符→目标PE盘符
    server_ip: 源设备 IP
    max_workers: 并行下载线程数 (默认 6)
    partition_count: NTFS 分区数 (2/3/4) — 2 分区时 D 盘仅下载 User 文件夹
    """
    import urllib.request
    import urllib.error

    base_url = f"http://{server_ip}:{TRANSFER_PORT}"
    local_partition_map = partition_map

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
    log(f"正在连接源设备 {server_ip}:{TRANSFER_PORT}...")
    for attempt in range(5):
        try:
            req = urllib.request.urlopen(f"{base_url}/ping", timeout=5)
            data = json.loads(req.read().decode("utf-8-sig"))
            if data.get("status") == "ok":
                log(f"已连接到源设备，可用分区: {data.get('partitions', [])}")
                break
        except Exception as e:
            if attempt == 4:
                log(f"无法连接到源设备: {e}")
                return False, 0, 0, errors
            time.sleep(2)

    # 第一阶段: 收集所有分区的文件列表
    all_download_tasks = []  # [(normal_partition, rel_path, fsize, target_path), ...]

    for normal_partition in ("D", "E", "F"):
        target_drive = local_partition_map.get(normal_partition, "").rstrip("\\")
        if not target_drive:
            log(f"分区 {normal_partition} 未配置映射，跳过")
            continue

        if not os.path.isdir(target_drive):
            log(f"目标分区 {target_drive} 不存在，跳过 {normal_partition}")
            errors.append(f"分区 {normal_partition}: 目标盘符 {target_drive} 不可用")
            continue

        log(f"\n{'='*40}")
        log(f"正在获取 {normal_partition} 盘文件列表...")
        try:
            req = urllib.request.urlopen(
                f"{base_url}/list?partition={normal_partition}", timeout=30
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
            target_path = os.path.join(target_drive, rel_path.replace("/", "\\"))
            all_download_tasks.append((normal_partition, rel_path, fsize, target_path))

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

    # 断点续传: 统计已存在且大小正确的文件 (跳过下载)
    skipped_files = 0
    skipped_bytes = 0
    remaining_tasks = []
    for task in all_download_tasks:
        _, _, fsize, target_path = task
        if os.path.isfile(target_path):
            existing_size = os.path.getsize(target_path)
            if existing_size == fsize:
                skipped_files += 1
                skipped_bytes += fsize
                continue
        remaining_tasks.append(task)
    if skipped_files > 0:
        log(f"断点续传: 跳过 {skipped_files} 个已存在文件 ({_fmt_size(skipped_bytes)})")
    all_download_tasks = remaining_tasks

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
        """后台线程: 每 2 秒上报一次总进度"""
        last_count = [completed_files[0]]
        while completed_files[0] < total_files:
            time.sleep(2)
            current = completed_files[0]
            if current != last_count[0]:
                progress()
                last_count[0] = current

    reporter_thread = threading.Thread(target=progress_reporter, daemon=True)
    reporter_thread.start()

    # 逐分区处理 (D → E → F)，避免跨分区并发 IO 导致崩溃
    for normal_partition in ("D", "E", "F"):
        partition_tasks = partition_task_map.get(normal_partition, [])
        if not partition_tasks:
            continue

        partition_total = len(partition_tasks)
        log(f"\n{'='*40}")
        log(f"开始传输 {normal_partition} 盘: {partition_total} 个文件")

        if partition_progress_callback:
            partition_progress_callback(normal_partition, 0, partition_total)

        # 为该分区创建目录
        _created_dirs = set()
        for task in partition_tasks:
            target_dir = os.path.dirname(task[3])
            if target_dir not in _created_dirs:
                _created_dirs.add(target_dir)
        for d in sorted(_created_dirs):
            os.makedirs(d, exist_ok=True)

        # 分区内: 按大小分组 (小文件批次 / 大文件单独)
        partition_batches = []   # [batch_tasks, ...]
        partition_singles = []   # [(normal_partition, rel_path, fsize, target_path), ...]

        current_batch = []
        current_batch_bytes = 0
        for task in partition_tasks:
            _, rel_path, fsize, target_path = task
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

            # 提交批次任务
            for batch in partition_batches:
                future = executor.submit(
                    _download_batch,
                    base_url,
                    normal_partition,
                    batch,
                    stats_lock,
                    completed_files,
                    completed_bytes,
                    errors,
                    log_callback,
                )
                futures.append(future)

            # 提交单文件任务
            for task in partition_singles:
                np, rp, fs, tp = task
                future = executor.submit(
                    _download_single_file,
                    base_url,
                    np,
                    rp,
                    fs,
                    tp,
                    stats_lock,
                    completed_files,
                    completed_bytes,
                    errors,
                    log_callback,
                    None,  # file_progress_callback 不再使用 (简化进度显示)
                )
                futures.append(future)

            # 等待该分区所有任务完成，同时更新分区进度
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    log(f"  [X] 线程异常: {e}")
                    errors.append(f"线程异常: {e}")

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

    # 最终总进度
    progress()
    success = len(errors) == 0
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


def _verify_source(ip: str, timeout: float = 2.0) -> bool:
    """验证目标 IP 是否是源设备 (发送 /ping 请求)"""
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.urlopen(
            f"http://{ip}:{TRANSFER_PORT}/ping", timeout=timeout
        )
        data = json.loads(req.read().decode("utf-8-sig"))
        return data.get("status") == "ok"
    except Exception:
        return False


def scan_source_device(log_callback=None) -> str:
    """
    扫描局域网找到源设备 (端口 9999 开放)
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
            if _verify_source(ip):
                return ip

    if log_callback:
        log_callback("未发现源设备")
    return ""
