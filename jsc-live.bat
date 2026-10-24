@echo off
rem Start the live subtitle window with the jsc conda environment.
rem   Double-click        = subtitles + reply suggestions for audio the PC is playing
rem   jsc-live.bat --source mic --suggest    = for your microphone
rem Any arguments are passed to "python -m jsc_live" (see app\README.md).
setlocal
set "PY=%USERPROFILE%\anaconda3\envs\jsc\python.exe"
if not exist "%PY%" (
  echo [jsc-live] Python of the conda env "jsc" was not found at: %PY%
  pause
  exit /b 1
)
cd /d "%~dp0app"
if "%~1"=="" (
  "%PY%" -m jsc_live --source loopback --suggest
) else (
  "%PY%" -m jsc_live %*
)
if errorlevel 1 pause
