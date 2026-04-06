param(
    [int]$AppPort = 8501,
    [switch]$KeepConfig = $false
)

$ErrorActionPreference = "Stop"

$taskStart = "HustSystem-Start-${AppPort}"
$taskHealth = "HustSystem-Health-${AppPort}"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$configPath = Join-Path (Join-Path (Join-Path $repo "tmp") "task_cfg") ("task_postgres_{0}.json" -f $AppPort)

& cmd.exe /c "schtasks /Query >nul 2>nul"
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Task Scheduler is unavailable in current context. Skip task removal."
    if (-not $KeepConfig -and (Test-Path $configPath)) {
        Remove-Item -LiteralPath $configPath -Force -ErrorAction SilentlyContinue
        Write-Host "Removed config: $configPath"
    }
    exit 0
}

function Remove-TaskIfExists {
    param([string]$TaskName)
    & cmd.exe /c "schtasks /Query /TN ""$TaskName"" >nul 2>nul"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Task not found: $TaskName"
        return
    }
    & cmd.exe /c "schtasks /Delete /F /TN ""$TaskName"" >nul 2>nul"
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Removed task: $TaskName"
    } else {
        Write-Warning "Failed to remove task: $TaskName"
    }
}

Remove-TaskIfExists -TaskName $taskStart
Remove-TaskIfExists -TaskName $taskHealth

if (-not $KeepConfig -and (Test-Path $configPath)) {
    Remove-Item -LiteralPath $configPath -Force -ErrorAction SilentlyContinue
    Write-Host "Removed config: $configPath"
}
