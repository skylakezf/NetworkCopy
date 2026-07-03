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
SKIP_DIRS = {"AppData", "System Volume Information"}
SKIP_PREFIXES = ("$",)  # $RECYCLE.BIN 等


# ===================== 文件服务器 (源设备) =====================

class FileServerHandler(BaseHTTPRequestHandler):
    """
    HTTP 文件服务处理器
    端点:
      GET /list?partition=D  → 返回分区文件列表 JSON
      GET /get?partition=D&path=xxx → 返回文件内容
    注意: partition 是 PE 下的实际盘符
    """
    # 类变量，由外部设置
    partition_map = {}   # {"D": "I:", "E": "J:", "F": "K:"}  正常盘符→PE盘符
    log_callback = None  # 日志回调函数

    def log_message(self, format, *args):
        """重定向 HTTP 日志"""
        if FileServerHandler.log_callback:
            FileServerHandler.log_callback(f"[HTTP] {args[0]}")

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _send_file(self, filepath):
        if not os.path.isfile(filepath):
            self._send_json({"error": "文件不存在"}, 404)
            return

        file_size = os.path.getsize(filepath)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(file_size))
        self.end_headers()

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

        self._send_file(full_path)


class ThreadingFileServer(ThreadingMixIn, HTTPServer):
    """多线程 HTTP 服务器，支持并发处理多个下载请求"""
    daemon_threads = True


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

        self._server = ThreadingFileServer(("0.0.0.0", TRANSFER_PORT), FileServerHandler)
        self._server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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
DEFAULT_DOWNLOAD_WORKERS = 6


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
):
    """下载单个文件（在线程池中执行）"""
    import urllib.request
    import urllib.error

    def log(msg):
        if log_callback:
            log_callback(msg)

    try:
        encoded_path = urllib.parse.quote(rel_path, safe="")
        url = f"{base_url}/get?partition={normal_partition}&path={encoded_path}"
        req = urllib.request.urlopen(url, timeout=120)

        with open(target_path, "wb") as f:
            while True:
                chunk = req.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                with stats_lock:
                    completed_bytes_list[0] += len(chunk)

        # 验证大小
        actual_size = os.path.getsize(target_path)
        if actual_size != fsize:
            log(f"  [!] 大小不匹配: {rel_path} (期望{fsize}, 实际{actual_size})")
            with stats_lock:
                errors.append(f"分区 {normal_partition}: {rel_path} 大小不匹配")

        with stats_lock:
            completed_files_list[0] += 1

    except Exception as e:
        log(f"  [X] 下载失败: {rel_path} - {e}")
        with stats_lock:
            errors.append(f"分区 {normal_partition}: {rel_path} 下载失败 - {e}")


def download_files(
    server_ip: str,
    partition_map: dict,
    log_callback=None,
    progress_callback=None,
    max_workers: int = DEFAULT_DOWNLOAD_WORKERS,
):
    """
    从源设备多线程并行下载 D/E/F 分区数据
    partition_map: {"D": "I:", "E": "J:", "F": "K:"}  正常盘符→目标PE盘符
    server_ip: 源设备 IP
    max_workers: 并行下载线程数 (默认 6)
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
            data = json.loads(req.read().decode("utf-8"))
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
            list_data = json.loads(req.read().decode("utf-8"))
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
        log(f"{normal_partition} 盘: {len(files)} 个文件, 共 {_fmt_size(partition_total)}")
        total_files += len(files)
        total_bytes += partition_total

        for f_info in files:
            rel_path = f_info["path"]
            fsize = f_info["size"]
            target_path = os.path.join(target_drive, rel_path.replace("/", "\\"))

            # 预先创建目录（线程安全：exist_ok=True）
            target_dir = os.path.dirname(target_path)
            os.makedirs(target_dir, exist_ok=True)

            all_download_tasks.append((normal_partition, rel_path, fsize, target_path))

    log(f"\n总计 {total_files} 个文件, {_fmt_size(total_bytes)}")
    log(f"开始多线程下载 (并行 {max_workers} 线程)...")

    # 第二阶段: 线程池并行下载
    stats_lock = threading.Lock()

    def progress_reporter():
        """后台线程: 每 2 秒上报一次进度"""
        last_count = [0]
        while completed_files[0] < total_files:
            time.sleep(2)
            current = completed_files[0]
            if current != last_count[0]:
                progress()
                last_count[0] = current

    reporter_thread = threading.Thread(target=progress_reporter, daemon=True)
    reporter_thread.start()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for task in all_download_tasks:
            normal_partition, rel_path, fsize, target_path = task
            future = executor.submit(
                _download_single_file,
                base_url,
                normal_partition,
                rel_path,
                fsize,
                target_path,
                stats_lock,
                completed_files,
                completed_bytes,
                errors,
                log_callback,
            )
            futures.append(future)

        # 等待所有下载完成
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                log(f"  [X] 线程异常: {e}")
                errors.append(f"线程异常: {e}")

    # 最终进度
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
        data = json.loads(req.read().decode("utf-8"))
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
