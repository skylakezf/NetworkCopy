# ============================================================
# Calc-AllocationUnitMigration.ps1
# 计算从 512Byte 分配单元迁移到 4096Byte 分配单元后的磁盘占用
# 目标分区: D:, E:, F:
# 跳过目录: AppData, WeChat Files
# 输出目录: F:\Appl\<YYYY-MM-DD> (与 out.cmd 一致)
#
# 微软 KB140365 官方公式:
#   磁盘占用 = ceil(FileSize / ClusterSize) × ClusterSize
# 本脚本对同一批文件分别按 512B / 4096B 对齐计算，确保对比公平。
# ============================================================

param(
    [string[]]$Drives = @('D', 'E', 'F'),
    [int]$SmallFileThreshold = 4096,
    [int]$OldCluster = 512,
    [int]$NewCluster = 4096,
    [switch]$Estimate
)

$ErrorActionPreference = 'Continue'
$script:smallWriter = $null
$script:fullWriter  = $null

# 输出目录: 与 out.cmd 一致 -> F:\Appl\<YYYY-MM-DD>
$dateFolder = (Get-Date).ToString('yyyy-MM-dd')
$applRoot = if ($env:APPL_ROOT) { $env:APPL_ROOT } else { 'F:\Appl' }
$OutputDir = Join-Path $applRoot $dateFolder
if (-not (Test-Path $OutputDir)) {
    New-Item -Path $OutputDir -ItemType Directory -Force | Out-Null
}

$smallCsvPath = Join-Path $OutputDir "SmallFiles_Under4KB.csv"
$fullCsvPath  = Join-Path $OutputDir "FullFilelist_DEF.csv"

# 清理旧文件避免被占用
Remove-Item -Path $smallCsvPath -Force -ErrorAction SilentlyContinue
Remove-Item -Path $fullCsvPath  -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 200

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " 磁盘分配单元迁移空间估算 (${OldCluster}B -> ${NewCluster}B)" -ForegroundColor Cyan
Write-Host " 输出目录: $OutputDir" -ForegroundColor DarkGray
Write-Host " ceil(FileSize / ClusterSize) × ClusterSize " -ForegroundColor DarkGray
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# 打开 StreamWriter
try {
    $script:smallWriter = New-Object System.IO.StreamWriter($smallCsvPath, $false, [System.Text.Encoding]::UTF8)
    $script:smallWriter.WriteLine("Drive,FullPath,FileName,SizeBytes")
} catch {
    Write-Host "无法创建 $smallCsvPath : $_" -ForegroundColor Red
}

try {
    $script:fullWriter = New-Object System.IO.StreamWriter($fullCsvPath, $false, [System.Text.Encoding]::UTF8)
    $script:fullWriter.WriteLine("Drive,FullPath,FileName,SizeBytes")
} catch {
    Write-Host "无法创建 $fullCsvPath : $_" -ForegroundColor Red
}

$results = @()
$totalSmallCountAll   = 0
$totalOldAlignedAll   = 0
$totalNewAlignedAll   = 0
$totalDriveUsedAll    = 0

foreach ($drive in $Drives) {
    $drivePath = "${drive}:\"
    if (-not (Test-Path $drivePath)) {
        Write-Host "[$drive :] 分区不存在，跳过。" -ForegroundColor Yellow
        continue
    }

    Write-Host "正在扫描 $drivePath ..." -ForegroundColor Green

    # 获取分区当前实际已用空间（含 NTFS 元数据，仅供参考）
    $driveUsed = 0
    try {
        $driveInfo = Get-PSDrive -Name $drive -ErrorAction Stop
        $driveUsed = $driveInfo.Used
    } catch {
        try {
            $driveObj = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='${drive}:'" -ErrorAction Stop
            $driveUsed = $driveObj.Size - $driveObj.FreeSpace
        } catch { }
    }

    # 两个累加器：分别按 512B 和 4096B 对齐
    $oldAlignedBytes = 0   # ceil(size/512) * 512  - 当前分配单元下的文件占用
    $newAlignedBytes = 0   # ceil(size/4096) * 4096 - 新分配单元下的文件占用
    $smallFileCount  = 0
    $fileCount       = 0
    $errorCount      = 0
    $startTime       = Get-Date

    # 需要跳过的文件夹名称（不进入遍历，大幅加速）
    $excludeDirs = @('AppData', 'WeChat Files')

    # .NET API EnumerateFileSystemEntries 比 Get-ChildItem 快 2-3 倍（惰性枚举，零额外对象创建）
    $dirQueue = New-Object System.Collections.Generic.Queue[string]
    $dirQueue.Enqueue($drivePath)

    try {
        while ($dirQueue.Count -gt 0) {
            $currentDir = $dirQueue.Dequeue()

            $entries = $null
            try {
                $entries = [System.IO.Directory]::EnumerateFileSystemEntries($currentDir)
            } catch {
                $errorCount++
                continue
            }

            foreach ($entryPath in $entries) {
                try {
                    # GetAttributes 利用操作系统枚举时的缓存，零额外磁盘 IO
                    $attr = [System.IO.File]::GetAttributes($entryPath)

                    if ($attr -band [System.IO.FileAttributes]::Directory) {
                        # 目录：排除指定文件夹，其余加入队列继续遍历
                        $dirName = [System.IO.Path]::GetFileName($entryPath)
                        if ($excludeDirs -notcontains $dirName) {
                            $dirQueue.Enqueue($entryPath)
                        }
                    } else {
                        # 文件：仅创建轻量 FileInfo 获取 Length
                        $fi   = New-Object System.IO.FileInfo($entryPath)
                        $size = $fi.Length
                        $name = $fi.Name

                        $csvPathEscaped = $entryPath -replace ',', '，'
                        $csvNameEscaped = $name      -replace ',', '，'

                        if ($script:fullWriter) {
                            $script:fullWriter.WriteLine("$drive,$csvPathEscaped,$csvNameEscaped,$size")
                        }

                        $fileCount++

                        # ceil(size/cluster) * cluster: 小于1簇的文件自动占满1簇
                        # 例如 412B 在 4096B 簇: ceil(412/4096)*4096 = 4096B
                        $oldAlignedSize = [math]::Ceiling($size / $OldCluster) * $OldCluster
                        $newAlignedSize = [math]::Ceiling($size / $NewCluster) * $NewCluster
                        $oldAlignedBytes += $oldAlignedSize
                        $newAlignedBytes += $newAlignedSize

                        if ($size -lt $SmallFileThreshold) {
                            $smallFileCount++
                            if ($script:smallWriter) {
                                $script:smallWriter.WriteLine("$drive,$csvPathEscaped,$csvNameEscaped,$size")
                            }
                        }

                        # 每 50000 个文件报告一次进度，减少性能开销
                        if ($fileCount % 50000 -eq 0) {
                            $elapsed = ((Get-Date) - $startTime).TotalSeconds
                            Write-Progress -Activity "扫描 $drivePath (已跳过 AppData / WeChat Files)" `
                                -Status "已处理 $fileCount 文件, 耗时 ${elapsed}s" `
                                -PercentComplete -1
                        }
                    }
                } catch {
                    $errorCount++
                }
            }
        }
    } catch {
        Write-Host "  扫描出错: $_" -ForegroundColor Red
    }

    $endTime = Get-Date
    $elapsed = ($endTime - $startTime).TotalSeconds

    # 数学保证: newAligned >= oldAligned
    $diffBytes = $newAlignedBytes - $oldAlignedBytes

    $driveUsedGB     = [math]::Round($driveUsed / 1GB, 2)
    $oldAlignedGB    = [math]::Round($oldAlignedBytes / 1GB, 2)
    $newAlignedGB    = [math]::Round($newAlignedBytes / 1GB, 2)
    $diffGB          = [math]::Round($diffBytes / 1GB, 2)

    $totalOldAlignedAll += $oldAlignedBytes
    $totalNewAlignedAll += $newAlignedBytes
    $totalDriveUsedAll  += $driveUsed
    $totalSmallCountAll += $smallFileCount

    $results += [PSCustomObject]@{
        Drive          = $drive
        FileCount      = $fileCount
        SmallCount     = $smallFileCount
        ErrorCount     = $errorCount
        DriveUsedGB    = $driveUsedGB
        OldAlignedGB   = $oldAlignedGB
        NewAlignedGB   = $newAlignedGB
        DiffGB         = $diffGB
        ElapsedSec     = [math]::Round($elapsed, 1)
    }

    Write-Progress -Activity "扫描 $drivePath" -Completed
    Write-Host "  文件总数: $fileCount | <4KB: $smallFileCount | 错误: $errorCount | 耗时: ${elapsed}s" -ForegroundColor Gray
    Write-Host "  当前文件占用(512B对齐): ${oldAlignedGB}GB -> 迁移后(4K对齐): ${newAlignedGB}GB (+${diffGB}GB)" -ForegroundColor Gray
    Write-Host "  磁盘实际已用(GPSDrive): ${driveUsedGB}GB (含NTFS元数据)" -ForegroundColor DarkGray
    Write-Host ""
}

# 关闭 StreamWriter
if ($script:smallWriter) {
    try { $script:smallWriter.Flush(); $script:smallWriter.Close(); $script:smallWriter.Dispose() } catch { }
}
if ($script:fullWriter) {
    try { $script:fullWriter.Flush(); $script:fullWriter.Close(); $script:fullWriter.Dispose() } catch { }
}

# ============================================================
# 汇总输出
# ============================================================
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " 汇总结果 (文件内容占用对比，同一批文件，两种对齐方式)" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host ("{0,-6} {1,10} {2,12} {3,10} {4,10} {5,10}" -f "分区", "文件总数", "<4KB数", "512B对齐", "4096B对齐", "增加") -ForegroundColor White
Write-Host ("{0,-6} {1,10} {2,12} {3,10} {4,10} {5,10}" -f "----", "--------", "------", "-------", "--------", "----") -ForegroundColor White

$origParts = @()
$newParts  = @()

foreach ($r in $results) {
    Write-Host ("{0,-6} {1,10:N0} {2,12:N0} {3,8}GB {4,8}GB {5,8}GB" -f
        "$($r.Drive):",
        $r.FileCount,
        $r.SmallCount,
        $r.OldAlignedGB,
        $r.NewAlignedGB,
        $r.DiffGB)

    $origParts += "$($r.Drive):$($r.OldAlignedGB)GB"
    $newParts  += "$($r.Drive):$($r.NewAlignedGB)GB"
}

Write-Host ""
Write-Host "原占用磁盘空间(512B对齐):   $($origParts -join ' ')" -ForegroundColor Yellow
Write-Host "预估迁移至新磁盘占用空间(4K对齐): $($newParts -join ' ')" -ForegroundColor Yellow

$totalOldAlignedGB = [math]::Round($totalOldAlignedAll / 1GB, 2)
$totalNewAlignedGB = [math]::Round($totalNewAlignedAll / 1GB, 2)
$totalDiffGB       = [math]::Round(($totalNewAlignedAll - $totalOldAlignedAll) / 1GB, 2)
$totalDriveUsedGB  = [math]::Round($totalDriveUsedAll / 1GB, 2)

Write-Host ""
Write-Host ("总计: 512B对齐 ${totalOldAlignedGB}GB -> 4K对齐 ${totalNewAlignedGB}GB (增加 ${totalDiffGB}GB)") -ForegroundColor Cyan
Write-Host ("      磁盘管理器中显示已用: ${totalDriveUsedGB}GB (含NTFS元数据、权限拒绝的目录等)") -ForegroundColor DarkGray

# ============================================================
# 分区空间阈值检测
# ============================================================
$thresholds = @{ 'D' = 180; 'E' = 180; 'F' = 29 }
$warnings = @()
foreach ($r in $results) {
    if ($thresholds.ContainsKey($r.Drive) -and $r.NewAlignedGB -gt $thresholds[$r.Drive]) {
        $warnings += "$($r.Drive): 预估 $($r.NewAlignedGB)GB > 阈值 $($thresholds[$r.Drive])GB"
    }
}
if ($warnings.Count -gt 0) {
    $driveNames = ($warnings | ForEach-Object { $_ -replace ':.*$', '' }) -join '、'
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        "磁盘 $driveNames 超出了新硬盘的最大空间利用值，需要注意磁盘分区空间大小。",
        '磁盘空间警告',
        'OK',
        'Warning'
    )
}

Write-Host ""
Write-Host "输出文件:" -ForegroundColor Green
Write-Host "  全量文件清单:    $fullCsvPath" -ForegroundColor Green
Write-Host "  <4KB 小文件清单:  $smallCsvPath ($totalSmallCountAll 个)" -ForegroundColor Green
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " 计算完成" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
