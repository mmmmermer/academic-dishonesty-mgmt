@echo off
setlocal

set "ROOT_DIR=%~dp0"
set "PS_SCRIPT=%ROOT_DIR%scripts\start_app_postgres_bg.ps1"
set "STOP_SCRIPT=%ROOT_DIR%scripts\stop_app_postgres_bg.ps1"
set "APP_PORT=8501"

if not exist "%PS_SCRIPT%" (
  echo [ERROR] Script not found: %PS_SCRIPT%
  pause
  exit /b 1
)

if not "%~2"=="" set "APP_PORT=%~2"

set "DB_PASSWORD=%~1"
if "%DB_PASSWORD%"=="" set "DB_PASSWORD=%HUST_DB_PASSWORD%"

if "%DB_PASSWORD%"=="" (
  set /p DB_PASSWORD=Please input PostgreSQL password for hustapp:
)

if "%DB_PASSWORD%"=="" (
  echo [ERROR] Empty password. Start canceled.
  pause
  exit /b 1
)

if exist "%STOP_SCRIPT%" (
  echo [INFO] Stopping existing app on port %APP_PORT% if any...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%STOP_SCRIPT%" -AppPort %APP_PORT% >nul 2>nul
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%" -DbPassword "%DB_PASSWORD%" -AppPort %APP_PORT%
if errorlevel 1 (
  echo [ERROR] Start failed.
  pause
  exit /b 1
)

echo.
echo [OK] App started on http://127.0.0.1:%APP_PORT%
start "" "http://127.0.0.1:%APP_PORT%"
exit /b 0
