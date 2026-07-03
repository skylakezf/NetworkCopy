"""
简易 DHCP 服务器 - 方案2: 源设备作为 DHCP 服务器给目标设备分发 IP
用于 PE 环境，源设备设置自身 IP 后，等待目标设备通过 ipconfig /renew 获取 IP

基于 DHCP RFC 2131 最小实现
"""
import socket
import struct
import threading
import time

SERVER_IP = "169.254.100.1"
DHCP_SUBNET = "169.254.100"
DHCP_MASK = "255.255.255.0"
DHCP_SERVER_PORT = 67
DHCP_CLIENT_PORT = 68

# DHCP 消息类型
DHCPDISCOVER = 1
DHCPOFFER = 2
DHCPREQUEST = 3
DHCPACK = 5
DHCPNAK = 6

# BOOTP/DHCP 操作码
BOOTREQUEST = 1
BOOTREPLY = 2
HTYPE_ETHERNET = 1

MAGIC_COOKIE = bytes([99, 130, 83, 99])


def _ip2int(ip):
    return struct.unpack("!I", socket.inet_aton(ip))[0]


def _int2ip(n):
    return socket.inet_ntoa(struct.pack("!I", n))


class MiniDHCPServer:
    """最小的 DHCP 服务器 - 只给外部客户端分配 IP，排除本地网卡"""

    def __init__(self, server_ip=SERVER_IP, subnet=DHCP_SUBNET, mask=DHCP_MASK,
                 exclude_macs=None):
        self.server_ip = server_ip
        self.server_int = _ip2int(server_ip)
        self.subnet = subnet
        self.mask = mask
        self.mask_int = _ip2int(mask)

        # 需要排除的本地 MAC 地址集合（避免本地网卡 DHCP 自响应）
        self._exclude_macs = set()
        if exclude_macs:
            for m in exclude_macs:
                self._exclude_macs.add(m.lower())

        # 从 subnet_base + 2 开始分配 (server 是 +1)
        self._next_ip = self.server_int + 1
        self._leases = {}      # mac -> {"ip": int, "hostname": str}
        self._running = False
        self._sock = None
        self._thread = None
        self._client_assigned = threading.Event()
        self._on_client = None  # callback(ip_str, mac_str, hostname)

    def _parse_dhcp(self, data):
        """解析 DHCP 数据包，返回 (op, htype, hlen, xid, flags, ciaddr, yiaddr, chaddr, options)"""
        if len(data) < 240:
            return None
        op, htype, hlen, hops = struct.unpack("!BBBB", data[:4])
        xid = struct.unpack("!I", data[4:8])[0]
        secs = struct.unpack("!H", data[8:10])[0]
        flags = struct.unpack("!H", data[10:12])[0]
        ciaddr = struct.unpack("!I", data[12:16])[0]
        yiaddr = struct.unpack("!I", data[16:20])[0]
        siaddr = struct.unpack("!I", data[20:24])[0]
        giaddr = struct.unpack("!I", data[24:28])[0]
        chaddr = data[28:44]

        # 解析 options
        options = {}
        if len(data) > 240 and data[236:240] == MAGIC_COOKIE:
            pos = 240
            while pos < len(data):
                code = data[pos]
                if code == 255:
                    break
                if code == 0:
                    pos += 1
                    continue
                if pos + 1 >= len(data):
                    break
                length = data[pos + 1]
                if pos + 2 + length > len(data):
                    break
                value = data[pos + 2 : pos + 2 + length]
                options[code] = value
                pos += 2 + length

        return (op, htype, hlen, xid, flags, chaddr, ciaddr, options)

    def _build_dhcp_packet(self, op, xid, yiaddr, chaddr, msg_type, options_extra=None):
        """构建 DHCP 响应包"""
        flags = 0x8000  # broadcast
        giaddr = 0

        # 基础 BOOTP 头
        pkt = struct.pack(
            "!BBBBIHHIIII",
            op, HTYPE_ETHERNET, 6, 0,  # op, htype, hlen, hops
            xid,
            0, flags,  # secs, flags
            0, yiaddr, self.server_int, giaddr,  # ciaddr, yiaddr, siaddr, giaddr
        )
        pkt += chaddr[:6].ljust(16, b'\x00')  # chaddr (16 bytes)
        pkt += b'\x00' * 192  # sname + file

        # DHCP options
        pkt += MAGIC_COOKIE
        # Option 53: Message Type
        pkt += bytes([53, 1, msg_type])
        # Option 54: Server Identifier
        server_ip_bytes = struct.pack("!I", self.server_int)
        pkt += bytes([54, 4]) + server_ip_bytes
        # Option 51: Lease Time (1 hour)
        pkt += bytes([51, 4, 0, 0, 14, 16])
        # Option 1: Subnet Mask
        mask_bytes = struct.pack("!I", self.mask_int)
        pkt += bytes([1, 4]) + mask_bytes
        # Option 3: Router (same as server)
        pkt += bytes([3, 4]) + server_ip_bytes
        # Option 28: Broadcast Address
        broadcast_int = (yiaddr & self.mask_int) | (~self.mask_int & 0xFFFFFFFF)
        pkt += bytes([28, 4]) + struct.pack("!I", broadcast_int)

        if options_extra:
            for code, value in options_extra.items():
                pkt += bytes([code, len(value)]) + value

        # Option 255: End
        pkt += bytes([255])

        return pkt

    def _is_local_mac(self, mac):
        """检查 MAC 地址是否为本地网卡"""
        return mac.lower() in self._exclude_macs

    def _handle_discover(self, xid, chaddr, options):
        """处理 DHCPDISCOVER → 分配 IP，返回 DHCPOFFER（排除本地 MAC）"""
        mac = ":".join(f"{b:02x}" for b in chaddr[:6])

        # 排除本地网卡的 DHCP 请求
        if self._is_local_mac(mac):
            print(f"[DHCP] DISCOVER from {mac} (LOCAL) → IGNORED")
            return None

        hostname = ""
        if 12 in options:
            try:
                hostname = options[12].decode("utf-8", errors="replace").strip("\x00")
            except Exception:
                pass

        if mac in self._leases:
            yiaddr = self._leases[mac]["ip"]
        else:
            yiaddr = self._next_ip
            self._leases[mac] = {"ip": yiaddr, "hostname": hostname}
            self._next_ip += 1

        print(f"[DHCP] DISCOVER from {mac} ({hostname}) → OFFER {_int2ip(yiaddr)}")
        return yiaddr

    def _handle_request(self, xid, chaddr, options):
        """处理 DHCPREQUEST → 确认租约，返回 DHCPACK（排除本地 MAC）"""
        mac = ":".join(f"{b:02x}" for b in chaddr[:6])

        # 排除本地网卡的 DHCP 请求
        if self._is_local_mac(mac):
            print(f"[DHCP] REQUEST from {mac} (LOCAL) → IGNORED")
            return None

        # 提取主机名 (option 12)
        hostname = ""
        if 12 in options:
            try:
                hostname = options[12].decode("utf-8", errors="replace").strip("\x00")
            except Exception:
                pass

        if mac in self._leases:
            yiaddr = self._leases[mac]["ip"]
        else:
            if 50 in options and len(options[50]) == 4:
                yiaddr = struct.unpack("!I", options[50])[0]
            else:
                yiaddr = self._next_ip
            self._leases[mac] = {"ip": yiaddr, "hostname": hostname}
            self._next_ip += 1

        ip_str = _int2ip(yiaddr)
        print(f"[DHCP] REQUEST from {mac} ({hostname}) → ACK {ip_str}")
        self._client_assigned.set()

        # 回调通知
        if self._on_client:
            try:
                self._on_client(ip_str, mac, hostname)
            except Exception:
                pass

        return yiaddr

    def _serve(self):
        """主循环 - 监听 DHCP 请求"""
        while self._running:
            try:
                self._sock.settimeout(1.0)
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except Exception:
                if self._running:
                    time.sleep(0.5)
                continue

            parsed = self._parse_dhcp(data)
            if not parsed:
                continue

            op, htype, hlen, xid, flags, chaddr, ciaddr, options = parsed
            if op != BOOTREQUEST:
                continue

            msg_type = options.get(53, b'\x00')[0] if 53 in options else 0

            try:
                if msg_type == DHCPDISCOVER:
                    yiaddr = self._handle_discover(xid, chaddr, options)
                    if yiaddr is None:
                        continue  # 本地 MAC，跳过不响应
                    pkt = self._build_dhcp_packet(BOOTREPLY, xid, yiaddr, chaddr, DHCPOFFER)
                    self._sock.sendto(pkt, ('255.255.255.255', DHCP_CLIENT_PORT))

                elif msg_type == DHCPREQUEST:
                    yiaddr = self._handle_request(xid, chaddr, options)
                    if yiaddr is None:
                        continue  # 本地 MAC，跳过不响应
                    pkt = self._build_dhcp_packet(BOOTREPLY, xid, yiaddr, chaddr, DHCPACK)
                    self._sock.sendto(pkt, ('255.255.255.255', DHCP_CLIENT_PORT))

            except Exception as e:
                print(f"[DHCP] Error: {e}")

    def start(self):
        """启动 DHCP 服务器"""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        # 允许绑定到广播地址
        try:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except Exception:
            pass

        self._sock.bind(('0.0.0.0', DHCP_SERVER_PORT))
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        print(f"[DHCP] Server started on 0.0.0.0:{DHCP_SERVER_PORT}")

    def stop(self):
        """停止 DHCP 服务器"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        print("[DHCP] Server stopped")

    def set_on_client(self, callback):
        """设置客户端连接回调: callback(ip_str, mac_str, hostname)"""
        self._on_client = callback

    def get_clients(self):
        """返回已分配的客户端列表: [{"ip": str, "mac": str, "hostname": str}, ...]"""
        result = []
        for mac, info in self._leases.items():
            result.append({
                "ip": _int2ip(info["ip"]),
                "mac": mac,
                "hostname": info.get("hostname", ""),
            })
        return result

    def wait_for_client(self, timeout=30):
        """等待客户端获取 IP，返回 (success, client_ip)"""
        return self._client_assigned.wait(timeout)


if __name__ == "__main__":
    server = MiniDHCPServer()
    server.start()
    print("Waiting for DHCP client... (run 'ipconfig /renew' on target)")
    try:
        if server.wait_for_client(60):
            print("Client assigned!")
        else:
            print("Timeout - no client connected")
    finally:
        server.stop()
