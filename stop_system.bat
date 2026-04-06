@echo off
setlocal

set "ROOT_DIR=%~dp0"
set "PS_SCRIPT=%ROOT_DIR%scripts\stop_app_postgres_bg.ps1"
set "APP_PORT=8501"

if not exist "%PS_SCRIPT%" (
  echo [ERROR] Script not found: %PS_SCRIPT%
  pause
  exit /b 1
)

if not "%~1"=="" set "APP_PORT=%~1"

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%" -AppPort %APP_PORT%
if errorlevel 1 (
  echo [ERROR] Stop encountered an error.
  pause
  exit /b 1
)

echo [OK] Stop command finished for port %APP_PORT%.
exit /b 0

