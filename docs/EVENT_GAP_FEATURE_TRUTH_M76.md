# M76 真值条件特征误差重放

M76 把 M75 的人工裁决关键点接回版本化特征库，回答“短缺口插值对实际评分特征造成多大误差”。它是 M74 反事实与 M75 人工坐标之间的特征级评测层，不是新的生产 Pose 路由。

## 固定评测范围

- 142 个 M75 `event×joint×frame` 任务；
- 11 个含可插值点的指标实例；
- 47 条 required-feature 记录；
- 25 个唯一特征；
- 6 个指标 ID：FS01-M03/M04/M05 与 FS02-M03/M04/M05；
- M74 的 2 个 FS09-M05 残差缺失的是必需阶段代理，没有可插值关键点，因此显式排除。

## 对照语义

M76 重算两套同名特征：

1. M74 有界 timestamp 线性插值下的候选值；
2. 只将这些缺口点替换为 M75 裁决坐标后的条件值。

两边共用同一非缺口 Pose、候选事件边界、Track、feature version 和 timestamp。人工可见点保留候选插值的有畈端点置信度，用于隔离坐标/可见性差异；该置信度不是人工真值置信度。人工不可见点恢复为 NaN，不使用 0 表示缺失。

输出数值特征的 MAE、P95 和 `candidate - truth-conditioned` Bias；`launch_direction_deg` 使用 360° 环形差；`unit=code` 的侧别特征只统计精确一致率，不伪造数值 Bias。

## 安全边界

M76 的误差是“已知候选事件内、短缺口坐标的特征影响”，不是：

- 整个 Pose 模型的特征误差；
- 事件检测 Event F1/IoU/Boundary MAE；
- 主球员身份准确率；
- A～E 标定误差或等级间差异；
- 允许生产插值、降低质量门禁或推进 F3/F4 的决定。

接受标准必须在真值结果揭示前由外部版本化协议预注册；当前代码不生成经验门槛。

## 运行

```powershell
$env:PYTHONPATH = "src"
python scripts/evaluate_m76_event_gap_feature_truth.py `
  --pack data/annotations/event-bounded-pose-gap-truth-m75-v1 `
  --output <new-output.json>
```

输出不可覆盖既有文件。评测器会从绑定 M74 报告重放 142 个插值点、候选特征和完整性，再核验 M75 编译真值；仅同步修改报告哈希或密封预测不能绕过来源重放。

当前真实产物为 `reports/measurement-recovery-m76/event-gap-feature-error-empty.json`：M75 人工 annotation/adjudication 均为 0，状态 `annotation_required`，metrics/per-feature/per-indicator/details 均为 null 或空集，生产、等级、门槛和成熟度晋级全部关闭。
