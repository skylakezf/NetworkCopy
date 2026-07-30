"""
网卡扫描模块
枚举所有物理网卡，返回 (名称 + 最高速率) 列表
格式如: Intel(R) Ethernet Connection I219-V 1Gbps
在 Windows PE 下运行，仅依赖 iphlpapi.dll (ctypes)
"""
import ctypes
from ctypes import wintypes

# ---- 常量 ----
MAX_ADAPTER_NAME_LENGTH = 256
MAX_ADAPTER_DESCRIPTION_LENGTH = 128
MAX_ADAPTER_ADDRESS_LENGTH = 8
MAX_INTERFACE_NAME_LEN = 256
MAXLEN_PHYSADDR = 8
MAXLEN_IFDESCR = 256

ERROR_SUCCESS = 0
ERROR_BUFFER_OVERFLOW = 111
ERROR_NO_DATA = 232


# ---- IP_ADAPTER_INFO 结构 (GetAdaptersInfo) ----
class IP_ADDR_STRING(ctypes.Structure):
    pass


IP_ADDR_STRING._fields_ = [
    ("Next", ctypes.POINTER(IP_ADDR_STRING)),
    ("IpAddress", ctypes.c_char * 16),
    ("IpMask", ctypes.c_char * 16),
    ("Context", wintypes.DWORD),
]


class IP_ADAPTER_INFO(ctypes.Structure):
    pass


IP_ADAPTER_INFO._fields_ = [
    ("Next", ctypes.POINTER(IP_ADAPTER_INFO)),
    ("ComboIndex", wintypes.DWORD),
    ("AdapterName", ctypes.c_char * (MAX_ADAPTER_NAME_LENGTH + 4)),
    ("Description", ctypes.c_char * (MAX_ADAPTER_DESCRIPTION_LENGTH + 4)),
    ("AddressLength", wintypes.UINT),
    ("Address", wintypes.BYTE * MAX_ADAPTER_ADDRESS_LENGTH),
    ("Index", wintypes.DWORD),
    ("Type", wintypes.UINT),
    ("DhcpEnabled", wintypes.UINT),
    ("CurrentIpAddress", ctypes.POINTER(IP_ADDR_STRING)),
    ("IpAddressList", IP_ADDR_STRING),
    ("GatewayList", IP_ADDR_STRING),
    ("DhcpServer", IP_ADDR_STRING),
    ("HaveWins", wintypes.BOOL),
    ("PrimaryWinsServer", IP_ADDR_STRING),
    ("SecondaryWinsServer", IP_ADDR_STRING),
    ("LeaseObtained", wintypes.DWORD),
    ("LeaseExpires", wintypes.DWORD),
]


# ---- MIB_IFROW 结构 (GetIfEntry) ----
class MIB_IFROW(ctypes.Structure):
    _fields_ = [
        ("wszName", wintypes.WCHAR * MAX_INTERFACE_NAME_LEN),
        ("dwIndex", wintypes.DWORD),
        ("dwType", wintypes.DWORD),
        ("dwMtu", wintypes.DWORD),
        ("dwSpeed", wintypes.DWORD),
        ("dwPhysAddrLen", wintypes.DWORD),
        ("bPhysAddr", wintypes.BYTE * MAXLEN_PHYSADDR),
        ("dwAdminStatus", wintypes.DWORD),
        ("dwOperStatus", wintypes.DWORD),
        ("dwLastChange", wintypes.DWORD),
        ("dwInOctets", wintypes.DWORD),
        ("dwInUcastPkts", wintypes.DWORD),
        ("dwInNUcastPkts", wintypes.DWORD),
        ("dwInDiscards", wintypes.DWORD),
        ("dwInErrors", wintypes.DWORD),
        ("dwInUnknownProtos", wintypes.DWORD),
        ("dwOutOctets", wintypes.DWORD),
        ("dwOutUcastPkts", wintypes.DWORD),
        ("dwOutNUcastPkts", wintypes.DWORD),
        ("dwOutDiscards", wintypes.DWORD),
        ("dwOutErrors", wintypes.DWORD),
        ("dwOutQLen", wintypes.DWORD),
        ("dwDescrLen", wintypes.DWORD),
        ("bDescr", wintypes.BYTE * MAXLEN_IFDESCR),
    ]


# ---- 网卡类型常量 ----
# 常见物理网卡类型
PHYSICAL_IF_TYPES = {
    6,   # IF_TYPE_ETHERNET_CSMACD
    71,  # IF_TYPE_IEEE80211 (Wi-Fi)
}


def _format_speed(bps: int) -> str:
    """将速率 (bps) 转换为可读格式"""
    if bps <= 0:
        return ""
    if bps >= 1_000_000_000:
        gbps = bps / 1_000_000_000
        if gbps >= 10:
            return f"{gbps:.0f}Gbps"
        return f"{gbps:.1f}Gbps".rstrip("0").rstrip(".") + "Gbps"
    if bps >= 1_000_000:
        mbps = bps / 1_000_000
        return f"{mbps:.0f}Mbps"
    if bps >= 1_000:
        kbps = bps / 1_000
        return f"{kbps:.0f}Kbps"
    return f"{bps}bps"


def _get_if_speed(if_index: int) -> int:
    """通过 ifIndex 获取接口速率 (bps)"""
    iphlpapi = ctypes.windll.iphlpapi
    if_row = MIB_IFROW()
    if_row.dwIndex = if_index
    ret = iphlpapi.GetIfEntry(ctypes.byref(if_row))
    if ret == ERROR_SUCCESS:
        return if_row.dwSpeed
    return 0


def scan_nics() -> list:
    """
    扫描所有网卡，返回列表，每项为 (显示名称, 适配器名称, 速率字符串)
    显示名称格式: Intel(R) Ethernet Connection I219-V 1Gbps
    适配器名称: 用于 netsh 配置 IP
    """
    iphlpapi = ctypes.windll.iphlpapi

    # 第一次调用获取所需缓冲区大小
    buf_size = wintypes.ULONG(0)
    ret = iphlpapi.GetAdaptersInfo(None, ctypes.byref(buf_size))
    if ret != ERROR_BUFFER_OVERFLOW:
        return []

    # 分配缓冲区并调用
    buf = ctypes.create_string_buffer(buf_size.value)
    ret = iphlpapi.GetAdaptersInfo(buf, ctypes.byref(buf_size))
    if ret != ERROR_SUCCESS:
        return []

    result = []
    adapter = ctypes.cast(buf, ctypes.POINTER(IP_ADAPTER_INFO))

    while adapter:
        desc = adapter.contents.Description.decode("gbk", errors="replace").strip()
        adapter_name = adapter.contents.AdapterName.decode("ascii", errors="replace").strip()
        if_index = adapter.contents.Index
        if_type = adapter.contents.Type

        # 获取速率
        speed_bps = _get_if_speed(if_index)
        speed_str = _format_speed(speed_bps)

        # 过滤虚拟网卡：Type 不是物理网卡且速度为 0 的跳过
        is_physical = if_type in PHYSICAL_IF_TYPES
        has_speed = speed_bps > 0

        # PE 中 MIB_IF_TYPE 可能不同，放宽条件
        if desc and adapter_name:
            display_name = f"{desc} {speed_str}" if speed_str else desc
            result.append((display_name, desc, adapter_name, speed_str))

        adapter = adapter.contents.Next

    return result


def get_nic_display_list() -> list:
    """获取用于 GUI 下拉框的网卡列表"""
    nics = scan_nics()
    return [nic[0] for nic in nics]  # 只返回显示名称


def get_adapter_name_by_display(display_name: str) -> str:
    """根据显示名称获取适配器名称 (用于 netsh)"""
    nics = scan_nics()
    for nic in nics:
        if nic[0] == display_name:
            return nic[2]  # adapter_name
    return ""


def get_local_ip(adapter_desc: str = "") -> str:
    """
    获取指定网卡的当前 IP 地址
    如果 adapter_desc 为空，返回第一个有 IP 的网卡地址
    """
    iphlpapi = ctypes.windll.iphlpapi
    buf_size = wintypes.ULONG(0)
    ret = iphlpapi.GetAdaptersInfo(None, ctypes.byref(buf_size))
    if ret != ERROR_BUFFER_OVERFLOW:
        return ""
    buf = ctypes.create_string_buffer(buf_size.value)
    ret = iphlpapi.GetAdaptersInfo(buf, ctypes.byref(buf_size))
    if ret != ERROR_SUCCESS:
        return ""

    adapter = ctypes.cast(buf, ctypes.POINTER(IP_ADAPTER_INFO))
    while adapter:
        desc = adapter.contents.Description.decode("gbk", errors="replace").strip()
        if not adapter_desc or adapter_desc in desc or desc in adapter_desc:
            ip_str = adapter.contents.IpAddressList.IpAddress.decode("ascii").strip()
            if ip_str and ip_str != "0.0.0.0":
                return ip_str
        adapter = adapter.contents.Next

    return ""


def get_local_mac_addresses() -> list:
    """
    获取本地所有物理网卡的 MAC 地址列表
    返回格式: ["aa:bb:cc:dd:ee:ff", ...]
    用于 DHCP 服务器排除本地网卡的自响应
    """
    iphlpapi = ctypes.windll.iphlpapi
    buf_size = wintypes.ULONG(0)
    ret = iphlpapi.GetAdaptersInfo(None, ctypes.byref(buf_size))
    if ret != ERROR_BUFFER_OVERFLOW:
        return []
    buf = ctypes.create_string_buffer(buf_size.value)
    ret = iphlpapi.GetAdaptersInfo(buf, ctypes.byref(buf_size))
    if ret != ERROR_SUCCESS:
        return []

    mac_list = []
    adapter = ctypes.cast(buf, ctypes.POINTER(IP_ADAPTER_INFO))
    while adapter:
        addr_len = adapter.contents.AddressLength
        if addr_len > 0:
            mac_bytes = adapter.contents.Address[:addr_len]
            mac_str = ":".join(f"{b:02x}" for b in mac_bytes)
            mac_list.append(mac_str)
        adapter = adapter.contents.Next

    return mac_list


# ---- IP_ADAPTER_INDEX_MAP (for IpReleaseAddress / IpRenewAddress) ----
class IP_ADAPTER_INDEX_MAP(ctypes.Structure):
    _fields_ = [
        ("Index", wintypes.DWORD),
        ("Name", wintypes.WCHAR * 128),
    ]


def get_adapter_index(adapter_desc: str) -> int:
    """获取指定网卡描述的 ifIndex, 用于单网卡 DHCP 操作"""
    iphlpapi = ctypes.windll.iphlpapi
    buf_size = wintypes.ULONG(0)
    ret = iphlpapi.GetAdaptersInfo(None, ctypes.byref(buf_size))
    if ret != ERROR_BUFFER_OVERFLOW:
        return 0
    buf = ctypes.create_string_buffer(buf_size.value)
    ret = iphlpapi.GetAdaptersInfo(buf, ctypes.byref(buf_size))
    if ret != ERROR_SUCCESS:
        return 0

    adapter = ctypes.cast(buf, ctypes.POINTER(IP_ADAPTER_INFO))
    while adapter:
        desc = adapter.contents.Description.decode("gbk", errors="replace").strip()
        if adapter_desc in desc or desc in adapter_desc:
            return adapter.contents.Index
        adapter = adapter.contents.Next
    return 0


def release_dhcp_ip(adapter_index: int) -> bool:
    """释放指定网卡的 DHCP IP (仅目标网卡, 不碰其他网卡)"""
    if adapter_index <= 0:
        return False
    iphlpapi = ctypes.windll.iphlpapi
    idx_map = IP_ADAPTER_INDEX_MAP()
    idx_map.Index = adapter_index
    ret = iphlpapi.IpReleaseAddress(ctypes.byref(idx_map))
    return ret == ERROR_SUCCESS


def renew_dhcp_ip(adapter_index: int) -> bool:
    """续租指定网卡的 DHCP IP (仅目标网卡, 不碰其他网卡)"""
    if adapter_index <= 0:
        return False
    iphlpapi = ctypes.windll.iphlpapi
    idx_map = IP_ADAPTER_INDEX_MAP()
    idx_map.Index = adapter_index
    ret = iphlpapi.IpRenewAddress(ctypes.byref(idx_map))
    return ret == ERROR_SUCCESS


# ---- NotifyAddrChange (事件驱动 IP 变化等待) ----
class _OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_ulong),
        ("InternalHigh", ctypes.c_ulong),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


def wait_for_ip_change(timeout_sec: float = 20.0) -> bool:
    """使用 NotifyAddrChange + OVERLAPPED 等待任意网卡 IP 变化 (带超时)。
    返回 True 表示 IP 发生了变化; False 表示超时。
    相比轮询 get_local_ip(), 无需消耗 CPU, 变化后亚毫秒级响应。
    """
    kernel32 = ctypes.windll.kernel32
    iphlpapi = ctypes.windll.iphlpapi

    h_event = kernel32.CreateEventW(None, True, False, None)
    if not h_event:
        return False

    ov = _OVERLAPPED()
    ov.hEvent = h_event

    handle = wintypes.DWORD(0)
    ret = iphlpapi.NotifyAddrChange(ctypes.byref(handle), ctypes.byref(ov))

    # NO_ERROR (0): 地址已变化, 立即返回
    if ret == 0:
        kernel32.CloseHandle(h_event)
        return True

    # ERROR_IO_PENDING (997): 挂起等待中
    if ret != 997:
        kernel32.CloseHandle(h_event)
        return False

    timeout_ms = wintypes.DWORD(int(timeout_sec * 1000))
    wait_ret = kernel32.WaitForSingleObject(h_event, timeout_ms)

    if wait_ret == 0:  # WAIT_OBJECT_0 — IP 已变化
        kernel32.CloseHandle(h_event)
        return True

    # 超时或取消 — 取消挂起的通知
    iphlpapi.CancelIPChangeNotify(ctypes.byref(ov))
    kernel32.CloseHandle(h_event)
    return False


if __name__ == "__main__":
    # 测试
    nics = scan_nics()
    for nic in nics:
        print(f"显示: {nic[0]}")
        print(f"  描述: {nic[1]}")
        print(f"  适配器: {nic[2]}")
        print()
