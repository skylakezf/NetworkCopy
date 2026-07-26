import json, urllib.request, zipfile, os

API = "https://api.github.com/repos/extremecoders-re/decompyle-builds/releases/latest"
req = urllib.request.Request(API, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=30) as r:
    data = json.load(r)
assets = data.get("assets", [])
print("Release:", data.get("tag_name"))
print("Assets:")
for a in assets:
    print("  ", a["name"], a["browser_download_url"])
url = assets[0]["browser_download_url"]
zip_path = r"c:\Users\Xinyi\Desktop\网络拷贝\_pycdc.zip"
with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=60) as r:
    with open(zip_path, "wb") as f:
        f.write(r.read())
print("downloaded", zip_path)
with zipfile.ZipFile(zip_path) as z:
    z.extractall(r"c:\Users\Xinyi\Desktop\网络拷贝\_pycdc")
print("extracted to _pycdc:", os.listdir(r"c:\Users\Xinyi\Desktop\网络拷贝\_pycdc"))
