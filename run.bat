@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM 优先使用打包好的 exe
if exist "%~dp0dist\磁盘拷贝工具.exe" (
    echo 启动磁盘拷贝工具 (exe)...
    start "" "%~dp0dist\磁盘拷贝工具.exe"
) else (
    REM 回退: 使用嵌入式 Python 运行源码
    echo 启动磁盘拷贝工具 (源码)...
    "%~dp0python-3.13.14-embed-amd64\python.exe" "%~dp0main.py"
)
pause
