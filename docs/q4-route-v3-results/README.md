# Q4 v3 正式结果与论文图

数据来自同一冻结版本下的30个共同场景、七策略、210次真实运行。
当前Q4 v2与左右夹击是一条基线，不重复计为两个模型。

- [全部20项指标与独立分析](analysis.md)
- [可核对的分析JSON](analysis.json)：逐场配对、逐源未复测原因、延迟分布、输入与分析脚本哈希。
- [原始汇总CSV](summary.csv)：比较器直接输出，包含场均和池化两种口径。
- [文件校验清单](manifest.json)

| 论文图 | PNG预览 | SVG矢量图 |
| --- | --- | --- |
| 完整任务时间、移动、clear尝试 | [PNG](completed_task_comparison.png) | [SVG](completed_task_comparison.svg) |
| 移动距离与追加复测次数 | [PNG](movement_vs_remeasurement.png) | [SVG](movement_vs_remeasurement.svg) |
| 预算与绕路阈值消融 | [PNG](budget_and_detour_ablations.png) | [SVG](budget_and_detour_ablations.svg) |
| 首次复测等待及从未复测数量 | [PNG](first_remeasurement_diagnostics.png) | [SVG](first_remeasurement_diagnostics.svg) |
| 预先固定uniform42真实轨迹 | [PNG](uniform42_route_b2_t5_actual_trajectory.png) | [SVG](uniform42_route_b2_t5_actual_trajectory.svg) |

轨迹图的[中英文图注](uniform42_route_b2_t5_actual_trajectory.caption.txt)与
[数据来源记录](uniform42_route_b2_t5_actual_trajectory.json)说明场景和频道选择规则、
动作序列与校验证据。仅离线读取真值绘图；没有重新调用或修改在线策略。

时间图只对完整任务取均值，本批210局均完成。复测成功率按实际对应次尝试汇总，
没有尝试时为缺失值。首次复测等待按真正复测的源池化，未复测源不记零。
`summary.csv`中的`mean_mean_*`表示“每场均值的平均”，
`pooled_*`才是合并源次后计算的指标；论文每源均值及成功率应使用独立分析所列池化口径。

预算2、τ5仍是应用默认；按本批T优先可手动选择预算1、τ5。预算1与其他τ值的
交叉组合尚未测，不宣称联合最优。J只用于有限候选的采样排序，不是连续最坏上界。
CPU、墙钟和虚拟任务时间T分别计量。

消融折线只连接已测参数点：预算取1/2/3，τ取0/5/20；连线不是对未测参数的预测。
该图纵轴局部放大了小差异，τ20相对τ5的平均T差仅0.47%，应结合配对表中的
4场更快、5场更慢、21场相同阅读，不能从线条斜率推断普适收益。

本目录只收录约1.9 MB的派生结果。约314 MB原始逐动作回放及完整矩阵JSON另行保存，
没有复制到仓库；分析JSON保留完整矩阵的文件名和SHA256。复现实验的命令见
[模型说明的运行与交付](../Q4_ROUTE_V3.md#运行与交付)。
