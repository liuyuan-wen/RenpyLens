@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "RENPYLENS_ACTION=run"
call "%~dp0scripts\launch_windows.bat" %*
exit /b %ERRORLEVEL%
