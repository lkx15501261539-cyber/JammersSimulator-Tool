@echo off
setlocal EnableExtensions DisableDelayedExpansion
pushd "%~dp0"
if errorlevel 1 goto location_failed

if not exist "model_sources\q4\q4.py" goto missing_model
if not exist "model_sources\q4\cu.py" goto missing_model
if exist ".venv\Scripts\python.exe" goto check_environment

echo Preparing the Q4 environment for the first run...
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
".venv\Scripts\python.exe" -c "from PySide6 import QtWidgets" >nul 2>&1
if not errorlevel 1 goto launch

echo Installing the Q4 desktop dependency. Internet access is required once.
".venv\Scripts\python.exe" -m pip install "PySide6>=6.6,<7"
if errorlevel 1 goto dependency_failed

:launch
echo Starting Q4: 25-point C/U model...
".venv\Scripts\python.exe" -m enhanced gui --problem 4 --error-model worst_edge
set "_q4_exit=%errorlevel%"
if "%_q4_exit%"=="0" goto success
echo.
echo Q4 could not start. Read the error above before closing this window.
pause
popd
exit /b %_q4_exit%

:success
popd
exit /b 0

:missing_model
echo Q4 model files were not found in model_sources\q4.
echo Extract the entire simulator repository before starting.
pause
popd
exit /b 1

:missing_python
echo Python 3.10 or newer was not found.
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
echo PySide6 installation failed. Check the network and the error above, then retry.
pause
popd
exit /b 1

:location_failed
echo Cannot open the simulator directory. Extract the full ZIP to a writable folder.
pause
exit /b 1
