# M75 事件内短缺口关键点真值

M75 用人工真值回答 M74 尚未回答的问题：由两个真实 Pose 端点线性插值得到的 142 个短缺口关节点，位置误差到底有多大。

工作包位于 `data/annotations/event-bounded-pose-gap-truth-m75-v1/`。它覆盖两段真实视频、54 个源帧、142 个 `event×joint×frame` 任务。第三段视频的残余实例属于事件阶段缺失，没有符合 M74 合同的插值点，因此不伪造标注任务。

## 盲化合同

- `review.html` 只加载无骨架原视频和人工任务，不加载插值坐标；
- 插值坐标及左右端点证据只保存在 `sealed-interpolation-predictions.jsonl`；
- 两名标注者必须独立完成 `annotations.csv`；
- 第三名 reviewer 必须未参与这两份标注，并在 `adjudications.csv` 逐点裁决；
- 不可见点必须给出原因，禁止以 `(0,0)` 表示缺失；
- 当前 Pose 缺失帧的裁剪框仅由 160 ms 内双侧主球员框并集生成，用于查看，不声明身份或关键点真值；
- 部分标注、候选置信度或工作台草稿都不能改变运行时。

打开入口：

`http://127.0.0.1:8765/data/annotations/event-bounded-pose-gap-truth-m75-v1/review.html`

每名标注者使用唯一 ID 单独完成并导出 CSV，不要导入或查看他人的记录。reviewer 导入至少两名标注者的 CSV 后裁决；页面仍不会显示插值预测。

## 编译和评测

```powershell
$env:PYTHONPATH = "src"
python scripts/compile_m75_event_gap_keypoint_truth.py --pack data/annotations/event-bounded-pose-gap-truth-m75-v1
python scripts/evaluate_m75_event_gap_keypoint_truth.py --pack data/annotations/event-bounded-pose-gap-truth-m75-v1 --output <new-output.json>
```

输出包括整体 MAE/P95、x/y Bias、可见真值上的预测有效率，以及按关节、视角和 FS01/FS02 事件族的结果。像素误差同时归一化到标注裁剪框长边。完成真实评测后仍需结果揭示前冻结的外部接受协议；代码不会从这些数据猜测允许插值的误差阈值。

当前真实状态为 0 条人工标注、0 条裁决，`compiled/validation-report.json` 为 `annotation_required`，`reports/measurement-recovery-m75/event-gap-keypoint-error-empty.json` 的全部 metrics 为 null。生产插值、A～E、threshold 和 F3/F4 晋级均未启用。
