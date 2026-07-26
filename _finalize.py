import os, marshal, importlib.util
from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

EXE = r"c:\Users\Xinyi\Desktop\网络拷贝\dist\磁盘拷贝工具_back.exe"
OUT = r"c:\Users\Xinyi\Desktop\网络拷贝\_recovered_pyc"
os.makedirs(OUT, exist_ok=True)
MAGIC = importlib.util.MAGIC_NUMBER

PROJECT = {'main', 'ui', 'control', 'nic_scanner', 'ip_config', 'disk_scanner',
           'file_transfer', 'verifier', 'dhcp_server', 'penetwork_config', 'logger'}
found = set()

arc = CArchiveReader(EXE)
# main is raw marshal in CArchive 's' entry
for name, (off, ln, uln, comp, tcode) in arc.toc.items():
    if tcode == 's' and name == 'main':
        raw = arc.extract(name)
        with open(os.path.join(OUT, "main.pyc"), "wb") as f:
            f.write(MAGIC); f.write(b'\x00' * 12); f.write(raw)
        found.add('main')

# PYZ modules
for name, (off, ln, uln, comp, tcode) in arc.toc.items():
    if tcode != 'z':
        continue
    pyz = arc.open_embedded_archive(name)
    for pname in pyz.toc:
        if pname in PROJECT:
            code = pyz.extract(pname)
            with open(os.path.join(OUT, pname + ".pyc"), "wb") as f:
                f.write(MAGIC); f.write(b'\x00' * 12); f.write(marshal.dumps(code))
            found.add(pname)

# Check presence of penetwork_config / logger anywhere
all_pyz = set()
for name, (off, ln, uln, comp, tcode) in arc.toc.items():
    if tcode != 'z':
        continue
    pyz = arc.open_embedded_archive(name)
    all_pyz.update(pyz.toc)
print("penetwork_config in bundle:", 'penetwork_config' in all_pyz or 'penetwork_config' in [n for n,_ in arc.toc.items()])
print("logger in bundle:", 'logger' in all_pyz)
print("Recovered project .pyc:", sorted(found))
# verify all load
for fn in sorted(os.listdir(OUT)):
    if fn.endswith('.pyc'):
        co = marshal.loads(open(os.path.join(OUT, fn), 'rb').read()[16:])
        assert hasattr(co, 'co_code'), fn
print("All .pyc verified as valid 3.13 code objects.")
