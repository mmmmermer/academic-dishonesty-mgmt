param(
    [int]$AppPort = 8501
)

$ErrorActionPreference = "Stop"

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pidFile = Join-Path (Join-Path $repo "tmp") ("streamlit_pg_{0}.pid" -f $AppPort)

function Get-ListeningPidsByPort {
    param([int]$Port)
    $lines = netstat -ano | Select-String ":$Port" | Select-String "LISTENING"
    $pids = @()
    foreach ($line in $lines) {
        $parts = ($line.ToString().Trim() -split "\s+")
        if ($parts.Length -ge 5) {
            $pidVal = 0
            if ([int]::TryParse($parts[$parts.Length - 1], [ref]$pidVal)) {
                $pids += $pidVal
            }
        }
    }
    return ($pids | Select-Object -Unique)
}

if (!(Test-Path $pidFile)) {
    Write-Warning "No PID file found for port ${AppPort}: $pidFile"
    $fallbackPids = Get-ListeningPidsByPort -Port $AppPort
    if (!$fallbackPids) {
        Write-Host "Port $AppPort not listening."
        exit 0
    }
    foreach ($listenPid in $fallbackPids) {
        try {
            Stop-Process -Id $listenPid -Force -ErrorAction Stop
            Write-Host "Stopped listener PID=$listenPid on port $AppPort."
        } catch {
            Write-Warning "Could not stop listener PID=$listenPid on port $AppPort."
        }
    }
    Start-Sleep -Milliseconds 400
    $afterFallback = Get-ListeningPidsByPort -Port $AppPort
    if ($afterFallback) {
        Write-Warning "Port $AppPort still listening after fallback cleanup."
    } else {
        Write-Host "Port $AppPort released."
    }
    exit 0
}

$pidText = (Get-Content -LiteralPath $pidFile -Raw).Trim()
$targetPid = 0
if (!([int]::TryParse($pidText, [ref]$targetPid))) {
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    throw "Invalid PID content in file: $pidFile"
}

$stopped = $false
try {
    $proc = Get-Process -Id $targetPid -ErrorAction Stop
    Stop-Process -Id $proc.Id -Force -ErrorAction Stop
    $stopped = $true
    Write-Host "Stopped process PID=$($proc.Id) for port $AppPort."
} catch {
    Write-Warning "Process PID=$targetPid is not running. Cleaning stale PID file."
}

Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue

Start-Sleep -Milliseconds 500
$portStillBusy = Get-ListeningPidsByPort -Port $AppPort
if ($portStillBusy) {
    foreach ($listenPid in $portStillBusy) {
        try {
            Stop-Process -Id $listenPid -Force -ErrorAction Stop
            Write-Host "Stopped leftover listener PID=$listenPid on port $AppPort."
        } catch {
            Write-Warning "Could not stop leftover PID=$listenPid on port $AppPort."
        }
    }
    Start-Sleep -Milliseconds 400
    $portStillBusyAfterCleanup = Get-ListeningPidsByPort -Port $AppPort
    if ($portStillBusyAfterCleanup) {
        Write-Warning "Port $AppPort still listening after cleanup. Manual check needed."
    } else {
        Write-Host "Port $AppPort released."
    }
} else {
    if ($stopped) {
        Write-Host "Port $AppPort released."
    } else {
        Write-Host "Port $AppPort not listening."
    }
}
