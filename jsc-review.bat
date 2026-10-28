@echo off
rem Local archive review only: no live recording or API startup.
setlocal
set "PY=%USERPROFILE%\anaconda3\envs\jsc\python.exe"
if not exist "%PY%" (
  echo [jsc-review] Python of the conda env "jsc" was not found at: %PY%
  pause
  exit /b 1
)
cd /d "%~dp0app"
"%PY%" -m jsc_live --review-window
if errorlevel 1 pause
