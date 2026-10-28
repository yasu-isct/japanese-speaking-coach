@echo off
rem Normal version: subtitles + reply suggestions, NOT recorded.
rem   Double-click                              = audio the PC is playing (videos, online classes, calls)
rem   jsc-live.bat --source mic --suggest       = your microphone
rem To record the session, use jsc-live-Â¼Òô.bat instead.
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
  "%PY%" -m jsc_live --source loopback --suggest --no-record
) else (
  "%PY%" -m jsc_live %* --no-record
)
if errorlevel 1 pause
