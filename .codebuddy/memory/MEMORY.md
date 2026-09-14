# 项目记忆

## 项目概述
- 项目名：磁盘拷贝工具
- 运行环境（2026-09-10 变更）：**目标环境为正常 Windows，不再考虑 WinPE**，随包分发嵌入式 Python 3.13.14；代码内仍保留 PE 兼容分支（`winpe_var` 运行环境单选 / 磁盘映射面板），但默认走"正常系统"模式（盘符一一对应）。用户 2026-09-10 明确：**代码暂不改动**，此仅为后续开发方向
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
- **不再以 WinPE 为约束（2026-09-10）**：正常 Windows 下有 PowerShell / WMI / Print Spooler / 完整用户配置文件，可放心调用；原"PE 无 WMI、无 pip，尽量只用标准库"仅作历史背景。现存 PE 专用分支清单见 2026-09-10.md
- 新增依赖 cryptography（已 pip 安装进 python-3.13.14-embed-amd64，PyInstaller 需 --hidden-import cryptography --collect-data cryptography）
- 盘符映射是核心难点，需用户手动指定
- 组网方式（2026-07-28 核实代码更正）：目标设备设静态 IP 169.254.100.1 并运行内置 DHCP 服务器，源设备通过 DHCP 获得 169.254.100.2；传输端口为 9999（不是 443/80）。**2026-09-10 用户确认：改为正常 Windows 后组网方式不变，仍是网线直连，保留 169.254 + 内置 DHCP，不改成局域网/交换机发现**
- 网卡全自动检测（2026-08-26 最终形态）：发送端 HTTP Server bind 0.0.0.0；接收端 DHCP 在所有有线网卡(Type=6)上广播 OFFER/ACK（多 send socket）。步骤 1"高级选项"中的手动网卡下拉框已彻底移除（含 `tk_select_box_mqfzkd6x`、`tk_label_nic_detail`、`_on_nic_selected`、`_get_adapter_desc`、`_manual_nic` 字段）；`_check_button_state` 不再依赖网卡选择（修复了发送端开始传输按钮一直被禁用的问题）；所有需要 adapter_desc 的地方走 `_get_adapter_desc_from_auto()`
- 所有传输端点 (/ping /list /get /batch_get /filelist) 必须携带 ?pwd=<验证码>，否则 403
- 边传边校验（2026-08-25）：传输前先下载 FullFilelist_DEF.csv（/filelist 端点）→ 每个文件下载完成后入队，由独立线程做"存在+大小"轻量校验（不做 MD5）→ 校验阶段通过 pre_verified.txt 确认清单增量跳过已确认文件，避免全量拷贝后二次读盘校验耗时过长
- 客户端不校验自签名证书 (CERT_NONE)，安全性由随机验证码保证；如需防 MITM 需改为校验证书
- **tkinter 线程约束（2026-08-26 实证）**：本嵌入式 Python 3.13.14 的 tkinter 禁止后台线程调用任何 tk 接口——连 `self.ui.after(...)`、`StringVar.get()` 都会抛 `RuntimeError: main thread is not in main loop`。后台线程更新 UI 必须走"线程安全队列 put + 主线程 `after` 轮询 drain"模式（参考 control.py `_verify_ui_q` + `_poll_verify_ui`），任何"后台线程直接调 after/config"的方案都会静默崩溃或死锁
- 校验流程（2026-08-26 重构）：校验页已移除，总步骤 7→6（0角色→1网卡→2磁盘→3连接→4传输→5导入配置[仅接收端,最后一步]）。传输完成 `_on_download_complete` → `_start_auto_verification()` 后台自动校验（增量确认+报告打包+自动上传），进度/日志经 UI 队列渲染到传输页。`_upload_verifier_report` 记录 `_verify_upload_thread` 供轮询判断存活
- 已知重大问题（2026-07-28 审查，尚未修复）：control.py 未向 FileServer/download_files/scan_source_device 传 auth_code 与 cert_paths，主流程实际跑不通；_send_json 缺 Content-Length 导致 HTTP/1.1 keep-alive 下 JSON 端点挂起；_download_batch except 分支对 4 元组按 3 值解包；verifier 重试下载走明文 http 且无 pwd；run_verification 未传 server_ip/gtmc_new_name。详见 2026-07-28.md
- **文件名标点/编码铁律（2026-09-09 实证）**：① 中文用户文件名大量含全角逗号「，」，任何"全角→半角"的兼容替换都必须以磁盘存在性验证为前提，禁止无条件字符串替换；② `FullFilelist_DEF.csv` 由 `systemconfig/calc_allocation_migration.py` 生成（csv.writer + utf-8-sig，标准引号转义），采集侧无字符集问题；③ 服务端 `/batch_get` 对读不到的文件返回 `data=b""` 空占位，所有消费方**必须**先判 `data_len==0 且 expected_size!=0 → 不落盘`，否则目标盘会出现 0KB 垃圾文件（主传输 `_download_batch` 有保护，verifier 重试下载原先没有，已修）
- 已修复（2026-07-29）：Windows 路径拼接 bug — .rstrip("\\") 后再 os.path.join 得到的是相对盘符路径（如 "J:foo" 而非 "J:\\foo"），导致文件写入当前工作目录。修复了 file_transfer.py:990 和 verifier.py:216/219/303 四处，rstrip 后补回 "\\"。
- **DHCP 发现加速铁律（2026-09-10 实证）**：源端是 DHCP 客户端（`control.py: _setup_source_network`），接收端是 DHCP 服务器（`dhcp_server.py: MiniDHCPServer`，绑 0.0.0.0:67，给源端分配 169.254.100.2）。① 源端每轮重试**必须 `IpReleaseAddress` + `IpRenewAddress`**——只 renew 会让 Windows DHCP 客户端停在指数退避（1/2/4/…/64s）里，表现为"获取 IP 超级慢"；② `IpRenewAddress` 是**阻塞**调用（无服务器时可阻塞数十秒），绝不能放在观察循环里，必须丢后台线程；③ 源端重试窗口要给足（现 300s）且在 `_transferring` 时退出，否则用户晚点才启动接收端 DHCP 就永远发现不了；④ 服务器对客户端 option 50 请求的**异网段地址必须回 DHCPNAK**（不能 ACK），否则客户端拿着错网段 IP 长期不再重新请求（"拿到 IP 却连不上"）；⑤ 应答要重复发送，目标端口用**客户端实际源端口**（回退 68）；⑥ 接收端只有网卡**完全没有 IP** 时才等 APIPA，有 IP 就立即启动 DHCP。

## 软件下载附件小工具（`软件下载附件/`，独立模块，非主程序）
- `Boot_Software_Install.cmd` 用随包 python 启 `install.py`（HTTP :8888），`index.html` 读 `Installer_Packages.xlsx`（由 `scan_installers.py` 扫描共享盘生成），勾选后 POST 回 install.py 执行"下载→安装"。
- **架构（用户 2026-09-10 明确）**：**下载服务由当前登录用户运行，安装步骤以 `.\lingtong`（本地管理员账户）身份运行**，密码在 `password.pem`（base64）。两者身份不同是设计，不是缺陷。
- **禁止弹 UAC、禁止自提权（用户 2026-09-10 明确要求）**：`Boot_Software_Install.cmd` **不得**做 `-Verb RunAs` 自提权，也**不得**走计划任务方案。原因：① 弹确认框打断批量安装，与"后台静默"目标冲突；② 用 `net session` 判断提权在 Server 服务未启动时即使已提权也返回失败 → **无限 RunAs 重启、程序反复运行**（用户实测踩到，已回滚）。曾错误加入这两套方案，2026-09-10 已全部删除。
- 安装启动只用 **`CreateProcessWithLogonW`**（`install.py: _launch_with_createprocesswithlogonw`，ctypes 直调 advapi32）：后台静默、无中间进程、不弹任何窗、能取到原始 Win32 错误码；失败才回退 PowerShell `Start-Process -Credential`（同一 API，仅作对照）。`LOGON_WITH_PROFILE=1` 会加载 lingtong 的用户配置；进程创建在调用方会话内，安装界面可见。
- **740 的根因 = UAC 令牌过滤（不是"没提权"）**：`CreateProcessWithLogonW` 的执行链是 `调用方 -> seclogon(Secondary Logon) -> LogonUser -> CreateProcessAsUser`，UAC 开启时它**只产生被过滤的普通令牌(Medium IL)**，目标安装包清单声明 `requireAdministrator`（QQ 音乐等绝大多数）时必然失败并返回 **740 ERROR_ELEVATION_REQUIRED**（中文报错"请求的操作需要提升"）。**调用方自己是不是管理员完全不影响结果**（2026-09-10 实测：下载服务是普通用户，`Start-Process -Credential` 直接回 740）。**2026-09-10 已实测确认**：目标机上 lingtong（RID 1001）令牌里 Administrators 组状态为 `deny_only` ⇒ **lingtong 确实是管理员，账户组成员身份没问题**，740 完全由 UAC 过滤造成。之后不要再怀疑"lingtong 是不是没在管理员组"。Start-Process -Credential 与 ctypes CreateProcessWithLogonW 两条路实测**都是 740**（同一 API，换顺序/换实现都没用）。唯一能在**不弹窗**前提下解决的办法是**预先配置目标机**，二选一：① `EnableLUA=0`（关掉 UAC）；② lingtong 就是**内置 Administrator(RID 500)** 且 `FilterAdministratorToken=0`（默认值）。两者都需目标机管理员权限 + 重启，运行时无解。
- **1783 RPC_X_BAD_STUB_DATA（2026-09-10 修）**：`CreateProcessWithLogonW` 报 1783 的两个已知诱因——① `lpEnvironment` 传了 `CreateEnvironmentBlock` 的块而不是 NULL（SO 27272919 的结论）；② `LOGON_WITH_PROFILE` 加载目标用户配置失败。修法：`lpUsername`/`lpDomain` **拆开传**（`'.\lingtong'` -> domain=`'.'`、user=`'lingtong'`；整体传 `'.\lingtong'` 时 seclogon 侧解析失败），并先用 `LOGON_WITH_PROFILE` 调一次、失败则退化为「不带 profile + 显式 `lpDesktop="winsta0\default"`」重试（仅对 1783/87/123 这类参数类错误重试）。
- **令牌探测（`install.py: _report_lingtong_token`）**：用 `LogonUser` + `GetTokenInformation(TokenElevation / TokenIntegrityLevel / **TokenGroups**)` + `LookupAccountNameW` 判定令牌状态，把三种都报 740 的情况区分开：① `admin_group='absent'`（令牌里没有 Administrators 组）= lingtong **根本不是管理员**，**连关 UAC 也没用**，得先 `net localgroup Administrators lingtong /add`；② `admin_group='deny_only'`（属性含 0x10 `SE_GROUP_USE_FOR_DENY_ONLY`）= 是管理员但**被 UAC 过滤**；③ `admin_group='enabled'` + High IL = 完整令牌，能装。RID 用 `GetSidSubAuthority` 取最后一个子授权判断是否为 500（内置 Administrator；RID 1001 之类都是后来新建的普通账户）。
- **`CheckTokenMembership` API 陷阱（2026-09-10 踩坑）**：它要求传入 **模拟令牌(impersonation token)**，而 `LogonUser` 返回的是**主令牌**，直接调用会失败（现象是判定值永远打印 `None`，静默失效）。判定"令牌里某个组的状态"必须走 `GetTokenInformation(TokenGroups)` 自己解析 `TOKEN_GROUPS` + `SID_AND_ATTRIBUTES` 数组，再用 `EqualSid` 比对。**验证手法**：`CreateRestrictedToken(hToken, 0, 1, &SID_AND_ATTRIBUTES{Administrators}, ...)` 能把该组标成 deny-only，正好复现 UAC 过滤令牌，用它做单元验证（实测 0xF=enabled / 0x10=deny_only）。
- **ctypes 命名铁律**：`GetTokenInformation` 的信息类常量（`_TOKEN_GROUPS=2` 等）不要和同名 `ctypes.Structure` 用同一个名字——Python 里后定义的类会**覆盖**前面的常量，调用时报 `'_ctypes.PyCStructType' object cannot be interpreted as an integer`。结构体加 `_STRUCT` 后缀。
- **架构重大变更（2026-09-10，用户主动要求"回退方案"）**：用户接受了"**运行 `Boot_Software_Install.cmd` 时弹一次 UAC，之后所有命令都带提权授权**"。引导脚本第一步用 `whoami /groups | findstr /C:"S-1-16-12288"` 判断令牌完整性级别（High = 已提权），未提权则 `powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"` 自提权重启，然后 `exit /b`。**绝对不要用 `net session` 判断提权**（Server 服务未启动时即使已提权也返回失败 → 无限 RunAs 重启，用户实测踩过）。实测：High→errorlevel 0、Medium→errorlevel 1，判别正确；RunAs 分支可用。
- **提权后的身份变化（2026-09-10 实测，最省事的一条路）**：普通账户（Users/Power Users）启动 `Boot_Software_Install.cmd` 时，UAC 会要求**输入管理员账户密码**；技术人员填入 lingtong → **提权后的进程身份就是 lingtong**（实测日志 `当前用户: QDNB5381\lingtong` + `本进程是否已提升: True`）。于是有了最简单可靠的一条：**直接用本进程自己的令牌 `subprocess.Popen` 启动安装包**（已提升的令牌创建"要求管理员"的子进程不会被拦，740 只发生在令牌未提升时），身份也正好是设计要求的 lingtong。
  **风险**：此时下载步骤也以 lingtong（本地账户）身份运行，`\\qitv1534\共享文件` 这类域共享可能因身份不足而访问失败 —— 必须盯住下载环节。
- **提权并不解决 740（关键认知）**：`CreateProcessWithLogonW` 无论调用方是不是管理员，都固定走交互式登录、都过滤令牌。提权的价值在于**解锁另外几条机制**（优先级由 `run_installer_as_lingtong` 里的 0a→0b→0c 实现）：
  **0a** 已提权且账户就是 lingtong → `_launch_direct_elevated` 直接用本进程令牌启动（身份最正确、最不易失败）；
  **0b** 已提权但身份不是 lingtong → `_launch_with_batch_token`（Batch 登录 + `CreateProcessWithTokenW`）；
  **0c** 兜底 → 仍用本进程令牌直启，此时**安装身份是当前账户而不是 lingtong**（HKCU 等会落到那个账户下），日志会明确提示。
  ① **方式 0：`LogonUser(LOGON32_LOGON_BATCH)` + `CreateProcessWithTokenW`**（`install.py: _launch_with_batch_token`）—— Batch 登录**不过滤**令牌（Task Scheduler 就是靠它拿完整令牌），而 `CreateProcessWithTokenW` 需要 `SeImpersonatePrivilege`，只有提权令牌才有。这是唯一"以 lingtong 身份 + 已提升"启动安装程序的机制。前提：lingtong 需要 **SeBatchLogonRight**（否则 LogonUser 返回 1385）。启动前会打印 Batch 令牌的 `已提升/完整性级别` 作为证据。
  ② **方式 3 提权分支**：本进程已提权时不再绕 lingtong 侧脚本（那条路依赖 .cmd，会被 SRP 拦），改为**本进程直接 subprocess 调 schtasks**，输出与退出码全部可见，且任务动作 `/tr` **直接指向安装包本身**（不依赖脚本）。动作不是脚本 → 没有 sentinel/自删，改为 10 分钟后由本进程删除任务。
- **方式三 计划任务 `/RL HIGHEST`（2026-09-10 定稿，`install.py: _launch_via_scheduled_task`）**：前两种方式都因 UAC 令牌过滤返回 740 时的**唯一出路**，但**必须是两段式**：
  ① `_cpwl_once` 用 `CreateProcessWithLogonW` 起一个 **lingtong 身份的 cmd.exe**（这一步**必然成功**——cmd.exe/schtasks.exe 的清单都不要求管理员，seclogon 能正常创建，不会有 740）；
  ② 由这个 lingtong 进程去跑 `schtasks /create ... /ru <COMPUTERNAME>\lingtong /rp <pwd> /rl HIGHEST /f && schtasks /run`。lingtong 是"被 UAC 过滤的管理员(deny_only)"，**这正是 Task Scheduler 唯一认的身份**；Task Scheduler 随后走 Batch 登录 + 最高权限，交出**未过滤的完整令牌**。
  全程不弹 UAC、不改任何 UAC 设置、不需要在目标机上做管理员操作。
  实现要点：(1) `/tr` 不要直接塞安装包路径（引号/中文多层转义必错），先生成一次性 `.cmd` 启动脚本；(2) 脚本与 sentinel/log **都放 `F:/Appl/Installers`（INSTALL_TEMP_DIR）**，不能放 `%TEMP%`——用户名带空格会让 `/tr` 被拆开，且被过滤的管理员写不进别人的用户配置文件目录；`.cmd` 按 **mbcs** 写盘（中文名不乱码）；(3) 脚本内 `echo ran ><sentinel>` → `start ""` 让安装程序**脱离父进程** → `schtasks /delete /f /tn <自身>` 自删；(4) 判定成功用**轮询 sentinel**（不能 sleep 猜，Task Scheduler 启动延迟可达数十秒），120s 未出现则请 lingtong 删掉任务（当前账户没权限删别人的任务）；(5) lingtong 侧 schtasks 输出重定向到 `lingtong_elevate.log` 才能看到"拒绝访问"之类的真因。
  **未验证的前提**：目标机的**组策略**是否允许普通管理员注册最高权限任务（部分加固环境会禁）；以及 lingtong 身份下发起的 schtasks 是否真被放行——只能靠目标机日志确认。
  **两段式的两个致命细节（2026-09-10 第二轮实测踩到，"进程起来了但日志文件根本不存在"）**：
  ① **必须显式指定 `lpCurrentDirectory`**（`_cpwl_once(..., cwd=通道目录)`）。传 NULL 时子进程继承**调用方的工作目录**，而调用方目录常在某个用户的 profile 下（`D:\...\<用户名>\Desktop\...`），被 UAC 过滤的 lingtong 进不去 → **cmd.exe 直接退出，什么都不做**。表现就是"`_cpwl_once` 返回 已启动(PID=…)，但 lingtong 侧一条输出都没有"。
  ② **lingtong 要写的目录必须显式授权**。被过滤的令牌里 Administrators 是 deny-only，那条 ACE 不生效，只有 Users 身份算数。所以要开一个专用子目录 `F:/Appl/Installers/lingtong_channel`，用 `icacls <dir> /grant *S-1-5-32-545:(OI)(CI)M` 给 Users Modify（`_ensure_channel_dir()`，进程内只做一次）。只授权这个子目录，安装包本体仍在上层，不额外扩大暴露面。
  ③ **不要用一条 `cmd /c (A && B) > log 2>&1` 一把梭**：一旦哪一步死了，日志文件都不会生成，等于零线索。改成先生成 `lingtong_elevate.cmd`（由 lingtong 身份执行），里面**逐步** `>>log` 写 `[stage] entered user=%USERNAME% cwd=%CD%` / `create rc=%ERRORLEVEL%` / `run rc=%ERRORLEVEL%`，失败时能直接看出卡在哪一步。失败后还要用 `schtasks /query` 从本进程复核任务到底建出来没有。
  **本地验证手法**：`cmd /c` + 重定向的语法、分步日志链路、通道目录授权，都可以在本机用"假身份（不存在的用户名）"实跑 `lingtong_elevate.cmd` 来验证——schtasks 会报 "No mapping between account names and security IDs"，正好证明日志链路是通的。唯一在本机无法复现的是 deny-only 令牌下 Task Scheduler 是否放行。
  **副作用（2026-09-10 实测踩到）**：带 `/rp` 的任务是 "Run whether user is logged on or not" → 进程落 **session 0，安装界面不可见**。第一次实测就卡在这里：日志显示"以 lingtong 身份启动成功、有 PID"，但等很久什么都没发生 —— 因为 `.exe` 的 `install_args` 是空列表，QQ.exe 弹了一个**看不见的对话框**在等人点，等于什么都没装。**这条链路能跑通的前提是安装包必须带静默开关**（NSIS 系 `/S`；Inno Setup 系 `/SILENT` 或 `/VERYSILENT`；MSI 用 `/qn`）。
  **界面可见性**：由于提权令牌只能来自 lingtong（唯一管理员），进程会话只能是 session 0（`/rp`）或 lingtong 自己的会话（`/it`），**无法出现在技术员所在的 console 会话**。所以要么静默安装，要么让技术员用 lingtong 账户登录。
- **静默参数与安装结果判据（2026-09-10 新增）**：`install.py` 新增 `SILENT_ARGS` 内置表（键=安装包文件名小写）+ 可选的脚本同目录 `silent_args.json` 覆盖（格式 `{"qq.exe": ["/S"]}`）+ `EXE_DEFAULT_SILENT_ARGS = ['/S']` 兜底；POST 里的 `packages[].args` 优先级最高。`_silent_args_for(filename)` 返回 (参数, 来源)，日志会打印来源（内置表 / silent_args.json / 默认猜测）。另外启动脚本改成 `start /wait` + `echo %DATE% %TIME% exitcode=%ERRORLEVEL% >> F:/Appl/Installers/lingtong_install_result.txt`：**session 0 里看不到任何界面和输出，只能靠退出码判断装成功没有**（0 = 安装包自报成功；文件里没记录 = 还在跑或卡住了）。
- **普通用户下 `net` 命令不可用**：`net user <name>` / `net localgroup Administrators` 在普通用户下返回 **WinError 5 拒绝访问**（缺 SAM 读权限），拿它做诊断只会一直刷报错。账号存在性 / 组成员判断一律走 ctypes。
- **子进程输出解码铁律**：中文 Windows 下 PowerShell / net / schtasks 的输出是 GBK(cp936)。用 `encoding='utf-8', errors='ignore'` 读会把中文整段吞掉，只剩 `ڳ´޷д: ĲҪ` 这类残渣（原文是"…无法运行此命令: 请求的操作需要提升。"）。必须按 `utf-8 → gbk → mbcs` 依次尝试（见 `install.py: _decode_console`）。
- **"程序闪退"的真相 = `exit_server()` 自我清理，不是崩溃（2026-09-10 查清）**：`InstallHandler.exit_server()` 依次做 ① 忘记 GTMC-VIP WiFi ② 删 `password.pem` ③ **删掉程序文件夹内全部文件（含子目录、含随包 Python）** ④ `os._exit(0)`。`os._exit` 不刷新输出、不走清理流程，而窗口是 `Boot_Software_Install.cmd` 用 `start` 拉起来的 —— 进程一退窗口立刻消失，于是"文字一闪就没了"，极易被误判为崩溃。**触发点只有两个**：前端发来 `exitServer=true`，或勾选了 `exitAfterInstall=true`（见 `do_POST` 里 `data.get('exitServer'/'exitAfterInstall', False)`）。**关键排查动作：看 `软件下载附件` 目录是不是被清空了、`password.pem` 还在不在**——在就说明是它，需要重新拷贝整个目录才能继续测。
- **"闪退"加固（2026-09-10）**：① `exit_server` 先把"触发原因+时间+要删的目录"写进 **程序文件夹之外**的 `install_server_last_exit.log`（落在上级目录，否则它把自己的日志一起删了，事后零线索）；② 删除前打印 10 秒倒计时，误触发可按 Ctrl+C 中止；③ `__main__` 用 try/except 包住 `run_server()`，致命错误走 `_report_fatal()` 打印 traceback 并**停住等回车**（`_hold_window()`），窗口再也不会无声消失；④ 新增 `_port_in_use()` 双实例检测。
- **双实例陷阱（2026-09-10 实测复现）**：Windows 的 `SO_REUSEADDR` 允许**两个进程同时绑 8888**（第二个实例照样能启动，bind 不报错），于是浏览器请求可能被**旧实例**接走，看到的行为不是最新代码 —— 排查时是极大干扰源，必须先排除。现在 `run_server()` 启动前用 `_port_in_use()`（主动连 127.0.0.1:8888）检测，已有实例就明确提示并退出，不再静默竞争端口。
- **`Boot_Software_Install.cmd` 加固（2026-09-10，已本地实测 200）**：① **`cd /d "%~dp0"` 必须加** —— `do_GET` 是按**进程当前目录**找 `index.html`/`xlsx` 的，从别的目录调用（快捷方式等）会直接 404；② python 输出重定向到 `install_server_console.log`；③ 启动前用 `if not exist` 检查随包 Python 与 `install.py` 是否存在（上一轮 `exit_server` 自我清理可能已把目录删空，那种情况 python 只会报"找不到文件"然后秒退）；④ python 退出后 `pause` 留住窗口，不再"闪一下就没了"；⑤ **本文件必须存成 ANSI(GBK) + CRLF** —— 存成 UTF-8 时 cmd 按 GBK 解出来就是 `姝ｅ湪鍚姩鏈湴鏈嶅姟鍣?` 这种乱码。
- **控制台 stdout 会让服务卡死（2026-09-10 实测复现，重要）**：同一个 `install.py`，**stdout 是文件时 HTTP 正常（200）**；**stdout 是控制台时**（`start "Installer Server" cmd /k "python install.py"`）会**LISTENING 但对请求一直不响应**（TCP 三次握手成功、`recv` 超时）。所以引导脚本一律把 python 输出重定向到日志文件，不要依赖控制台实时输出。
- **lingtong 通道自检（2026-09-10 新增，`install.py: _channel_selftest`）**：现象是"lingtong 的 cmd.exe 起来了（有 PID），但提权脚本连第一行都没执行、计划任务也没建"。**必须把三件事分开验，否则每轮只能猜**：
  T1 起进程+写目录（`cmd /c echo > 文件`，**不涉及脚本文件**）；
  T2 **.cmd 脚本能不能执行**（写个探针 .cmd 由它自己落文件）；
  T3 以 lingtong 身份**直接调 `schtasks.exe`**（不套脚本）能否建出 `/RL HIGHEST` 任务。
  三项都用"文件有没有落下"当证据，日志直接给结论：`T1 通 + T2 不通` ⇒ **.cmd 被软件限制策略(SRP)/AppLocker 拦**（这不是权限问题，加权限没用）；`T1/T2 通 + T3 不通` ⇒ Task Scheduler 不接受该账户注册最高权限任务。
- **SRP/AppLocker 的重要线索（2026-09-10）**：目标机 方式 1 报 `管理员用策略规则 %2 限制了对 %1 的访问`（Win32 **1260** `ERROR_ACCESS_DISABLED_BY_POLICY`）⇒ 该机器**有软件限制策略在拦执行**，且拦的正是 `F:\Appl\Installers` 下的东西。若确认 `.exe` 也被拦，则"下载到 F:\ 再执行"的整套设计都要换目录。排查命令：`reg query "HKLM\SOFTWARE\Policies\Microsoft\Windows\Safer\CodeIdentifiers" /s`、`reg query "HKLM\SOFTWARE\Policies\Microsoft\Windows\Safer\CodeIdentifiers\0\Paths" /s`、`Get-AppLockerPolicy -Effective -Xml`。
- **不依赖脚本的备用路径（`install.py: _launch_task_without_script`）**：当自检判定 `.cmd` 被拦（t2 为假）时自动启用——直接用 `schtasks.exe` 作为 lingtong 的命令行（**不经过任何脚本文件**），计划任务动作 `/tr` 也直接指向安装包本身。代价：没有 sentinel、没有退出码落盘、拿不到任何安装过程输出，只能确认"任务已建出并触发"；任务无法自删，改为安排 10 分钟后请 lingtong 删除。
- **`_cpwl_run_wait`（新增）**：`CreateProcessWithLogonW` 启动后 `WaitForSingleObject` + `GetExitCodeProcess`。像"直接调 schtasks.exe"这种没有脚本/重定向的调用，**退出码是唯一线索**。
- 排查开关：`install.py` 顶部 `DEBUG = True`，日志同时进控制台和 `install_debug.log`（每次启动清空）。启动横幅打印 UAC 注册表（EnableLUA / FilterAdministratorToken / ConsentPromptBehaviorAdmin 等）；安装前依次打印 ① **令牌探测结论**（SID/RID、完整性级别、TokenElevation、是否有效属于 Administrators，并直接给出"A. 关 UAC / B. 内置 Administrator"的解决指引）② 目标程序存在性与大小 ③ `query user` 会话列表。`check_admin` 响应含 `isAdmin`/`isElevated`/`uac`——`isAdmin` 只表示"在 Administrators 组内"，与"本进程是否已提升"是两回事（UAC 开启时二者会不一致）。
