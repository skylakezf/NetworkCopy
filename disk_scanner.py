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
IOCTL_DISK_GET_DRIVE_LAYOUT_EX = 0x00070050
StorageDeviceProperty = 0
PropertyStandardQuery = 0

DRIVE_FIXED = 3
DRIVE_REMOVABLE = 2

NTFS_PARTITION_TYPES = {0x07}           # MBR NTFS 类型
NTFS_GPT_GUIDS = {                       # GPT NTFS/基本数据分区 GUID
    bytes([0xA2, 0xA0, 0xD0, 0xEB, 0xE5, 0xB9, 0x33, 0x44,
           0x87, 0xC0, 0x68, 0xB6, 0xB7, 0x26, 0x99, 0xC7]),
}


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

            disks.append({"name": name, "size_bytes": size_bytes, "disk_number": i})

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


def get_partition_count(disk_number: int) -> int:
    """
    通过 IOCTL_DISK_GET_DRIVE_LAYOUT_EX 获取物理磁盘的 NTFS 分区数量
    disk_number: PhysicalDrive 编号 (0, 1, 2, ...)
    返回: NTFS 分区数 (0 表示获取失败或不含 NTFS 分区)
    """
    kernel32 = ctypes.windll.kernel32
    path = f"\\\\.\\PhysicalDrive{disk_number}"
    handle = kernel32.CreateFileW(
        path, 0,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None, OPEN_EXISTING, 0, None,
    )
    if handle == INVALID_HANDLE_VALUE:
        return 0

    try:
        buf = ctypes.create_string_buffer(32768)
        returned = wintypes.DWORD(0)
        ok = kernel32.DeviceIoControl(
            handle, IOCTL_DISK_GET_DRIVE_LAYOUT_EX,
            None, 0, buf, ctypes.sizeof(buf),
            ctypes.byref(returned), None,
        )
        if not ok or returned.value < 48:
            return 0

        raw = bytes(buf.raw[:returned.value])

        # DRIVE_LAYOUT_INFORMATION_EX 结构:
        #   DWORD PartitionStyle  (offset 0)
        #   DWORD PartitionCount   (offset 4)
        #   union { MBR, GPT }     (offset 8)
        #   PARTITION_INFORMATION_EX array (offset 48)
        partition_style = struct.unpack_from("<I", raw, 0)[0]
        partition_count = struct.unpack_from("<I", raw, 4)[0]

        ntfs_count = 0

        # PARTITION_INFORMATION_EX 起始偏移 48
        # 每个元素: PartitionStyle(4) + StartingOffset(8) + PartitionLength(8) +
        #           PartitionNumber(4) + RewritePartition(1) + padding(3) +
        #           MBR(16) or GPT(128) = 144 字节
        PARTITION_INFO_SIZE = 144
        for i in range(partition_count):
            offset = 48 + i * PARTITION_INFO_SIZE
            if offset + PARTITION_INFO_SIZE > len(raw):
                break

            pi_style = struct.unpack_from("<I", raw, offset)[0]

            if pi_style == 0:  # MBR
                # Mbr 在偏移 32: 4 bytes (BootIndicator) + 3 bytes padding +
                # PartitionType(1) at offset 4 of Mbr sub-struct
                # 实际上 Mbr 在 PARTITION_INFORMATION_EX 偏移 32
                mbr_offset = offset + 36  # 32 + 4 (start of Mbr + BootIndicator)
                if mbr_offset < len(raw):
                    ptype = raw[mbr_offset]
                    if ptype in NTFS_PARTITION_TYPES:
                        ntfs_count += 1

            elif pi_style == 1:  # GPT
                # Gpt 在偏移 32: PartitionType GUID (16 bytes) + PartitionId GUID (16 bytes) + ...
                gpt_offset = offset + 32
                if gpt_offset + 16 <= len(raw):
                    guid = raw[gpt_offset:gpt_offset + 16]
                    if guid in NTFS_GPT_GUIDS:
                        ntfs_count += 1

        return ntfs_count
    finally:
        kernel32.CloseHandle(handle)


def _debug(msg: str):
    """调试输出: 写入 stderr 并立即刷新"""
    import sys
    try:
        print(f"[PartMap] {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass


def get_partition_details(disk_number: int) -> list:
    """
    返回物理磁盘上 NTFS 分区的详情列表, 按磁盘上的物理顺序 (StartingOffset) 排序:
        [(starting_offset, partition_number, drive_letter), ...]
    兼容 GPT 与 MBR 分区表 (布局解析中 pi_style 0=MBR / 1=GPT 均已处理;
    MBR 逻辑分区号可能不连续, 因此排序依据是 StartingOffset 而非分区号)。
    通过 IOCTL 获取分区偏移 → FindFirstVolume 枚举卷 → 匹配偏移
    """
    kernel32 = ctypes.windll.kernel32

    # 设置 argtypes 避免 64 位下访问违例
    _debug("设置 Volume API argtypes...")
    try:
        kernel32.FindFirstVolumeW.argtypes = [wintypes.LPWSTR, wintypes.DWORD]
        kernel32.FindFirstVolumeW.restype = wintypes.HANDLE
        kernel32.FindNextVolumeW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD]
        kernel32.FindNextVolumeW.restype = wintypes.BOOL
        kernel32.FindVolumeClose.argtypes = [wintypes.HANDLE]
        kernel32.FindVolumeClose.restype = wintypes.BOOL
        kernel32.GetVolumePathNamesForVolumeNameW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, wintypes.LPDWORD,
        ]
        kernel32.GetVolumePathNamesForVolumeNameW.restype = wintypes.BOOL
        _debug("argtypes 设置完成")
    except Exception as e:
        _debug(f"argtypes 设置失败: {e}")

    # ---- 第一步: IOCTL 获取分区布局 ----
    path = f"\\\\.\\PhysicalDrive{disk_number}"
    _debug(f"步骤1: 打开 {path}")
    try:
        handle = kernel32.CreateFileW(
            path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE,
            None, OPEN_EXISTING, 0, None,
        )
    except Exception as e:
        _debug(f"CreateFileW 异常: {e}")
        return []
    if handle == INVALID_HANDLE_VALUE:
        _debug(f"无法打开 {path} (INVALID_HANDLE_VALUE)")
        return []
    _debug(f"步骤1 完成: handle={handle}")

    partitions = []  # [(starting_offset, partition_number), ...]
    try:
        buf = ctypes.create_string_buffer(32768)
        returned = wintypes.DWORD(0)
        _debug("步骤2: DeviceIoControl(IOCTL_DISK_GET_DRIVE_LAYOUT_EX)")
        ok = kernel32.DeviceIoControl(
            handle, IOCTL_DISK_GET_DRIVE_LAYOUT_EX,
            None, 0, buf, ctypes.sizeof(buf),
            ctypes.byref(returned), None,
        )
        if not ok or returned.value < 48:
            _debug(f"步骤2 失败: ok={ok}, returned={returned.value}")
            return []

        raw = bytes(buf.raw[:returned.value])
        partition_count = struct.unpack_from("<I", raw, 4)[0]
        PARTITION_INFO_SIZE = 144
        _debug(f"步骤2 完成: partition_count={partition_count}, returned_size={returned.value}")

        for i in range(partition_count):
            offset = 48 + i * PARTITION_INFO_SIZE
            if offset + PARTITION_INFO_SIZE > len(raw):
                _debug(f"  分区{i}: 偏移越界 ({offset}+{PARTITION_INFO_SIZE} > {len(raw)})")
                break

            pi_style = struct.unpack_from("<I", raw, offset)[0]
            # x64 8-byte alignment: pad at +4, StartingOffset at +8
            start_offset = struct.unpack_from("<Q", raw, offset + 8)[0]
            # offset+16=PartitionLength(8), offset+24=PartitionNumber(4)
            part_number = struct.unpack_from("<I", raw, offset + 24)[0]

            is_ntfs = False
            if pi_style == 0:  # MBR
                mbr_offset = offset + 32
                if mbr_offset < len(raw):
                    ptype = raw[mbr_offset]
                    is_ntfs = ptype in NTFS_PARTITION_TYPES
                    _debug(f"  分区{i}: MBR ptype={ptype:#04x} ntfs={is_ntfs} "
                           f"offset={start_offset} part_num={part_number}")
            elif pi_style == 1:  # GPT
                gpt_offset = offset + 32
                if gpt_offset + 16 <= len(raw):
                    guid = raw[gpt_offset:gpt_offset + 16]
                    is_ntfs = guid in NTFS_GPT_GUIDS
                    _debug(f"  分区{i}: GPT ntfs={is_ntfs} "
                           f"offset={start_offset} part_num={part_number}")
            else:
                _debug(f"  分区{i}: 未知 style={pi_style}, 跳过")

            if is_ntfs:
                partitions.append((start_offset, part_number))
    except Exception as e:
        _debug(f"步骤2 异常: {e}")
        import traceback
        traceback.print_exc(file=__import__('sys').stderr)
        return []
    finally:
        kernel32.CloseHandle(handle)

    if not partitions:
        _debug("步骤2 结果: 未发现 NTFS 分区")
        return []

    _debug(f"步骤2 结果: 发现 {len(partitions)} 个 NTFS 分区: {partitions}")

    # ---- 第二步: 枚举卷并匹配偏移 ----
    _debug("步骤3: FindFirstVolumeW")
    try:
        volume_name_buf = ctypes.create_unicode_buffer(260)
        find_handle = kernel32.FindFirstVolumeW(volume_name_buf, ctypes.sizeof(volume_name_buf))
    except Exception as e:
        _debug(f"FindFirstVolumeW 异常: {e}")
        return []
    if find_handle == INVALID_HANDLE_VALUE:
        _debug("FindFirstVolumeW 失败 (INVALID_HANDLE_VALUE)")
        return []
    _debug(f"步骤3 完成: find_handle={find_handle}")

    result = {}
    vol_index = 0
    try:
        while True:
            vol_path = volume_name_buf.value.rstrip("\\")  # \\?\Volume{...}
            vol_index += 1
            _debug(f"  vol[{vol_index}]: path={vol_path}")

            # 获取卷对应的磁盘盘符 (可能多个，但 PE 下通常只有一个)
            _debug(f"  vol[{vol_index}]: GetVolumePathNamesForVolumeNameW")
            path_names_buf = ctypes.create_unicode_buffer(260)
            path_names_len = wintypes.DWORD()
            try:
                has_path = kernel32.GetVolumePathNamesForVolumeNameW(
                    vol_path + "\\", path_names_buf, 260, ctypes.byref(path_names_len)
                )
            except Exception as e:
                _debug(f"  vol[{vol_index}]: GetVolumePathNamesForVolumeNameW 异常: {e}")
                has_path = False

            drive_letters = []
            if has_path:
                raw_paths = path_names_buf.value
                _debug(f"  vol[{vol_index}]: raw_paths={repr(raw_paths)}, "
                       f"len={path_names_len.value}")
                if raw_paths:
                    for dl in raw_paths.split("\0"):
                        dl = dl.strip()
                        if len(dl) >= 2 and dl[1] == ":":
                            drive_letters.append(dl[0].upper())
                _debug(f"  vol[{vol_index}]: drive_letters={drive_letters}")
            else:
                _debug(f"  vol[{vol_index}]: 无盘符 (has_path=False)")

            if not drive_letters:
                _debug(f"  vol[{vol_index}]: 跳过, FindNextVolumeW...")
                try:
                    next_ok = kernel32.FindNextVolumeW(
                        find_handle, volume_name_buf, ctypes.sizeof(volume_name_buf)
                    )
                except Exception as e:
                    _debug(f"  vol[{vol_index}]: FindNextVolumeW 异常: {e}")
                    break
                if not next_ok:
                    _debug(f"  vol[{vol_index}]: FindNextVolumeW=False, 结束枚举")
                    break
                continue

            # 获取卷的磁盘盘区
            _debug(f"  vol[{vol_index}]: 打开卷 {vol_path}")
            try:
                vol_handle = kernel32.CreateFileW(
                    vol_path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE,
                    None, OPEN_EXISTING, 0, None,
                )
            except Exception as e:
                _debug(f"  vol[{vol_index}]: CreateFileW(vol) 异常: {e}")
                vol_handle = INVALID_HANDLE_VALUE

            if vol_handle != INVALID_HANDLE_VALUE:
                _debug(f"  vol[{vol_index}]: 卷打开成功, DeviceIoControl(VOLUME_DISK_EXTENTS)")
                extent_buf = ctypes.create_string_buffer(1024)
                extent_returned = wintypes.DWORD(0)
                IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS = 0x00560000
                try:
                    has_extents = kernel32.DeviceIoControl(
                        vol_handle, IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS,
                        None, 0, extent_buf, 1024,
                        ctypes.byref(extent_returned), None,
                    )
                except Exception as e:
                    _debug(f"  vol[{vol_index}]: DeviceIoControl(VOLUME_DISK_EXTENTS) 异常: {e}")
                    has_extents = False
                kernel32.CloseHandle(vol_handle)

                if has_extents and extent_returned.value >= 8:
                    num_extents = struct.unpack_from("<I", bytes(extent_buf.raw))[0]
                    _debug(f"  vol[{vol_index}]: num_extents={num_extents}")
                    for ei in range(num_extents):
                        # VOLUME_DISK_EXTENTS: [NumberOfDiskExtents(4)] + padding(4) + Extents[];
                        # 每个 DISK_EXTENT(24字节, 8字节对齐): DiskNumber(4) + padding(4) + StartingOffset(8) + ExtentLength(8)
                        ext_offset = 8 + ei * 24
                        ext_disk = struct.unpack_from("<I", bytes(extent_buf.raw), ext_offset)[0]
                        ext_start = struct.unpack_from("<Q", bytes(extent_buf.raw), ext_offset + 8)[0]
                        _debug(f"  vol[{vol_index}]:   extent[{ei}]: disk={ext_disk} "
                               f"start={ext_start} (0x{ext_start:x})")

                        if ext_disk == disk_number:
                            for part_offset, part_number in partitions:
                                if ext_start == part_offset:
                                    _debug(f"  vol[{vol_index}]:   MATCH! 分区{part_number} → "
                                           f"{drive_letters[0]}:")
                                    result[part_number] = (part_offset, drive_letters[0])
                                    break
                else:
                    _debug(f"  vol[{vol_index}]: 获取盘区失败 "
                           f"(ok={has_extents}, returned={extent_returned.value})")
            else:
                _debug(f"  vol[{vol_index}]: 无法打开卷")

            _debug(f"  vol[{vol_index}]: FindNextVolumeW...")
            try:
                next_ok = kernel32.FindNextVolumeW(
                    find_handle, volume_name_buf, ctypes.sizeof(volume_name_buf)
                )
            except Exception as e:
                _debug(f"  vol[{vol_index}]: FindNextVolumeW 异常: {e}")
                break
            if not next_ok:
                _debug(f"  vol[{vol_index}]: FindNextVolumeW=False, 结束枚举")
                break
    except Exception as e:
        _debug(f"枚举卷循环异常: {e}")
        import traceback
        traceback.print_exc(file=__import__('sys').stderr)
    finally:
        _debug("步骤3 结束: FindVolumeClose")
        kernel32.FindVolumeClose(find_handle)

    # 按 StartingOffset (物理顺序) 排序
    details = sorted(
        (off, pn, dl) for pn, (off, dl) in result.items()
    )
    _debug(f"最终映射结果 (物理顺序): {details}")
    return details


def get_partition_drive_mapping(disk_number: int) -> dict:
    """
    返回物理磁盘上 NTFS 分区的 分区号→盘符 映射
    例如: {1: "D", 2: "E", 3: "F", 4: "G"}
    """
    return {pn: dl for _, pn, dl in get_partition_details(disk_number)}


# =================== 公开接口 ===================

# 模块级: 下拉框索引 → PhysicalDrive 编号的映射
_disk_number_map = {}  # {dropdown_index: physical_drive_number}


def get_disk_number(index: int) -> int:
    """根据下拉框索引获取 PhysicalDrive 编号"""
    return _disk_number_map.get(index, -1)


def get_disk_list() -> list:
    """
    获取 GUI 磁盘下拉框数据
    优先 IOCTL (diskpart 同类)，SetupAPI 作为补充
    返回: ["WDC PC SN730 SDBQNTY-512G-1001 (512GB)", ...]
    """
    global _disk_number_map
    _disk_number_map = {}

    # 主力: IOCTL (最可靠，diskpart 同原理)
    disks = _scan_disks_ioctl()

    # 补充: SetupAPI (可提供更友好的名称)
    if not disks:
        disks = _scan_disks_setupapi()

    if not disks:
        return ["未检测到磁盘"]

    result = []
    for dropdown_idx, d in enumerate(disks):
        name = d["name"]
        size = d.get("size_bytes", 0)
        disk_num = d.get("disk_number", dropdown_idx)
        _disk_number_map[dropdown_idx] = disk_num
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
