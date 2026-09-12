@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "_q4_version=1"
if /i "%~1"=="--v2" (
  set "_q4_version=2"
  shift
)
pushd "%~dp0"
if errorlevel 1 goto location_failed

if not exist "model_sources\q4\q4.py" goto missing_model
if not exist "model_sources\q4\cu.py" goto missing_model
if not exist "requirements-q3-v2.txt" goto missing_model
if "%_q4_version%"=="2" if not exist "model_sources\q4\q4_v2.py" goto missing_model
if "%_q4_version%"=="2" if not exist "model_sources\q4\opportunities.py" goto missing_model
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
".venv\Scripts\python.exe" -c "import sys; import importlib.metadata as md; from pathlib import Path; from pip._vendor.packaging.requirements import Requirement; from PySide6 import QtWidgets; import numpy, numba, scipy, mpmath; specs=[Requirement(s) for s in Path('requirements-q3-v2.txt').read_text().splitlines() if s.strip() and not s.startswith('#')]; sys.exit(0 if all(r.specifier.contains(md.version(r.name)) for r in specs) else 1)" >nul 2>&1
if not errorlevel 1 goto ready

echo Installing dependencies for all five GUI models. Internet access is required once.
".venv\Scripts\python.exe" -m pip install -r "requirements-q3-v2.txt"
if errorlevel 1 goto dependency_failed
".venv\Scripts\python.exe" -c "import sys; import importlib.metadata as md; from pathlib import Path; from pip._vendor.packaging.requirements import Requirement; from PySide6 import QtWidgets; import numpy, numba, scipy, mpmath; specs=[Requirement(s) for s in Path('requirements-q3-v2.txt').read_text().splitlines() if s.strip() and not s.startswith('#')]; sys.exit(0 if all(r.specifier.contains(md.version(r.name)) for r in specs) else 1)" >nul 2>&1
if errorlevel 1 goto dependency_failed

:ready
if /i "%~1"=="--check-only" goto check_only

:launch
if "%_q4_version%"=="2" goto launch_v2
echo Starting Q4: 25-point C/U model...
".venv\Scripts\python.exe" -m enhanced gui --problem 4 --error-model worst_edge
goto launch_result

:launch_v2
echo Starting Q4 v2: certified left/right opportunity retesting...
".venv\Scripts\python.exe" -m enhanced gui --problem 4 --model q4_opportunity_v2 --error-model worst_edge

:launch_result
set "_q4_exit=%errorlevel%"
if "%_q4_exit%"=="0" goto success
echo.
echo Q4 could not start. Read the error above before closing this window.
if /i not "%~1"=="--check-only" pause
popd
exit /b %_q4_exit%

:check_only
if "%_q4_version%"=="2" goto check_only_v2
echo Checking the Q4 model, discovery certificate, and desktop window...
".venv\Scripts\python.exe" -c "from enhanced.q4_adapter import _controller_module; model=_controller_module(); assert model.discovery_certificate(model.search_points())['passed']; from PySide6.QtWidgets import QApplication; from enhanced.ui import Window; from enhanced.world import ScenarioConfig; from enhanced.strategy import DEFAULT_Q1; app=QApplication(['launcher-check', '-platform', 'offscreen']); w=Window(ScenarioConfig(problem=4, error_model='worst_edge'), DEFAULT_Q1); w.show(); app.processEvents(); assert w.model.currentData() == 'q4_cu'; w.close(); print('PASS: Q4 model, discovery certificate, and desktop window')"
goto check_result

:check_only_v2
echo Checking the Q4 v2 model, discovery certificate, and desktop window...
".venv\Scripts\python.exe" -c "from enhanced.q4_adapter import _controller_module; model=_controller_module(model='q4_opportunity_v2'); assert model.discovery_certificate(model.search_points())['passed']; from PySide6.QtWidgets import QApplication; from enhanced.ui import Window; from enhanced.world import ScenarioConfig; from enhanced.strategy import DEFAULT_Q1; app=QApplication(['launcher-check', '-platform', 'offscreen']); w=Window(ScenarioConfig(problem=4, error_model='worst_edge'), DEFAULT_Q1); w.model.setCurrentIndex(w.model.findData('q4_opportunity_v2')); w.show(); app.processEvents(); assert w.model.currentData() == 'q4_opportunity_v2'; w.close(); print('PASS: Q4 v2 model, discovery certificate, and desktop window')"

:check_result
set "_q4_exit=%errorlevel%"
if "%_q4_exit%"=="0" goto success
echo Q4 validation failed. Read the error above.
popd
exit /b %_q4_exit%

:success
popd
exit /b 0

:missing_model
echo Q4 model files or the unified dependency list were not found.
echo Extract the entire simulator repository before starting.
if /i not "%~1"=="--check-only" pause
popd
exit /b 1

:missing_python
echo Python 3.10 or newer was not found.
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
echo Unified GUI dependency installation failed. Check the network and the error above, then retry.
if /i not "%~1"=="--check-only" pause
popd
exit /b 1

:location_failed
echo Cannot open the simulator directory. Extract the full ZIP to a writable folder.
if /i not "%~1"=="--check-only" pause
exit /b 1
