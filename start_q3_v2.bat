@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
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
".venv\Scripts\python.exe" -c "import sys; import importlib.metadata as md; from pathlib import Path; from pip._vendor.packaging.requirements import Requirement; from PySide6 import QtWidgets; import numpy, numba, scipy, mpmath; specs=[Requirement(s) for s in Path('requirements-q3-v2.txt').read_text().splitlines() if s.strip() and not s.startswith('#')]; sys.exit(0 if all(r.specifier.contains(md.version(r.name)) for r in specs) else 1)" >nul 2>&1
if not errorlevel 1 goto ready

echo Installing missing dependencies. Internet access is required for installation.
".venv\Scripts\python.exe" -m pip install -r "requirements-q3-v2.txt"
if errorlevel 1 goto dependency_failed
".venv\Scripts\python.exe" -c "from PySide6 import QtWidgets; import numpy, numba, scipy, mpmath" >nul 2>&1
if errorlevel 1 goto dependency_failed

:ready
if /i "%~1"=="--check-only" goto check_only

:launch
echo Starting Q3 v2.0: 7-point hexagon C/U model...
".venv\Scripts\python.exe" -m enhanced gui --model hexagon_v2 --error-model baseline_fixed_field
set "_q3v2_exit=%errorlevel%"
if "%_q3v2_exit%"=="0" goto success
echo.
echo Q3 v2.0 could not start. Read the error above before closing this window.
if /i not "%~1"=="--check-only" pause
popd
exit /b %_q3v2_exit%

:check_only
echo Checking the original Q3 v2.0 archive and desktop window...
".venv\Scripts\python.exe" -c "from enhanced.baseline import default_archive, prepare_model; prepare_model(default_archive('hexagon_v2'), 'hexagon_v2'); from PySide6.QtWidgets import QApplication; from enhanced.ui import Window; from enhanced.world import ScenarioConfig; from enhanced.strategy import DEFAULT_Q1; app=QApplication(['launcher-check', '-platform', 'offscreen']); w=Window(ScenarioConfig(error_model='baseline_fixed_field'), DEFAULT_Q1); w.model.setCurrentIndex(w.model.findData('hexagon_v2')); w.show(); app.processEvents(); assert w.model.currentData() == 'hexagon_v2'; w.close(); print('PASS: Q3 v2.0 model archive and desktop window')"
set "_q3v2_exit=%errorlevel%"
if "%_q3v2_exit%"=="0" goto success
echo Q3 v2.0 validation failed. Read the error above.
popd
exit /b %_q3v2_exit%

:success
popd
exit /b 0

:missing_model
echo Q3 v2.0 model or dependency files were not found.
echo Extract the entire simulator repository, including the models folder.
if /i not "%~1"=="--check-only" pause
popd
exit /b 1

:missing_python
echo Python 3.10 or newer was not found. Python 3.12 is recommended.
echo Install 64-bit Python for Windows and enable the Python launcher or PATH option.
echo Then double-click this launcher again.
if /i not "%~1"=="--check-only" pause
popd
exit /b 1

:environment_failed
echo Failed to create the local Python environment. Read the error above.
if /i not "%~1"=="--check-only" pause
popd
exit /b 1

:invalid_environment
echo The existing .venv is incompatible or its Python executable is unavailable.
echo Rename the .venv folder to keep a backup, then run this launcher again.
if /i not "%~1"=="--check-only" pause
popd
exit /b 1

:dependency_failed
echo Dependency installation or import failed. Read the error above and retry.
echo The first installation requires Internet access.
if /i not "%~1"=="--check-only" pause
popd
exit /b 1

:location_failed
echo Cannot open the simulator directory. Extract the full ZIP to a writable folder.
if /i not "%~1"=="--check-only" pause
exit /b 1
