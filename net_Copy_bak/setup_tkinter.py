"""
setup_tkinter.py - 获取 tkinter 组件并部署到嵌入式 Python
策略:
  1. 有系统 Python 3.13 → 直接复制
  2. 目录下有完整安装包 → 静默安装 + 提取
  3. 都没有 → 提供下载链接
"""
import os
import shutil
import subprocess
import sys
import time
import glob

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EMBED_DIR = os.path.join(SCRIPT_DIR, "python-3.13.14-embed-amd64")

# 可能的安装程序文件名 (python.org 标准命名)
INSTALLER_NAMES = [
    "python-3.13.14-amd64.exe",
    "python-3.13.14-amd64-full.exe",
    "python-3.13.14.exe",
]


def find_system_python313():
    """查找系统已安装的 Python 3.13"""
    candidates = [
        r"C:\Python313",
        r"C:\Program Files\Python313",
        r"C:\Program Files (x86)\Python313",
    ]
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if localappdata:
        base = os.path.join(localappdata, "Programs", "Python")
        if os.path.isdir(base):
            for d in sorted(os.listdir(base), reverse=True):
                full = os.path.join(base, d)
                if os.path.isfile(os.path.join(full, "python.exe")):
                    candidates.append(full)

    for p in candidates:
        exe = os.path.join(p, "python.exe")
        if os.path.isfile(exe):
            try:
                r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5)
                if "3.13" in (r.stdout + r.stderr):
                    return p
            except Exception:
                pass
    return None


def find_installer():
    """在脚本目录查找完整安装包"""
    for name in INSTALLER_NAMES:
        path = os.path.join(SCRIPT_DIR, name)
        if os.path.isfile(path):
            size = os.path.getsize(path)
            # 完整安装包 > 20MB
            if size > 20_000_000:
                return path
    return None


def verify():
    """检查嵌入式目录中 tkinter 是否就位"""
    required = {
        "_tkinter.pyd": os.path.join(EMBED_DIR, "_tkinter.pyd"),
        "tcl86t.dll":   os.path.join(EMBED_DIR, "tcl86t.dll"),
        "tk86t.dll":    os.path.join(EMBED_DIR, "tk86t.dll"),
        "tkinter/":     os.path.join(EMBED_DIR, "tkinter"),
        "tcl/":         os.path.join(EMBED_DIR, "tcl"),
    }
    all_ok = True
    for label, path in required.items():
        ok = os.path.exists(path)
        print(f"  {'[OK]' if ok else '[MISS]'} {label}")
        if not ok:
            all_ok = False
    return all_ok


def copy_from_python(python_dir):
    """从 Python 安装目录复制 tkinter 到嵌入式目录"""
    dll_dir = os.path.join(python_dir, "DLLs")
    lib_dir = os.path.join(python_dir, "Lib")
    tcl_dir = os.path.join(python_dir, "tcl")

    copied = 0
    for fname in ["_tkinter.pyd", "tcl86t.dll", "tk86t.dll"]:
        src = os.path.join(dll_dir, fname)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(EMBED_DIR, fname))
            print(f"  [OK] {fname} ({os.path.getsize(src):,} bytes)")
            copied += 1
        else:
            print(f"  [MISS] {fname}")

    if os.path.isdir(tcl_dir):
        dst = os.path.join(EMBED_DIR, "tcl")
        if os.path.exists(dst):
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(tcl_dir, dst)
        n = sum(1 for _ in glob.glob(os.path.join(dst, "**", "*"), recursive=True))
        print(f"  [OK] tcl/ ({n} files)")
        copied += 1
    else:
        print(f"  [MISS] tcl/")

    tkinter_src = os.path.join(lib_dir, "tkinter")
    if os.path.isdir(tkinter_src):
        dst = os.path.join(EMBED_DIR, "tkinter")
        if os.path.exists(dst):
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(tkinter_src, dst)
        n = sum(1 for _ in glob.glob(os.path.join(dst, "**", "*.py"), recursive=True))
        print(f"  [OK] tkinter/ ({n} .py files)")
        copied += 1
    else:
        print(f"  [MISS] tkinter/")

    return copied


def install_and_extract(installer_path):
    """静默安装 Python 到临时目录，然后提取 tkinter"""
    temp_dir = os.path.join(SCRIPT_DIR, "_tmp_py_install")
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)

    print(f"\nRunning: {os.path.basename(installer_path)}")
    print("Silent install to temp directory (1-2 minutes)...")

    cmd = [
        installer_path,
        "/quiet", "/norestart",
        "InstallAllUsers=0",
        "Include_test=0",
        "Include_doc=0",
        "Include_tcltk=1",
        "Include_pip=0",
        "Include_launcher=0",
        f"TargetDir={temp_dir}",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=600)
        time.sleep(5)  # 等待文件完全写入
    except subprocess.TimeoutExpired:
        print("Timeout after 10 minutes")
        shutil.rmtree(temp_dir, ignore_errors=True)
        return False
    except Exception as e:
        print(f"Install failed: {e}")
        shutil.rmtree(temp_dir, ignore_errors=True)
        return False

    # 检查安装结果
    python_exe = os.path.join(temp_dir, "python.exe")
    if not os.path.isfile(python_exe):
        print("Install seems to have failed (no python.exe)")
        shutil.rmtree(temp_dir, ignore_errors=True)
        return False

    print("Install complete. Copying tkinter files...")
    copy_from_python(temp_dir)

    # 清理
    print("Cleaning up temp install...")
    shutil.rmtree(temp_dir, ignore_errors=True)
    return True


def main():
    os.chdir(SCRIPT_DIR)

    print("=" * 55)
    print("  tkinter setup for Python 3.13.14 embedded")
    print("=" * 55)
    print()

    print("Check embedded Python...")
    if verify():
        print("\ntkinter already ready!")
        input("Press Enter to exit...")
        return

    print()

    # 策略 1: 系统 Python 3.13
    sys_python = find_system_python313()
    if sys_python:
        print(f"Found Python 3.13 at: {sys_python}")
        print("Copying tkinter files...")
        copy_from_python(sys_python)
        print()
        print("--- Result ---")
        if verify():
            print("\nDone.")
        input("Press Enter to exit...")
        return

    # 策略 2: 目录下的完整安装包
    installer = find_installer()
    if installer:
        print(f"Found installer: {os.path.basename(installer)}")
        print(f"Size: {os.path.getsize(installer):,} bytes")
        if install_and_extract(installer):
            print()
            print("--- Result ---")
            if verify():
                print("\nDone.")
            input("Press Enter to exit...")
            return

    # 策略 3: 都无法自动完成
    print("Python 3.13 not found on this system.")
    print("No full installer (>20MB) found in project directory.")
    print()
    print("Option A (recommended, automatic):")
    print("  1. Download Python 3.13.14 installer:")
    print("     https://www.python.org/ftp/python/3.13.14/python-3.13.14-amd64.exe")
    print("  2. Save it to this directory")
    print("  3. Run setup.bat again")
    print()
    print("Option B (manual):")
    print("  Install Python 3.13.14 normally, then run setup.bat again")
    print()
    print("Option C (manual copy):")
    print("  From any Python 3.13.14 installation, copy to")
    print("  python-3.13.14-embed-amd64/:")
    print("    DLLs\\_tkinter.pyd")
    print("    DLLs\\tcl86t.dll")
    print("    DLLs\\tk86t.dll")
    print("    tcl\\          (entire dir)")
    print("    Lib\\tkinter\\  (entire dir)")

    input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()
