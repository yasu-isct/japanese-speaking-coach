@echo off
rem Recording version: same as jsc-live.bat, but the session is archived to logs\sessions\
rem (full audio, every sentence, suggestions) and that folder opens when the window is closed.
rem   Double-click                                   = audio the PC is playing
rem   jsc-live-Â¼Òô.bat --source mic --suggest       = your microphone
rem Recordings stay on this PC (logs\ is not committed). In a call they contain the other person's voice.
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
rem Open the newest session folder (set JSC_NO_OPEN=1 to skip, e.g. in tests).
if defined JSC_NO_OPEN goto :eof
for /f "delims=" %%d in ('dir /b /ad /o-n "%~dp0logs\sessions" 2^>nul') do (
  start "" explorer "%~dp0logs\sessions\%%d"
  goto :eof
)
