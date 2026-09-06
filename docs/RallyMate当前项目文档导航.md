# RallyMate 当前项目文档导航

> 更新日期：2026-09-05  
> 用途：区分当前事实、专项说明、验收记录和历史材料。

## 第一次了解项目，请先读这四项

1. `RallyMate产品说明书_当前态_v1.0.md`  
   面向产品、教练和项目负责人。先用白话解释一整套脚步动作和最早六项评分标准，再说明产品已经交付什么、哪些仍不能承诺、用户怎样使用、下一阶段如何验收。

2. `RallyMate技术架构与实现说明书_当前态_v1.0.md`  
   面向研发、算法、测试和运维。回答模型、数据流、事件、特征、质量门禁、标定、安全发布、产物、测试和维护方式。

3. `DYNAMIC_SCORING_OBSERVER_M81.md`  
   面向希望直接看效果的人。说明三视频动态观察器的数据范围、使用方式和安全边界。

4. `RallyMate接口与端到端链路_当前态_v1.0.md` +
   `diagrams/RallyMate完整系统架构_当前态_v1.0.drawio`  
   面向开发、集成和交接人员。前者记录 9 个显式 HTTP 路由、18 项产物白名单、Demo v1.2、
   M89～M93 Python API、M96 evaluation-only 标注试点和 M97 模型结论；后者是可编辑五页系统图。

这些材料共同构成当前阅读入口。若它们与机器 registry 或 JSON Schema 冲突，以机器文件为准；
生产 registry 的生命周期归类和当前角色以 `registry-lifecycle.json` 为准。

浏览器阅读入口：`http://127.0.0.1:8765/reports/rallymate-current-docs/index.html`。当前文档服务必须用以下命令启动：

```powershell
.\scripts\serve_current_docs.ps1
```

该服务只托管 `reports/rallymate-current-docs`，不要用通用仓库根目录 HTTP 服务代替。修改产品、技术或 API Markdown 后，可另行运行 `scripts/build_current_product_technical_docs.ps1` 重新生成带目录和样式的 `product.html`、`technical.html` 和 `api.html`；构建和受限托管是两个不同动作。用户上传与结果 Demo 位于 `http://127.0.0.1:8000/`。

## 当前机器事实真源

- Registry 生命周期权威：`registry-lifecycle.json`（Schema：
  `contracts/registry-lifecycle.schema.json`）
- 指标成熟度：`metric-feasibility-pose-wave-v2.json`
- Pose部署：`models/rtmpose/deployment-presets.json`
- M95 离线 Pose 候选：`models/rtmpose/m95-shadow-candidates.json`
- M95 微调就绪状态：`reports/m95-pose-finetune-readiness/readiness.json`
- 当前 Demo v1.2 结果适配：`src/rallymate_service/user_demo.py`
- 当前 Beta 训练评价：`src/rallymate_service/training_evaluation.py`
- Demo v1.2 精确合同：`reports/user-demo-v1.2/demo-contract.json`
- Demo v1.2 字段留存记录：`reports/user-demo-v1.2/field-change-record.json`
- Demo v1.1 历史精确合同：`reports/m96-user-demo-v1.1/demo-contract.json`
- Demo v1.1 历史字段记录：`reports/m96-user-demo-v1.1/field-change-record.json`
- M96 M/L/X 同帧诊断：`reports/m96-rtmpose-same-frame-diagnostic/diagnostic-report.json`
- M96 24×26 evaluation-only pilot：`reports/m96-pose-pilot/pilot-contract.json`
- M97 X 测量恢复结论：`reports/measurement-recovery-m97/summary/report.json`
- 事件契约：`contracts/events.schema.json`
- 特征契约：`contracts/feature-result.schema.json`
- 评分契约：`contracts/score-result.schema.json`
- 主球员契约：`contracts/primary-player.schema.json`
- 当前13项评分依赖快照：`indicator-scoring-requirements-pose-wave-v1.json`
- 三视频观察器派生证据：`reports/scoring-visual-observer-m81/manifest.json`
- 生产离线重评分账本契约：`contracts/trusted-scoring-run-bundles.schema.json`
- 故意不可加载的账本模板：`calibration/trusted-scoring-run-bundles.template.json`
- 人工真值就绪状态：`data/annotations/scoring-truth-pack-v1/compiled/validation-report.json`

M81 v1.1.0 已显式绑定 M78 的三份 scoring summary，但 M78/M81 都位于普通报告目录，
只属于 F2 派生观察证据，不是运维信任根、人工真值、准确率结论或正式评分授权。

以下文件不是当前机器真源：`metric-feasibility.json` 是早期六指标历史注册表；
`metric-measurement-plans.json` 仍绑定旧 feasibility/event 版本，只能作为规划/历史材料，
不得覆盖当前 `.17` registry 和 scoring requirements 快照。上述归类现已由 lifecycle manifest
机器化约束，而非仅靠文档提醒；详见 `REGISTRY_LIFECYCLE.md`。

## 按角色阅读

### 产品负责人 / 管理者

1. 产品说明书；
2. `MINIMUM_SCORING_LOOP_ACCEPTANCE.md` 的当前验收章节；
3. 动态观察器；
4. 需要决策标注投入时再读 `SCORING_TRUTH_COLLECTION.md`。

### 教练 / 标注负责人

1. 产品说明书中的指标和语义边界；
2. `SCORING_TRUTH_COLLECTION.md`；
3. `POSE_DIAGNOSTIC_CLIP_TRUTH_M80.md`；
4. M96 pilot 的 A/B 独立标注入口和 C 裁决入口；
5. 真值工作台和裁决台。

### 算法工程师

1. 技术架构说明书；
2. `POSE_DEPLOYMENT_PROFILES.md`；
3. `F2_ERROR_BUDGET_READINESS.md`；
4. `RTMPOSE_X_AND_FINETUNE_READINESS_M95.md`；
5. `RTMPOSE_MLX_SAME_FRAME_DIAGNOSTIC_M96.md`；
6. M97 测量恢复 summary；
7. `EVENT_TRUTH_EVALUATION.md`；
8. `MATURITY_EVIDENCE_BUNDLE.md`。

### 后端 / DevOps

1. 技术架构说明书的运行时、信任链和环境配置；
2. `RallyMate接口与端到端链路_当前态_v1.0.md`；
3. `diagrams/RallyMate完整系统架构_当前态_v1.0.drawio`；
4. `RUNTIME_SCORING_PROFILE_BINDING.md`；
5. `CALIBRATION_CANDIDATE_FITTING.md`；
6. `deploy/docker-compose.yml` 和 `deploy/.env.example`。

### 测试 / 数据质量

1. 技术架构说明书的契约、验证和测试策略；
2. `MINIMUM_SCORING_LOOP_ACCEPTANCE.md`；
3. `SCORING_BLOCKER_TAXONOMY.md`；
4. M96 24×26 pilot 合同、空白包和 A/B/C 门禁；
5. 各真值与误差专项说明。

## 专项文档索引

### 接口、Demo 与架构

- `RallyMate接口与端到端链路_当前态_v1.0.md`
- `diagrams/RallyMate完整系统架构_当前态_v1.0.drawio`
- `RTMPOSE_L_SHADOW_CANDIDATE_M94.md`
- `../reports/m94-api-demo-model-shadow/field-change-record.json`
- `RTMPOSE_X_AND_FINETUNE_READINESS_M95.md`
- `../reports/m95-model-candidate-finetune-readiness/field-change-record.json`
- 当前 Demo v1.2：`../src/rallymate_service/user_demo.py` 与
  `../src/rallymate_service/training_evaluation.py`
- [Demo v1.2 实现摘要](../reports/user-demo-v1.2/summary.md)
- [Demo v1.2 精确合同](../reports/user-demo-v1.2/demo-contract.json)
- [Demo v1.2 字段留存记录](../reports/user-demo-v1.2/field-change-record.json)
- [Demo v1.1 历史实施摘要](../reports/m96-user-demo-v1.1/summary.md)
- [Demo v1.1 历史精确合同](../reports/m96-user-demo-v1.1/demo-contract.json)
- [Demo v1.1 历史字段记录](../reports/m96-user-demo-v1.1/field-change-record.json)
- 本地用户 Demo：`http://127.0.0.1:8000/`

### Pose和模型

- `POSE_MODEL_BASELINE.md`
- `POSE_BACKEND_MIGRATION_PLAN.md`
- `POSE_BACKEND_AB_REPORT.md`
- `POSE_DEPLOYMENT_PROFILES.md`
- `POSE_SCORING_MODEL_COMPARISON.md`
- `RTMPOSE_X_AND_FINETUNE_READINESS_M95.md`
- [M96 M/L/X 同帧诊断专项](RTMPOSE_MLX_SAME_FRAME_DIAGNOSTIC_M96.md)
- [M96 同帧诊断摘要](../reports/m96-rtmpose-same-frame-diagnostic/summary.md)
- [M96 同帧字段记录](../reports/m96-rtmpose-same-frame-diagnostic/field-change-record.json)
- [M97 X 测量恢复摘要](../reports/measurement-recovery-m97/summary/summary.md)
- [M97 X 测量恢复字段记录](../reports/measurement-recovery-m97/field-change-record.json)

### 事件、特征和误差

- `EVENT_TRUTH_EVALUATION.md`
- `F2_ERROR_BUDGET_READINESS.md`
- `EVENT_BOUNDED_POSE_GAP_COUNTERFACTUAL_M74.md`
- `EVENT_GAP_KEYPOINT_TRUTH_M75.md`
- `EVENT_GAP_FEATURE_TRUTH_M76.md`
- `FS09_PHASE_TRUTH_M77.md`

### 人工真值

- `SCORING_TRUTH_COLLECTION.md`
- `POSE_DIAGNOSTIC_REVIEW.md`
- `POSE_DIAGNOSTIC_CLIP_TRUTH_M79.md`
- `POSE_DIAGNOSTIC_CLIP_TRUTH_M80.md`
- [M96 24×26 evaluation-only pilot 摘要](../reports/m96-pose-pilot/summary.md)
- [M96 pilot 不可变合同](../reports/m96-pose-pilot/pilot-contract.json)
- [M96 pilot 字段记录](../reports/m96-pose-pilot/field-change-record.json)

### 标定和生产发布

- `CALIBRATION_DATASET_COMPILER.md`
- `CALIBRATION_CANDIDATE_FITTING.md`
- `CALIBRATION_RANKING_ONLY.md`
- `MATURITY_EVIDENCE_BUNDLE.md`
- `RUNTIME_SCORING_PROFILE_BINDING.md`

### 多视频实验

`MULTIVIDEO_*`、`POSE_OBSERVABILITY_*`、M66～M73、M96 同帧诊断和 M97 测量恢复文档属于模型与观测恢复实验。它们用于解释候选方案，不代表默认生产路径已经改变，也不代表准确率或正式评分。

部署注册表已加入只能显式选择的 `rtmpose-l-halpe26-analysis-shadow`。M95 又在独立候选注册表中登记
RTMPose-X Halpe26 384×288，并完成 3 个开发帧、4 个 ROI 的本机加载/输出烟测。M96 在同一 45 帧、
同一 ROI 上完成 M/L/X Halpe26 同帧诊断；M97 的 X 相对 M70 新恢复 +4/丢失 0，但相对 M71 只新恢复
+1、丢失 3，净少 2，因此不晋级。上述结果都没有真人关键点真值或独立视频验证，不是准确率结论。
生产默认仍为 `rtmpose-m-halpe26-online`（M256）。

### M96 可执行人工入口

当前真正可开始的最小试点是 `data/annotations/m96-halpe26-development-pilot-v1`：3 个开发视频各 8 个确定性分布帧，共 24 帧；每帧完整 Halpe26 26 点，即 A、B 每人 624 个点任务。它严格排除 c235 holdout，不含模型坐标，且固定为 evaluation-only，不接训练、dataset export 或 promotion。A、B 可分别启动：

```powershell
python .\scripts\serve_m96_pose_pilot.py --bundle .\data\annotations\m96-halpe26-development-pilot-v1 --role A --port 8766
python .\scripts\serve_m96_pose_pilot.py --bundle .\data\annotations\m96-halpe26-development-pilot-v1 --role B --port 8767
```

当前 A/B/C 人工行均为 0、准确率为 `null`。先由数据负责人把治理模板中的 `REPLACE_WITH` 替换为真实授权/subject/session/split，再由不同角色完成 A/B；只有两份原始提交均通过严格 intake，才可生成只显示必要分歧的 C 裁决包。现有 1,976 个 M95 条目只是空白历史标注任务，不是训练清单或 dataset manifest。

### Demo v1.2 快速口径

普通用户页以 `training_evaluation` 的“动作表现参考分（Beta）”为主，严格只显示 FS01 4 项、FS02 4 项、
FS09 5 项共 13 项。每项给出自然语言观察、证据摘要和训练建议，每类动作另有
`performance_assessment`；分数为 `null` 或评价 unavailable 时显示“暂无法评价”，不显示 0。结果还显示
GPU/CPU、设备和 RTMPose profile。标注视频默认关闭以降低 CPU 编码开销，用户显式开启后才显示骨架
视频；普通用户页不展示 raw JSON 或内部报告链接。

`/?job_id=<UUID>` 可直接恢复一个已有任务，只保存不敏感 job ID；静态资源以 `?v=1.2.0` 避免旧缓存。
当前真实任务 `d7c617d6-89d2-44f2-83e5-de25cb24b689` 已验证 78/100、13/13，三类动作分为
78/77/78，运行于 GPU RTX 5070 Ti。该旧任务的 `cpu_thread_limit=null` 是因为生成时尚未记录该字段；
新任务才写默认 4。正式分仍为 `null`，这些数字不是准确率或教练标定分。

Beta 分只用于训练复盘，不是准确率、正式技术分或教练标定分。`formal_scoring.available=false`，正式分和
grade 仍为 `null`。v1.1 的 `final_demo_score`、`analysis_quality`、`display_score`、
`formation_assessment` 继续作为兼容历史字段保留；M96 的合同和字段记录不回写。页面仍支持多文件顺序
提交、刷新恢复和旧任务 409 `legacy_job_requires_reanalysis`。这些变化没有新增路由或产物，主服务仍为
9 个显式路由、18 项下载白名单。

## 历史文档如何使用

- `RallyMate推理评分系统技术设计与维护手册_v1.0.md`：包含完整演进历史和大量维护细节，适合追溯某项决策，不建议作为第一次阅读入口。
- `SCORING_FEASIBILITY_PLAN.md`：记录从仓库审计到各里程碑的实施过程，适合项目管理和验收追溯。
- 早期“第一阶段视觉感知”“模型训练微调”等文档：描述项目初期目标，部分默认模型、指标数量和当前状态已被后续实现替代。

## 当前最容易误读的十点

1. YOLO 仍用于对象检测，但 YOLO Pose 已不是默认评分人体姿态模型；
2. 13项 F2 表示特征可测量，不表示可以给 A～E；
3. 动态观察器中的骨架和事件是模型输出，不是人工真值；
4. 当前人工提交行为 0，因此所有正式评分仍必须关闭；
5. Demo v1.2 的“动作表现参考分（Beta）”只用于训练复盘，不是准确率、教练标定分、正式总分或 A～E；
6. Beta 单项分缺失时是 `null`，页面显示“暂无法评价”；0 只能是实际算出的 0，不能代替 unavailable；
7. v1.1 的 `final_demo_score`、`analysis_quality`、`display_score` 只是兼容历史，普通用户页不再将其作为表现主分；
8. Demo 只展示严格 13 项，动作幅度来自真实特征，但数值大小本身不代表动作好坏；
9. M96 pilot 是 24×26 的 evaluation-only 空白入口，A/B/C 均为 0 人工行；两个 M95 包中的 1,976 个条目也只是空白历史任务，都不是训练清单；
10. X384 的烟测、M96 同帧覆盖和 M97 恢复结果都不是准确率；M97 相对 M71 有 1 项新增却丢失 3 项，因此 X 不晋级，默认仍为 M256。
