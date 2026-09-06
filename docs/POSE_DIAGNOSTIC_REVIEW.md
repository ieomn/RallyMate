# RallyMate Pose 诊断视频复核队列

版本：`pose-diagnostic-review-queue-v1.0.0`

## 目的

该工具把 `events.jsonl` 中重复出现的关键点跳变、左右点交换、主球员身份歧义和 source Track 切换候选，按源帧与具体关节证据跨 FS01/FS02/FS09 去重。每个任务保留：

- 视频时间与源帧/processed frame；
- 事件 ID、事件码和边界；
- 异常关节或左右关节对；
- 前一帧、候选帧和后一帧的模型坐标与置信度；
- 被该候选实际阻断的指标及仅告警指标；
- 输入 frames、timeline、events、scores、summary 的路径和 SHA-256；
- 可播放连续视频和精确 seek 时间。

候选是启发式模型诊断，不是人工真值。生成队列不会修正关键点、解除质量门禁、生成等级或创建阈值。

## 当前入口

- `reports/pose-diagnostic-review/halpe26-same-window/index.html`：39 个去重任务，35 个唯一候选帧；
- `reports/pose-diagnostic-review/wholebody133-same-window/index.html`：80 个任务，59 个唯一候选帧；
- `reports/pose-diagnostic-review/halpe26-full/index.html`：261 个任务，198 个唯一候选帧。

页面使用连续 H.264 动态视频而不是单张截图。点击任务的播放按钮会跳转到对应时刻；选择 `confirmed_issue / false_positive / uncertain / not_observable` 后可以导出 CSV。草稿只保存在浏览器 localStorage，不能视为仓库真值。

## 生成命令

```powershell
$env:PYTHONPATH="$PWD\src"
python scripts/build_pose_diagnostic_review_queue.py `
  --run-dir reports/fs09-pose-wave-v2/850cb0006b406c7176eeda8d711cd065-halpe26-930-1530 `
  --review-video reports/pose-scoring-ab/rtmpose-halpe26-full-body-26points-31s-51s-browser.mp4 `
  --review-video-offset-ms 31000 `
  --output reports/pose-diagnostic-review/halpe26-same-window/queue.json `
  --html-output reports/pose-diagnostic-review/halpe26-same-window/index.html
```

输出已存在时默认拒绝覆盖；只有明确重建同一机器审计快照时才能传 `--overwrite`。`review-video-offset-ms` 仅做显示视频时间换算，不是事件或 A～E 阈值。

## 复核 CSV 校验

浏览器导出列为：

```text
task_id,diagnostic_type,candidate_source_frame_index,timestamp_ms,decision,annotator_id,reviewed_at,notes
```

非 pending 决定必须填写 annotator ID 和 ISO-8601 `reviewed_at`。校验命令：

```powershell
$env:PYTHONPATH="$PWD\src"
python scripts/validate_pose_diagnostic_review_decisions.py `
  --queue reports/pose-diagnostic-review/halpe26-same-window/queue.json `
  --decisions pose-diagnostic-review-decisions.csv `
  --output reports/pose-diagnostic-review/halpe26-same-window/decision-validation.json
```

校验器会先重新计算队列绑定的 frames、primary timeline、events、scores、summary、复核视频和源视频 SHA-256；任一来源被替换或修改都会 fail closed。即使全部任务完成，报告状态也只能是 `review_complete_not_adjudicated`，`truth_status=not_adjudicated`。单名复核者的 CSV 不能直接修改质量策略；若要评测 jump/swap 诊断 precision/recall 或解除门禁，还需要独立复核、冲突裁决、版本化评测协议和相应 maturity evidence。

## 全时间线真值与漏检评测

候选复核页只包含模型已经报告的条目，因此最多能支持候选 precision 审计，不能单独计算 recall。`pose-diagnostic-truth-pack-v1.0.0` 新增两份相互独立的人工输入：

- `pose-diagnostic-coverage.csv`：按诊断类型声明已经完整审阅的源帧区间；只有 `accepted + all_model_relevant_scopes` 才使该区间内“没有 truth positive”具有显式负样本语义；
- `pose-diagnostic-positives.csv`：稀疏记录人工确认的 jump 关节、swap 左右关节对、身份歧义帧或 source Track 身份连续性失败帧。

v1 采用预先固定的精确匹配：同一 `source_frame_index`，jump 还要求同一 joint，swap 还要求同一有序左右关节对；不包含时间容忍或经验阈值。每个 accepted 区间或真阳性要求至少两名唯一标注者和一名不在标注者集合内的独立裁决者，并携带带时区的裁决时间。真阳性落在未接受覆盖区间、关节名不属于源 Pose、重复范围、弱复核、队列/运行产物/视频 SHA 被修改时全部 fail closed。

CSV 中的 annotator/adjudicator ID 和时间字段只参与契约校验与链内追溯，不能自行认证真人身份、独立性或预注册时点。正式证据采集仍需在受控账号、签名或外部审计登记下完成；缺少可信身份锚时不得据此放宽生产门禁。

当前真实入口：

- `reports/pose-diagnostic-truth/halpe26-same-window-v1/index.html`；
- `reports/pose-diagnostic-truth/wholebody133-same-window-v1/index.html`。

两个工作台都绑定同一 600 帧/31–51 秒窗口，但显示各自模型的连续标注视频。当前 coverage 与 positives 均为空，评测状态为 `annotation_required`，precision/recall/F1 全为 null。浏览器 localStorage 草稿不能视为真值，且必须分别导出两份 CSV 后再运行：

```powershell
$env:PYTHONPATH="$PWD\src"
python scripts/evaluate_pose_diagnostic_truth.py `
  --manifest reports/pose-diagnostic-truth/halpe26-same-window-v1/manifest.json `
  --coverage <人工裁决后的-coverage.csv> `
  --positives <人工裁决后的-positives.csv> `
  --output reports/pose-diagnostic-truth/halpe26-same-window-v1/evaluation-adjudicated.json
```

部分覆盖时报告可以输出 `accepted_coverage_only` 的局部统计，但不得称为全时间线 recall。只有四类诊断都覆盖全部源帧时，顶层状态才是 `evaluated_full_timeline`。即使评测完成，也不会自动修改 quality policy、F0～F4、A～E 或阈值；门禁调整仍需预注册接受协议、人工审查和 maturity evidence。

## 质量门禁接受协议与人工策略审核

`pose-diagnostic-quality-gate-review-v1.0.0` 把诊断真值评测与质量策略发布分开。仓库不提供默认 precision、recall、F1 或样本量门槛，也不会根据当前结果倒推一个能通过的数值。接受条件必须写入外部可信登记的 `pose-diagnostic-gate-acceptance-protocol-v1.0.0`，并在任何绑定评测的 `generated_at` 之前完成注册。示例 `examples/pose-diagnostic-gate-acceptance-protocol.template.json` 故意包含不可执行占位值，复制后仍须由教练/评测负责人确定、独立复核并登记，不能直接运行。

审查器会重新读取 manifest 绑定的 coverage/positives CSV，重新计算四类诊断的 TP/FP/FN 和 precision/recall/F1，并要求重算结果与输入 evaluation 完全相同；它不会信任报告自报的统计值。多个视频/模型范围按原始计数汇总，不平均各范围的比例。协议还必须精确绑定预期 scope 的 manifest canonical SHA、source queue SHA 和运行产物 binding SHA。协议晚于结果、范围缺失或多出、CSV 后改、版本不符都会 fail closed。

当前无协议、无人工真值的真实审查命令如下：

```powershell
$env:PYTHONPATH="$PWD\src"
python scripts/review_pose_diagnostic_quality_gate.py `
  --bundle reports/pose-diagnostic-truth/halpe26-same-window-v1/manifest.json reports/pose-diagnostic-truth/halpe26-same-window-v1/evaluation.json `
  --bundle reports/pose-diagnostic-truth/wholebody133-same-window-v1/manifest.json reports/pose-diagnostic-truth/wholebody133-same-window-v1/evaluation.json `
  --output reports/pose-diagnostic-quality-gate-review-v1-empty.json
```

当前输出为 `annotation_and_protocol_required`，阻断项是 `external_preregistered_acceptance_protocol_missing` 与 `full_timeline_diagnostic_truth_missing`。未来提供已经外部登记的协议时可加 `--protocol <registered-protocol.json>`；即使所有接受条件通过，状态也只能到 `eligible_for_human_policy_review`，不会自动写入下一版 quality policy。策略发布仍需人工决定、新版本策略、maturity evidence、独立测试和既有 production 信任链。

当前仓库全量回归为 395 tests OK（2 skipped）；两份真实 manifest、两份空白 evaluation、新的空白策略审查报告及相关 Schema 均通过校验，两个工作台脚本均通过 JavaScript 语法检查。测试通过只证明接口、安全拒绝和追溯契约，不证明候选诊断准确。

## 契约与代码

- `src/rallymate_evaluation/pose_diagnostic_review.py`
- `scripts/build_pose_diagnostic_review_queue.py`
- `scripts/validate_pose_diagnostic_review_decisions.py`
- `contracts/pose-diagnostic-review-queue.schema.json`
- `contracts/pose-diagnostic-review-decision-report.schema.json`
- `src/rallymate_evaluation/pose_diagnostic_truth.py`
- `scripts/build_pose_diagnostic_truth_pack.py`
- `scripts/evaluate_pose_diagnostic_truth.py`
- `contracts/pose-diagnostic-truth-pack.schema.json`
- `contracts/pose-diagnostic-evaluation.schema.json`
- `src/rallymate_scoring/diagnostic_policy_review.py`
- `scripts/review_pose_diagnostic_quality_gate.py`
- `contracts/pose-diagnostic-gate-acceptance-protocol.schema.json`
- `contracts/pose-diagnostic-quality-gate-review.schema.json`
- `tests/test_pose_diagnostic_review.py`
- `tests/test_pose_diagnostic_truth.py`
- `tests/test_diagnostic_policy_review.py`
