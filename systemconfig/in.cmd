@echo off
setlocal enabledelayedexpansion

if not defined APPL_ROOT set "APPL_ROOT=F:\Appl"
set "src=%APPL_ROOT%"

rem =============================================
rem === Auto-detect latest config from systemconfig.ini ===
rem =============================================
set "AUTO_CONFIG="
set "AUTO_TIME="
if exist "F:\systemconfig.ini" (
    for /f "usebackq tokens=2 delims==" %%a in (`findstr /c:"LastExportPath=" "F:\systemconfig.ini"`) do set "AUTO_CONFIG=%%a"
    for /f "usebackq tokens=2 delims==" %%a in (`findstr /c:"LastExportTime=" "F:\systemconfig.ini"`) do set "AUTO_TIME=%%a"
)

if defined AUTO_CONFIG (
    if exist "!AUTO_CONFIG!" (
        echo.
        echo =======================================
        echo  检测到从旧设备迁移的最新配置文件
        echo  路径: !AUTO_CONFIG!
        if defined AUTO_TIME echo  导出时间: !AUTO_TIME!
        echo =======================================
        echo.
        :ask_auto
        set "USE_AUTO="
        set /p "USE_AUTO=直接使用此配置? [Y/n]: "
        if /i "!USE_AUTO!"=="" set "USE_AUTO=Y"
        if /i "!USE_AUTO!"=="Y" (
            set "in=!AUTO_CONFIG!"
            echo.
            echo 已选择自动检测的配置: !in!
            echo =======================================
            goto import_config
        )
        if /i "!USE_AUTO!"=="N" (
            echo.
            echo 跳过自动配置，请手动选择...
        ) else (
            echo 请输入 Y 或 N
            goto ask_auto
        )
    )
)

rem =============================================
rem === Prepare folder list ====
rem =============================================
set "counter=1"
set "folders_file=%temp%\_folders.txt"
del /f /q "%folders_file%" 2>nul

rem Get folders from dir command and save to temp file
for /f "delims=" %%d in ('dir /b /ad "%src%" ^| sort /r') do (
    echo !counter!. %%d >> "%folders_file%"
    set /a counter+=1
)

rem === Check if any folders found ====
set /a "counter-=1"
if %counter% equ 0 (
    echo Error: No folders found in %src%.
    pause
    exit /b 1
)

rem === Display folder list for user selection ====
echo.
echo Available folders:
echo =======================================
type "%folders_file%"
echo =======================================
echo.
echo 导入配置文件
rem === Get user selection ====
:select_folder
set /p "choice=Please select the folder number (1-%counter%): "

rem === Validate user input ====
if "%choice%"=="" goto select_folder
for /f "delims=0123456789" %%i in ("%choice%") do (
    if not "%%i"=="" goto select_folder
)

if %choice% lss 1 goto select_folder
if %choice% gtr %counter% goto select_folder

rem === Get the selected folder ====
set "selected_folder="
set "current=1"
for /f "tokens=2 delims=. " %%f in ('type "%folders_file%"') do (
    if !current! equ %choice% (
        set "selected_folder=%%f"
        goto break_loop
    )
    set /a current+=1
)
:break_loop

set "in=%src%\%selected_folder%"
echo.
echo Selected folder: %in%
echo =======================================

:import_config

rem === Outlook ===
if exist "%in%\Outlook_Rules.reg" reg import "%in%\Outlook_Rules.reg"
if exist "%in%\Outlook_Profiles.reg" reg import "%in%\Outlook_Profiles.reg"
if exist "%in%\Outlook_AutoArchive.reg" reg import "%in%\Outlook_AutoArchive.reg"

rem === Chrome ===
set "chrome=%LOCALAPPDATA%\Google\Chrome\User Data\Default"
if exist "%in%\Chrome_Bookmarks.json" copy "%in%\Chrome_Bookmarks.json" "%chrome%\Bookmarks" /y
if exist "%in%\Chrome_LoginData.sqlite" copy "%in%\Chrome_LoginData.sqlite" "%chrome%\Login Data" /y

rem === Edge ===
set "edge=%LOCALAPPDATA%\Microsoft\Edge\User Data\Default"
if exist "%in%\Edge_Bookmarks.json" copy "%in%\Edge_Bookmarks.json" "%edge%\Bookmarks" /y
if exist "%in%\Edge_LoginData.sqlite" copy "%in%\Edge_LoginData.sqlite" "%edge%\Login Data" /y

rem === Input Methods ===
if exist "%in%\InputMethod.reg" reg import "%in%\InputMethod.reg"

rem === Printers ===
echo.正在添加网络打印机 GTMCPrinter
echo.正在访问 \\QITV3260 服务器...
rem 添加网络打印机
rundll32 printui.dll,PrintUIEntry /in /n "\\QITV3260\GTMCPrinter"
rem 将打印机设置为默认打印机
rundll32 printui.dll,PrintUIEntry /y /n "\\QITV3260\GTMCPrinter"
echo.打印机添加完成并已设置为默认打印机

@REM 下载程序部分

@REM @REM 运行scan_installers.py生成Installer_Packages.xlsx
@REM @echo.
@REM @echo 正在扫描安装包...
@REM "%~dp0python-3.12.4-embed-amd64\python-3.12.4-embed-amd64\python.exe" "%~dp0scan_installers.py"

@REM @REM 启动本地服务器
@REM @echo.
@REM @echo 正在启动本地服务器...
@REM start "Installer Server" "%~dp0python-3.12.4-embed-amd64\python-3.12.4-embed-amd64\python.exe" "%~dp0install.py"

@REM @REM 打开前端页面
@REM @echo.
@REM @echo 正在打开软件选择页面...
@REM start http://localhost:8888/
rem === Installed Programs ===


endlocal
