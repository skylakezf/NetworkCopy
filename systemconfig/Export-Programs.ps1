# PowerShell script to export installed programs to CSV format
param(
    [string]$OutputPath
)

# Load Windows Forms for MessageBox dialogs
Add-Type -AssemblyName System.Windows.Forms

# Create output directory if it doesn't exist
if (-not (Test-Path -Path $OutputPath)) {
    New-Item -ItemType Directory -Path $OutputPath | Out-Null
}

# The Programs and Features list is generated from these registry keys
# but with specific filtering to exclude certain entries
$uninstallKeys = @(
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    "HKLM:\SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall"  # For 32-bit programs on 64-bit systems
)

$programs = @()

foreach ($keyPath in $uninstallKeys) {
    if (Test-Path -Path $keyPath) {
        $subKeys = Get-ChildItem -Path $keyPath
        foreach ($subKey in $subKeys) {
            # Get display name - this is what appears in Programs and Features
            $displayName = Get-ItemProperty -Path $subKey.PSPath -Name "DisplayName" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty DisplayName
            
            # Get SystemComponent flag - Programs and Features hides entries where SystemComponent=1
            $systemComponent = Get-ItemProperty -Path $subKey.PSPath -Name "SystemComponent" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty SystemComponent
            
            # Get ReleaseType - Programs and Features hides updates and hotfixes
            $releaseType = Get-ItemProperty -Path $subKey.PSPath -Name "ReleaseType" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty ReleaseType
            
            # Get ParentKeyName - Programs and Features hides components of other programs
            $parentKeyName = Get-ItemProperty -Path $subKey.PSPath -Name "ParentKeyName" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty ParentKeyName
            
            # Get install location if available
            $installLocation = Get-ItemProperty -Path $subKey.PSPath -Name "InstallLocation" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty InstallLocation
            
            # Clean up install location to remove extra quotes if any
            if ($installLocation) {
                $installLocation = $installLocation.Trim('"')
            }
            
            # Only include entries that would appear in Programs and Features:
            # 1. Has a DisplayName
            # 2. Is not a system component (SystemComponent -ne 1)
            # 3. Is not an update/hotfix (ReleaseType not "Update" or "Hotfix")
            # 4. Is not a component of another program (no ParentKeyName)
            if ($displayName -and $systemComponent -ne 1 -and $releaseType -notin @("Update", "Hotfix") -and -not $parentKeyName) {
                $programs += [PSCustomObject]@{
                    DisplayName = $displayName
                    InstallLocation = $installLocation
                }
            }
        }
    }
}

# Remove duplicates based on DisplayName (in case same program appears in multiple registry locations)
$programs = $programs | Sort-Object DisplayName -Unique

# Export DisplayName to sheet1.csv
$programs | Select-Object DisplayName | Export-Csv -Path "$OutputPath\Installed_Programs.csv" -Encoding UTF8 -NoTypeInformation


# Remove old text file if it exists
if (Test-Path -Path "$OutputPath\Installed_Programs.txt") {
    Remove-Item -Path "$OutputPath\Installed_Programs.txt" -Force
}

Write-Host "Programs exported successfully to $OutputPath"

# ============================================================
# 导出后扫描程序清单，检测需警告的软件
# ============================================================

# --- 检测除 Office Starter 2010 之外的其他 Office 套件 ---
$otherOffice = $programs | Where-Object {
    $name = $_.DisplayName
    # 匹配含 Office 或 Microsoft 365 的程序
    (($name -like "*Office*") -or ($name -like "*Microsoft 365*")) `
    -and ($name -notlike "*Office Starter 2010*") `
    -and ($name -notlike "*Office Click-to-Run*")      # 排除 Office 运行时组件
}

if ($otherOffice) {
    $officeNames = ($otherOffice | ForEach-Object { $_.DisplayName }) -join "`n    "
    [System.Windows.Forms.MessageBox]::Show(
        "电脑安装了其他版本的Office,留意用户是否使用的为Office2010：`n`n    $officeNames",
        "注意",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Warning
    ) | Out-Null
}

# --- 检测除 Google Chrome / Microsoft Edge 之外的其他浏览器 ---
$otherBrowsers = $programs | Where-Object {
    $name = $_.DisplayName
    ($name -like "*浏览器*") -or
    ($name -like "*Firefox*") -or ($name -like "*Opera*") -or
    ($name -like "*Brave*") -or ($name -like "*Vivaldi*") -or
    ($name -like "*Chromium*") -or ($name -like "*Maxthon*") -or
    ($name -like "*Safari*") -or ($name -like "*UC*") -or
    ($name -like "*opera*") -or ($name -like "*Browser*")
} | Where-Object {
    # 排除 Google Chrome 和 Microsoft Edge
    $name = $_.DisplayName
    ($name -notlike "*Google Chrome*") -and
    ($name -notlike "*Microsoft Edge*") -and
    ($name -notlike "*Microsoft Edge Update*")
}

if ($otherBrowsers) {
    $browserNames = ($otherBrowsers | ForEach-Object { $_.DisplayName }) -join "`n    "
    $confirmResult = [System.Windows.Forms.MessageBox]::Show(
        "电脑安装了其他浏览器,需要同步备份下列浏览器的书签：`n`n    $browserNames`n",
        "注意",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Warning
    )
    if ($confirmResult -eq [System.Windows.Forms.DialogResult]::No) {
        Write-Host "用户取消了操作（存在其他浏览器）。"
        exit 0
    }
}

# ============================================================
# 获取屏幕分辨率（WMI Win32_VideoController + DPI API 回退）
# ============================================================
Add-Type -AssemblyName System.Drawing

$scaleX = 1.0
$scaleY = 1.0
$physWidth = 0
$physHeight = 0

$screen = [System.Windows.Forms.Screen]::PrimaryScreen
if ($null -eq $screen) {
    Write-Host "错误: 无法获取屏幕信息"
    exit 1
}
$logicW = $screen.Bounds.Width
$logicH = $screen.Bounds.Height

# 方法 1：WMI Win32_VideoController（绕过 DPI 虚拟化）
try {
    $gpu = Get-CimInstance -ClassName Win32_VideoController -ErrorAction Stop |
           Where-Object { $_.CurrentHorizontalResolution -gt 0 } |
           Select-Object -First 1
    if ($gpu) {
        $physWidth  = $gpu.CurrentHorizontalResolution
        $physHeight = $gpu.CurrentVerticalResolution
        if ($physWidth -gt 0 -and $physHeight -gt 0 -and $logicW -gt 0 -and $logicH -gt 0) {
            $scaleX = $physWidth  / $logicW
            $scaleY = $physHeight / $logicH
            Write-Host "WMI 视频控制器: ${physWidth}x${physHeight} / 逻辑: ${logicW}x${logicH}"
        }
    }
} catch {
    Write-Host "WMI 查询失败: $_"
}

# 方法 2：GetDpiForMonitor (Shcore.dll) 回退
if ($physWidth -le 0 -or $scaleX -le 0.01) {
    try {
        $dpinative = Add-Type -MemberDefinition @"
[DllImport("user32.dll")]
public static extern IntPtr MonitorFromPoint(int x, int y, uint dwFlags);
[DllImport("Shcore.dll")]
public static extern int GetDpiForMonitor(IntPtr hmonitor, uint dpiType, out uint dpiX, out uint dpiY);
[DllImport("user32.dll")]
public static extern IntPtr GetDC(IntPtr hWnd);
[DllImport("gdi32.dll")]
public static extern int GetDeviceCaps(IntPtr hdc, int nIndex);
[DllImport("user32.dll")]
public static extern int ReleaseDC(IntPtr hWnd, IntPtr hDC);
"@ -Name 'NativeDPI2' -PassThru -ErrorAction Stop

        $cx = $logicW / 2
        $cy = $logicH / 2
        $hMonitor = $dpinative::MonitorFromPoint([int]$cx, [int]$cy, 2)
        if ($hMonitor -ne [IntPtr]::Zero) {
            $dpiX = 0u
            $dpiY = 0u
            $hr = $dpinative::GetDpiForMonitor($hMonitor, 0, [ref]$dpiX, [ref]$dpiY)
            if ($hr -eq 0 -and $dpiX -gt 0 -and $dpiY -gt 0) {
                $scaleX = $dpiX / 96.0
                $scaleY = $dpiY / 96.0
                Write-Host "GetDpiForMonitor: dpiX=$dpiX, dpiY=$dpiY"
            }
        }
        # 回退 GetDeviceCaps
        if ($scaleX -le 0 -or $scaleX -eq 1.0) {
            $hdc = $dpinative::GetDC([IntPtr]::Zero)
            if ($hdc -ne [IntPtr]::Zero) {
                $gdpiX = $dpinative::GetDeviceCaps($hdc, 88)
                $gdpiY = $dpinative::GetDeviceCaps($hdc, 90)
                $null = $dpinative::ReleaseDC([IntPtr]::Zero, $hdc)
                if ($gdpiX -gt 0) { $scaleX = $gdpiX / 96.0 }
                if ($gdpiY -gt 0) { $scaleY = $gdpiY / 96.0 }
                Write-Host "GetDeviceCaps: dpiX=$gdpiX, dpiY=$gdpiY"
            }
        }
    } catch {
        Write-Host "DPI API 调用失败: $_"
    }
}

if ($scaleX -le 0) { $scaleX = 1.0 }
if ($scaleY -le 0) { $scaleY = 1.0 }

if ($physWidth  -le 0) { $physWidth  = [math]::Round($logicW * $scaleX) }
if ($physHeight -le 0) { $physHeight = [math]::Round($logicH * $scaleY) }

if ($physWidth -le 0) { $physWidth = $logicW; Write-Host "警告: 回退到逻辑宽度" }
if ($physHeight -le 0) { $physHeight = $logicH; Write-Host "警告: 回退到逻辑高度" }

$scalePercent = [math]::Round($scaleX * 100)
$resolutionText = "宽度: $physWidth`n高度: $physHeight`n缩放倍率: ${scalePercent}%"
Set-Content -Path "$OutputPath\Screen_Resolution.txt" -Value $resolutionText
Write-Host "物理分辨率: ${physWidth}x${physHeight} (缩放: ${scalePercent}%)"

# ============================================================
# 截取桌面截图（物理分辨率）
# ============================================================
$bmp = New-Object System.Drawing.Bitmap ([int]$physWidth, [int]$physHeight)
$graphics = [System.Drawing.Graphics]::FromImage($bmp)
$graphics.CopyFromScreen(0, 0, 0, 0, (New-Object System.Drawing.Size ([int]$physWidth, [int]$physHeight)))
$bmp.Save("$OutputPath\screenshot.png")
$graphics.Dispose()
$bmp.Dispose()
Write-Host "桌面截图已保存到: $OutputPath\screenshot.png"
