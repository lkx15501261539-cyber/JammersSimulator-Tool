# Windows 模型启动入口

请先完整解压 `enhanced-mock` 仓库，安装 64 位 Python（推荐 3.12，脚本最低检查
3.10），并在首次准备依赖时联网。在仓库文件夹中双击：

| 文件 | 打开的模型 | 模型来源 |
| --- | --- | --- |
| `启动界面.bat` | 默认第三问 Baseline 2.0，可在界面切换模型 | 转调 Q3 v2 启动器 |
| `start_q3_v2.bat` | 第三问六边形 7 点 Baseline 2.0 | `models/Baseline_v2.0_七点六边形.zip` |
| `start_q4.bat` | 第四问 25 点 C/U | `model_sources/q4/` |

窗口打开后点击 **开始模拟**；计算完成后可播放该局日志。Q4 的启动不再要求另行
下载 CUMCM-2026 论文仓库。Q3 v2.0 仍从原 ZIP 校验并执行模型，不改写原包。

两个脚本均在模拟器目录内使用 `.venv`。Q3 v2.0 使用
`requirements-q3-v2.txt`，包含原交付包的 numpy、numba、scipy、mpmath 版本范围
及桌面依赖 PySide6；启动时先在本地核对版本及可导入状态，满足后直接离线启动，缺少或版本不符时才调用 pip 安装。
Q4 模型本身只使用标准库，桌面需要 PySide6。

若环境创建、依赖准备或模型启动失败，窗口会保留错误并暂停，退出状态也会返回
调用方。若已有 `.venv` 不兼容，脚本提示保留备份后重建，不自动删除用户文件。

也可在已准备好的 Python 环境中启动：

```sh
python -m pip install -r requirements-q3-v2.txt
python -m enhanced gui --model hexagon_v2 --error-model baseline_fixed_field
python -m enhanced gui --problem 4 --error-model worst_edge
```

两个启动器都支持 `--check-only`：执行相同的环境准备、校验模型并创建 Qt 窗口后退出，不在失败时等待键盘输入。普通双击仍打开可见窗口。脚本只在自己的进程中启用 UTF-8，不改变系统区域或编码设置。

完整 Windows 交付包见 [交付说明](WINDOWS_DELIVERY.md)，包内保留“启动界面.bat”旧名称；过去下载的 ZIP 不会自动更新。

Windows x64 的三个正常启动入口和 54 项模型/接口检查已通过，包含中文与空格路径。详见 [Windows 验证记录](WINDOWS_VALIDATION.md)。
