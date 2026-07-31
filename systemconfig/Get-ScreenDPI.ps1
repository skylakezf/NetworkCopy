param(
    [Parameter(Mandatory=$true)]
    [string]$OutputPath,
    [string]$FileName,
    [switch]$ResolutionOnly
)

# ============================================================
# 加载必要的程序集
# ============================================================
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# ============================================================
# 辅助函数：获取真实物理分辨率和 DPI 缩放
# 策略：优先用 WMI Win32_VideoController（绕过 DPI 虚拟化），
# 再回退到 GetDpiForMonitor → GetDeviceCaps
# ============================================================
function Get-PhysicalResolution {
    $scaleX = 1.0
    $scaleY = 1.0
    $physW = 0
    $physH = 0

    $screen = [System.Windows.Forms.Screen]::PrimaryScreen
    if ($null -eq $screen) { return $null }
    $logicW = $screen.Bounds.Width
    $logicH = $screen.Bounds.Height

    # 方法 1：WMI Win32_VideoController（不受 DPI 虚拟化影响）
    try {
        $gpu = Get-CimInstance -ClassName Win32_VideoController -ErrorAction Stop |
               Where-Object { $_.CurrentHorizontalResolution -gt 0 } |
               Select-Object -First 1
        if ($gpu) {
            $physW = $gpu.CurrentHorizontalResolution
            $physH = $gpu.CurrentVerticalResolution
            if ($physW -gt 0 -and $physH -gt 0 -and $logicW -gt 0 -and $logicH -gt 0) {
                $scaleX = $physW / $logicW
                $scaleY = $physH / $logicH
                Write-Host "WMI 视频控制器: ${physW}x${physH} / 逻辑: ${logicW}x${logicH}"
                if ($scaleX -gt 0.01 -and $scaleY -gt 0.01) {
                    return @{ PhysW = $physW; PhysH = $physH; ScaleX = $scaleX; ScaleY = $scaleY }
                }
            }
        }
    } catch {
        Write-Host "WMI 查询失败: $_"
    }

    # 方法 2：GetDpiForMonitor (Shcore.dll)
    try {
        $dpiNative = Add-Type -MemberDefinition @"
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
        $hMonitor = $dpiNative::MonitorFromPoint([int]$cx, [int]$cy, 2)
        if ($hMonitor -ne [IntPtr]::Zero) {
            $dpiX = 0u
            $dpiY = 0u
            $hr = $dpiNative::GetDpiForMonitor($hMonitor, 0, [ref]$dpiX, [ref]$dpiY)
            if ($hr -eq 0 -and $dpiX -gt 0 -and $dpiY -gt 0) {
                $scaleX = $dpiX / 96.0
                $scaleY = $dpiY / 96.0
                Write-Host "GetDpiForMonitor: dpiX=$dpiX, dpiY=$dpiY, 倍率=${scaleX}"
                $physW = [math]::Round($logicW * $scaleX)
                $physH = [math]::Round($logicH * $scaleY)
                if ($scaleX -gt 0.01 -and $physW -gt 0) {
                    return @{ PhysW = $physW; PhysH = $physH; ScaleX = $scaleX; ScaleY = $scaleY }
                }
            } else {
                Write-Host "GetDpiForMonitor 调用失败 (HR=0x$($hr.ToString('X8')))"
            }
        }

        # 回退到 GetDeviceCaps
        $hdc = $dpiNative::GetDC([IntPtr]::Zero)
        if ($hdc -ne [IntPtr]::Zero) {
            $dpiX = $dpiNative::GetDeviceCaps($hdc, 88)
            $dpiY = $dpiNative::GetDeviceCaps($hdc, 90)
            $null = $dpiNative::ReleaseDC([IntPtr]::Zero, $hdc)
            if ($dpiX -gt 0) { $scaleX = $dpiX / 96.0 }
            if ($dpiY -gt 0) { $scaleY = $dpiY / 96.0 }
            Write-Host "GetDeviceCaps: dpiX=$dpiX, dpiY=$dpiY, 倍率=$scaleX"
        }
    } catch {
        Write-Host "DPI API 调用失败: $_"
    }

    if ($scaleX -le 0) { $scaleX = 1.0 }
    if ($scaleY -le 0) { $scaleY = 1.0 }
    $physW = [math]::Round([math]::Max(1, $logicW) * $scaleX)
    $physH = [math]::Round([math]::Max(1, $logicH) * $scaleY)
    return @{ PhysW = $physW; PhysH = $physH; ScaleX = $scaleX; ScaleY = $scaleY }
}

# ============================================================
# 获取分辨率信息
# ============================================================
$res = Get-PhysicalResolution
if ($null -eq $res) {
    Write-Host "错误: 无法获取屏幕信息"
    exit 1
}

$physWidth  = $res.PhysW
$physHeight = $res.PhysH
$scalePercent = [math]::Round($res.ScaleX * 100)

# ============================================================
# 写入分辨率文件
# ============================================================
$resolutionText = "宽度: $physWidth`n高度: $physHeight`n缩放倍率: ${scalePercent}%"
Set-Content -Path "$OutputPath\Screen_Resolution.txt" -Value $resolutionText
Write-Host "物理分辨率: ${physWidth}x${physHeight} (缩放: ${scalePercent}%)"

# ============================================================
# 如果只需要分辨率，到此结束
# ============================================================
if ($ResolutionOnly) {
    exit 0
}

# ============================================================
# 截图（物理分辨率）
# ============================================================
if (-not $FileName) {
    Write-Host "错误: 未指定截图文件名"
    exit 1
}

$bmp = New-Object System.Drawing.Bitmap ([int]$physWidth, [int]$physHeight)
$graphics = [System.Drawing.Graphics]::FromImage($bmp)
$graphics.CopyFromScreen(0, 0, 0, 0, (New-Object System.Drawing.Size ([int]$physWidth, [int]$physHeight)))
$savePath = Join-Path $OutputPath $FileName
$bmp.Save($savePath, [System.Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose()
$bmp.Dispose()
Write-Host "截图已保存: $savePath"
