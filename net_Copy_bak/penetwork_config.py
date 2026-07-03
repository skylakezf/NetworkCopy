"""
PENetwork 配置模块 - 首选方案: 修改 PENetwork.ini 的 [Static IP address] 段落
从官方文档确认的 INI 格式:
  [Static IP address]
  NetAdapter1.UseDHCP=0
  NetAdapter1.IP=10.0.0.1
  NetAdapter1.SM=255.255.255.0
  NetAdapter1.DG=
  NetAdapter1.DNS=
  NetAdapter1.MAC=
"""
import os
import subprocess
import time

PENETWORK_DIRS = [
    r"X:\Program Files\PENetwork",
    r"X:\Program Files (x86)\PENetwork",
]

SOURCE_IP = "169.254.100.1"
TARGET_IP = "169.254.100.2"
SUBNET_MASK = "255.255.255.0"


def find_penetwork_dir():
    """查找 PENetwork 目录"""
    for d in PENETWORK_DIRS:
        if os.path.isdir(d) and os.path.isfile(os.path.join(d, "PENetwork.exe")):
            return d
    # 搜索 X:\ 常见路径
    for drive in ["X:", "C:"]:
        try:
            for root, dirs, _ in os.walk(drive + "\\", topdown=True):
                dirs[:] = [d for d in dirs if d not in ("Windows", "System32", "WinSxS")]
                if "PENetwork.exe" in os.listdir(root):
                    return root
                if root.count("\\") > 3:
                    break
        except Exception:
            pass
    return None


def is_available():
    """PENetwork 是否可用"""
    return find_penetwork_dir() is not None


def _generate_static_ip_section(ip, mac=""):
    """生成 [Static IP address] 段落的完整内容"""
    return f"""[Static IP address]
Computername=MININT-DISKCOPY
Workgroup=WORKGROUP
NetAdapter1.UseDHCP=0
NetAdapter1.IP={ip}
NetAdapter1.SM={SUBNET_MASK}
NetAdapter1.DG=
NetAdapter1.DNS=
NetAdapter1.WINS=
NetAdapter1.MAC={mac}
StartSharing=0
ShareAll=0
NetPath=
Desc.Line1=IP: {ip}
Desc.Line2=SM: {SUBNET_MASK}
Desc.Line3=DiskCopy Tool
"""


def _write_penetwork_ini(penetwork_dir, ip):
    """向 PENetwork.ini 写入静态 IP 配置"""
    ini_path = os.path.join(penetwork_dir, "PENetwork.ini")

    # 读取现有内容，保留其他段落
    existing_sections = []
    if os.path.isfile(ini_path):
        try:
            with open(ini_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            content = ""

        # 提取非 [Static IP address] 的段落
        in_static_section = False
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped == "[Static IP address]":
                in_static_section = True
                continue
            if stripped.startswith("[") and stripped.endswith("]") and stripped != "[Static IP address]":
                in_static_section = False
            if not in_static_section:
                existing_sections.append(line)
    else:
        # 新文件，添加基础配置
        existing_sections = [
            "[PENetwork]",
            "AutoStart=Yes",
            "UseProfiles=No",
            "ShowMain=No",
            "",
        ]

    # 追加新的静态 IP 段落
    new_content = "\n".join(existing_sections).rstrip()
    if new_content:
        new_content += "\n\n"
    new_content += _generate_static_ip_section(ip)

    os.makedirs(penetwork_dir, exist_ok=True)
    with open(ini_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    return ini_path


def _restart_penetwork(penetwork_dir, log_callback=None):
    """重启 PENetwork 以应用新配置"""
    exe_path = os.path.join(penetwork_dir, "PENetwork.exe")
    if not os.path.isfile(exe_path):
        return False

    try:
        # 杀掉旧进程
        subprocess.run(
            ["taskkill", "/F", "/IM", "PENetwork.exe"],
            capture_output=True, timeout=5,
        )
        time.sleep(1.5)
    except Exception:
        pass

    try:
        subprocess.Popen(
            [exe_path],
            cwd=penetwork_dir,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        if log_callback:
            log_callback("[PENetwork] 已重启, 等待 IP 生效...")
        time.sleep(3)
        return True
    except Exception as e:
        if log_callback:
            log_callback(f"[PENetwork] 启动失败: {e}")
        return False


def _verify_ip(expected_ip, log_callback=None):
    """验证当前 IP 是否为目标 IP"""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(3)
        s.connect(("8.8.8.8", 1))
        actual = s.getsockname()[0]
        s.close()
        if log_callback:
            log_callback(f"[PENetwork] 当前 IP: {actual}")
        return actual == expected_ip
    except Exception:
        return False


def configure_ip(adapter_desc, device_type, log_callback=None):
    """
    首选方案: 修改 PENetwork.ini 的 [Static IP address] 段落 → 重启 PENetwork
    返回 (success, ip_string, message)
    """
    ip = SOURCE_IP if "源" in str(device_type) else TARGET_IP
    label = "源设备" if "源" in str(device_type) else "目标设备"

    penetwork_dir = find_penetwork_dir()
    if not penetwork_dir:
        return False, "", "PENetwork 未找到, 使用 APIPA 自动扫描"

    if log_callback:
        log_callback(f"[PENetwork] 找到: {penetwork_dir}")
        log_callback(f"[PENetwork] 配置 {label}: {ip}/24")

    # 1. 写入 INI
    try:
        ini_path = _write_penetwork_ini(penetwork_dir, ip)
        if log_callback:
            log_callback(f"[PENetwork] INI 已写入: {ini_path}")
    except Exception as e:
        return False, "", f"PENetwork INI 写入失败: {e}"

    # 2. 重启 PENetwork
    if not _restart_penetwork(penetwork_dir, log_callback):
        if log_callback:
            log_callback("[PENetwork] 重启失败, 请手动运行 PENetwork.exe")
        return True, ip, f"INI 已写入, 请手动运行 PENetwork.exe 使 {ip} 生效"

    # 3. 验证 IP
    if _verify_ip(ip, log_callback):
        return True, ip, f"IP 已设置为 {ip}/24 (PENetwork)"
    else:
        time.sleep(5)
        if _verify_ip(ip, log_callback):
            return True, ip, f"IP 已设置为 {ip}/24 (PENetwork)"
        if log_callback:
            log_callback("[PENetwork] IP 未验证到, 但配置已写入, 继续...")
        return True, ip, f"配置已写入 ({ip}), 等待生效中"
