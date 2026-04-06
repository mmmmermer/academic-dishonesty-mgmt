param(
    [string]$DbUser = "hustapp",
    [string]$DbPassword = "123456",
    [string]$DbHost = "127.0.0.1",
    [int]$DbPort = 5432,
    [string]$DbName = "hustsystem",
    [string]$AppHost = "127.0.0.1",
    [int]$AppPort = 8501,
    [int]$HealthIntervalMinutes = 3
)

$ErrorActionPreference = "Stop"

if ($HealthIntervalMinutes -lt 1) {
    throw "HealthIntervalMinutes must be >= 1."
}

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$startScript = Join-Path $PSScriptRoot "start_app_postgres_bg.ps1"
$healthScript = Join-Path $PSScriptRoot "healthcheck_app_postgres.ps1"
$tmpDir = Join-Path $repo "tmp"
$configRoot = Join-Path $tmpDir "task_cfg"
$configPath = Join-Path $configRoot ("task_postgres_{0}.json" -f $AppPort)
$psExe = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
$taskStart = "HustSystem-Start-${AppPort}"
$taskHealth = "HustSystem-Health-${AppPort}"

if (!(Test-Path $startScript)) { throw "Missing script: $startScript" }
if (!(Test-Path $healthScript)) { throw "Missing script: $healthScript" }
if (!(Test-Path $psExe)) { throw "PowerShell not found: $psExe" }
New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
New-Item -ItemType Directory -Path $configRoot -Force | Out-Null

& cmd.exe /c "schtasks /Query >nul 2>nul"
if ($LASTEXITCODE -ne 0) {
    throw "Task Scheduler is unavailable in current context. Run this script in local PowerShell with sufficient permissions."
}

$config = [ordered]@{
    DbUser = $DbUser
    DbPassword = $DbPassword
    DbHost = $DbHost
    DbPort = $DbPort
    DbName = $DbName
    AppHost = $AppHost
    AppPort = $AppPort
}
$config | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding UTF8

$argStart = "-NoProfile -ExecutionPolicy Bypass -File `"$startScript`" -ConfigPath `"$configPath`""
$argHealth = "-NoProfile -ExecutionPolicy Bypass -File `"$healthScript`" -ConfigPath `"$configPath`" -AutoStart"

if ($argStart.Length -gt 220 -or $argHealth.Length -gt 220) {
    throw "Task command too long. Please shorten workspace path."
}

function Invoke-SchtasksChecked {
    param([string[]]$TaskArgs)
    if (!$TaskArgs -or $TaskArgs.Count -eq 0) {
        throw "Empty schtasks arguments."
    }
    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $output = & schtasks.exe @TaskArgs 2>&1
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $oldEap
    if ($exitCode -ne 0) {
        throw "schtasks failed. Args: $($TaskArgs -join ' ') ExitCode=$exitCode Output=$($output -join ' ')"
    }
}

Invoke-SchtasksChecked -TaskArgs @("/Create", "/F", "/TN", $taskStart, "/SC", "ONLOGON", "/TR", "`"$psExe`" $argStart")
Invoke-SchtasksChecked -TaskArgs @("/Create", "/F", "/TN", $taskHealth, "/SC", "MINUTE", "/MO", "$HealthIntervalMinutes", "/TR", "`"$psExe`" $argHealth")

Write-Host "Installed tasks:"
Write-Host "  $taskStart  (on logon)"
Write-Host "  $taskHealth (every ${HealthIntervalMinutes} minute(s))"
Write-Host "Config file:"
Write-Host "  $configPath"
Write-Host "List tasks with:"
Write-Host "  schtasks /Query /TN $taskStart /V /FO LIST"
Write-Host "  schtasks /Query /TN $taskHealth /V /FO LIST"
if ($DbPassword -eq "123456") {
    Write-Warning "Database password is still default 123456. Change it before production."
}
