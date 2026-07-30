# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

datas = [('certs', 'certs')]
datas += collect_data_files('cryptography')
datas += collect_data_files('ttkbootstrap')


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['tkinter', 'tkinter.ttk', 'urllib.parse', 'json', 'csv', 'hashlib', 'concurrent.futures', 'http.server', 'struct', 're', 'threading', 'socket', 'subprocess', 'ctypes', 'ctypes.wintypes', 'urllib.request', 'urllib.error', 'cryptography', 'cryptography.hazmat.primitives.asymmetric.rsa', 'cryptography.hazmat.primitives.serialization', 'cryptography.hazmat.backends.openssl.backend', 'ttkbootstrap', 'ttkbootstrap.style', 'ttkbootstrap.themes', 'ttkbootstrap.constants', 'ttkbootstrap.window', 'ttkbootstrap.tooltip', 'PIL', 'PIL.Image', 'PIL.ImageTk', 'secrets', 'tls_utils'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='磁盘拷贝工具',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
