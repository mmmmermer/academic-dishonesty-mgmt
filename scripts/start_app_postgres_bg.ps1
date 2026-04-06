param(
    [string]$ConfigPath = "",
    [string]$DbUser = "hustapp",
    [string]$DbPassword = "123456",
    [string]$DbHost = "127.0.0.1",
    [int]$DbPort = 5432,
    [string]$DbName = "hustsystem",
    [string]$AppHost = "127.0.0.1",
    [int]$AppPort = 8501
)

$ErrorActionPreference = "Stop"

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repo ".venv\Scripts\python.exe"
$app = Join-Path $repo "app.py"
$logDir = Join-Path $repo "logs"
$pidDir = Join-Path $repo "tmp"
$pidFile = Join-Path $pidDir ("streamlit_pg_{0}.pid" -f $AppPort)
$stdoutLog = Join-Path $logDir ("streamlit_pg_{0}.out.log" -f $AppPort)
$stderrLog = Join-Path $logDir ("streamlit_pg_{0}.err.log" -f $AppPort)
$hostExe = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

if ($ConfigPath) {
    if (!(Test-Path $ConfigPath)) {
        throw "Config file not found: $ConfigPath"
    }
    $cfg = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    if ($cfg.DbUser -and -not $PSBoundParameters.ContainsKey("DbUser")) { $DbUser = [string]$cfg.DbUser }
    if ($cfg.DbPassword -and -not $PSBoundParameters.ContainsKey("DbPassword")) { $DbPassword = [string]$cfg.DbPassword }
    if ($cfg.DbHost -and -not $PSBoundParameters.ContainsKey("DbHost")) { $DbHost = [string]$cfg.DbHost }
    if ($cfg.DbPort -and -not $PSBoundParameters.ContainsKey("DbPort")) { $DbPort = [int]$cfg.DbPort }
    if ($cfg.DbName -and -not $PSBoundParameters.ContainsKey("DbName")) { $DbName = [string]$cfg.DbName }
    if ($cfg.AppHost -and -not $PSBoundParameters.ContainsKey("AppHost")) { $AppHost = [string]$cfg.AppHost }
    if ($cfg.AppPort -and -not $PSBoundParameters.ContainsKey("AppPort")) { $AppPort = [int]$cfg.AppPort }
    $pidFile = Join-Path $pidDir ("streamlit_pg_{0}.pid" -f $AppPort)
    $stdoutLog = Join-Path $logDir ("streamlit_pg_{0}.out.log" -f $AppPort)
    $stderrLog = Join-Path $logDir ("streamlit_pg_{0}.err.log" -f $AppPort)
}

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

if (!(Test-Path $python)) {
    throw "Python not found: $python"
}
if (!(Test-Path $app)) {
    throw "App file not found: $app"
}
if (!(Test-Path $hostExe)) {
    throw "PowerShell host not found: $hostExe"
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
New-Item -ItemType Directory -Path $pidDir -Force | Out-Null

if (Test-Path $pidFile) {
    $pidText = (Get-Content -LiteralPath $pidFile -Raw).Trim()
    $oldPid = 0
    if ([int]::TryParse($pidText, [ref]$oldPid)) {
        try {
            $oldProc = Get-Process -Id $oldPid -ErrorAction Stop
            Write-Host "Already running. PID=$($oldProc.Id) Port=$AppPort"
            Write-Host "URL: http://${AppHost}:$AppPort"
            exit 0
        } catch {
            Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
        }
    } else {
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    }
}

$portInUse = netstat -ano | Select-String ":$AppPort" | Select-String "LISTENING"
if ($portInUse) {
    throw "Port $AppPort is already in use. Stop existing process or choose another port."
}

$encodedPassword = [System.Uri]::EscapeDataString($DbPassword)
$databaseUrl = "postgresql+psycopg2://$DbUser`:$encodedPassword@$DbHost`:$DbPort/$DbName"

# Child process sets DATABASE_URL and keeps streamlit in background.
$command = "$env:ALLOW_SQLITE_FALLBACK = '0'; $env:DATABASE_URL = '$databaseUrl'; & '$python' -m streamlit run '$app' --server.address=$AppHost --server.port=$AppPort 1>>'$stdoutLog' 2>>'$stderrLog'"

$proc = Start-Process `
    -FilePath $hostExe `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $command) `
    -WindowStyle Hidden `
    -PassThru

Set-Content -LiteralPath $pidFile -Value $proc.Id -Encoding ascii
Start-Sleep -Milliseconds 1200

if ($proc.HasExited) {
    $errTail = ""
    if (Test-Path $stderrLog) {
        $errTail = (Get-Content -LiteralPath $stderrLog | Select-Object -Last 20) -join [Environment]::NewLine
    }
    throw "Background start failed. Check logs: $stderrLog`n$errTail"
}

for ($i = 0; $i -lt 60; $i++) {
    $listenPids = Get-ListeningPidsByPort -Port $AppPort
    if ($listenPids.Count -gt 0) {
        Set-Content -LiteralPath $pidFile -Value ($listenPids[0]) -Encoding ascii
        break
    }
    Start-Sleep -Milliseconds 500
}

$savedPid = (Get-Content -LiteralPath $pidFile -Raw).Trim()
Write-Host "Started in background. WrapperPID=$($proc.Id) AppPID=$savedPid Port=$AppPort"
if ($savedPid -eq "$($proc.Id)") {
    Write-Warning "App listener PID not detected in time. PID file currently stores wrapper PID."
}
Write-Host "URL: http://${AppHost}:$AppPort"
Write-Host "PID file: $pidFile"
Write-Host "Stdout log: $stdoutLog"
Write-Host "Stderr log: $stderrLog"
if ($DbPassword -eq "123456") {
    Write-Warning "Database password is still default 123456. Change it before production."
}
