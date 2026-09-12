@echo off
setlocal EnableExtensions DisableDelayedExpansion
if not exist "%~dp0start_q3_v2.bat" goto missing_launcher
call "%~dp0start_q3_v2.bat" %*
exit /b %errorlevel%

:missing_launcher
echo The Q3 v2.0 launcher was not found. Extract the entire simulator ZIP.
if /i not "%~1"=="--check-only" pause
exit /b 1
