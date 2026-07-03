"""
磁盘扫描模块
Phase 2: 枚举物理磁盘 + PE 环境下可用盘符
策略: SetupAPI (主力) → IOCTL_STORAGE_QUERY_PROPERTY (回退)
纯 ctypes 实现，不依赖 WMI
"""
import ctypes
import struct
from ctypes import wintypes

# ---- 常量 ----
GENERIC_READ = 0x80000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
StorageDeviceProperty = 0
PropertyStandardQuery = 0

DRIVE_FIXED = 3
DRIVE_REMOVABLE = 2


# =================== IOCTL 方式 ===================

class STORAGE_PROPERTY_QUERY(ctypes.Structure):
    _fields_ = [
        ("PropertyId", wintypes.DWORD),
        ("QueryType", wintypes.DWORD),
        ("AdditionalParameters", ctypes.c_ubyte * 1),
    ]


class STORAGE_DEVICE_DESCRIPTOR(ctypes.Structure):
    _fields_ = [
        ("Version",            wintypes.DWORD),
        ("Size",               wintypes.DWORD),
        ("DeviceType",          ctypes.c_ubyte),
        ("DeviceTypeModifier",  ctypes.c_ubyte),
        ("RemovableMedia",      wintypes.BOOLEAN),
        ("CommandQueueing",     wintypes.BOOLEAN),
        ("VendorIdOffset",      wintypes.DWORD),
        ("ProductIdOffset",     wintypes.DWORD),
        ("ProductRevisionOffset", wintypes.DWORD),
        ("SerialNumberOffset",  wintypes.DWORD),
        ("BusType",            wintypes.DWORD),
        ("RawPropertiesLength", wintypes.DWORD),
        ("RawDeviceProperties", ctypes.c_ubyte * 1),
    ]


def _buf2str(raw: bytes, offset: int) -> str:
    """从 bytes buffer 指定偏移提取 null-terminated ASCII 字符串"""
    if offset == 0 or offset >= len(raw):
        return ""
    end = offset
    while end < len(raw) and raw[end] != 0:
        end += 1
    chunk = raw[offset:end]
    try:
        s = chunk.decode("ascii", errors="replace")
    except Exception:
        return ""
    return "".join(c for c in s if 32 <= ord(c) < 127).strip()


def _looks_like_serial(s: str) -> bool:
    """判断字符串是否像序列号/hex ID（不超过8个纯字母数字、或含大量下划线/点）"""
    if not s:
        return True
    # 纯十六进制/数字串，长度较短
    if len(s) <= 8 and all(c.isalnum() for c in s):
        return True
    # 含下划线或点的 ID 格式 (如 001B_448B_4613_699F.)
    special = sum(1 for c in s if c in "_.-")
    if special >= 2 and len(s) > 8:
        return True
    return False


def _get_disk_size(handle) -> int:
    """IOCTL_DISK_GET_DRIVE_GEOMETRY_EX — 兼容 access=0 的句柄"""
    kernel32 = ctypes.windll.kernel32
    IOCTL_DISK_GET_DRIVE_GEOMETRY_EX = 0x000700A0
    buf = ctypes.create_string_buffer(256)
    returned = wintypes.DWORD(0)
    ok = kernel32.DeviceIoControl(
        handle, IOCTL_DISK_GET_DRIVE_GEOMETRY_EX,
        None, 0, buf, 256,
        ctypes.byref(returned), None,
    )
    if ok and returned.value >= 32:
        # DISK_GEOMETRY_EX.DiskSize 在偏移 24 (跳过 DISK_GEOMETRY 的 24 字节)
        return struct.unpack_from("<Q", bytes(buf.raw[:returned.value]), 24)[0]
    return 0


def _format_size(size_bytes: int) -> str:
    if size_bytes <= 0:
        return ""
    gb = size_bytes / (1024 ** 3)
    if gb >= 1000:
        return f"{gb / 1024:.1f}TB"
    return f"{gb:.0f}GB"


# =================== SetupAPI 方式 (主力) ===================

def _scan_disks_setupapi() -> list:
    """
    通过 SetupAPI 获取物理磁盘 FriendlyName
    返回: [{"name": "WDC PC SN730...", "size_gb": 512}, ...]
    """
    try:
        setupapi = ctypes.windll.setupapi
    except AttributeError:
        return []

    kernel32 = ctypes.windll.kernel32

    # GUID_DEVCLASS_DISKDRIVE = {4D36E967-E325-11CE-BFC1-08002BE10318}
    GUID_DISK = (0x4D36E967, 0xE325, 0x11CE,
                 (0xBF, 0xC1, 0x08, 0x00, 0x2B, 0xE1, 0x03, 0x18))

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    DIGCF_PRESENT = 0x00000002
    SPDRP_FRIENDLYNAME = 0x0000000C
    SPDRP_DEVICEDESC = 0x00000000

    class SP_DEVINFO_DATA(ctypes.Structure):
        _fields_ = [
            ("cbSize",    wintypes.DWORD),
            ("ClassGuid", GUID),
            ("DevInst",   wintypes.DWORD),
            ("Reserved",  ctypes.c_void_p),
        ]

    guid = GUID(*GUID_DISK)
    hDevInfo = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None,
        DIGCF_PRESENT
    )
    if hDevInfo == INVALID_HANDLE_VALUE:
        return []

    disks = []
    try:
        dev_data = SP_DEVINFO_DATA()
        dev_data.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
        idx = 0

        while setupapi.SetupDiEnumDeviceInfo(hDevInfo, idx, ctypes.byref(dev_data)):
            idx += 1
            name = ""

            # 尝试 SPDRP_FRIENDLYNAME
            req_buf = wintypes.DWORD(0)
            setupapi.SetupDiGetDeviceRegistryPropertyW(
                hDevInfo, ctypes.byref(dev_data),
                SPDRP_FRIENDLYNAME, None,
                None, 0, ctypes.byref(req_buf)
            )
            if req_buf.value > 0:
                buf = ctypes.create_unicode_buffer(req_buf.value // 2 + 1)
                ok = setupapi.SetupDiGetDeviceRegistryPropertyW(
                    hDevInfo, ctypes.byref(dev_data),
                    SPDRP_FRIENDLYNAME, None,
                    buf, ctypes.sizeof(buf), None,
                )
                if ok:
                    name = buf.value.strip()

            # Fallback: SPDRP_DEVICEDESC
            if not name:
                req_buf2 = wintypes.DWORD(0)
                setupapi.SetupDiGetDeviceRegistryPropertyW(
                    hDevInfo, ctypes.byref(dev_data),
                    SPDRP_DEVICEDESC, None,
                    None, 0, ctypes.byref(req_buf2)
                )
                if req_buf2.value > 0:
                    buf = ctypes.create_unicode_buffer(req_buf2.value // 2 + 1)
                    ok = setupapi.SetupDiGetDeviceRegistryPropertyW(
                        hDevInfo, ctypes.byref(dev_data),
                        SPDRP_DEVICEDESC, None,
                        buf, ctypes.sizeof(buf), None,
                    )
                    if ok:
                        name = buf.value.strip()

            if name:
                disks.append({"name": name, "size_bytes": 0})

    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hDevInfo)

    return disks


# =================== Registry 方式 (补充) ===================

def _get_disk_model_from_registry(disk_number: int) -> str:
    """从注册表获取磁盘型号 (diskpart 同类做法)"""
    try:
        import winreg
    except ImportError:
        return ""

    # SCSI/NVMe 磁盘在 Enum\SCSI 下，IDE/SATA 在 Enum\IDE 下
    for enum_key in (r"SCSI", r"IDE"):
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                fr"SYSTEM\CurrentControlSet\Enum\{enum_key}",
            ) as key:
                idx = 0
                while True:
                    try:
                        sub_name = winreg.EnumKey(key, idx)
                        idx += 1
                    except OSError:
                        break

                    with winreg.OpenKey(key, sub_name) as dev_key:
                        j = 0
                        while True:
                            try:
                                inst_name = winreg.EnumKey(dev_key, j)
                                j += 1
                            except OSError:
                                break

                            inst_path = (
                                fr"SYSTEM\CurrentControlSet\Enum\{enum_key}"
                                fr"\{sub_name}\{inst_name}"
                            )
                            try:
                                with winreg.OpenKey(
                                    winreg.HKEY_LOCAL_MACHINE, inst_path
                                ) as inst_key:
                                    try:
                                        addr, _ = winreg.QueryValueEx(
                                            inst_key, "Address"
                                        )
                                        if addr == disk_number:
                                            # 优先 FriendlyName
                                            for val_name in (
                                                "FriendlyName",
                                                "DeviceDesc",
                                                "HardwareID",
                                            ):
                                                try:
                                                    model, _ = winreg.QueryValueEx(
                                                        inst_key, val_name
                                                    )
                                                    if model:
                                                        # HardwareID 是多行，取第一行
                                                        if isinstance(model, list):
                                                            model = model[0]
                                                        # 去掉前缀如 "SCSI\..."
                                                        if "\\" in str(model):
                                                            model = str(model).split("\\")[-1]
                                                        return str(model).strip()
                                                except OSError:
                                                    continue
                                    except OSError:
                                        pass
                            except OSError:
                                continue
        except OSError:
            continue
    return ""


# =================== IOCTL 方式 (主力) ===================

def _scan_disks_ioctl() -> list:
    """
    通过 IOCTL_STORAGE_QUERY_PROPERTY 枚举物理磁盘 (diskpart 同类做法)
    返回: [{"name": "WDC PC SN730...", "size_bytes": 512118698496}, ...]
    """
    kernel32 = ctypes.windll.kernel32
    disks = []

    for i in range(16):
        path = f"\\\\.\\PhysicalDrive{i}"
        handle = kernel32.CreateFileW(
            path, 0,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None, OPEN_EXISTING, 0, None,
        )
        if handle == INVALID_HANDLE_VALUE:
            continue

        try:
            query = STORAGE_PROPERTY_QUERY()
            query.PropertyId = StorageDeviceProperty
            query.QueryType = PropertyStandardQuery

            buf_size = 4096
            buf = ctypes.create_string_buffer(buf_size)
            returned = wintypes.DWORD(0)

            ok = kernel32.DeviceIoControl(
                handle, IOCTL_STORAGE_QUERY_PROPERTY,
                ctypes.byref(query), ctypes.sizeof(query),
                buf, buf_size,
                ctypes.byref(returned), None,
            )

            name = ""
            size_bytes = _get_disk_size(handle)

            if ok and returned.value >= ctypes.sizeof(STORAGE_DEVICE_DESCRIPTOR):
                raw = bytes(buf.raw[:returned.value])
                desc = ctypes.cast(buf, ctypes.POINTER(STORAGE_DEVICE_DESCRIPTOR)).contents

                vendor = _buf2str(raw, desc.VendorIdOffset)
                product = _buf2str(raw, desc.ProductIdOffset)
                revision = _buf2str(raw, desc.ProductRevisionOffset)
                serial = _buf2str(raw, desc.SerialNumberOffset)

                parts = []
                if vendor:
                    parts.append(vendor)
                if product:
                    if product != vendor:
                        parts.append(product)
                    elif not vendor:
                        parts.append(product)
                if revision and not _looks_like_serial(revision):
                    rev_clean = revision.strip()
                    if rev_clean and rev_clean not in " ".join(parts):
                        parts.append(rev_clean)

                if parts:
                    name = " ".join(parts)

            # IOCTL 没拿到名字 → 注册表回退
            if not name:
                name = _get_disk_model_from_registry(i)

            # 没有任何名字 → 跳过 (可能是虚拟/无效设备)
            if not name:
                continue

            disks.append({"name": name, "size_bytes": size_bytes})

        finally:
            kernel32.CloseHandle(handle)

    return disks


# =================== 盘符扫描 ===================

def scan_drive_letters() -> list:
    """
    扫描 PE 下所有可用的固定磁盘盘符
    返回: ["C", "D", "F", "I", "J", ...]  (不含冒号)
    """
    kernel32 = ctypes.windll.kernel32
    bitmask = kernel32.GetLogicalDrives()
    if bitmask == 0:
        return []

    letters = []
    for i in range(26):
        if bitmask & (1 << i):
            letter = chr(ord("A") + i)
            drive_path = f"{letter}:\\"
            drive_type = kernel32.GetDriveTypeW(drive_path)
            if drive_type in (DRIVE_FIXED, DRIVE_REMOVABLE):
                letters.append(letter)
    return letters


# =================== 公开接口 ===================

def get_disk_list() -> list:
    """
    获取 GUI 磁盘下拉框数据
    优先 IOCTL (diskpart 同类)，SetupAPI 作为补充
    返回: ["WDC PC SN730 SDBQNTY-512G-1001 (512GB)", ...]
    """
    # 主力: IOCTL (最可靠，diskpart 同原理)
    disks = _scan_disks_ioctl()

    # 补充: SetupAPI (可提供更友好的名称)
    if not disks:
        disks = _scan_disks_setupapi()

    if not disks:
        return ["未检测到磁盘"]

    result = []
    for d in disks:
        name = d["name"]
        size = d.get("size_bytes", 0)
        if size:
            size_str = _format_size(size)
            name = f"{name} ({size_str})"
        result.append(name)

    return result


def get_drive_letter_list() -> list:
    """获取 GUI 分区下拉框数据"""
    letters = scan_drive_letters()
    if not letters:
        return ["无可用分区"]
    return letters


if __name__ == "__main__":
    print("=== SetupAPI ===")
    for d in _scan_disks_setupapi():
        print(f"  {d['name']}")
    print("\n=== IOCTL ===")
    for d in _scan_disks_ioctl():
        print(f"  {d['name']} ({_format_size(d['size_bytes'])})" if d['size_bytes'] else d['name'])
    print("\n=== GUI 数据 ===")
    print(f"  Disk list: {get_disk_list()}")
    print(f"  Drive letters: {get_drive_letter_list()}")
