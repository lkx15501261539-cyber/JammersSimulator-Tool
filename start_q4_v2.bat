@echo off
setlocal EnableExtensions DisableDelayedExpansion
if not exist "%~dp0start_q4.bat" (
  echo Shared Q4 launcher not found. Extract the entire simulator repository.
  if /i not "%~1"=="--check-only" pause
  exit /b 1
)
call "%~dp0start_q4.bat" --v2 %*
exit /b %errorlevel%
