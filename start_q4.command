#!/bin/zsh
# Share environment preparation with the Q3 v2 launcher.
jammers_launcher="${0:a:h}/启动界面.command"
if [[ ! -f "$jammers_launcher" ]]; then
  print -r -- '缺少共用启动文件“启动界面.command”，请完整解压模拟器。' >&2
  jammers_check_only=0
  for jammers_arg in "$@"; do
    [[ "$jammers_arg" == --check-only ]] && jammers_check_only=1
  done
  if (( ! jammers_check_only )) && [[ -t 0 ]]; then
    read -r '?按回车关闭此窗口。'
  fi
  exit 1
fi
exec /bin/zsh "$jammers_launcher" --q4 "$@"
