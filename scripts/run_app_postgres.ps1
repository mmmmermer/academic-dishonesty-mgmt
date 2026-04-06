param(
    [string]$DbUser = "hustapp",
    [string]$DbPassword = "123456",
    [string]$DbHost = "127.0.0.1",
    [int]$DbPort = 5432,
    [string]$DbName = "hustsystem",
    [string]$AppHost = "127.0.0.1",
    [int]$AppPort = 8501
)

$ErrorActionPreference = "Stop"

$repo = "E:\hustsystem"
$python = Join-Path $repo ".venv\Scripts\python.exe"
$app = Join-Path $repo "app.py"

if (!(Test-Path $python)) {
    throw "Python not found: $python"
}
if (!(Test-Path $app)) {
    throw "App file not found: $app"
}

$encodedPassword = [System.Uri]::EscapeDataString($DbPassword)
$env:DATABASE_URL = "postgresql+psycopg2://$DbUser`:$encodedPassword@$DbHost`:$DbPort/$DbName"
Write-Host "DATABASE_URL set for PostgreSQL target: ${DbHost}:${DbPort}/${DbName} (user=$DbUser)"
if ($DbPassword -eq "123456") {
    Write-Warning "Database password is still the default value 123456. Change it before production deployment."
}

& $python -m streamlit run $app --server.address=$AppHost --server.port=$AppPort
