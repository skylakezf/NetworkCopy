import os, marshal, importlib.util
from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

EXE = r"c:\Users\Xinyi\Desktop\网络拷贝\dist\磁盘拷贝工具_back.exe"
OUT = r"c:\Users\Xinyi\Desktop\网络拷贝\_extracted"
PYZ_OUT = os.path.join(OUT, "pyz")
os.makedirs(PYZ_OUT, exist_ok=True)

MAGIC = importlib.util.MAGIC_NUMBER          # 16-byte pyc header prefix
print("Python MAGIC_NUMBER =", MAGIC.hex())

arc = CArchiveReader(EXE)
# Collect project modules: top-level names that are NOT stdlib and NOT PyInstaller bootstrap
PROJECT = {'main', 'ui', 'control', 'nic_scanner', 'ip_config', 'disk_scanner',
           'file_transfer', 'verifier', 'dhcp_server', 'penetwork_config', 'logger'}

project_written = []
for name, (off, ln, uln, comp, tcode) in arc.toc.items():
    if tcode != 'z':
        continue
    pyz = arc.open_embedded_archive(name)
    for pname in pyz.toc:
        code = pyz.extract(pname)
        psafe = pname.replace("\\", "_").replace("/", "_")
        out_path = os.path.join(PYZ_OUT, psafe + ".pyc")
        with open(out_path, "wb") as f:
            f.write(MAGIC)
            f.write(b'\x00' * 12)            # bit_field(4) + mtime(4) + size(4) = 0
            f.write(marshal.dumps(code))
        if pname in PROJECT:
            project_written.append(pname)
            print("PROJECT module:", pname, "->", out_path)

print("\nProject modules recovered:", project_written)
print("Done.")
