@echo off
@REM 启动本地服务器
@echo.
@echo 正在启动本地服务器...

@REM ==== 第一步：自提权（整个流程只弹这一次 UAC）====
@REM 为什么要提权：安装阶段需要一个**完整（未过滤）**的管理员令牌。
@REM   · 已提升的令牌可以直接启动"要求管理员"的安装包（740 只发生在令牌未提升时）；
@REM   · 还能用 CreateProcessWithTokenW（它需要 SeImpersonatePrivilege，只有提权令牌才有）。
@REM 注意：普通账户触发 UAC 时会要求输入**管理员账户密码** —— 填 lingtong 的，
@REM       提权出来的进程身份就是 lingtong，正好是安装需要的身份。
@REM 判断"是否已提权"用**令牌完整性级别**(S-1-16-12288 = High)。
@REM 千万不要用 `net session`：Server 服务未启动时即使已提权也会返回失败，
@REM 会导致无限 RunAs 重启、程序反复运行（这个坑踩过，务必不要改回去）。
whoami /groups | findstr /C:"S-1-16-12288" >nul 2>&1
if errorlevel 1 (
    @echo 需要管理员权限，正在请求 UAC 授权（整个流程只弹这一次）...
    @echo 提示: 普通账户请填 lingtong 的账户密码，提权后身份就是 lingtong。
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    @echo.
    @echo 如果 UAC 被取消，本窗口就此结束；需要时请重新运行本文件。
    exit /b
)
@echo 已获得管理员权限。

@REM ==== 第二步：固定工作目录 ====
@REM do_GET 是按进程当前目录找 index.html / xlsx 的；从别处调用（快捷方式、
@REM 提权后的新进程默认目录是 System32）会直接 404。
cd /d "%~dp0"
set "PY=%~dp0python-3.12.4-embed-amd64\python-3.12.4-embed-amd64\python.exe"
set "APP=%~dp0install.py"
set "LOG=%~dp0install_server_console.log"
@REM 输出重定向到文件时，Python 默认**块缓冲**，日志要等到进程退出才落盘
@REM （上一轮 Ctrl+C 就把整个 POST 的输出丢了）。-u 关掉缓冲，同时固定 UTF-8。
set "PYTHONIOENCODING=utf-8"

@REM ==== 第三步：启动前检查文件是否还在 ====
@REM 上一次 exit_server 自我清理会把程序目录删空，那种情况下 python 只会报
@REM "找不到文件"然后秒退，看不到任何原因。
if not exist "%PY%" (
    @echo [错误] 找不到随包 Python:
    @echo         %PY%
    @echo         多半是上一次 exit_server 自我清理把程序目录删空了，请重新拷贝整个目录。
    pause
    exit /b 1
)
if not exist "%APP%" (
    @echo [错误] 找不到 install.py:
    @echo         %APP%
    @echo         多半是上一次 exit_server 自我清理把程序目录删空了，请重新拷贝整个目录。
    pause
    exit /b 1
)

@REM ==== 第四步：启动服务 ====
@REM -u 无缓冲；输出同时落日志（窗口关掉也不丢线索）。程序退出后 pause 留住窗口。
if not exist "%LOG%" type nul > "%LOG%"
@echo 服务输出日志: %LOG%
start "Installer Server" cmd /c ""%PY%" -u "%APP%" >> "%LOG%" 2>&1 & echo. & echo [提示] 服务已退出，按任意键关闭本窗口。日志: %LOG% & pause"

@REM 再开一个窗口实时跟随日志：主窗口的输出被重定向了，看不到进度
start "Installer Server 日志" powershell -NoProfile -Command "Get-Content -Wait -Encoding UTF8 '%LOG%'"

@REM ==== 第五步：打开前端页面 ====
@echo.
@echo 正在打开软件选择页面...
start http://localhost:8888/
rem === Installed Programs ===
