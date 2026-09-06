# RallyMate F2 误差预算就绪报告

版本：`f2-error-budget-readiness-v1.0.0`

该报告用于回答一个比“特征能不能算”更严格的问题：当前每个指标是否已经同时具备事件误差、特征误差、Pose 误差、事件边界误差、平滑误差、缺失值影响和外部等级间距判断，从而具备 F2→F3 的证据条件。

它不是评分器，也不是 maturity evidence 的替代品。报告只能汇编和重放已有证据；不会生成 A～E、经验阈值、教练共识、准确率或成熟度晋级。

## 当前真实结果

机器报告：`reports/f2-error-budget-readiness-m64.json`

- registry：`pose-wave-2026-08-22.17`，13 个 F2 指标、51 个 Pose required feature；
- 三段视频均有 13/13 指标的 measured 候选；
- 平滑反事实为 9,965/9,971，10/13 指标的全部 Pose 特征完整，3/13 含少量 partial；
- 人工事件、关键点和语义记录均为 0；
- Event F1、Segment IoU、Boundary MAE、特征 MAE/P95/Bias、分视角误差、Pose/边界/真值条件平滑/缺失值误差均未评测；
- 外部预注册等级间距判断为 0/13；
- F2→F3 ready 为 0/13，grade=0、threshold=0。

## 重建命令

```powershell
$env:PYTHONPATH="$PWD\src"
python scripts/build_f2_error_budget_readiness.py `
  --registry metric-feasibility-pose-wave-v2.json `
  --smoothing-coverage reports/smoothing-counterfactual-coverage-m63.json `
  --calculation-coverage reports/multivideo-indicator-calculation-coverage/m63-halpe26-three-video-fs02-m05-first-step-evidence-v1/coverage.json `
  --run-directory reports/scoring-candidate-multivideo-m63/runs/3ae77ee3271d67de171585a5c39ddd69 `
  --run-directory reports/scoring-candidate-multivideo-m63/runs/850cb0006b406c7176eeda8d711cd065 `
  --run-directory reports/scoring-candidate-multivideo-m63/runs/8d7754d0de6d315674013d5b69a0b6ba `
  --manual-events data/annotations/scoring-truth-pack-v1/compiled/manual-events.jsonl `
  --manual-keypoints data/annotations/scoring-truth-pack-v1/compiled/manual-keypoints.jsonl `
  --manual-semantics data/annotations/scoring-truth-pack-v1/compiled/manual-semantics.jsonl `
  --output reports/f2-error-budget-readiness-m64.json
```

输出路径不可覆盖。构建器会强校验 registry、M63 smoothing report、三视频 calculation coverage、每段 frames/primary timeline/events 和三类人工真值文件的 SHA；随后重新执行三段真值评测。任一来源漂移、视频集合不完整、指标/特征集合不一致或安全字段被修改都会 fail closed。

## 与 F0～F4 的关系

- F2 calculation measured：说明版本化特征函数在候选事件上能运行；
- smoothing counterfactual complete：说明可以从序列化 raw evidence 重放无额外平滑对照；
- 两者都不等于准确率；
- F2→F3 还必须取得人工事件与关键点真值，计算 Event F1/IoU/Boundary MAE 与特征 MAE/P95/Bias/分视角误差，完成 Pose、边界、真值条件平滑和缺失值误差预算，并由外部预注册协议判断误差是否显著小于潜在等级间距；
- 通过上述条件后，仍须由 `maturity-evidence` 的顺序证据链和人工审查执行逐级晋级；不得直接修改 registry 到 F3/F4。
