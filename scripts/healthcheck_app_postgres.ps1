param(
    [string]$ConfigPath = "",
    [string]$DbUser = "hustapp",
    [string]$DbPassword = "123456",
    [string]$DbHost = "127.0.0.1",
    [int]$DbPort = 5432,
    [string]$DbName = "hustsystem",
    [string]$AppHost = "127.0.0.1",
    [int]$AppPort = 8501,
    [int]$HttpTimeoutSeconds = 5,
    [int]$StartupWaitSeconds = 20,
    [switch]$AutoStart = $true
)

$ErrorActionPreference = "Stop"

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$tmpDir = Join-Path $repo "tmp"
$lockFile = Join-Path $tmpDir ("healthcheck_{0}.lock" -f $AppPort)
$startScript = Join-Path $PSScriptRoot "start_app_postgres_bg.ps1"
$url = "http://${AppHost}:$AppPort"

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
    $lockFile = Join-Path $tmpDir ("healthcheck_{0}.lock" -f $AppPort)
    $url = "http://${AppHost}:$AppPort"
}

if (!(Test-Path $startScript)) {
    throw "Start script not found: $startScript"
}
New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null

$lockStream = $null
try {
    $lockStream = [System.IO.File]::Open($lockFile, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
} catch {
    Write-Host "Healthcheck skipped: lock is held by another process."
    exit 0
}

function Test-AppHttp {
    param(
        [string]$TargetUrl,
        [int]$TimeoutSec
    )
    try {
        $null = Invoke-WebRequest -Uri $TargetUrl -UseBasicParsing -TimeoutSec $TimeoutSec
        return $true
    } catch {
        return $false
    }
}

try {
    if (Test-AppHttp -TargetUrl $url -TimeoutSec $HttpTimeoutSeconds) {
        Write-Host "Healthy: $url"
        exit 0
    }

    if (-not $AutoStart) {
        Write-Warning "Unhealthy and AutoStart disabled: $url"
        exit 1
    }

    Write-Warning "Unhealthy detected, starting app on ${AppHost}:${AppPort} ..."
    if ($ConfigPath) {
        & $startScript -ConfigPath $ConfigPath
    } else {
        & $startScript `
            -DbUser $DbUser `
            -DbPassword $DbPassword `
            -DbHost $DbHost `
            -DbPort $DbPort `
            -DbName $DbName `
            -AppHost $AppHost `
            -AppPort $AppPort
    }

    $deadline = (Get-Date).AddSeconds($StartupWaitSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-AppHttp -TargetUrl $url -TimeoutSec $HttpTimeoutSeconds) {
            Write-Host "Recovered: $url"
            exit 0
        }
        Start-Sleep -Milliseconds 500
    }

    Write-Error "Recovery failed within ${StartupWaitSeconds}s: $url"
    exit 1
} finally {
    if ($lockStream) {
        $lockStream.Close()
        $lockStream.Dispose()
    }
}
