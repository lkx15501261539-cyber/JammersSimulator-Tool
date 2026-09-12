# Baseline 1.0 原代码接入与核对

此次接入不编辑 `run.py`、`solver.py`、`第一问.py`、`config.json` 或任何原模型文件。
界面直接读取用户交付 ZIP，验证每个模型的 SHA256SUMS 清单，将运行所需文件逐字节
解压到 `.cache/baselines/<ZIP SHA256>/<模型名>/`，运行前后再次核对文件散列。
Numba 缓存和任务输出写在外部目录；导入缓存不会改变任何模型源文件。

原始交付包 SHA256：
`f22270328189183d943673a8e165e9f85be7dd56537580cccecef68fc995a302`

- 六边形清单：79 个文件全部通过校验。
- 螺旋清单：80 个文件全部通过校验。
- 两模型 `run.py`、`solver.py`、`第一问.py` 完全相同，仅路线配置不同。
- 使用原包中的第一问文件，不替换为可能已更新的相邻论文文件。
- `run.py`：`f903209f711bb14cc9e841eb29298272618d1ad0ed0974aa94de96174c28a45f`
- `solver.py`：`f656140be18e2a69707a2fc1fe319bdb701a6c56ce34de6328048e278fdea4ad`
- `第一问.py`：`2456a59a084a8e0d5e06619d772ed96038840a69fd78140009a7dcb804bb9deb`

## 手动运行

macOS 已安装依赖时双击仓库中的 `启动界面.command`，Windows 对应 `启动界面.bat`。
也可执行 `python -m enhanced gui`。

1. 顶部选择“六边形 7 点”或“螺旋 12 点”。
2. 选择原交付 ZIP；本机优先使用 `models/` 中的原样副本，否则寻找 Downloads。
3. 选择场景和 seed，保持 `baseline_fixed_field` 可与原版测试器对照。
4. 点击 **开始模拟**。首次运行可能需要编译计算模块，界面显示进度。
5. 计算完成后自动连续播放，可暂停、单步、拖动时间轴及选择最高 50×。

计算速度和虚拟运动速度是两个概念：模型通常数秒至数十秒计算完；一局可能包含
数千秒虚拟任务时间。选择 50× 可加快观看。停止按钮取消当前计算并保存部分日志。
不同场景可能产生模型原有的 UNRESOLVED，不会被伪造为清除成功。

Git 不包含用户交付 ZIP；换电脑需要选择同一交付包。可用 `BASELINE_ARCHIVE`
环境变量设置路径。Windows 使用相同 Python/Qt 源码，尚未做 Windows 实机核验。

## 原代码如何调用

独立 Python 子进程导入原 `run.py`，调用原 `warmup()`，然后执行：

```python
execute(original_config, output, response_backend,
        robot_id='BASELINE-SIM', live=False)
```

`response_backend.exchange(path, payload)` 仅通过进程管道发送四种官方请求并接收响应。
不复制或重写 Controller，不更改锚点、扫描顺序、测点优化、最多 5 次 followup、尾扫、
清除判据或预算。世界真值保留在父进程，策略进程只获得公开响应。
父进程的 Q1 定位更新仅供 UI 观察，不返回给模型。
这是一项接口隔离，未把不可信任的 Python 代码当成安全沙箱执行。

每局除了原有五份仿真文件，还保存 `baseline-original/` 中原程序自己生成的
`session.json`、`actions.jsonl`、`decisions.json`、`summary.json`；
`reconciliation.json` 核对原模型统计和模拟器事件统计，`worker.log` 保留运行错误。
Replay 只读事件，模型代码和 ZIP 都不再需要。

## 核对结果（macOS，2026-09-12）

| 场景 / seed | 六边形 | 螺旋 |
|---|---:|---:|
| uniform / 20260912 | 10/10；5971.12 虚拟秒 | 10/10；5999.21 虚拟秒 |
| uniform / 42，实际界面点击开始 | 15/15；7193.69 虚拟秒 | 15/15；7007.09 虚拟秒 |

四局均正常完成，原模型文件与配置保持一致，移动距离、虚拟时间、测量、切频、清除
数量与模拟器事件统计一致；GUI 两局均验证开始后自动播放。
首次包含 Numba 编译约 20.8 秒，缓存后本机两次 GUI 运行约 3.1 秒；不保证其他
电脑或更复杂场景具有相同耗时。

历史接入阶段测试：**31 passed**，无跳过；这是动画升级前的接入验收记录，
本轮动画核验见 [ANIMATION_ACCEPTANCE.md](ANIMATION_ACCEPTANCE.md)。当时新增测试包括：

- 两模型 × 两个 seed，逐条对照原版 OfflineBackend 与增强 World 的 18 个请求；
  检查 near/clear/接收边界、同点误差、幂等、切频、clear 不切频及完整时间等式。
- 两个原模型完整任务、原配置逐项相等、进程隔离、文件校验、Replay 与统计核对。
- 取消后的部分日志，真实 Q1 开始按钮、模型选择、自动播放、停止及安全关闭。

`baseline_fixed_field` 完全采用原测试器的 `.994*sin(...)` 固定误差表达式；
该系数为两位小数舍入留出了余量。原有 `deterministic_hash_fixed` 和 `worst_edge`
保留为另外的压力场景，其边界差异见 COMPATIBILITY.md。这些核对证明接入一致，
不等价于官方模拟器认证，也不证明每个 seed 都会 100% 清除。

图中 Q1 直径圆可能不能覆盖整个定位区域，而原模型仍可根据另一最小包围圆成功清除；
这两个圆不是同一几何量。UI 的 `Circle covers` 始终表示 Q1 直径圆覆盖判定。
