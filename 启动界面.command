#!/bin/zsh
# Q3 v2 by default; start_q4.command selects the shared Q4 path.
typeset -i jammers_problem=3 jammers_check_only=0
typeset -a jammers_gui_args=()
while (( $# )); do
  case "$1" in
    --q4) jammers_problem=4 ;;
    --check-only) jammers_check_only=1 ;;
    --) shift; jammers_gui_args+=("$@"); break ;;
    *) jammers_gui_args+=("$1") ;;
  esac
  shift
done

jammers_fail() {
  local jammers_code="$1"
  shift
  print -r -- "$*" >&2
  if (( ! jammers_check_only )) && [[ -t 0 ]]; then
    read -r '?按回车关闭此窗口。'
  fi
  exit "$jammers_code"
}

# Resolve an explicit override before changing directory, without resolving the
# venv Python symlink: its original path is needed to select that environment.
jammers_python=''
if [[ -n "${JAMMERS_PYTHON:-}" ]]; then
  jammers_python="${JAMMERS_PYTHON:a}"
fi
cd -- "${0:a:h}" || jammers_fail 1 '无法打开模拟器文件夹，请完整解压后重试。'
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8

if (( jammers_problem == 4 )); then
  [[ -f model_sources/q4/q4.py && -f model_sources/q4/cu.py ]] || \
    jammers_fail 1 '缺少 model_sources/q4 模型文件，请完整解压模拟器。'
else
  [[ -f 'models/Baseline_v2.0_七点六边形.zip' && -f requirements-q3-v2.txt ]] || \
    jammers_fail 1 '缺少 Q3 v2.0 模型包或依赖清单，请完整解压模拟器。'
fi

jammers_python_compatible() {
  [[ -x "$1" ]] && "$1" -c \
    "import sys, struct; sys.exit(0 if sys.version_info >= (3, 10) and struct.calcsize('P') == 8 else 1)" \
    >/dev/null 2>&1
}

if [[ -n "$jammers_python" ]]; then
  jammers_python_compatible "$jammers_python" || \
    jammers_fail 1 'JAMMERS_PYTHON 指定的 Python 不可用，需要 Python 3.10 或以上的 64 位环境。'
elif [[ -e .venv || -L .venv ]]; then
  jammers_python="$PWD/.venv/bin/python"
  jammers_python_compatible "$jammers_python" || \
    jammers_fail 1 '现有 .venv 不兼容或不完整。请先将它改名备份，再重新启动；脚本不会删除它。'
else
  print -r -- '首次启动：准备本文件夹内的 Python 环境。'
  typeset -a jammers_candidates=(
    /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12
    /opt/homebrew/bin/python3.12
    /usr/local/bin/python3.12
  )
  for jammers_name in python3.12 python3.13 python3.11 python3.10 python3; do
    jammers_candidate="$(command -v "$jammers_name" 2>/dev/null)" || continue
    jammers_candidates+=("$jammers_candidate")
  done
  for jammers_candidate in "${jammers_candidates[@]}"; do
    if jammers_python_compatible "$jammers_candidate"; then
      jammers_python="$jammers_candidate"
      break
    fi
  done
  [[ -n "$jammers_python" ]] || \
    jammers_fail 1 '未找到 Python 3.10 或以上的 64 位环境。请从 python.org 安装 macOS 版 Python 3.12 后重试。'
  "$jammers_python" -m venv .venv
  jammers_code=$?
  (( jammers_code == 0 )) || jammers_fail "$jammers_code" '无法创建 Python 环境，请查看上方错误。'
  jammers_python="$PWD/.venv/bin/python"
fi

jammers_check_dependencies() {
  "$jammers_python" - "$jammers_problem" <<'PY'
import sys
from PySide6 import QtWidgets, __version_info__
assert (6, 6) <= __version_info__ < (7,), 'PySide6 must be >=6.6,<7'
if sys.argv[1] == '3':
    import importlib.metadata as md
    from pathlib import Path
    from pip._vendor.packaging.requirements import Requirement
    import numpy, numba, scipy, mpmath
    requirements = [Requirement(line.strip()) for line in
                    Path('requirements-q3-v2.txt').read_text(encoding='utf-8').splitlines()
                    if line.strip() and not line.lstrip().startswith('#')]
    assert all(r.specifier.contains(md.version(r.name)) for r in requirements), 'Dependency version mismatch'
PY
}

print -r -- "检查 Q${jammers_problem} 桌面与模型依赖：$jammers_python"
if ! jammers_check_dependencies >/dev/null 2>&1; then
  print -r -- '安装缺失或版本不符的依赖，此步骤需要联网。'
  if (( jammers_problem == 4 )); then
    "$jammers_python" -m pip install 'PySide6>=6.6,<7'
  else
    "$jammers_python" -m pip install -r requirements-q3-v2.txt
  fi
  jammers_code=$?
  (( jammers_code == 0 )) || jammers_fail "$jammers_code" '依赖安装失败，请查看上方错误并检查网络。'
  jammers_check_dependencies
  jammers_code=$?
  (( jammers_code == 0 )) || jammers_fail "$jammers_code" '安装后依赖校验仍未通过，请查看上方错误。'
fi

if (( jammers_check_only )); then
  "$jammers_python" - "$jammers_problem" <<'PY'
import sys
problem = int(sys.argv[1])
if problem == 4:
    from enhanced.q4_adapter import _controller_module
    model = _controller_module()
    assert model.discovery_certificate(model.search_points())['passed']
else:
    from enhanced.baseline import default_archive, prepare_model
    prepare_model(default_archive('hexagon_v2'), 'hexagon_v2')
from PySide6.QtWidgets import QApplication
from enhanced.ui import Window
from enhanced.world import ScenarioConfig
from enhanced.strategy import DEFAULT_Q1
app = QApplication(['launcher-check', '-platform', 'offscreen'])
w = Window(ScenarioConfig(problem=problem, error_model='worst_edge' if problem == 4 else 'baseline_fixed_field'), DEFAULT_Q1)
if problem == 3:
    w.model.setCurrentIndex(w.model.findData('hexagon_v2'))
w.show()
app.processEvents()
assert w.model.currentData() == ('q4_cu' if problem == 4 else 'hexagon_v2')
w.close()
print(f'PASS: Q{problem} model and desktop window')
PY
else
  if (( jammers_problem == 4 )); then
    print -r -- '打开第四问：25 点 C/U 模型。'
    "$jammers_python" -m enhanced gui --problem 4 --error-model worst_edge "${jammers_gui_args[@]}"
  else
    print -r -- '打开第三问：七点六边形 Baseline 2.0。'
    "$jammers_python" -m enhanced gui --model hexagon_v2 --error-model baseline_fixed_field "${jammers_gui_args[@]}"
  fi
fi
jammers_code=$?
(( jammers_code == 0 )) || jammers_fail "$jammers_code" '模拟器启动或校验失败，请查看上方错误。'
exit 0
