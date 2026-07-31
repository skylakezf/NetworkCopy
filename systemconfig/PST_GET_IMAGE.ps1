param([string]$OutputPath = ".")

# ============================================================
# 加载必要的程序集
# ============================================================
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# ============================================================
# 定义 Win32 帮助类
# ============================================================
try {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class WinOLHelper {
    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    public static extern IntPtr FindWindow(string lpClassName, string lpWindowName);
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
    [DllImport("user32.dll")]
    public static extern IntPtr PostMessage(IntPtr hWnd, int Msg, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")]
    public static extern IntPtr GetDC(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern int ReleaseDC(IntPtr hWnd, IntPtr hDC);
    [DllImport("gdi32.dll")]
    public static extern int GetDeviceCaps(IntPtr hdc, int nIndex);
    [DllImport("user32.dll")]
    public static extern IntPtr MonitorFromPoint(int x, int y, uint dwFlags);
    [DllImport("Shcore.dll")]
    public static extern int GetDpiForMonitor(IntPtr hmonitor, uint dpiType, out uint dpiX, out uint dpiY);

    // 枚举所有顶层窗口
    private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

    private static IntPtr _foundHwnd;
    private static string _searchTitle;

    private static bool EnumCallback(IntPtr hWnd, IntPtr lParam) {
        var sb = new StringBuilder(256);
        GetWindowText(hWnd, sb, 256);
        if (sb.ToString().IndexOf(_searchTitle, StringComparison.OrdinalIgnoreCase) >= 0) {
            _foundHwnd = hWnd;
            return false;
        }
        return true;
    }

    public static IntPtr FindWindowByTitle(string partialTitle) {
        _foundHwnd = IntPtr.Zero;
        _searchTitle = partialTitle;
        EnumWindows(new EnumWindowsProc(EnumCallback), IntPtr.Zero);
        return _foundHwnd;
    }
}
"@
} catch {
    Write-Host "类型已存在，使用缓存版本。"
}

$SW_RESTORE = 9
$SW_MAXIMIZE = 3
$WM_CLOSE = 0x0010
$LOGPIXELSX = 88
$LOGPIXELSY = 90

# ============================================================
# 辅助函数
# ============================================================

function Get-DpiScale {
    <#
    .SYNOPSIS
    获取主显示器的真实物理分辨率和 DPI 缩放。
    策略：WMI Win32_VideoController（绕过 DPI 虚拟化）→ GetDpiForMonitor → GetDeviceCaps。
    返回包含 X (缩放倍率)、Y (缩放倍率)、Percent (缩放百分比) 的哈希表。
    #>
    $scaleX = 1.0
    $scaleY = 1.0

    $screen = [System.Windows.Forms.Screen]::PrimaryScreen
    if ($null -eq $screen) { return @{ X = 1.0; Y = 1.0; Percent = 100 } }
    $logicW = $screen.Bounds.Width
    $logicH = $screen.Bounds.Height
    if ($logicW -le 0) { $logicW = 1 }
    if ($logicH -le 0) { $logicH = 1 }

    # 方法 1：WMI Win32_VideoController（不受 DPI 虚拟化影响）
    $wmiOk = $false
    try {
        $gpu = Get-CimInstance -ClassName Win32_VideoController -ErrorAction Stop |
               Where-Object { $_.CurrentHorizontalResolution -gt 0 } |
               Select-Object -First 1
        if ($gpu) {
            $physW = $gpu.CurrentHorizontalResolution
            $physH = $gpu.CurrentVerticalResolution
            if ($physW -gt 0 -and $physH -gt 0) {
                $scaleX = $physW / $logicW
                $scaleY = $physH / $logicH
                $wmiOk = $true
                Write-Host "WMI 视频分辨率: ${physW}x${physH} / 逻辑: ${logicW}x${logicH}"
            }
        }
    } catch {
        Write-Host "WMI 查询失败: $_"
    }

    # 方法 2：GetDpiForMonitor (Shcore.dll)
    if (-not $wmiOk) {
        $cx = $screen.Bounds.X + $logicW / 2
        $cy = $screen.Bounds.Y + $logicH / 2
        $hMonitor = [WinOLHelper]::MonitorFromPoint([int]$cx, [int]$cy, 2)
        $dpiOk = $false

        if ($hMonitor -ne [IntPtr]::Zero) {
            $dpiX = 0u
            $dpiY = 0u
            $hr = [WinOLHelper]::GetDpiForMonitor($hMonitor, 0, [ref]$dpiX, [ref]$dpiY)
            if ($hr -eq 0 -and $dpiX -gt 0 -and $dpiY -gt 0) {
                $scaleX = $dpiX / 96.0
                $scaleY = $dpiY / 96.0
                $dpiOk = $true
                Write-Host "GetDpiForMonitor: dpiX=$dpiX, dpiY=$dpiY"
            } else {
                Write-Host "GetDpiForMonitor 调用失败 (HR=0x$($hr.ToString('X8')))"
            }
        }

        # 回退：GetDeviceCaps
        if (-not $dpiOk) {
            $hdc = [WinOLHelper]::GetDC([IntPtr]::Zero)
            if ($hdc -ne [IntPtr]::Zero) {
                $gdpiX = [WinOLHelper]::GetDeviceCaps($hdc, $LOGPIXELSX)
                $gdpiY = [WinOLHelper]::GetDeviceCaps($hdc, $LOGPIXELSY)
                [WinOLHelper]::ReleaseDC([IntPtr]::Zero, $hdc) | Out-Null
                if ($gdpiX -gt 0) { $scaleX = $gdpiX / 96.0 }
                if ($gdpiY -gt 0) { $scaleY = $gdpiY / 96.0 }
                Write-Host "GetDeviceCaps: dpiX=$gdpiX, dpiY=$gdpiY"
            }
        }
    }

    if ($scaleX -le 0) { $scaleX = 1.0 }
    if ($scaleY -le 0) { $scaleY = 1.0 }

    return @{
        X       = $scaleX
        Y       = $scaleY
        Percent = [math]::Round($scaleX * 100)
    }
}

function Show-Desktop {
    Write-Host "显示桌面..."
    (New-Object -ComObject Shell.Application).MinimizeAll()
    Start-Sleep -Seconds 2
}

function Take-Screenshot {
    param([string]$FileName)
    $s = [System.Windows.Forms.Screen]::PrimaryScreen
    if ($null -eq $s) {
        Write-Host "错误: 无法获取主显示器信息"
        return
    }

    $dpi = Get-DpiScale
    $pw = [math]::Round($s.Bounds.Width * $dpi.X)
    $ph = [math]::Round($s.Bounds.Height * $dpi.Y)

    if ($pw -le 0) {
        Write-Host "警告: 宽度为 $pw，回退到逻辑分辨率"
        $pw = $s.Bounds.Width
    }
    if ($ph -le 0) {
        Write-Host "警告: 高度为 $ph，回退到逻辑分辨率"
        $ph = $s.Bounds.Height
    }

    Write-Host "截图中... 物理分辨率: ${pw}x${ph} (缩放: $($dpi.Percent)%)"
    $bmp = New-Object System.Drawing.Bitmap ([int]$pw, [int]$ph)
    $graphics = [System.Drawing.Graphics]::FromImage($bmp)
    $graphics.CopyFromScreen(0, 0, 0, 0, (New-Object System.Drawing.Size ([int]$pw, [int]$ph)))
    $savePath = Join-Path $OutputPath $FileName
    $bmp.Save($savePath, [System.Drawing.Imaging.ImageFormat]::Png)
    $graphics.Dispose()
    $bmp.Dispose()
    Write-Host "截图已保存: $savePath"
}

function Close-WindowByTitle {
    param([string]$Title)
    $h = [WinOLHelper]::FindWindowByTitle($Title)
    if ($h -ne [IntPtr]::Zero) {
        [WinOLHelper]::PostMessage($h, $WM_CLOSE, [IntPtr]::Zero, [IntPtr]::Zero)
        Write-Host "已关闭窗口: $Title"
    }
}

# ============================================================
# 第 1 步：显示桌面
# ============================================================
Write-Host "======== 第 1 步：显示桌面 ========"
Show-Desktop

# ============================================================
# 第 2 步：打开 Outlook 数据文件对话框
# ============================================================
Write-Host "======== 第 2 步：打开 Outlook 数据文件 ========"

$mlcfgPath = $null
$possibleCplPaths = @(
    "${env:ProgramFiles(x86)}\Microsoft Office\Office14\MLCFG32.CPL",
    "${env:ProgramFiles}\Microsoft Office\Office14\MLCFG32.CPL",
    "${env:ProgramFiles(x86)}\Microsoft Office\root\Office14\MLCFG32.CPL",
    "${env:ProgramFiles}\Microsoft Office\root\Office14\MLCFG32.CPL",
    "${env:SystemRoot}\SysWOW64\MLCFG32.CPL",
    "${env:SystemRoot}\System32\MLCFG32.CPL"
)

foreach ($path in $possibleCplPaths) {
    if (Test-Path $path) { $mlcfgPath = $path; break }
}

if (-not $mlcfgPath) {
    $searchRoots = @(
        "${env:ProgramFiles(x86)}\Microsoft Office",
        "${env:ProgramFiles}\Microsoft Office"
    )
    foreach ($root in $searchRoots) {
        if (Test-Path $root) {
            $found = Get-ChildItem -Path $root -Filter "MLCFG32.CPL" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($found) { $mlcfgPath = $found.FullName; break }
        }
    }
}

if (-not $mlcfgPath) {
    Write-Host "未找到 mlcfg32.cpl，请确认 Outlook 2010 已安装。"
    Write-Host "跳过 PST 数据文件截图。"
} else {
    Write-Host "找到邮件控制面板项: $mlcfgPath"

    Start-Process "rundll32.exe" "shell32.dll,Control_RunDLL `"$mlcfgPath`""

    $maxWait = 15
    $hwnd = [IntPtr]::Zero

    for ($i = 0; $i -lt $maxWait; $i++) {
        Start-Sleep -Seconds 1

        $hwnd = [WinOLHelper]::FindWindowByTitle("邮件设置")
        if ($hwnd -eq [IntPtr]::Zero) {
            $hwnd = [WinOLHelper]::FindWindowByTitle("Mail Setup")
        }

        if ($hwnd -ne [IntPtr]::Zero) {
            $sb = New-Object System.Text.StringBuilder 256
            [WinOLHelper]::GetWindowText($hwnd, $sb, 256)
            Write-Host "找到窗口: $($sb.ToString())"
            break
        }
    }

    if ($hwnd -ne [IntPtr]::Zero) {
        # 激活窗口并点击"数据文件(F)"按钮
        [WinOLHelper]::ShowWindow($hwnd, $SW_RESTORE)
        [WinOLHelper]::SetForegroundWindow($hwnd) | Out-Null
        Start-Sleep -Milliseconds 500
        
        [System.Windows.Forms.SendKeys]::SendWait("%F")
        Start-Sleep -Seconds 2
        Write-Host "已打开「数据文件」对话框"

        # 截图
        Take-Screenshot "pst_datafiles.png"

        # 关闭窗口
        Start-Sleep -Milliseconds 500
        Close-WindowByTitle "帐户设置"
        Close-WindowByTitle "邮件设置"
        Close-WindowByTitle "Mail Setup"
    } else {
        Write-Host "未找到「邮件设置」窗口。"
    }
}

# ============================================================
# 第 3 步：显示桌面
# ============================================================
Write-Host "======== 第 3 步：显示桌面 ========"
Show-Desktop

# ============================================================
# 第 4 步：打开 程序和功能，全屏 + 小图标 + 截图
# ============================================================
Write-Host "======== 第 4 步：打开 程序和功能 ========"

Start-Process "control" "appwiz.cpl"
Start-Sleep -Seconds 3

$appwizHwnd = [IntPtr]::Zero
for ($i = 0; $i -lt 15; $i++) {
    $appwizHwnd = [WinOLHelper]::FindWindowByTitle("程序和功能")
    if ($appwizHwnd -eq [IntPtr]::Zero) {
        $appwizHwnd = [WinOLHelper]::FindWindowByTitle("Programs and Features")
    }
    if ($appwizHwnd -ne [IntPtr]::Zero) { break }
    Start-Sleep -Seconds 1
}

if ($appwizHwnd -eq [IntPtr]::Zero) {
    Write-Host "未找到「程序和功能」窗口。"
} else {
    $sb = New-Object System.Text.StringBuilder 256
    [WinOLHelper]::GetWindowText($appwizHwnd, $sb, 256)
    Write-Host "找到窗口: $($sb.ToString())"

    # 最大化
    [WinOLHelper]::ShowWindow($appwizHwnd, $SW_MAXIMIZE)
    Start-Sleep -Milliseconds 500
    [WinOLHelper]::SetForegroundWindow($appwizHwnd) | Out-Null
    Start-Sleep -Milliseconds 500

    # 设置小图标: Alt+V（查看菜单）→ N（小图标）
    Write-Host "设置视图为小图标..."
    [System.Windows.Forms.SendKeys]::SendWait("%V")
    Start-Sleep -Milliseconds 500
    [System.Windows.Forms.SendKeys]::SendWait("N")
    Start-Sleep -Seconds 1

    # 截图
    Take-Screenshot "appwiz_programs.png"

    # 关闭窗口
    Start-Sleep -Milliseconds 500
    [WinOLHelper]::PostMessage($appwizHwnd, $WM_CLOSE, [IntPtr]::Zero, [IntPtr]::Zero)
    Write-Host "已关闭 程序和功能"
}

# ============================================================
# 第 5 步：显示桌面
# ============================================================
Write-Host "======== 第 5 步：显示桌面 ========"
Show-Desktop

# ============================================================
# 第 6 步：打开 设备和打印机，全屏 + 小图标 + 截图
# ============================================================
Write-Host "======== 第 6 步：打开 设备和打印机 ========"

Start-Process "control" "printers"
Start-Sleep -Seconds 3

$devPrintHwnd = [IntPtr]::Zero
for ($i = 0; $i -lt 15; $i++) {
    $devPrintHwnd = [WinOLHelper]::FindWindowByTitle("设备和打印机")
    if ($devPrintHwnd -eq [IntPtr]::Zero) {
        $devPrintHwnd = [WinOLHelper]::FindWindowByTitle("Devices and Printers")
    }
    if ($devPrintHwnd -ne [IntPtr]::Zero) { break }
    Start-Sleep -Seconds 1
}

if ($devPrintHwnd -eq [IntPtr]::Zero) {
    Write-Host "未找到「设备和打印机」窗口。"
} else {
    $sb = New-Object System.Text.StringBuilder 256
    [WinOLHelper]::GetWindowText($devPrintHwnd, $sb, 256)
    Write-Host "找到窗口: $($sb.ToString())"

    # 最大化
    [WinOLHelper]::ShowWindow($devPrintHwnd, $SW_MAXIMIZE)
    Start-Sleep -Milliseconds 500
    [WinOLHelper]::SetForegroundWindow($devPrintHwnd) | Out-Null
    Start-Sleep -Milliseconds 500

    # 设置小图标: Alt+V（查看菜单）→ N（小图标）
    Write-Host "设置视图为小图标..."
    [System.Windows.Forms.SendKeys]::SendWait("%V")
    Start-Sleep -Milliseconds 500
    [System.Windows.Forms.SendKeys]::SendWait("N")
    Start-Sleep -Seconds 1

    # 截图
    Take-Screenshot "devices_printers.png"

    # 关闭窗口
    Start-Sleep -Milliseconds 500
    [WinOLHelper]::PostMessage($devPrintHwnd, $WM_CLOSE, [IntPtr]::Zero, [IntPtr]::Zero)
    Write-Host "已关闭 设备和打印机"
}

Write-Host "======== 完成 ========"
