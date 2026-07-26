import os
from PyInstaller.archive.readers import CArchiveReader

EXE = r"c:\Users\Xinyi\Desktop\网络拷贝\dist\磁盘拷贝工具_back.exe"
arc = CArchiveReader(EXE)

# list PYZ module names
for name, (off, ln, uln, comp, tcode) in arc.toc.items():
    if tcode != 'z':
        continue
    pyz = arc.open_embedded_archive(name)
    names = list(pyz.toc)
    print("PYZ module count:", len(names))
    for n in names:
        if n.split('.')[0] in ('penetwork_config', 'logger', 'main', 'ui', 'control',
                                 'dhcp_server', 'disk_scanner', 'file_transfer',
                                 'ip_config', 'nic_scanner', 'verifier'):
            print("  INTEREST:", n)
    # also show any top-level non-stdlib looking names
    break

# extract 'main' script (type 's') which is a full pyc
OUT = r"c:\Users\Xinyi\Desktop\网络拷贝\_extracted"
os.makedirs(OUT, exist_ok=True)
for name, (off, ln, uln, comp, tcode) in arc.toc.items():
    if tcode == 's' and name == 'main':
        data = arc.extract(name)
        with open(os.path.join(OUT, "main.pyc"), "wb") as f:
            f.write(data)
        print("wrote main.pyc, first bytes:", data[:4].hex())
