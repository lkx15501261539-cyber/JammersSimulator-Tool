# Q3 Baseline 2.0 接入与验证

本分支新增模型 `hexagon_v2`（七点六边形 · Baseline 2.0），保留 `hexagon_v1`、`spiral_v1` 和 `q4_cu`。这是用户提供的 Q3 v2.0 原模型接入，不修改其数学方法、配置或源码，也不把它当作 Q4 控制器使用。

## 打开模拟

Windows 双击仓库根目录 `start_q3_v2.bat`。首次准备环境需要联网并已安装 64 位 Python 3.10+；推荐 Python 3.12。其他平台：

```sh
python -m pip install -r requirements-enhanced.txt
python -m enhanced gui --model hexagon_v2
```

选择场景和 Seed，点击“开始模拟”。计算完成后自动播放，底部可以暂停、调整倍速或拖动时间轴。模型菜单切换 v1/v2 时分别保留其 ZIP 路径，v2 默认使用仓库自带原包。Q4 不显示 Q3 压缩包控件。

复现本次给用户打开的回放：

```sh
python -m enhanced baseline --model hexagon_v2 --seed 47 --scenario clustered --error-model baseline_fixed_field --output runs/v2-47
python -m enhanced gui --replay runs/v2-47
```

输出目录必须是新目录。每次运行保留原模型的 `baseline-original/actions.jsonl`、`decisions.json`、`summary.json`，以及模拟器的事件、真值和计时统计。模型只读取接口响应，真值仅供环境及回放使用。

## 原包与适配边界

- 原包：`models/Baseline_v2.0_七点六边形.zip`，完整保留模型文档、源码、测试和原始验证记录。
- SHA256：`24a8d60fc64dcb22fd1bfa0d7629d3782465d003d0a28ea64a64cfb4f502eae4`。
- 运行前核对原包清单中的 105 个文件，再提取 11 个运行文件；运行后重新核对这些文件的字节指纹。
- v2 的 `run.py` 没有 v1 的 `execute` 接口。桥接使用 v2 原始 `JournalClient` 和 `run.Controller`，先完成原模型预热及几何认证，再连接模拟器响应管道；没有误用 `baseline_runtime` 内继承的 v1 控制器入口。
- 控制器在独立进程运行，隔离 v1/v2/Q4 的同名几何模块。只读观察器复制实际全局任务、C/U 决策、光学路线和检测预算，不调用额外规划或改写任务。

界面显示 v2 的真实规则：1200 m 七点覆盖、当前频道优先扫描 UNKNOWN、每源最多 3 次追加检测、C/U 选择、最小增量插入与两轮任务 2-opt。C/U 卡片的费用包含当时冻结的后继任务费用，不能作为全场尚未发现源的总费用保证。Q3 v1 的走廊规则、5 次预算和固定尾扫不用于 v2。

## 本次实际验证

2026-09-12，macOS / Python 3.12：

- 原包 46 项测试通过；实际毫米停点的七点连续覆盖认证通过，165 单元、0 未决。
- 模拟器 174 项测试通过。其中 1 项本地 HTTP 监听测试首次受沙箱限制，开放本机监听后单独重跑通过。
- 随仓库提供的 Q4 核心 18 项测试通过；Q4 不再依赖外部论文目录才能加载。
- 观察器与直接运行原 v2 控制器的受控场景请求/响应、决策和统计逐项相同；两个 v1 原模型的完整运行及兼容检查通过。
- 原包、配置和源码未变，计时按 `T=L_move/5+N_sw+5N_meas+3N_clr+2N_succ` 对账，追加检测不超过每源 3 次。

下面三场由本次 enhanced mock 实际新运行，采用固定有界误差 `baseline_fixed_field`，不是原包验证结果的转述：

| 场景 | Seed | 清除数 | 虚拟时间 / s | 检测次数 | 失败光学尝试 |
|---|---:|---:|---:|---:|---:|
| 均匀 uniform | 42 | 15 / 15 | 4980.878256 | 75 | 39 |
| 聚集 clustered | 47 | 12 / 12 | 4541.514792 | 90 | 23 |
| 边缘 boundary-biased | 48 | 14 / 14 | 4949.900693 | 94 | 196 |

三场共 41 / 41 个源清除，全部完成且计时一致。失败光学尝试属于模型策略，每次 3 秒已计入总时间。新集成测试另运行 Seed 20260912，10 / 10 个源清除。结构化结果见 [baseline-v2-validation.json](baseline-v2-validation.json)。

## 范围

本次没有启动官方演练或使用正式测试次数。Windows 启动器已补充实际 Windows x64 验证，三个正常窗口入口及 54 项模型/接口检查通过，详见 [后续 Windows 验证记录](WINDOWS_VALIDATION.md)。有限自建案例不构成所有合法场景限时完成、全局最优或理想三角函数形式化证明。原论文与前三问核心结论未修改。
