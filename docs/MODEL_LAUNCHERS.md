# 模型启动入口

## Windows

请先完整解压 `enhanced-mock` 仓库，安装 64 位 Python（推荐 3.12，脚本最低检查
3.10），并在首次准备依赖时联网。在仓库文件夹中双击：

| 文件 | 打开的模型 | 模型来源 |
| --- | --- | --- |
| `启动界面.bat` | 默认第三问 Baseline 2.0，可在界面切换模型 | 转调 Q3 v2 启动器 |
| `start_q3_v2.bat` | 第三问六边形 7 点 Baseline 2.0 | `models/Baseline_v2.0_七点六边形.zip` |
| `start_q4.bat` | 第四问 1.0：25 点 C/U | `model_sources/q4/q4.py` |
| `start_q4_v2.bat` | 第四问 2.0：左右机会复测 | `model_sources/q4/q4_v2.py` |

窗口打开后点击 **开始模拟**；计算完成后可播放该局日志。Q4 的启动不再要求另行
下载 CUMCM-2026 论文仓库。Q3 v2.0 仍从原 ZIP 校验并执行模型，不改写原包。

启动脚本均在模拟器目录内使用 `.venv`。Q3 v2.0 使用
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
python -m enhanced gui --problem 4 --model q4_opportunity_v2 --error-model worst_edge
```

启动器都支持 `--check-only`：执行相同的环境准备、校验模型并创建 Qt 窗口后退出，不在失败时等待键盘输入。`start_q4_v2.bat` 共用 `start_q4.bat --v2`，普通双击仍打开可见窗口。脚本只在自己的进程中启用 UTF-8，不改变系统区域或编码设置。

完整 Windows 交付包见 [交付说明](WINDOWS_DELIVERY.md)，包内保留“启动界面.bat”旧名称；过去下载的 ZIP 不会自动更新。

此前 Windows x64 的三个正常启动入口和 54 项模型/接口检查已通过，包含中文与空格路径。详见 [Windows 验证记录](WINDOWS_VALIDATION.md)。该记录测试的是 Q4 v1，不能作为本次 v2 新入口的 Windows 验证结果。

## macOS

完整解压新版模拟器，并安装 macOS 版 64 位 Python 3.12（最低检查 3.10）。
双击 `启动界面.command` 默认打开 Q3 七点六边形 Baseline 2.0；双击
`start_q4.command` 打开 Q4 1.0 的 25 点 C/U 模型，`start_q4_v2.command`
打开 Q4 2.0 左右机会复测。三个入口共用环境准备逻辑。

常规启动只使用模拟器目录内的 `.venv`，缺少时创建；检查依赖版本与导入后，
只有缺失或版本不符时才联网安装。Q3 依照 `requirements-q3-v2.txt`，Q4 桌面
仅需 `PySide6>=6.6,<7`。安装后会再校验一次，不兼容的已有环境不会自动删除。
显式设置 `JAMMERS_PYTHON` 可选择已有 Python 环境（包括共享环境），缺失依赖
也会安装到这个主动选择的环境；不会自动改用或修改父目录 `.venv`。

在终端中使用已核对的源码：

```sh
./启动界面.command --check-only
./start_q4.command --check-only
./start_q4_v2.command --check-only
JAMMERS_PYTHON="/完整路径/.venv/bin/python" ./start_q4.command
./启动界面.command --replay "examples/q3-v2-demo"
```

`--check-only` 执行依赖准备、模型核验和 Qt 离屏窗口检查后退出，不等待按键。
其他参数转交界面，目录及参数支持中文和空格。普通交互启动失败时保留错误和
退出码并等待回车，非交互调用不会挂起。Python UTF-8 设置仅限启动器进程。

本交付包含未签名的 Python 源码及 `.command` 文件，尚未提供签名、公证的
`.app`。若提示“无法验证开发者／Apple 无法验证是否包含恶意软件”，先核对
下载来源和文件，确认信任后可按 [Apple 官方说明](https://support.apple.com/zh-cn/102445)
对该单个项目使用“系统设置 → 隐私与安全性 → 仍要打开”。这类提示既不能证明
文件有恶意行为，也不能证明它安全。不要关闭 Gatekeeper 或批量解除隔离。
“包含恶意软件”“将损坏电脑”及“文件已损坏”需要先停止并核对来源，不能按
普通未验证开发者提示直接放行。

`ModuleNotFoundError: No module named 'PySide6'` 是 Python 环境缺依赖的问题，
新版入口会检查并补齐；它与 macOS 安全弹窗不同。过去下载的 ZIP 和旧版目录
不会自动更新。更完整的使用说明见根目录 `Mac启动说明.txt`。

## 不同文件夹与版本

每个解压目录是一份独立模拟器副本，不会自动扫描别的版本文件夹，也不会因 GitHub 更新自动升级。
建议固定使用最新完整交付包；它可在模型选择框中切换已经整合的 Q3 v1 六边形、Q3 v1 螺旋、Q3 v2 七点六边形、Q4 v1 及 Q4 v2 左右机会复测。
Q3 v1 需要对应原始 ZIP（本次完整交付已附带）；界面用于模拟与回放，不是源码编辑器。新增模型需要先接入适配器后才会出现在列表。

2026-09-12 Mac 更新验证：Q3 v2 和 Q4 的 `--check-only` 均已在当前 Mac 的 Python 3.12 / PySide6 6.11.2 环境成功构建窗口并校验模型；15 项交付测试通过。
这不等于所有 macOS 版本均已测试，也不代表已经完成 Apple 签名或公证。

Q4 v2 的实现范围和保留限制见 [第四问 v2.0](Q4_V2.md)。上面的历史 Mac 记录
同样只覆盖当时发布的 Q4 v1；本次 v2 的结果须另行核验。
