# 项目记忆

> 2026-09-15 精简重写（原文件过大被截断）。历史细节见 `.codebuddy/memory/YYYY-MM-DD.md` 日记。

## 项目概述
- 项目名：磁盘拷贝工具。源设备（旧机）经网线直连把 D/E/F 盘数据拷到目标设备（新机）并校验。
- 目标环境（2026-09-10 变更）：**正常 Windows，不再考虑 WinPE**；随包分发嵌入式 Python 3.13.14。代码内仍保留 PE 兼容分支（`winpe_var` 单选 / 磁盘映射面板），默认走"正常系统"模式（盘符一一对应）。
- GUI：Tkinter（Tkinter 布局助手生成）。入口 `main.py` → 布局 `ui.py` → 业务 `control.py`；模块：`nic_scanner` / `ip_config` / `disk_scanner` / `file_transfer` / `verifier` / `logger` / `tls_utils` / `dhcp_server` / `config_transfer`。

## 关键架构与约束
- **组网**：网线直连。接收端跑内置 DHCP（`dhcp_server.py`，绑 0.0.0.0:67）给源端分配 169.254.100.2；源端是 DHCP 客户端。传输端口 **9999**（不是 443/80）。2026-09-10 用户确认：改正常 Windows 后组网方式不变，不改成局域网/交换机发现。
- **鉴权**：随机 4 位验证码（`tls_utils.generate_auth_code`）。`/ping /list /get /batch_get /filelist` 必须带 `?pwd=` 验证码，否则 403。客户端 `CERT_NONE` 不校验证书；自签证书（cryptography，SAN=IP）缓存于 `certs/`。
- **步骤**：6 步（0角色→1高级设置→2连接→3传输→4[校验已并入传输页]→5导入配置[仅接收端]）。
- **tkinter 线程铁律**：本嵌入式 Python 禁止后台线程调用任何 tk 接口（连 `after`、`StringVar.get()` 都抛 `RuntimeError: main thread is not in main loop`）。后台线程更新 UI 必须走"队列 put + 主线程 `after` 轮询 drain"（参考 `control.py _verify_ui_q` / `_poll_verify_ui`）。
- **文件名标点/编码铁律**：① 中文文件名大量含全角逗号「，」，全角→半角替换必须以磁盘存在性验证为前提，禁止无条件替换；② `FullFilelist_DEF.csv` 由 `systemconfig/calc_allocation_migration.py` 生成（csv.writer + utf-8-sig）；③ `/batch_get` 对读不到的文件返回空占位 data，消费方必须判"长度为 0 且期望大小非 0 则不落盘"，否则目标盘出现 0KB 垃圾文件。
- **Windows 路径拼接**：对盘符做 rstrip 去掉尾部反斜杠后再 `os.path.join` 会得到相对盘符路径（如 J:foo 而非 J 盘绝对路径），必须补回反斜杠。
- **DHCP 发现加速铁律**：① 源端每轮重试必须 `IpReleaseAddress` + `IpRenewAddress`（只 renew 会停在指数退避，表现为"获取 IP 超级慢"）；② `IpRenewAddress` 阻塞，必须丢后台线程；③ 源端重试窗口给足（现 300s）且在 `_transferring` 时退出；④ 服务器对 option 50 请求的异网段地址必须回 **DHCPNAK**（否则客户端拿错网段 IP 后长期不再请求）；⑤ 应答重复发送，目标端口用客户端实际源端口（回退 68）；⑥ 接收端只有网卡完全没有 IP 时才等 APIPA。
- 边传边校验：传输前下载 `FullFilelist_DEF.csv` → 每文件下载完成后入队做"存在+大小"轻量校验（不做 MD5）→ 校验阶段用 `pre_verified.txt` 增量跳过，避免全量二次读盘。

## UI 提示规则（2026-09-15 定）
- 传输页 step3 的"开始传输/开始接收"按钮是**隐藏**的（`go_step` 对 step 不等于 2 时一律 `hide_start_button()`），异常提示只能引导用户点左下角「< 上一步」返回后重试。**不要写"点击「重新启动传输」"**。
- 状态行 `tk_label_transfer_status` 与红色错误区 `show_transfer_error()` **必须讲同一件事**；`show_transfer_error(msg, status_text=...)` 支持传状态行文案（默认"请返回修改验证码"，仅适用于接收端验证码填错）。`_set_status` 必须写在 `show_transfer_error` **之后**，否则被覆盖。
- **网络中断时发送端必须隐藏验证码横幅**（`ui.hide_auth_code()`），否则出现"上方让输入验证码 / 下方说网络中断"的矛盾。`ui._auth_code_visible` 保证 `go_step(3)` 不会把已隐藏的横幅重新 pack 出来。
- 文案常量集中在 `control.py` 顶部：`NET_LOST_TITLE` / `NET_LOST_MSG_SRC` / `NET_LOST_MSG_TGT`、`AUTH_FAIL_TITLE` / `AUTH_FAIL_MSG_SRC` / `AUTH_FAIL_MSG_TGT`、`TRANSFER_INCOMPLETE_*`。
- 发送端判定中断后必须 `_transferring = False` 并 `after_cancel(_source_done_after)`，否则用户按提示返回后点"开始传输"会被"传输正在进行中"拦下。

## 软件下载附件小工具（`软件下载附件/`，独立模块，非主程序）
- `Boot_Software_Install.cmd`（**必须存 ANSI/GBK + CRLF**）用随包 python 启 `install.py`（HTTP :8888），`index.html` 读 `Installer_Packages.xlsx`（`scan_installers.py` 扫描共享盘生成），勾选后 POST 回 install.py 执行"下载→安装"。
- 架构：**下载服务由当前登录用户运行，安装步骤以本地管理员账户 lingtong 身份运行**，密码在 `password.pem`(base64)。引导脚本会弹一次 UAC 自提权（用 `whoami /groups` 找 `S-1-16-12288` 判 High IL，**绝不能用 `net session`**——Server 服务未启动时即使已提权也返回失败，会导致无限重启）。普通账户启动并输入 lingtong 密码后，**提权进程身份就是 lingtong**。
- **740 = UAC 令牌过滤，不是"没提权"**：`CreateProcessWithLogonW` 固定只给被过滤令牌(Medium IL)，安装包声明 requireAdministrator 时必然 740；调用方是不是管理员不影响。lingtong 实测 Administrators 组状态为 deny_only（确实是管理员）。不弹窗的唯一解是预先配置目标机：`EnableLUA=0`，或让 lingtong 就是内置 Administrator(RID 500)。
- 机制优先级：0a 已提权且身份即 lingtong → 直接用本进程令牌 subprocess 启动；0b 提权但身份不是 lingtong → `LogonUser(BATCH)` + `CreateProcessWithTokenW`（Batch 登录不过滤令牌，需 SeImpersonatePrivilege，lingtong 需 SeBatchLogonRight）；0c 兜底本进程令牌（身份不是 lingtong，日志会提示）。
- 方式三 计划任务 `/RL HIGHEST` 是 740 时的唯一出路，**必须两段式**：先 `CreateProcessWithLogonW` 起 lingtong 身份的 cmd（必然成功），再由它跑 `schtasks /create ... /rp /rl HIGHEST /f && schtasks /run`。细节：`lpCurrentDirectory` 必须显式指定（否则继承调用方 profile 目录，被过滤令牌进不去 → 静默退出）；lingtong 要写的目录需 `icacls` 给 Users Modify；脚本按 mbcs 写盘；逐步 `>>log` 记录 stage 便于定位；带 `/rp` 的任务落 session 0，**安装界面不可见，必须带静默开关**（NSIS `/S`、Inno `/VERYSILENT`、MSI `/qn`）。
- 其他踩坑：① 控制台 stdout 会让服务卡死（LISTENING 但不响应），输出必须重定向到日志文件；② 端口 8888 可被双实例同时绑定（SO_REUSEADDR），排查前先排除旧实例；③ 中文 Windows 子进程输出是 GBK，解码要按 utf-8→gbk→mbcs 依次尝试；④ "闪退"真相是 `exit_server()` 自我清理（删程序目录含随包 Python）后 `os._exit(0)`，看目录是否被清空即可确认；⑤ 普通用户下 `net user` / `net localgroup` 返回拒绝访问，账号判断一律走 ctypes。
- 令牌探测用 `LogonUser` + `GetTokenInformation(TokenGroups)` 自己解析（**`CheckTokenMembership` 要求模拟令牌，主令牌会静默失败**）；结构体名与信息类常量不能同名（后者会被类覆盖）。
