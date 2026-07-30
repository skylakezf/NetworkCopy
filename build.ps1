<#
.SYNOPSIS
  磁盘拷贝工具 PyInstaller 构建脚本 (pwsh)
.DESCRIPTION
  在项目根目录下运行此脚本，使用嵌入式 Python 调用 PyInstaller 打包。
  输出到 dist/ 目录，生成单文件 exe。
  原生支持 UTF-8，无需 chcp 切换代码页。
#>

$ErrorActionPreference = "Stop"
$script:PythonExe = $null
$script:BuildTimedOut = $false

# ---- 助手函数 ----
function Write-Green  { param($s) Write-Host $s -ForegroundColor Green }
function Write-Yellow { param($s) Write-Host $s -ForegroundColor Yellow }
function Write-Red    { param($s) Write-Host $s -ForegroundColor Red }

# Win10 部分版本控制台默认编码不是 UTF-8，显式设为 UTF-8
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
} catch { }

# ---- 切换到脚本所在目录 ----
Set-Location -LiteralPath $PSScriptRoot
Write-Green " 工作目录: $PSScriptRoot"

# ---- 定位嵌入 Python ----
$PythonCandidates = @(
    "$PSScriptRoot\python-3.13.14-embed-amd64\python.exe",
    "$PSScriptRoot\python-3.13.14-embed-amd64\python3.exe",
    "$PSScriptRoot\python-3.13.14-embed-amd64\pythonw.exe"
)
foreach ($candidate in $PythonCandidates) {
    if (Test-Path -LiteralPath $candidate) {
        $script:PythonExe = $candidate
        break
    }
}

if (-not $script:PythonExe) {
    Write-Red "✗ 未找到嵌入式 Python，请确认 python-3.13.14-embed-amd64 目录存在"
    exit 1
}
Write-Green "✓ Python: $script:PythonExe"

# ---- 超时看门狗（保险：5 分钟后强制杀死） ----
$watchdogJob = Start-Job -ScriptBlock {
    $timeout = 300  # 秒
    Start-Sleep -Seconds $timeout
    Write-Warning "BUILD TIMEOUT: 构建超过 $timeout 秒，自动终止"
    Stop-Process -Name "python" -Force -ErrorAction SilentlyContinue
}

$buildTimer = [System.Diagnostics.Stopwatch]::StartNew()

# ---- PyInstaller 参数 ----
$AppName  = "磁盘拷贝工具"
$MainPy   = "main.py"

$HiddenImports = @(
    "tkinter", "tkinter.ttk",
    "urllib.parse", "json", "csv", "hashlib",
    "concurrent.futures", "http.server", "struct", "re",
    "threading", "socket", "subprocess", "ctypes", "ctypes.wintypes",
    "urllib.request", "urllib.error",
    "cryptography",
    "cryptography.hazmat.primitives.asymmetric.rsa",
    "cryptography.hazmat.primitives.serialization",
    "cryptography.hazmat.backends.openssl.backend",
    "ttkbootstrap",
    "ttkbootstrap.style",
    "ttkbootstrap.themes",
    "ttkbootstrap.constants",
    "ttkbootstrap.window",
    "ttkbootstrap.tooltip",
    "PIL",
    "PIL.Image",
    "PIL.ImageTk",
    "secrets", "tls_utils"
)

$CollectData = @("cryptography", "ttkbootstrap")

# 构建命令行参数列表，避免引号/分号被 pwsh 错误解析
$PyArgs = @(
    "-m", "PyInstaller",
    "--onefile",
    "--windowed",
    "--noupx",                    # 禁用 UPX — 防止 bootstrap.ttf 等二进制文件解压失败
    "--name", $AppName
)
foreach ($imp in $HiddenImports) {
    $PyArgs += "--hidden-import"
    $PyArgs += $imp
}
foreach ($cd in $CollectData) {
    $PyArgs += "--collect-data"
    $PyArgs += $cd
}
$PyArgs += "--add-data"
$PyArgs += "certs;certs"        # 在 .NET 字符串中分号不会被当成语句分隔符
$PyArgs += $MainPy

# ---- 打印参数（调试用） ----
Write-Yellow "--- PyInstaller 参数 ---"
$PyArgs -join " " | Write-Host
Write-Yellow "------------------------"

# ---- 清理旧的构建输出以避免增量构建问题 ----
$buildDir  = Join-Path $PSScriptRoot "build"
$distDir   = Join-Path $PSScriptRoot "dist"
$specFile  = Join-Path $PSScriptRoot "$AppName.spec"
foreach ($d in @($buildDir, $distDir)) {
    if (Test-Path -LiteralPath $d) {
        Remove-Item -LiteralPath $d -Recurse -Force -ErrorAction SilentlyContinue
        Write-Yellow "  已清理: $d"
    }
}
# spec 文件由 PyInstaller 自动重新生成 (含 --noupx 参数), 无需手动删除

# ---- 执行构建 ----
Write-Green ">> 开始 PyInstaller 打包..."

$proc = Start-Process -FilePath $script:PythonExe `
    -ArgumentList $PyArgs `
    -NoNewWindow -Wait -PassThru

$buildTimer.Stop()
Stop-Job -Job $watchdogJob -ErrorAction SilentlyContinue
Remove-Job  -Job $watchdogJob -Force -ErrorAction SilentlyContinue

# ---- 结果判断 ----
if ($proc.ExitCode -ne 0) {
    Write-Red "✗ PyInstaller 构建失败 (exit code: $($proc.ExitCode))"
    Write-Red "  耗时: $($buildTimer.Elapsed.TotalSeconds.ToString('F1')) 秒"
    Write-Red "  请检查上方输出，常见原因：打包超大文件（如整个 Python 目录）请用排除规则"
    exit $proc.ExitCode
}

# ---- 验证产物 ----
$exePath = Join-Path $distDir "$AppName.exe"
if (-not (Test-Path -LiteralPath $exePath)) {
    Write-Red "✗ 未找到产物: $exePath"
    exit 2
}

$fileInfo = Get-Item -LiteralPath $exePath
$sizeMB   = ($fileInfo.Length / 1MB).ToString("F1")

Write-Green "✓ 构建成功！"
Write-Green "  产物: $exePath"
Write-Green "  大小: $sizeMB MB"
Write-Green "  耗时: $($buildTimer.Elapsed.TotalSeconds.ToString('F1')) 秒"

exit 0
