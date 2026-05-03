<# ===========================
 DualSaver.ps1（Windows標準のみ）
 監視フォルダに置いた/更新したファイルを自動で2か所へ複製。
=========================== #>

### ====== CONFIG ====== ###
# 監視元（ここに「放り込む」）
$WatchRoot      = "D:\Inbox"              # 例：D:\Inbox
# 保存先A / B
$DestA          = "E:\PatentStoreA"       # 例：外付けHDD
$DestB          = "F:\PatentStoreB"       # 例：別HDD or 別パーティション
# ログ出力（CSV）
$LogPath        = "D:\DualSaverLogs\DualSaver_log.csv"
# 検証や動作チューニング
$EnableHash     = $true       # コピー後にSHA256で整合性検証
$RetryMax       = 3           # 失敗時の最大リトライ回数
$DebounceMs     = 1200        # 検出→処理までの待機（ms）
$CopyTimeoutSec = 300         # 大容量ファイルのタイムアウト目安（秒）
$PreserveTree   = $true       # 監視ルート相対のフォルダ構造を保存先で再現する
# フィルタ（空配列は無制限）
$AllowExt       = @(".txt",".md",".docx",".xlsx",".pptx",".pdf")  # 例
$MaxSizeBytes   = 0  # 0=無制限。例：1GB=1073741824
### ==================== ###

# 事前作成
New-Item -ItemType Directory -Force -Path $WatchRoot,$DestA,$DestB,(Split-Path $LogPath) | Out-Null
if (!(Test-Path $LogPath)) { "Timestamp,Action,Source,DestA,DestB,Result,TimeMs,HashMatch,Note" | Out-File -Encoding UTF8 $LogPath }

# ユーティリティ
function Write-Log($action,$src,$da,$db,$result,$timems,$hashOk,$note) {
    $ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss.fff")
    "$ts,$action,""{0}"",""{1}"",""{2}"",$result,$timems,$hashOk,""{3}""" -f $src,$da,$db,$note |
        Add-Content -Path $LogPath -Encoding UTF8
}

function Get-RelPath($full,$root) {
    $rel = Resolve-Path -LiteralPath $full -ErrorAction SilentlyContinue
    if (!$rel) { return "" }
    $rel = [IO.Path]::GetRelativePath($root, $full)
    return $rel
}

function Ensure-Dir($path) {
    $dir = Split-Path -LiteralPath $path -Parent
    if ($dir -and !(Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
}

function Next-Versioned-Path($destPath) {
    if (!(Test-Path -LiteralPath $destPath)) { return $destPath }
    $dir = Split-Path $destPath -Parent
    $name = Split-Path $destPath -Leaf
    $base = [IO.Path]::GetFileNameWithoutExtension($name)
    $ext  = [IO.Path]::GetExtension($name)
    $date = Get-Date -Format "yyyy-MM-dd_HHmmss"
    $i = 1
    while ($true) {
        $candidate = Join-Path $dir ("{0}_{1}_{2:D3}{3}" -f $base,$date,$i,$ext)
        if (!(Test-Path -LiteralPath $candidate)) { return $candidate }
        $i++
    }
}

function Copy-With-Retry($src,$dst) {
    Ensure-Dir $dst
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $lastErr = ""
    for ($i=0; $i -le $RetryMax; $i++) {
        try {
            # 同名衝突はバージョン付与で回避
            $target = if (Test-Path -LiteralPath $dst) { Next-Versioned-Path $dst } else { $dst }
            Copy-Item -LiteralPath $src -Destination $target -Force -ErrorAction Stop
            $sw.Stop()
            return @{ Ok=$true; Path=$target; Ms=$sw.ElapsedMilliseconds; Err="" }
        } catch {
            $lastErr = $_.Exception.Message
            Start-Sleep -Milliseconds ([Math]::Min(5000, (300 * [math]::Pow(2,$i))))
        }
    }
    $sw.Stop()
    return @{ Ok=$false; Path=$dst; Ms=$sw.ElapsedMilliseconds; Err=$lastErr }
}

function Hash-Equal($p1,$p2) {
    try {
        $h1 = (Get-FileHash -LiteralPath $p1 -Algorithm SHA256).Hash
        $h2 = (Get-FileHash -LiteralPath $p2 -Algorithm SHA256).Hash
        return $h1 -eq $h2
    } catch { return $false }
}

# 監視セットアップ
$fsw = New-Object IO.FileSystemWatcher $WatchRoot
$fsw.IncludeSubdirectories = $true
$fsw.EnableRaisingEvents = $true
$fsw.NotifyFilter = [IO.NotifyFilters]'FileName, LastWrite, Size, DirectoryName'

$processing = [System.Collections.Concurrent.ConcurrentDictionary[string, datetime]]::new()

$handler = Register-ObjectEvent -InputObject $fsw -EventName "Changed" -Action {
    param($sender,$eventArgs)
    $path = $eventArgs.FullPath
    try {
        # 除外：一時ファイル / 隠し / 監視外
        if (!(Test-Path -LiteralPath $path)) { return }
        $leaf = Split-Path -LiteralPath $path -Leaf
        if ($leaf.StartsWith("~$")) { return }

        # デバウンス：最後に見た時刻から一定時間経過まで待つ
        $now = Get-Date
        $processing[$path] = $now
        Start-Sleep -Milliseconds $using:DebounceMs
        # 他のイベントで上書きされていないか
        if ($processing.ContainsKey($path)) {
            $last = $processing[$path]
            if ( (New-TimeSpan -Start $last -End (Get-Date)).TotalMilliseconds -lt ($using:DebounceMs - 50) ) { return }
            [void]$processing.TryRemove($path, [ref]([datetime]::MinValue))
        }

        # ファイル属性確認（フォルダはスキップ）
        if ((Get-Item -LiteralPath $path).PSIsContainer) { return }

        # フィルタ：拡張子／サイズ
        if ($using:AllowExt.Count -gt 0) {
            $ext = [IO.Path]::GetExtension($path).ToLowerInvariant()
            if (-not ($using:AllowExt -contains $ext)) { return }
        }
        if ($using:MaxSizeBytes -gt 0) {
            $len = (Get-Item -LiteralPath $path).Length
            if ($len -gt $using:MaxSizeBytes) { return }
        }

        # 相対パス → 保存先の最終パス
        $rel = [IO.Path]::GetRelativePath($using:WatchRoot, $path)
        $dstRel = if ($using:PreserveTree) { $rel } else { Split-Path -Leaf $path }
        $destAPath = Join-Path $using:DestA $dstRel
        $destBPath = Join-Path $using:DestB $dstRel

        # コピー（A→B）
        $resA = Copy-With-Retry -src $path -dst $destAPath
        $resB = Copy-With-Retry -src $path -dst $destBPath

        $hashOk = ""
        if ($using:EnableHash -and $resA.Ok -and $resB.Ok) {
            $hashOk = (Hash-Equal $path $resA.Path) -and (Hash-Equal $path $resB.Path)
        }

        $note = ""
        if (-not $resA.Ok -or -not $resB.Ok) {
            $note = "A:${($resA.Err)} | B:${($resB.Err)}"
        }

        Write-Log -action "COPY" -src $path -da $resA.Path -db $resB.Path `
                 -result ("A:{0}/B:{1}" -f $resA.Ok,$resB.Ok) `
                 -timems ([Math]::Max($resA.Ms,$resB.Ms)) -hashOk $hashOk -note $note

    } catch {
        Write-Log -action "ERROR" -src $path -da "" -db "" -result "EXCEPTION" -timems 0 -hashOk "" -note $_.Exception.Message
    }
}

Write-Host "DualSaver running. Watching: $WatchRoot"
Write-Host "Press Ctrl+C to stop."
while ($true) { Start-Sleep -Seconds 5 }
