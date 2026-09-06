# RallyMate RTMPose 部署档位与真实 Worker 烟测

状态：RTMPose Halpe26 已作为 F2 特征测量默认；尚未通过 RallyMate 人工真值和 F4 独立测试，不授权正式 A～E。  
验证日期：2026-08-22  
验证 GPU：NVIDIA GeForce RTX 5070 Ti

## 1. 已接入的四套显式预设

| preset | 模型与拓扑 | profile | 项目定位 | 当前状态 |
|---|---|---|---|---|
| `yolo-baseline` | YOLO Pose / COCO17 | realtime | 粗粒度基线与回滚 | 显式回滚 |
| `rtmpose-m-halpe26-online` | RTMPose-M 256×192 / Halpe26 | realtime | 在线 F2 测量主模型 | 默认；未 F4 晋级 |
| `rtmpose-m-halpe26-analysis` | RTMPose-M 384×288 / Halpe26 | analysis + flip-test | 离线深度分析候选 | 已接入，未 F4 晋级 |
| `rtmpose-m-wholebody133-analysis` | RTMPose-M 256×192 / COCO-WholeBody133 | analysis | 离线全身精细点候选 | 已接入，未 F4 晋级 |

注册表 `models/rtmpose/deployment-presets.json` 将 checkpoint、MMPose config、profile、输入尺寸、原生拓扑和晋级状态绑定在一起。未设置环境变量时，Service 读取注册表默认值 `rtmpose-m-halpe26-online`；设置 `RALLYMATE_POSE_PRESET=yolo-baseline` 可显式回滚。预设优先于零散的 Pose 环境变量；YOLO 人体检测保持不变。WholeBody133 仍是显式分析候选。

“默认”严格限定为 Pose 观测和 F2 特征计算。它不代表模型准确率已由 RallyMate 真值验证，不把 13 项提升到 F3/F4，也不允许输出未经标定的 A～E。

## 2. 实际执行链路

三档烟测都执行了同一条服务 Worker 路径：

```text
JobDatabase
  -> PersistentVisionRunner（模型常驻/预加载）
  -> process_one
  -> YOLO person detect
  -> RTMPose-M Halpe26 / WholeBody133 ROI pose
  -> stable primary player timeline
  -> FS01 / FS02 / FS09 candidate events
  -> versioned feature library
  -> registry-derived 13-indicator quality gate
  -> calibration_required / unavailable
```

请求分别为 `examples/request-rtmpose-online-smoke.json`、`examples/request-rtmpose-analysis-smoke.json` 与 `examples/request-rtmpose-wholebody133-analysis-smoke.json`。三者都显式声明 `metric-feasibility-pose-wave-v2.json`，烟测脚本从该注册表读取当前 13 项 ID，不再保存第二份硬编码列表；脚本还要求 request、service 与实际产物的注册表路径、版本和 SHA-256 一致。

Halpe26 两档处理冻结视频前 120 帧，WholeBody133 档处理 31–35 秒的 120 帧。因此下表只能验证各自的部署链路、吞吐和契约，不能作为模型准确率或同窗 A/B 结论。

另有一份专门验证“无覆盖时默认值”的烟测：`examples/request-default-rtmpose-m37-smoke.json` 不依赖 Pose preset 环境覆盖；执行前显式清除 `RALLYMATE_POSE_PRESET`，烟测命令也不传 `--preset`。产物 `runs/rtmpose-m-halpe26-default-m37-smoke/deployment-smoke.json` 记录 `preset_source=deployment_registry_default`，实际选择在线 Halpe26，120 帧耗时 3.893 秒、30.822 FPS，6 个候选事件、26 条指标记录、7 条 `calibration_required` / 19 条 `unavailable`、grade=0、threshold=0、bundle passed。机器汇总见 `reports/default-pose-routing-audit-m37.json`。

M38 使用同一默认选择链路重跑 120 帧，并由 Pipeline 自动物化 `runs/rtmpose-m-halpe26-default-m38-smoke/calculation-readiness.json`。报告按 13 项 required-feature 合同证明 11 项至少有一个完整候选、17/26 条指标事件实例 measured，并明确 FS01-M03、FS02-M05 在短窗口仍缺完整可测候选。对应 97 秒全片报告 `reports/indicator-calculation-readiness-halpe256-full.json` 为 13/13、403/442；两者均保持 `formal_scoring_ready=false`、grade=0、threshold=0。

M39 进一步在同一默认链路物化 `indicator-measurement-portfolio.json`。它只从每项完整 measured 候选中按观测置信度和质量状态选择一个代表特征向量，不读取特征值或动作表现。真实 120 帧 Worker 为 11/13 项，97 秒全片为 13/13 项；每条均带单位、版本、事件、Track 和证据帧。短片缺少的 FS01-M03、FS02-M05 继续 unavailable，所有 grade/threshold 仍为空。

M40 新增同动作周期产物 `scoring-cycle-measurement.json`。真实 120 帧默认 Worker 将 6 个事件唯一闭合为 2 组 FS01→FS02→FS09，最佳周期 11/13 特征完整、5/13 评分上下文通过，完整周期 0；97 秒全片将 102 个事件闭合为 34 个周期，其中 25 个周期在同一 Track、同一次动作内 13/13 项全部 measured，代表周期 12/13 评分上下文通过。系统不跨周期补值，周期候选仍不等于人工事件真值。

## 3. 本机烟测结果

| 项目 | Halpe26 Online 256×192 | Halpe26 Analysis 384×288 | WholeBody133 Analysis 256×192 |
|---|---:|---:|---:|
| 处理帧数 | 120 | 120 | 120 |
| Pipeline elapsed | 4.003 s | 4.819 s | 5.164 s |
| 有效处理吞吐 | 29.976 FPS | 24.900 FPS | 23.240 FPS |
| Track / Pose 覆盖 | 100% / 100% | 100% / 100% | 100% / 100% |
| FS01 / FS02 / FS09 候选事件 | 2 / 2 / 2 | 2 / 2 / 2 | 3 / 3 / 3 |
| 13 项指标事件记录 | 26 | 26 | 39 |
| `calibration_required` | 7 | 6 | 9 |
| `unavailable` | 19 | 20 | 30 |
| 非空 A～E grade / threshold version | 0 / 0 | 0 / 0 | 0 / 0 |
| 左/右足踝诊断有效 | 3/4、1/4 | 3/4、3/4 | 6/6、6/6 |
| 跨产物 bundle 校验 | passed | passed | passed |

这次实测证明三套部署配置均能通过主服务 `PersistentVisionRunner` 跑通当前 `.14 / event v0.4.1 / phase v0.3 / quality v1.6` 注册表声明的 13 项 Pose 指标链路；其中 WholeBody 模型实际保留 133 个原生点，没有回落成 Halpe26。这里的 `unavailable` 包含 score-only 的边界、身份、跳点、左右点和语义门禁，不等于特征计算失败；模型准确率必须使用相同边界与人工校正关键点另行评测。

机器产物：

- `runs/rtmpose-m-halpe26-online-smoke/deployment-smoke.json`
- `runs/rtmpose-m-halpe26-analysis-smoke/deployment-smoke.json`
- `runs/rtmpose-m-wholebody133-analysis-smoke/deployment-smoke.json`
- 三个运行目录内的 `events.jsonl`、`features.jsonl`、`indicator-features.jsonl`、`scores.jsonl`、`summary.json` 和 HTML 报告。

`deployment-smoke.json` schema 1.1.0 保留 schema 1.0.0 的顶层 `indicator_ids` 等字段，同时新增版本化注册表、threshold 空值计数、bundle 校验以及 `accuracy_claim=false` 语义，避免历史读取器失效或把烟测误当准确率。

## 4. 启动和回滚

先准备隔离运行时：

```powershell
.\scripts\prepare_rtmpose_runtime.ps1
```

重跑三档烟测：

```powershell
.\scripts\run_pose_deployment_smoke.ps1 -Profile Online
.\scripts\run_pose_deployment_smoke.ps1 -Profile Analysis
.\scripts\run_pose_deployment_smoke.ps1 -Profile WholeBody
```

启动本地 API + 常驻 Worker：

```powershell
.\scripts\run_rtmpose_service.ps1 -Profile Online -Port 8000
.\scripts\run_rtmpose_service.ps1 -Profile Analysis -Port 8000
.\scripts\run_rtmpose_service.ps1 -Profile WholeBody -Port 8000
```

独立 Worker：

```powershell
.\scripts\run_rtmpose_worker.ps1 -Profile Online
.\scripts\run_rtmpose_worker.ps1 -Profile WholeBody
```

回滚时必须显式设置 `RALLYMATE_POSE_PRESET=yolo-baseline`，或使用 `scripts/run_local_inference.ps1 -PosePreset yolo-baseline`。不设置该变量将使用 Halpe26 默认。由于 RTMPose 依赖隔离的 NumPy 1.x / MMPose 环境，不要用原 YOLO Python 启动 RTMPose Worker。

## 5. 对“能否评分”的准确结论

目前可以完成的是：对候选事件输出当前注册表 13 项指标的版本化测量值、单位、置信度、有效性、原因和证据帧；Halpe26 与 WholeBody133 都可输出 COCO17 无法提供的脚趾证据和二维膝—踝—前足角，WholeBody133 还保留完整脚跟、手部与面部原生点供后续非本轮特征使用。

目前不能宣称的是：事件识别准确、二维角等于真实三维踝背屈角，或这 13 项已能可信地产生 A～E。没有人工事件边界、人工校正关键点、多教练标签和独立测试时，统一评分接口必须保持 `calibration_required` 或 `unavailable`。

界面和报告不得再把 `calibration_required` / `unavailable` 显示成 0 分。部署烟测中的 13 项状态为 F2 `measured` 或 `unavailable`，表示 RTMPose 已经把候选事件和版本化特征送入评分契约；只有加载通过数据校验、标注一致性和独立测试门禁的标定资产后，状态才允许变为 `scored` 并出现 A～E。

该门禁现由代码强制执行，而不只是流程约定：生产阈值或序数资产必须使用标定契约 1.2.0，并携带可追溯的独立测试报告 SHA-256、正式批准状态和精确特征版本/单位绑定。三套运行目录均已通过评分跨产物校验，能够追溯到视频、帧、稳定 Track、事件、特征函数、注册表和模型版本。

已获批准的生产标定资产可以在单次请求中显式列入 `scoring.calibration_assets`，或由服务环境变量 `RALLYMATE_SCORING_CALIBRATION_ASSETS` 配置（Windows 多路径以 `;` 分隔）。但资产 JSON 自称 `passed` 或 `approved_for_scoring=true` 不构成生产授权：服务还必须由运维配置 `RALLYMATE_SCORING_TRUSTED_PROMOTION_LEDGER`，账本中的唯一 active entry 必须与资产 canonical SHA-256、完整 `promotion_lineage` 和晋级报告哈希精确一致。Pipeline 随后把晋级时 registry version/canonical SHA-256/indicator ID 与本次运行实际注册表精确绑定，并以实际注册表确认 F4；任务产物中的自报成熟度不能替代该校验。普通上传和任务请求不能选择账本路径，服务也拒绝把账本放在作业可写的 `uploads`、`requests` 或 `runs` 目录。Pipeline 在创建运行目录和启动 GPU 推理前完成这些校验，并只把不可 JSON 序列化且完成 registry binding 的运行时授权包装传给评分函数；反序列化后的普通资产必须重新经过账本与注册表验证。`test_only` 资产不会通过生产 Pipeline。当前仓库没有任何生产标定资产或可信生产账本，因此示例请求保持空列表或省略该字段。
# 全片模型路由审计（M30）

三种配置已在同一 2,911 帧真实视频上完成完整闭环和固定边界复算。模型各自切分时，Halpe256 为 `403/442`、Halpe384 为 `415/442`、WholeBody133 为 `373/429` 条指标事件特征完整；这组数字同时包含事件边界变化，不能直接用于路由。

固定使用 Halpe256 的 102 个候选事件 ID、Track、起止时间和阶段后，结果变为：

- `rtmpose-m-halpe26-256x192`：`403/442`；
- `rtmpose-m-halpe26-384x288`：`399/442`，相对基准 `-4`；
- `rtmpose-m-wholebody133-256x192`：`369/442`，相对基准 `-34`。

因此当前路由保持 Halpe256 为评分主配置。Halpe384 是待人工真值验证的分析候选；WholeBody133 用于完整 133 点动态输出及未来手/脸/足部特征，不作为当前 13 项自动 fallback。任何逐帧、逐事件或逐特征择优拼接都会改变误差分布并破坏单模型 provenance，当前契约明确禁止。

机器报告：`reports/pose-profile-routing-audit-full.json`。其中 `accuracy_claim=false`、`ground_truth_provided=false`、`grades_generated=false`、`thresholds_generated=false`；路由不构成 F3/F4 或正式评分认可。
