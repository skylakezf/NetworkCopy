"""
IP 地址配置模块
将指定网卡设置为静态 IP
源设备: 169.254.100.1/16
目标设备: 169.254.100.2/16
说明: 169.254.x.x 为 APIPA 链路本地地址段, 天生 /16 (掩码 255.255.0.0)。
      若一端用 /24 而另一端因 API 设置失败回退到 APIPA (/16), 会造成掩码非对称,
      源端无法回包导致连接超时。因此全链路统一使用 /16。
主力: ctypes + iphlpapi.dll (Windows API, PE 兼容)
回退: netsh (仅在 netsh 可用时)
"""
import ctypes
from ctypes import wintypes
import socket
import struct

# 常量
SOURCE_IP = "169.254.100.1"
TARGET_IP = "169.254.100.2"
# 统一使用 /16 掩码, 与 169.254.x.x (APIPA) 地址段一致, 避免两端掩码非对称导致源端无法回包
SUBNET_MASK = "255.255.0.0"


def get_ip_for_type(device_type: str) -> str:
    if device_type == "源设备":
        return SOURCE_IP
    elif device_type == "目标设备":
        return TARGET_IP
    return ""


# =================== ctypes API 方式 (主力, PE 可用) ===================

def _ip_to_uint(ip_str: str) -> int:
    """IP 字符串 → 32位无符号整数 (网络字节序)"""
    return struct.unpack("!I", socket.inet_aton(ip_str.strip()))[0]


# IP_ADAPTER_INFO 结构 (GetAdaptersInfo)
class _IP_ADDR_STRING(ctypes.Structure):
    pass
_IP_ADDR_STRING._fields_ = [
    ("Next", ctypes.c_void_p),
    ("IpAddress", ctypes.c_char * 16),
    ("IpMask", ctypes.c_char * 16),
    ("Context", wintypes.DWORD),
]

class _IP_ADAPTER_INFO(ctypes.Structure):
    pass
_IP_ADAPTER_INFO._fields_ = [
    ("Next", ctypes.POINTER(_IP_ADAPTER_INFO)),
    ("ComboIndex", wintypes.DWORD),
    ("AdapterName", ctypes.c_char * 260),
    ("Description", ctypes.c_char * 132),
    ("AddressLength", wintypes.UINT),
    ("Address", wintypes.BYTE * 8),
    ("Index", wintypes.DWORD),
    ("Type", wintypes.UINT),
    ("DhcpEnabled", wintypes.UINT),
    ("CurrentIpAddress", ctypes.c_void_p),
    ("IpAddressList", _IP_ADDR_STRING),
    ("GatewayList", _IP_ADDR_STRING),
    ("DhcpServer", _IP_ADDR_STRING),
    ("HaveWins", wintypes.BOOL),
    ("PrimaryWinsServer", _IP_ADDR_STRING),
    ("SecondaryWinsServer", _IP_ADDR_STRING),
    ("LeaseObtained", wintypes.DWORD),
    ("LeaseExpires", wintypes.DWORD),
]


def _find_adapter_index(adapter_desc: str) -> int:
    """
    通过网卡描述名称查找适配器索引 (Index)
    使用 GetAdaptersInfo (iphlpapi.dll) — 结构体明确，比 GetAdaptersAddresses 更可靠
    """
    try:
        iphlpapi = ctypes.windll.iphlpapi
    except AttributeError:
        return -1

    # 获取所需缓冲区大小
    buf_size = wintypes.ULONG(0)
    iphlpapi.GetAdaptersInfo(None, ctypes.byref(buf_size))
    if buf_size.value == 0:
        return -1

    buf = ctypes.create_string_buffer(buf_size.value)
    ret = iphlpapi.GetAdaptersInfo(
        ctypes.cast(buf, ctypes.c_void_p),
        ctypes.byref(buf_size),
    )
    if ret != 0:
        return -1

    # 遍历链表
    ptr = ctypes.cast(buf, ctypes.POINTER(_IP_ADAPTER_INFO))
    while ptr:
        try:
            info = ptr.contents
            desc = info.Description.decode("gbk", errors="replace").strip()
            if adapter_desc in desc or desc in adapter_desc:
                return info.Index
            ptr = info.Next
        except Exception:
            break

    return -1


def set_ip_via_api(adapter_desc: str, ip_str: str, mask_str: str = SUBNET_MASK) -> tuple:
    """
    通过 Windows IP Helper API 设置静态 IP
    使用 AddIPAddress / DeleteIPAddress
    """
    iphlpapi = ctypes.windll.iphlpapi

    if_index = _find_adapter_index(adapter_desc)
    if if_index < 0:
        return False, f"未找到网卡: {adapter_desc[:50]}"

    ip_addr = _ip_to_uint(ip_str)
    ip_mask = _ip_to_uint(mask_str)

    # 先尝试删除该适配器上已有的 non-DHCP IP (用 DeleteIPAddress)
    # DeleteIPAddress 需要 NTEContext, 我们通过 GetIpAddrTable 获取
    # 简化方案: 直接用 AddIPAddress, 返回的 NTEContext 用于后续管理

    nte_context = wintypes.ULONG(0)
    nte_instance = wintypes.ULONG(0)

    ret = iphlpapi.AddIPAddress(
        wintypes.ULONG(ip_addr),
        wintypes.ULONG(ip_mask),
        wintypes.ULONG(if_index),
        ctypes.byref(nte_context),
        ctypes.byref(nte_instance),
    )

    if ret == 0:
        return True, f"IP 已设置为 {ip_str}/24 (API)"
    elif ret == 87:  # ERROR_INVALID_PARAMETER
        return False, "API 参数无效 (网卡可能未启用)"
    elif ret == 5010:  # ERROR_OBJECT_ALREADY_EXISTS
        # IP 已存在，可能是之前设置成功的
        return True, f"IP 已设置为 {ip_str}/24 (已存在)"
    else:
        return False, f"API 设置失败 (错误码: {ret})"


# =================== netsh 回退 ===================

def set_ip_via_netsh(adapter_desc: str, ip: str, mask: str = SUBNET_MASK) -> tuple:
    """netsh 方式 (仅在 netsh 存在时调用)"""
    import subprocess
    try:
        subprocess.run(
            ["netsh", "interface", "ip", "set", "address",
             f'"{adapter_desc}"', "dhcp"],
            capture_output=True, text=True, timeout=10,
            encoding="gbk", errors="replace",
        )
        result = subprocess.run(
            ["netsh", "interface", "ip", "set", "address",
             f'"{adapter_desc}"', "static", ip, mask],
            capture_output=True, text=True, timeout=15,
            encoding="gbk", errors="replace",
        )
        if result.returncode == 0:
            return True, f"IP 已设置为 {ip}/24"
        combined = result.stdout + result.stderr
        if "ok" in combined.lower() or "确定" in combined:
            return True, f"IP 已设置为 {ip}/24"
        return False, f"netsh 失败"
    except FileNotFoundError:
        return False, "netsh 不可用"
    except Exception as e:
        return False, f"netsh 异常: {e}"


# =================== 公开接口 ===================

def set_ip_address(adapter_desc: str, device_type: str) -> tuple:
    """
    根据设备类型设置 IP 地址
    优先 ctypes API (PE 兼容), 失败回退 netsh
    """
    ip = get_ip_for_type(device_type)
    if not ip:
        return False, "未知的设备类型"

    # 主力: Windows IP Helper API (PE 可用)
    success, msg = set_ip_via_api(adapter_desc, ip)
    if success:
        return True, msg

    # 回退: netsh
    success2, msg2 = set_ip_via_netsh(adapter_desc, ip)
    if success2:
        return True, msg2

    return False, f"IP 配置失败:\n  API: {msg}\n  netsh: {msg2}"


if __name__ == "__main__":
    result = set_ip_address("以太网", "源设备")
    print(f"{'成功' if result[0] else '失败'}: {result[1]}")
