# 项目记忆

## 项目概述
- 项目名：磁盘拷贝工具
- 运行环境：Windows PE + 嵌入式 Python
- GUI 框架：Tkinter（由 Tkinter 布局助手生成）
- 功能：源设备通过网络将 D/E/F 盘数据拷贝至目标设备，并校验

## 技术栈
- GUI: Tkinter + ttk（标准库）
- 网卡枚举: ctypes + iphlpapi.dll
- IP 配置: subprocess(netsh) → fallback ctypes API
- 磁盘枚举: ctypes + DeviceIoControl
- 网络传输: HTTPS (http.server + ssl) + urllib.request，客户端 CERT_NONE
- 校验: hashlib.md5 + csv
- 并发: threading
- 鉴权: 随机 4 位英文数字验证码 (tls_utils.generate_auth_code, secrets)
- 证书: cryptography 生成自签名证书 (SAN=IP)，缓存于 certs/ 目录

## 代码架构
- main.py → 入口
- ui.py → 纯布局
- control.py → 业务逻辑层
- 模块: nic_scanner / ip_config / disk_scanner / file_transfer / verifier / logger / tls_utils

## 关键约束
- PE 无 WMI、无 pip，尽量只用标准库
- 新增依赖 cryptography（已 pip 安装进 python-3.13.14-embed-amd64，PyInstaller 需 --hidden-import cryptography --collect-data cryptography）
- 盘符映射是核心难点，需用户手动指定
- 组网方式（2026-07-28 核实代码更正）：目标设备设静态 IP 169.254.100.1 并运行内置 DHCP 服务器，源设备通过 DHCP 获得 169.254.100.2；传输端口为 9999（不是 443/80）
- 所有传输端点 (/ping /list /get /batch_get) 必须携带 ?pwd=<验证码>，否则 403
- 客户端不校验自签名证书 (CERT_NONE)，安全性由随机验证码保证；如需防 MITM 需改为校验证书
- 已知重大问题（2026-07-28 审查，尚未修复）：control.py 未向 FileServer/download_files/scan_source_device 传 auth_code 与 cert_paths，主流程实际跑不通；_send_json 缺 Content-Length 导致 HTTP/1.1 keep-alive 下 JSON 端点挂起；_download_batch except 分支对 4 元组按 3 值解包；verifier 重试下载走明文 http 且无 pwd；run_verification 未传 server_ip/gtmc_new_name。详见 2026-07-28.md
- 已修复（2026-07-29）：Windows 路径拼接 bug — .rstrip("\\") 后再 os.path.join 得到的是相对盘符路径（如 "J:foo" 而非 "J:\\foo"），导致文件写入当前工作目录。修复了 file_transfer.py:990 和 verifier.py:216/219/303 四处，rstrip 后补回 "\\"。
