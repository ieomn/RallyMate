# RallyMate 评分参考上下文

`FS02-M02 重心向目标方向转换`需要区分两件事：模型观测到的身体移动方向，以及教练希望运动员到达的目标方向。前者可由 Pose 计算，后者不能从同一条轨迹反推。

## 生成空白工作清单

```powershell
$env:PYTHONPATH = "$PWD\src"
python scripts/build_scoring_reference_context.py `
  --events reports/fs09-pose-wave-v2/850cb0006b406c7176eeda8d711cd065/events.jsonl `
  --video-id 850cb0006b406c7176eeda8d711cd065 `
  --video-sha256 71D3F59B7A8B966EF7645CAC412CB03679376F70A2F080D270391A32697D7FA9 `
  --output data/annotations/scoring-reference-context-v1/target-directions.json
```

生成文件中每个 FS02 事件初始为 `pending`。不要把 `launch_direction_deg` 抄成目标方向。教练或训练计划提供 image-plane 目标方向后，将单条记录改为：

- `status: accepted`
- `target_direction_deg: -180..180`
- `coordinate_frame: image_plane`
- `confidence: 0..1`
- 非空且不同的 `observer_id`、`reviewer_id`
- `reason: null`

无法确认时使用 `unobservable`，方向、坐标系、置信度和人员字段继续为 null，并填写原因。

## 回放评分闭环

`scripts/run_fs09_pose_wave_v2.py` 接受 `--scoring-reference-context`；Pipeline 请求则在 `scoring.reference_context` 中填写文件路径。输入文件在推理前后必须保持相同 SHA-256。推荐两阶段执行：先完成一次检测/事件生成，再从该次 `events.jsonl` 生成工作清单；填写并复核后复用该次 `frames.jsonl` 与 `primary-player.jsonl` 做无 GPU 评分回放。accepted 记录边界不一致时会拒绝，pending 记录因没有评分作用可以被忽略并重新生成。

当参考方向和 Pose 运动方向都有效时，系统输出 `target_direction_alignment_error_deg`。该值只解除 FS02-M02 的目标方向上下文阻断；它不会生成 A～E、不会创建阈值，也不会覆盖身份、关键点、事件边界、阶段或标定门禁。

## M42 完整评分向量

从 registry `pose-wave-2026-08-22.15` 起，指标合同明确区分两层：

- `measurement_features`：可以由当前 Pose/传感器独立计算，用于 F2 可测性、证据和 Pose 误差审计；
- `required_features`：进入标定、独立测试和正式评分的完整有序向量。

FS02-M02 的 measurement vector 是 `body_center_speed_body_s`、`hip_center_relative_to_ankle_support`、`torso_lean_deg`、`launch_direction_deg`；scoring vector 在末尾追加 `target_direction_alignment_error_deg`。因此“4 个 Pose 特征均有效”只允许 `feature_status=measured`，不能等价为完整评分输入可用。只有 accepted image-plane 目标与同一事件精确绑定时，第 5 个特征才会 `valid=true`，`scoring_feature_status` 才可能 measured。

`target_direction_alignment_error_deg=0` 表示实测启动方向与人工目标真正相同，不是缺失占位。pending、unobservable、缺 reviewer、边界不一致或不支持的 court-plane 目标必须输出 `value=null, valid=false`。Loop、离线 batch、人工事件特征、标定 compiler、independent evaluator 和 Bundle validator 都校验这 5 项的顺序与完整性，不能通过丢弃第 5 项重新评分。

当前真实工作清单位于 `data/annotations/scoring-reference-context-v1/850cb0006b406c7176eeda8d711cd065-fs02-target-directions.json`，34 条记录全部 pending。

## M43 人工误差评测

`scripts/evaluate_scoring_truth.py --manual-semantics ...` 会把目标方向对齐作为正式的评分上下文特征误差项，而不再只评测 Pose-only 特征。每条可评测样本必须同时具备：

- 已匹配的 FS02 预测事件与人工事件边界；
- 人工校正 Pose，用于重新计算 `launch_direction_deg`；
- 与人工事件 ID 精确绑定的 accepted `target_direction`；
- `coordinate_frame=image_plane`、有限方向/置信度、不同的 annotator 与 reviewer。

评测输出 `target_direction_alignment_error_deg` 的 MAE、P95、Bias、有效率和分视角结果，并区分 Pose 误差、事件边界误差、平滑误差与语义/关键点缺失影响。缺 target、pending/unobservable、自审或不支持的坐标系都保持 invalid/null；空 semantic 文件只会得到 `manual_semantic_record_count=0` 和 `context_feature_truth_complete=false`。这些误差必须再与外部预注册的潜在等级间差异比较，不能由本工具自动产生 F3、阈值或 A～E。
