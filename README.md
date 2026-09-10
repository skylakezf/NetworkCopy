# 磁盘拷贝工具 (Disk Copy Tool)

> 不拆硬盘、不设 IP，用一根网线把旧电脑（源设备）D/E/F 盘数据整盘搬到新电脑（目标设备），
> 传输完成后自动逐文件校验（MD5），保证一个字节都不差。

面向 **0 IT 基础用户**的磁盘数据迁移工具，运行于 **Windows PE** 环境，
采用**全自动组网 + HTTPS 加密传输 + 验证码鉴权**，全程图形化向导操作。

---

## 功能特性

- **全自动组网**：目标设备内置 DHCP 服务器自动分配 IP（`169.254.100.x`），源设备插上网线即自动入网，无需手动配置
- **多盘并行传输**：D/E/F 盘并发拷贝，支持断点续传（已存在且大小一致的文件自动跳过）
- **传输提速**：1MB 缓冲写 + TCP_NODELAY + 2MB 接收窗口 + 4MB 分块，千兆网卡下大幅缩短耗时
- **智能跳过**：自动跳过系统垃圾、回收站、临时文件、他人用户目录等（详见下文），不搬"垃圾"
- **目录/文件时间戳保留**：源端所有目录与文件的修改时间原样还原
- **同名冲突处理**：目标端已有同名文件时弹窗对比大小，由用户决定"保留已有"或"覆盖重新下载"，绝不强制覆盖
- **完整性校验**：MD5 逐文件核对，生成 `校验报告.csv`（含文件类型 G 列），二次校验仅重查缺失文件，不一致自动重传
- **网络断开检测**：传输期间实时探测 TCP 9999 端口，断网立即红色提示并停止无效重试
- **系统配置导入导出**：可搬移 Outlook、Chrome/Edge 收藏夹、打印机、输入法、IP 配置、已安装程序清单
- **磁盘分配单元迁移估算**：预测 512B → 4096B 分配单元迁移后的磁盘占用变化
- **低分辨率适配**：1280×700 横版向导式界面，右侧步骤指示器，低色域优化

---

## 工作原理

```
┌─────────────────────────────┐        网线直连 / 交换机          ┌─────────────────────────────┐
│   源设备（发送端 · 旧电脑）   │ ◄──────────────────────────────► │   目标设备（接收端 · 新电脑）   │
│                             │                                  │                             │
│  HTTP Server (HTTPS :9999)  │    内置 DHCP 服务器 (169.254.100.1)│                             │
│  bind 0.0.0.0              │  ──► 源设备自动获取 169.254.100.2  │                             │
│  生成 4 位验证码 + 自签证书  │                                  │  输入验证码 → 拉取文件清单 →     │
│  扫描 D/E/F → 提供文件      │                                  │  并发下载 → 校验 → 导入配置     │
└─────────────────────────────┘                                  └─────────────────────────────┘
```

| 项目 | 说明 |
|------|------|
| 传输协议 | HTTPS（`http.server` + `ssl` 自签名证书） |
| 传输端口 | **9999** |
| 鉴权方式 | 随机 4 位英文数字验证码，所有端点必须携带 `?pwd=<验证码>`，错误返回 403 |
| 组网方式 | 目标端运行内置 DHCP 服务器，源端自动获取 `169.254.100.2`；两端掩码统一 `/16`（与 APIPA 一致） |
| 证书策略 | 客户端不校验自签名证书（CERT_NONE），安全性由随机验证码保证 |
| 文件校验 | MD5 + CSV 报告，32 线程并发校验 |

---

## 界面流程（共 7 步）

> 发送端自动跳过第 5 步（导入配置仅接收端可见）。

1. **网络设置** — 自动检测有线网卡（DHCP 组网）；发送端在此可"导出系统配置"
2. **磁盘选择** — 自动扫描并勾选要传输的盘（D/E/F），支持高级分区映射
3. **连接验证** — 发送端显示验证码 / 接收端输入验证码
4. **数据传输** — 自动拷贝 + 实时进度与速度显示
5. **导入配置**（仅接收端）— 还原 Outlook、收藏夹、打印机等系统设置
6. **数据校验** — 自动核对 MD5 指纹，生成校验报告
7. **完成 / 报告**

---

## 自动跳过规则

程序自动"不搬"以下内容（所有层级递归生效），无需用户设置：

| 类别 | 内容 | 原因 |
|------|------|------|
| 系统缓存 | `AppData`、`Application Data` | 后台缓存 / NTFS 交接点（防死循环） |
| 回收站 | `$RECYCLE.BIN` 等 `$` 前缀文件夹 | 垃圾 |
| 系统还原 | `System Volume Information` | 系统备份 |
| 注册表锁 | `ntuser.dat`、`*.dat.log*` | 被系统锁定 |
| 临时文件 | `*.tmp`、`*.log`、`~$*.docx` | 临时垃圾 |
| 虚拟内存 | `pagefile.sys`、`hiberfil.sys`、`swapfile.sys` | 巨大且无用 |
| 已装软件 | 根目录 `Program Files`、`ProgramData` | 重装更稳 |
| 他人账号 | `Users` 下非当前登录用户目录 | 不搬别人电脑的内容 |
| 微信冗余 | `WeChat Files` | 已在其他目录单独处理 |
| 占用中文件 | 被其他程序锁定/无权限读取的文件 | 系统不让读，自动跳过并记入清单 |

> **D 盘双分区模式特殊规则**：只搬当前用户的 `User/` 个人目录，同时保留 D 盘根目录下非 `User/` 的工作目录（如 `GTMC_User_Profiles`）。

---

## 系统配置导入导出

发送端"导出系统配置"将配置导出到 `F:\Appl\YYYY-MM-DD\`；接收端第 5 步选择该文件夹自动还原：

- Outlook 邮件账号与规则、自动存档
- Chrome / Edge 收藏夹
- 打印机（导入时自动连接 `\\QITV3260\GTMCPrinter` 并设为默认打印机）
- 输入法（五笔、拼音词库）
- IP 网络配置
- 已安装程序清单（`Installed_Programs_Regedit.csv` / `StartMenu_Apps.csv`，供照单重装）
- 磁盘分配单元迁移估算（`FullFilelist_DEF.csv`）

未配置的项目自动跳过（提示"未配置，正常跳过"），不报错。导出记录追加写入 `F:\systemconfig.ini`，支持多次导出、取最新。

---

## 项目结构

```
NetworkzCopy/
├── main.py                    # 入口
├── ui.py                      # GUI 布局（ttkbootstrap 横版向导）
├── control.py                 # 业务逻辑层（状态机、线程调度）
├── nic_scanner.py             # 网卡枚举（ctypes + iphlpapi.dll）
├── ip_config.py               # IP 配置（netsh / ctypes API）
├── disk_scanner.py            # 磁盘/分区枚举（DeviceIoControl，GPT/MBR）
├── file_transfer.py           # HTTPS 文件服务器 + 并发下载（核心传输）
├── verifier.py                # MD5 校验、CSV 报告、重试下载
├── config_transfer.py         # 系统配置导出/导入（含压缩上传）
├── dhcp_server.py             # 内置迷你 DHCP 服务器
├── tls_utils.py               # 验证码生成 + 自签名证书管理
├── systemconfig/              # 分配单元迁移估算等子模块
├── tests/                     # 全部自动化测试套件
├── run_tests.py               # 测试唯一入口（自动发现 tests/）
├── _cleanup_zero_byte_dupes.py  # 维护脚本: 清理 0KB 垃圾文件
├── certs/                     # 自签名证书（server.pem / server.key）
├── build.ps1                  # PyInstaller 打包脚本
├── .github/workflows/
│   └── python-package-conda.yml   # GitHub Actions 自动构建
└── python-3.13.14-embed-amd64/    # 嵌入式 Python 运行环境（本地开发用，不入库）
```

---

## 技术栈

| 类别 | 技术 |
|------|------|
| 语言 / 运行时 | Python 3.13 + 嵌入式发行版（本地）/ conda（CI） |
| GUI | ttkbootstrap（基于 Tkinter） |
| 网络传输 | HTTPS（`http.server` + `ssl`）+ `urllib.request`，客户端 CERT_NONE |
| 系统调用 | ctypes + Windows API（`iphlpapi.dll` / `DeviceIoControl` / `SetThreadExecutionState`） |
| 校验 | `hashlib.md5` + `csv` |
| 证书 | `cryptography` 生成自签名证书（SAN 含 IP/主机名） |
| 打包 | PyInstaller（`--onefile --windowed --noupx`） |
| 测试 | 自定义测试脚本（无 mainloop 的 Tk 测试框架） |

第三方依赖：`cryptography`、`ttkbootstrap`、`Pillow`、`pyinstaller`

---

## 构建打包

### 本地构建（Windows）

```powershell
# 在项目根目录执行（自动定位嵌入式 Python 或 PATH 中的 python）
.\build.ps1
```

产物：`dist\磁盘拷贝工具.exe`（单文件，约 22MB）。
`build.ps1` 支持环境变量覆盖：

- `PYTHON_EXE` — 指定 Python 解释器路径（CI 场景）
- `BUILD_TIMEOUT_SECONDS` — 构建超时上限（默认 900 秒）

### GitHub Actions 自动构建

推送 `main` / `beta` 分支、`v*` 标签，或手动触发时，`.github/workflows/python-package-conda.yml` 会自动：

1. 创建 conda 环境（Python 3.13 + tk，tk 提供 tkinter）
2. 安装 `pyinstaller ttkbootstrap cryptography pillow`
3. 调用 `build.ps1` 打包
4. 上传 `dist/*.exe` 为构建产物

---

## 测试

所有测试集中在 `tests/`，由唯一入口 `run_tests.py` 统一调度（串行执行，避免 Tk 窗口与传输端口冲突）：

```powershell
# 全部套件
.\python-3.13.14-embed-amd64\python.exe run_tests.py
# 只跑名称含 ui 的套件 / 跳过耗时 E2E / 列出套件
.\python-3.13.14-embed-amd64\python.exe run_tests.py --only ui
.\python-3.13.14-embed-amd64\python.exe run_tests.py --fast
.\python-3.13.14-embed-amd64\python.exe run_tests.py --list
```

| 套件 | 覆盖范围 | 数量 |
|------|----------|------|
| `tests/test_ui.py` | UI 全流程 / 按钮 / 导航 / 验证码横幅 | 60 项 |
| `tests/test_func.py` | 过滤规则 / HTTPS 端点 / CSV 路径修复 / 端点传输 | 33 项 |
| `tests/test_verify_e2e.py` | 增量确认 / 全盘清单 / 下载+边传边校验 E2E | 32 项 |
| `tests/test_full_flow.py` | 端到端主流程：标点文件名 / 空目录 / 目录树一致性 / 校验 | 16 项 |
| `tests/test_punctuation.py` | 文件名标点(半角/全角) + 0KB 垃圾文件防护 | 26 项 |
| `tests/test_device_replacement.py` | 设备更换端到端（导出→传输→导入→校验） | 25 项 |
| `tests/test_device_transfer_e2e.py` | 两设备间实际传输 E2E（需证书） | 33 项 |
| `tests/test_user_cases.py` | 普通用户场景用例（映射《测试用例.md》） | 21 项 |
| `tests/test_button_states.py` | 按钮状态矩阵 / 断网 / 验证码三场景 | 14 项 |
| `tests/test_ui_readability.py` | 布局可读性与按钮可见性 | 11 项 |
| `tests/test_disconnect.py` | 断线 / 验证码错误的完成判定 | 7 项 |
| `tests/test_thread_safe.py` | 后台线程 tkinter 调用约束 | 7 项 |

合计 **12 套件 / 285 用例**。新增套件只需在 `tests/` 放入 `test_xxx.py`（含 `main()`），
`run_tests.py` 会自动发现，无需修改入口脚本。

维护脚本：`_cleanup_zero_byte_dupes.py` 用于清理历史遗留的 0KB 垃圾文件（默认 dry-run，
`--delete` 才真删，详见文件头说明）。

---

## 更新日志

### 2026-08-25
- 新增 GitHub Actions 自动构建（Conda + PyInstaller），workflow：`.github/workflows/python-package-conda.yml`
- `build.ps1` 支持 `PYTHON_EXE` / `BUILD_TIMEOUT_SECONDS` 环境变量，适配 CI 环境
- 新增 `.gitignore`，排除构建产物与嵌入式 Python 目录

### 2026-08-16
- 校验结果上传：校验完成后自动打包 `<主机名>_verifierReport.zip` 上传；跳过校验时创建空 `<主机名>_Unverifi.zip`
- 二次校验只校验缺失文件（不再全量重扫），其余行保留原值写回
- 移除持久化跳过列表：网络错误不再被永久跳过，下次传输自动重试所有失败文件；权限不足/文件占用等确定性失败直接跳过
- 接收端"使用此配置"自动导入、导入完成自动跳转校验页；校验页直接关闭程序时自动发送跳过信息
- `systemconfig.ini` 多次导出追加记录，最新记录在末尾
- 发送端未导出配置直接下一步弹窗提醒；高级设置手动更改自动值弹窗确认
- 新增 `测试用例.md` 与 `_test_user_cases.py`（21 项用户场景自动化测试）

### 2026-08-11
- 修复发送端传输页验证码横幅不显示（固定位置 + 提前显示）
- 断网检测放弃 ICMP ping，改用 TCP 9999 端口探测（ping 被防火墙拦截会误报）
- 断网后批次/单文件重试立即中止，UI 实时红色提示 + "重新接收"按钮
- 新增完整 UI 测试（62 项）与功能测试（30 项）；修复 `ssl.wrap_socket` 已移除导致的 TLS 探测误报

### 2026-08-07
- 依据源码编写操作指南底稿 `note.md`，并制作面向 0 基础用户的操作指南 PPT（真实界面截图 + 步骤引导标注）

### 2026-08-05
- 修复 `.dat` 过滤误杀微信数据文件（微信聊天记录以 `.dat` 存储，从全局过滤中移除，改用精确匹配 `ntuser.dat`）
- 修复 `escape_csv` 将路径逗号转全角导致校验误报（改用标准 csv 引号转义），三层防御兼容旧版 CSV
- 修复 UTF-16 LE 编码 CSV 无法解码（自动检测 BOM/编码）
- 新增传输过程网络断开检测（后台 TCP 监控线程）

### 2026-08-04
- 跳过 `NTUSER.DAT` 及事务日志（`.dat`、`.dat.log*`）
- 接收端文件写入失败强制 3 次重试再跳过
- 新增持久化跳过列表（`_netcopy_skiplist.json`，后续已移除）
- 校验器支持 `systemconfig.ini` 自动定位 CSV；校验报告新增文件类型 G 列（fileinfo.com 集成 + 内置映射）

### 2026-08-03
- 验证码错误时不再跳步，红色提示 + "重试接收"可修正重连
- 大磁盘 `/list` 超时修复：服务端流式写入 + 客户端超时提升到 300s
- 配置导出后自动压缩上传 Profile 服务器（`<主机名>_<日期>.zip`）
- 修复后台线程直接操作 Tk 控件导致的静默崩溃
- 设备更换全流程端到端测试（25 项）

### 2026-08-02
- 网卡选择改为全自动（发送端 bind 0.0.0.0；接收端 DHCP 在所有有线网卡广播），手动选择移入"高级选项"
- 磁盘自动选择、步骤进入时自动扫描
- 接收端"寻找旧电脑"（DHCP）移入步骤 1；步骤 3 简化为验证码输入
- 校验并发提升至 32 线程；`calc_allocation_migration.py` 纯 Python 实现分配单元迁移估算
- 导出区域状态表格 + 日志弹窗；修复 PowerShell CSV 中文乱码

### 2026-07-31
- 集成系统配置导入导出（`config_transfer.py`），总步骤 6 → 7
- PyInstaller `--noupx` 修复 bootstrap.ttf 解压失败（exe 22.2MB）
- 接收端 UI 全面改造 + 独立校验页面
- 文件夹修改时间保留、只拷贝当前登录用户、D 盘双分区模式
- 传输速度实时显示；关闭窗口线程彻底退出（`os._exit` 兜底）
- DHCP IP 获取提速（单网卡 API + 事件驱动，30s+ → 3~5s）

### 2026-07-30
- 横板向导式 UI 重构（ttkbootstrap，1280×700 低分辨率适配），5 步向导
- 从 Git 恢复全部源码

### 2026-07-29
- 防火墙根因确认与修复（`netsh` 放行 TCP 9999）
- 移除接收端自设 IP（依赖 APIPA /16 直连，修复 TLS 握手失败）
- 性能优化：服务端缓冲写 1MB + TCP_NODELAY + 2MB 接收窗口 + 4MB 分块（1GB 约 3 分钟 → 大幅提速）
- 服务端线程数上限（信号量 16）修复 WinError 10054/10060
- 传输期间阻止系统休眠/锁屏；异常退出清理后台进程
- 修复 Windows 路径拼接 bug（`.rstrip("\\")` 导致相对盘符路径，文件写入当前工作目录）
- 同名文件冲突对话框（保留已有 / 覆盖重新下载）

### 2026-07-28
- 全项目 Bug 审查：修复致命接口脱节（验证码/证书未透传）、`_send_json` 缺 Content-Length、4 元组解包错误、明文重试等 P0 问题
- 分区自动映射修复（`VOLUME_DISK_EXTENTS` 结构偏移），按物理分区顺序映射（兼容 GPT/MBR）
- 验证码醒目 UI + 手动 IP 直连 + WinPE 运行环境切换

### 2026-07-27
- 新增验证码鉴权 + HTTPS 自签名证书（`tls_utils.py`）
- 文件修改时间保留（mtime 透传）
- 下载前询问是否覆盖已存在文件；传输支持暂停/继续/取消
- 移除 hover Tooltip，改为"使用说明 (README)"按钮弹窗

### 2026-07-26
- 修复 MBR 分区自动映射失败（`PARTITION_INFORMATION_EX` 偏移错误）

### 2026-07-10
- GTMC_User_Profiles 路径校验修复（CSV 内字符串替换）
- 多线程进度显示重构（共享字典 + 主线程定时刷新，防竞态）

### 2026-06-16 / 06-17
- 嵌入式 Python + tkinter 组件本地化部署（无需联网安装）
- 网络架构定稿：目标端 DHCP，源端 HTTP 服务器；文件传输过滤规则初版

---

## 注意事项与已知限制

- **客户端不校验自签名证书**：SSL 仅防被动窃听，不防中间人；安全性由随机验证码保证（如需防 MITM 需改为校验证书）
- **源端需放行防火墙 TCP 9999**：程序会自动 `netsh` 添加放行规则；非管理员时依赖 Windows 安全中心弹窗手动允许
- **运行环境**：目标端为 Windows PE（源端可普通 Windows）；PE 下无 WMI/无 pip，程序尽量只用标准库
- **盘符映射**：源端 D/E/F 与目标端盘符可能不同，需在"高级选项"中手动指定（自动按物理分区顺序预填）
- **跳过清单**：被占用/无权限读取的文件会记入日志与校验报告，不视为传输错误
- 嵌入式 Python 目录（约 99MB）不应提交到 Git；CI 使用 conda 环境构建

---

## 安全说明

- 传输全程 HTTPS 加密；随机 4 位验证码在连接建立前鉴权，错误验证码请求返回 403
- 自签名证书由程序首次运行时生成并缓存于 `certs/`（或随 exe 打包）
- 校验报告与配置 ZIP 上传至 Profile 服务器（`http://ipcheck.gtmcl.com:3000/api/upload`）
