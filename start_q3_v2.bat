@echo off
setlocal EnableExtensions DisableDelayedExpansion
pushd "%~dp0"
if errorlevel 1 goto location_failed

if not exist "models\Baseline_v2.0_*.zip" goto missing_model
if not exist "requirements-q3-v2.txt" goto missing_model
if exist ".venv\Scripts\python.exe" goto check_environment

echo Preparing the Q3 v2.0 environment for the first run...
py -3 -c "import sys, struct; sys.exit(0 if sys.version_info >= (3, 10) and struct.calcsize('P') == 8 else 1)" >nul 2>&1
if not errorlevel 1 goto create_with_py
python -c "import sys, struct; sys.exit(0 if sys.version_info >= (3, 10) and struct.calcsize('P') == 8 else 1)" >nul 2>&1
if not errorlevel 1 goto create_with_python
goto missing_python

:create_with_py
py -3 -m venv ".venv"
if errorlevel 1 goto environment_failed
goto check_environment

:create_with_python
python -m venv ".venv"
if errorlevel 1 goto environment_failed
goto check_environment

:check_environment
".venv\Scripts\python.exe" -c "import sys, struct; sys.exit(0 if sys.version_info >= (3, 10) and struct.calcsize('P') == 8 else 1)" >nul 2>&1
if errorlevel 1 goto invalid_environment

echo Checking Q3 v2.0 desktop and original model dependencies...
".venv\Scripts\python.exe" -m pip install -r "requirements-q3-v2.txt"
if errorlevel 1 goto dependency_failed
".venv\Scripts\python.exe" -c "from PySide6 import QtWidgets; import numpy, numba, scipy, mpmath" >nul 2>&1
if errorlevel 1 goto dependency_failed

:launch
echo Starting Q3 v2.0: 7-point hexagon C/U model...
".venv\Scripts\python.exe" -m enhanced gui --model hexagon_v2 --error-model baseline_fixed_field
set "_q3v2_exit=%errorlevel%"
if "%_q3v2_exit%"=="0" goto success
echo.
echo Q3 v2.0 could not start. Read the error above before closing this window.
pause
popd
exit /b %_q3v2_exit%

:success
popd
exit /b 0

:missing_model
echo Q3 v2.0 model or dependency files were not found.
echo Extract the entire simulator repository, including the models folder.
pause
popd
exit /b 1

:missing_python
echo Python 3.10 or newer was not found. Python 3.12 is recommended.
echo Install 64-bit Python for Windows and enable the Python launcher or PATH option.
echo Then double-click this launcher again.
pause
popd
exit /b 1

:environment_failed
echo Failed to create the local Python environment. Read the error above.
pause
popd
exit /b 1

:invalid_environment
echo The existing .venv is incompatible or its Python executable is unavailable.
echo Rename the .venv folder to keep a backup, then run this launcher again.
pause
popd
exit /b 1

:dependency_failed
echo Dependency installation or import failed. Read the error above and retry.
echo The first installation requires Internet access.
pause
popd
exit /b 1

:location_failed
echo Cannot open the simulator directory. Extract the full ZIP to a writable folder.
pause
exit /b 1
