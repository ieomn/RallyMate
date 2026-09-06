# RallyMate 十三项 Pose 指标真值采集与标定操作说明

版本：`scoring-truth-pack-v0.3.0`  
范围：固定机位、单个主球员、FS01/FS02/FS09、十三个 Pose-only 指标  
当前状态：M89 三视频技术交接、M90 放行记录契约，以及 M93 的 A/B 独立标注、C 裁决和私有事件/阶段 intake 代码均已实现并通过测试；M90 零标签计划已冻结，但尚未创建实际负责人放行记录、M93 执行包或真实提交，也没有真实人工真值、正式等级或生产标定

## 0. 先执行的最小人工试点（M86–M88）

**开始人工事件/阶段标注前，必须先完成 M90 的本地负责人放行。** 这一步只允许 A、B、C
按一份冻结计划开始独立标注。M93 已实现后续执行代码，但没有精确通过校验的实际放行记录时，
不能构建 A/B 执行包、C 裁决包或中央 intake；仓库中的工作台源文件不是可直接使用的标注包。

放行记录固定使用 `scoring-truth-operator-reviewed-local-release-v1.0.0`，并精确引用：

- `scoring-truth-event-annotation-plan-v2.0.0` 的原始文件 SHA-256；
- M89 的 manifest、bundle、content root 和 source projection；
- 三个视频任务、FS01/FS02/FS09、完整视频审阅要求；
- A/B 独立标注、C 在两份提交后再裁决的角色规则；
- scope 与角色规则摘要 SHA-256。

该记录是本地工作流的放行单，不是身份认证，不证明负责人身份或填写时间，也不替代真实标签。
它不允许标定、晋级或生产评分；其中 `calibration_authorized`、`promotion_authorized` 和
`production_scoring_authorized` 均固定为 `false`。模板
`examples/scoring-truth-operator-release-record.template.json` 故意不能直接通过校验，不能把模板
当作已放行记录。

`data/annotations/fs09-phase-truth-m77-v1/` 是包含密封候选与源谱系的私有权威包，
不得分发或作为 HTTP 根。`data/annotations/fs09-phase-truth-m77-blind-handoff-v1/`
是可移动的公开技术包：两段媒体各 5 秒、24 FPS、无 Pose/候选边界/候选阶段叠加，
并带候选选择的粗粒度目标提示点。提示点只用于识别目标动作，不约束人工边界；它和
candidate-contract SHA 也不是对已知仓库/模型输出的保密机制。未来人员必须只在隔离设备
接触公开 authorized bundle，不能访问仓库、reports、runtime events 或其他 annotation pack。

分析口径在 0 标签状态下由内容哈希绑定，但它不是外部登记或生产接受协议。当前仍为
0 annotation、0 adjudication、0 compiled event；两个同一视频/会话的任务只可用于流程和
边界敏感性诊断，不是独立测试、coach grade truth 或 F3/F4 证据。浏览器 decision/export
时间来自可修改的客户端时钟，只提供未篡改工作台的一致性，不是可信时间戳。

放行后，预期流程才是两名独立 annotator 各自提交、第三人把裁决精确绑定到两份 revision，
再由中央建立私有 intake session。完整且当前权威的边界见 `docs/FS09_PHASE_TRUTH_M77.md`。

### 0.1 M89 三视频全片 event/phase 技术交接

`data/annotations/scoring-truth-event-handoff-m89-v1/` 是独立、可移动的三视频全片技术包。
它只包含 `review.html`、本地 JS/CSS、包内白名单 Range 服务、说明文件和三段明确授权的
完整视频；不包含主真值包的候选 ID、候选边界、候选阶段、质量标记、模型绑定或本机源路径。
M89 本身仍是纯技术交接：页面只允许播放、seek 和逐帧，不含任何真实人工标签、教练等级、
阈值或正式评分。M90 不会改写 M89；它只在随后新建的、精确绑定 M89 的标注工作流中检查
本地负责人放行记录。

中央可重放私有来源绑定并验证 Schema：

```powershell
$env:PYTHONPATH = "$PWD\src"
python scripts/build_scoring_truth_event_handoff.py `
  --source-pack data/annotations/scoring-truth-pack-v1 `
  --video-directory FULL-TEST `
  --output data/annotations/scoring-truth-event-handoff-m89-v1 `
  --validate-only
```

复制到任意目录后，只在包目录内运行随包服务；不要使用仓库根目录或通用静态服务器：

```powershell
Set-Location <scoring-truth-event-handoff-directory>
python .\serve_scoring_truth_event_handoff.py --directory . --bind 127.0.0.1 --port 8765
```

浏览器实测三段媒体均完整可 seek，时长分别为 48.067、97.033、479.833 秒；第一段
`30000/1001` FPS 的 `+1 帧` 推进约 33.366 ms。通过这些技术检查不等于已经产生人工事件、
教练等级、阈值或 F3/F4 证据；当前 M89 仍为 0 条真实标签、0 个正式等级。

M89 的 `scoring_truth_intake` 只负责把未来收到的五份私有 CSV 原字节、来源、编译结果和
精确目录拓扑原子冻结成 `PRIVATE` 会话。它明确包含候选信息，并始终声明未获标定授权、
不可晋级、不可用于生产；当前技术包没有导出，因此不能进入该 intake。它是旧私有模板的
五 CSV intake，不等于下面 M93 新增的三角色事件/阶段 JSON intake。

### 0.2 M93 A/B/C 事件/阶段执行与私有 intake

M93 没有改写或解锁 M89。它新增一条独立的、运行时才核对 M90 放行记录的执行链：

1. 用同一份计划、实际负责人放行记录和完整 M89 技术包分别构建角色锁定的 A、B 执行包；
2. A、B 各自完整审阅三段视频并导出一份规范 JSON；零事件是合法观察结论，但仍须逐视频确认完整审阅；
3. 只有两份提交均与各自执行包、角色和 revision 精确一致时，才可构建 C 裁决包；
4. C 必须处理 A/B 每个来源事件，允许接受、合并、拆分、拒绝或给出带理由的 C 新增事件，并须再次确认三段完整视频；
5. 中央把 A/B/C 原始提交、三份来源清单、M90 binding、M89 完整来源树和编译结果原子冻结为新的 `PRIVATE` intake。

A/B execution manifest 版本为 `scoring-truth-event-execution-bundle-v1.0.0`，C adjudication
manifest 版本为 `scoring-truth-event-adjudication-bundle-v1.0.0`；两类提交版本分别为
`scoring-truth-event-annotation-submission-v1.0.0` 和
`scoring-truth-event-adjudication-submission-v1.0.0`。浏览器工作台不嵌入机器候选边界、阶段值、
关键点或等级；A/B 不允许导入同伴提交，C 只接受构包时已经固定摘要的两份提交。

每份 bundle ref 只有 `{bundle_id, manifest_binding_sha256}`。`manifest_binding_sha256` 对 manifest
去掉 `artifacts`、`content_root_sha256` 和自身后的规范 JSON 投影计算，用来避免页面 bootstrap
与 manifest 形成自引用；完整目录仍由外部读取的 manifest 原始摘要、逐文件摘要和 content root
共同核对。A、B、C 的匿名角色 ID 先做 trim、NFKC 和大小写折叠后必须三者互异。

工作台按执行 ID、角色、bundle binding 和规范化参与者 ID 隔离本地草稿。修改某段 A/B 事件会
清除该段完整审阅确认；C 的来源视频 revision 变化会清除对应视频确认并使相关裁决过期。即使 A/B
三段视频均确认“没有目标事件”，C 也必须完成 3/3 视频确认后才能导出。客户端和本地生成时间只作
同一流程内的先后顺序核对，不证明实际身份或受信时间。

构建与重放入口为：

```powershell
$env:PYTHONPATH = "$PWD\src"
python scripts/build_scoring_truth_event_execution.py --plan <PLAN> --operator-release <RELEASE> --handoff <M89_DIR> --role-slot A --output <A_DIR>
python scripts/build_scoring_truth_event_execution.py --plan <PLAN> --operator-release <RELEASE> --handoff <M89_DIR> --role-slot B --output <B_DIR>
python scripts/build_scoring_truth_event_adjudication.py --annotator-a-bundle <A_DIR> --annotator-a-submission <A_JSON> --annotator-b-bundle <B_DIR> --annotator-b-submission <B_JSON> --output <C_DIR>
python scripts/ingest_scoring_truth_event_phase.py --plan <PLAN> --release-record <RELEASE> --handoff <M89_DIR> --execution-a-bundle <A_DIR> --execution-a-submission <A_JSON> --execution-b-bundle <B_DIR> --execution-b-submission <B_JSON> --adjudication-bundle <C_DIR> --adjudication-submission <C_JSON> --output-dir <PRIVATE_INTAKE_DIR>
```

这些命令不会创建负责人放行记录。当前仓库没有可供上述命令使用的实际放行实例，也没有生成
任何规范 M93 A/B/C bundle、提交或 intake。现有内容仅是代码、Schema、工作台源文件和合成测试；
真实标签仍为 0，13 项仍为 F2，正式 A～E 仍为 0。M93 execution/UI/intake 定向验证为
37/37（13.242 秒），连同 M90/M89 的联合验证为 63/63（21.427 秒，0 跳过），全仓 Draft 2020-12
Schema 为 125/125，最终全仓回归为 909/909（779.863 秒，0 失败/错误）；这些数字只表示软件合同
通过，不是网球动作准确率。

## 1. 私有权威模板（当前禁止直接标注）

目录：`data/annotations/scoring-truth-pack-v1/`

**HARD STOP：该目录不是公开盲包或 authorized handoff。** 它同时包含候选事件 ID、
精确候选边界、关键点抽样时间和私有源路径，只能由中央操作员保存和核验；不得分发、
不得作为 HTTP 根，也不得由 A/B/C 或教练直接填写。以下工作台入口目前只用于播放与
布局技术 QA，所有会写草稿、接受记录、导入或导出的控件应为禁用状态。

| 文件 | 用途 |
|---|---|
| `review.html` | 离线人工真值工作台：视频 seek、事件/phase、关键点点击、语义任务和 CSV 导出 |
| `truth-workbench.js/.css` | 工作台的本地脚本和样式；无 CDN、无网络请求 |
| `event-annotations.csv` | 全视频人工事件边界、阶段点和边界不确定度 |
| `full-video-review-completion.csv` | 证明标注者完整审阅过视频，而非只确认模型候选 |
| `semantic-annotations.csv` | 方向、侧别、接触/稳定时间和相机运动审计；不可观察也必须显式说明 |
| `keypoint-annotations.csv` | 试点事件内逐帧肩/髋/膝/踝/脚趾/脚跟真值 |
| `coach-labels.csv` | 三名教练的 A～E 或分指标排序任务 |
| `manifest.json` | 视频 SHA、选择协议、候选来源、数量和安全声明 |
| `compiled/validation-report.json` | 编译后的缺失、错误、一致性和就绪状态 |

当前试点包含 9 个候选事件（每个视频每类事件各 1 个）、150 个密集关键点帧、2,100 个关节行、51 个语义真值任务和 234 个教练任务。候选只用于控制试点工作量，不是真值，也不能用于计算无偏 Event Recall。

### 1.1 打开和保存工作台

可直接双击 `review.html`（`file://` 模式）。工作台内的视频是相对 URL `../../../FULL-TEST/<video_id>.mp4`，HTML 不内嵌本机绝对路径、视频数据、模型骨架或模型坐标。如果直接打开时浏览器禁止视频或目录写入，可在仓库根目录运行已加固的本地 Range 服务：

```powershell
python scripts/range_http_server.py --directory . --bind 127.0.0.1 --port 8765 `
  --allow data/annotations/scoring-truth-pack-v1/review.html `
  --allow data/annotations/scoring-truth-pack-v1/truth-workbench.css `
  --allow data/annotations/scoring-truth-pack-v1/truth-workbench.js `
  --allow FULL-TEST/3ae77ee3271d67de171585a5c39ddd69.mp4 `
  --allow FULL-TEST/850cb0006b406c7176eeda8d711cd065.mp4 `
  --allow FULL-TEST/8d7754d0de6d315674013d5b69a0b6ba.mp4
```

再打开 `http://127.0.0.1:8765/data/annotations/scoring-truth-pack-v1/review.html`。该服务只允许 loopback bind 与 loopback `Host`，关闭目录列表，并且只服务命令中逐项列出的 3 个工作台文件与 3 段指定视频；第四段或任何其他仓库文件均返回 404。URL 只解码一次，链接/重解析点与密封候选会被拒绝。它只用于中央技术 QA，不接收真值。不要使用 `python -m http.server`，也不要把整个 `FULL-TEST`、私有包或 intake session 设为允许目录。

当前不得在此目录保存草稿、写回目录、下载后覆盖 CSV、重新导入或原地编译。
M89 已提供只含三段指定媒体、完全不含候选/模型输出/私有路径的事件技术包。只有新的
M90 放行记录精确通过校验后，才可在另建的标注工作流中产生 A/B/C 导出，中央再通过原子
intake 建立新的私有不可变会话。后续各标注步骤描述的是该未来流程的语义要求，不是对当前
私有模板的直接写入授权。

## 2. 正确标注顺序

### 2.1 第一遍：完整视频独立事件标注

标注者先打开 `review.html`，只使用“事件与阶段”页签完整观看每个视频。该页签不显示候选事件。需要找出视频内所有 FS01、FS02、FS09，而不是只判断系统给出的候选是否正确。否则漏掉的事件不可见，Event F1 会被高估。

可在工作台中用当前视频时间分别打 `start/end` 和该事件族的必需 phase。工作台会阻止缺少边界、必需 phase、`annotator_id`、`reviewer_id`、`annotation_confidence` 或 `boundary_uncertainty_ms` 的事件直接变成 `accepted`。`event-annotations.csv` 每行定义一个人工事件：

- `event_id` 使用人工稳定 ID，不复用候选 ID；
- `start_ms/end_ms` 是人工边界；
- 只填写该事件适用的阶段时间，其余阶段列留空；
- `annotation_confidence` 是标注者对本次边界的自评，不是模型准确率；
- `boundary_uncertainty_ms` 必须显式填写；
- `view_group` 使用预先约定的视角分组；
- `annotator_id` 使用稳定匿名 ID。
- 多名标注者的原始边界先保留，只有裁决后的最终行设置 `adjudication_status=accepted`；
- 教练表中的 `event_id` 必须改填裁决后的人工事件 ID，不能沿用 `candidate_event_id`。

完成后在 `full-video-review-completion.csv` 将对应记录设为 `true`。每个视频必须至少有两名独立事件标注者确认完整审阅；只审一个视频、只有一名标注者，或只提交 FS01 都不会把事件真值标记为就绪。

### 2.2 第二遍：事件语义真值

在 `semantic-annotations.csv` 中把关键帧无法表达的真值绑定到裁决后的人工 `event_id`：方向、支撑/启动/制动侧、脚接触或稳定时刻、稳定区间和相机运动审计。

- 能观察时设置 `observable=true` 并填写对应类型的值；
- 二维固定机位确实无法观察时设置 `observable=false`，值列保持空白，并填写非空 `null_reason`；
- `observable=false` 是诚实的观测结论，不会被补成模型值，也不等于该语义可用于误差评测；
- 只有裁决后的最终记录设置 `adjudication_status=accepted`。

### 2.3 第三遍：关键点密集标注与裁决

事件独立标注完成后，才进入 `review.html` 的“密集关键点”页签。选择试点片段和原始帧后，在画面中点击当前解剖学关节；工作台使用视频内容区而不是黑边计算归一化坐标。对 `keypoint-annotations.csv` 中列出的每一帧、每个关节标注：

- 坐标是原始画面归一化坐标，原点在左上；
- 左右按运动员解剖学左右，而不是画面左右；
- 不可见关节设置 `visible=false`，坐标留空；
- 0 是合法坐标，不能表示缺失；
- 初标和复核完成后，只有最终裁决行设置 `adjudication_status=accepted`；
- 同一视频/帧/关节只能有一个 accepted 真值。

评测器现在会把所有未显式标注的位置保持为缺失，不再用模型预测补齐。关键点覆盖不足时只返回 `insufficient_keypoint_ground_truth_coverage`。

### 2.4 第四遍：多教练评分或排序

至少两名教练独立填写 `coach-labels.csv`。最低覆盖不是“任意一个指标有两名教练”，而是每个视频的全部 13 个指标都至少有一个相同人工事件、相同标签类型被两名教练共同标注，并且每个指标的一致性都能单独计算：

- `label_type=grade` 填 A～E；
- `label_type=ranking` 填同一 `rank_group_id` 内的名次；
- 空白表示未标注，不会解释为 E 或 0；
- 教练在评分前应查看动作定义和完整事件上下文，但不能查看模型分数或未来阈值。

系统计算等级的 quadratic weighted kappa 和排序的 Kendall tau。该结果只用于决定标签是否足以标定，不会自动晋级 F3/F4。

## 3. 编译与验证

当前 `manifest.json.scoring_source_binding` 已绑定 registry
`pose-wave-2026-08-22.17`、`primary-player-v0.3.0`、event v0.4.1、phase v0.3、quality v1.6、
`minimum-scoring-loop-v0.6.0` 和 M53 全片 Halpe `indicator-features.jsonl`（SHA-256
`F95DA9A233C358D5CEFB379892163422ACF2B519EC58BBEADEEFA26EF7B88702`）。
该绑定只声明机器测量来源，候选事件仍不是真值，也不会把空白教练任务变成等级。

FS02-M02 的完整标定向量在 M42 中包含 `target_direction_alignment_error_deg`。人工 target 仍从已有 `target_direction` semantic 任务进入：只有 accepted image-plane 值、同一人工事件和完整 lineage 才会参与精确关联；pending/unobservable 不会被解释为 0。当前 semantic truth 仍为 0，因此新空标定数据集为 annotation_required。

M43 的误差评测必须同时传入人工 semantic；仅提供一个空文件路径不等于已有语义真值。报告会分别记录 `manual_semantic_record_count`、`required_context_features` 与 `context_feature_truth_complete`：

```powershell
$env:PYTHONPATH = "$PWD\src"
python .\scripts\evaluate_scoring_truth.py `
  --frames .\reports\scoring-candidate-multivideo-m53\runs\850cb0006b406c7176eeda8d711cd065\frames.jsonl `
  --primary-timeline .\reports\scoring-candidate-multivideo-m53\runs\850cb0006b406c7176eeda8d711cd065\primary-player.jsonl `
  --predicted-events .\reports\scoring-candidate-multivideo-m53\runs\850cb0006b406c7176eeda8d711cd065\events.jsonl `
  --manual-events .\data\annotations\scoring-truth-pack-v1\compiled\manual-events.jsonl `
  --manual-keypoints .\data\annotations\scoring-truth-pack-v1\compiled\manual-keypoints.jsonl `
  --manual-semantics .\data\annotations\scoring-truth-pack-v1\compiled\manual-semantics.jsonl `
  --registry .\metric-feasibility-pose-wave-v2.json `
  --output .\reports\truth-pack-empty-evaluation-m53.json
```

有人工 FS02 事件后，评测器将模型 Pose/预测边界值与人工校正 Pose/人工边界/人工目标方向精确关联，输出目标方向对齐误差的 MAE、P95、Bias、有效率、分视角统计，以及 Pose、事件边界、平滑和缺失四类反事实预算。它不自动决定 F2→F3，也不生成阈值。

## 当前视频任务入口（M53）

不要再用历史 M31 的 129 项清单判断当前覆盖。当前入口为：

`reports/scoring-truth-action-worklist-halpe256-full-m53/index.html`

该页面从当前 `.17` / loop v0.6.0 全片产物生成，覆盖 266/266 条 unavailable，并按四类路由：

- `pose_diagnostic_truth`：进入当前 M53 Pose 跳点、左右交换或 Track 连续性视频队列；
- `manual_keypoint_feature_truth`：在评分真值工作台完成人工事件和密集关键点；
- `scoring_reference_context`：填写并独立复核 FS02 目标方向；
- `manual_event_or_semantic_truth`：完成人工事件边界、阶段、启动脚侧别或稳定阶段语义。

同一指标实例可能需要多个动作，必须查看 `concurrent_truth_requirements`；不能把完成一个任务解释为该实例已恢复。JSON/CSV 只用于任务路由，人工结果仍必须写入各自受控模板、经独立复核和 Python 编译。清单本身不会修改 quality/scoring gate。

## 当前行动证据完成度（M45）

Pose 视频队列中的浏览器决定只属于 `review_complete_not_adjudicated`，不能直接作为真值。当前 97 秒视频的完整时间线 Pose 真值入口为：

`reports/pose-diagnostic-truth/halpe26-full-m53-v1/index.html`

该包覆盖 2,911 帧和 261 个候选诊断任务。标注者必须填写完整时间线 coverage，稀疏阳性另填 positives，并由两名独立标注者和未参与标注的 reviewer 完成裁决。当前 accepted coverage/positive 均为 0。

完成任一轮人工录入、独立复核和 Python 编译后，用以下命令重新生成行动证据状态：

```powershell
$env:PYTHONPATH = "$PWD\src"
python .\scripts\evaluate_scoring_truth_action_readiness.py `
  --worklist .\reports\scoring-truth-action-worklist-halpe256-full-m53\worklist.json `
  --truth-validation .\data\annotations\scoring-truth-pack-v1\compiled\validation-report.json `
  --manual-events .\data\annotations\scoring-truth-pack-v1\compiled\manual-events.jsonl `
  --manual-keypoints .\data\annotations\scoring-truth-pack-v1\compiled\manual-keypoints.jsonl `
  --manual-semantics .\data\annotations\scoring-truth-pack-v1\compiled\manual-semantics.jsonl `
  --scoring-truth-evaluation .\reports\truth-pack-empty-evaluation-m53.json `
  --pose-truth-manifest .\reports\pose-diagnostic-truth\halpe26-full-m53-v1\manifest.json `
  --pose-truth-evaluation .\reports\pose-diagnostic-truth\halpe26-full-m53-v1\evaluation.json `
  --reference-context .\data\annotations\scoring-reference-context-v1\850cb0006b406c7176eeda8d711cd065-fs02-target-directions.json `
  --output .\reports\scoring-truth-action-readiness-halpe256-full-m53.json `
  --overwrite
```

当前输出为 `annotation_required`：168/168 工作项、266/266 指标实例未满足。该报告只回答“所需人工证据是否已按契约齐备”；即使变为 `evidence_satisfied_pending_scoring_recompute`，也不会自动改变原评分，必须重新运行评分闭环。没有教练标定时最高仍是 `calibration_required`。

## 共享证据批次（M46）

不要逐条重复执行 168 个 work item。当前共享证据入口为：

`reports/scoring-truth-refresh/m53-empty-registry-17-v1/evidence-plan/index.html`

它把相同原始证据折叠为 87 个单元，并按可共同支撑的指标实例数排序。最先的 4 个单元是完整时间线 `keypoint_jump`、`left_right_swap`、`primary_identity_ambiguity` 和 `source_track_switch`；其后是事件边界、阶段、密集关键点、特征真值、支撑/启动侧语义和目标方向。一次 accepted 证据会在重跑 M45 后自动反映到所有依赖 work item，不需要重复复制。

```powershell
$env:PYTHONPATH = "$PWD\src"
python .\scripts\build_scoring_truth_evidence_plan.py `
  --readiness .\reports\scoring-truth-action-readiness-halpe256-full-m53.json `
  --output-dir .\reports\scoring-truth-evidence-plan-halpe256-full-m53 `
  --overwrite
```

当前 87/87 单元仍需标注。这个排序只代表“共享影响范围”，不代表模型准确率、动作优劣、人工工时或最终等级；同一 work item 依赖多个证据单元时必须全部完成。

## 单命令刷新全部证据（M47）

完成或修改任意人工 CSV 后，推荐使用下面的统一命令；不要再手工按顺序覆盖多份评测 JSON：

```powershell
$env:PYTHONPATH = "$PWD\src"
python .\scripts\refresh_scoring_truth_evidence.py `
  --pack .\data\annotations\scoring-truth-pack-v1 `
  --frames .\reports\scoring-candidate-multivideo-m53\runs\850cb0006b406c7176eeda8d711cd065\frames.jsonl `
  --primary-timeline .\reports\scoring-candidate-multivideo-m53\runs\850cb0006b406c7176eeda8d711cd065\primary-player.jsonl `
  --predicted-events .\reports\scoring-candidate-multivideo-m53\runs\850cb0006b406c7176eeda8d711cd065\events.jsonl `
  --registry .\metric-feasibility-pose-wave-v2.json `
  --worklist .\reports\scoring-truth-action-worklist-halpe256-full-m53\worklist.json `
  --pose-truth-manifest .\reports\pose-diagnostic-truth\halpe26-full-m53-v1\manifest.json `
  --reference-context .\data\annotations\scoring-reference-context-v1\850cb0006b406c7176eeda8d711cd065-fs02-target-directions.json `
  --output-root .\reports\scoring-truth-refresh
```

默认 refresh ID 由 UTC 时间和所有输入的内容指纹组成。命令会预检 CSV、创建不可变快照、重算事件/特征/Pose/M45/M46、原子更新 `latest.json`，并重建总报告。只有自动化任务明确不需要总报告时才使用 `--skip-main-report`。

当前最新入口为 `reports/scoring-truth-refresh/m53-empty-registry-17-v1/index.html`。它显示 0/168 work item 和 0/87 evidence unit 已满足；这代表人工输入仍为空，不是模型得分为零。该刷新命令不会调用 A～E 标定后端。

`build_scoring_truth_pack.py` 只用于创建全新的空白真值包；它会生成模板 CSV，已有
人工输入后不得对同一目录再次运行。需要为一个全新的包选择当前候选 bundle 时，使用
`--candidate-events-template`（模板必须包含 `{video_id}`）；构建器会把逐视频路径、SHA-256、
detector version 和 source ID 写入 manifest。对现有包更新校验结果只运行下面的
`compile_scoring_truth_pack.py`，该命令不会清空模板或人工输入。

```powershell
$env:PYTHONPATH = "$PWD\src"
.\.venv\Scripts\python.exe .\scripts\compile_scoring_truth_pack.py `
  --pack .\data\annotations\scoring-truth-pack-v1
```

只有需要 CI 强制阻止未完成标注时才加 `--require-complete`。编译器输出：

- `compiled/manual-events.jsonl`
- `compiled/manual-keypoints.jsonl`
- `compiled/manual-semantics.jsonl`
- `compiled/coach-labels.jsonl`
- `compiled/validation-report.json`
- `compiled/by-video/<video_id>/`：可直接用于单视频评测的三份 JSONL

`compiled/validation-report.json` 使用 `truth-readiness-matrix-v1.0.0`，逐项列出 `video × event/required phase × indicator × annotator overlap` 覆盖和阻断原因。`ready_for_evaluation_and_calibration_review` 只有在以下条件全部满足时出现：每个视频有两名完整审阅者；每个视频都有 FS01/FS02/FS09 且每条 accepted 事件包含该事件族全部必需阶段；密集关键点模板完成且每个事件族有可观测核心骨架；语义任务均已裁决；每个视频的 13 项均有两名教练同项重叠且逐指标一致性可计算。它只表示输入完整、契约合法，可进入人工复核，不代表自动通过 F3/F4。

## 4. 运行事件与特征误差评测

以当前已经生成完整 13 项 Pose Wave v2 bundle 的 `850cb0006b406c7176eeda8d711cd065` 为例：

```powershell
.\.venv\Scripts\python.exe .\scripts\evaluate_scoring_truth.py `
  --frames .\reports\fs09-pose-wave-v2\850cb0006b406c7176eeda8d711cd065\frames.jsonl `
  --primary-timeline .\reports\fs09-pose-wave-v2\850cb0006b406c7176eeda8d711cd065\primary-player.jsonl `
  --predicted-events .\reports\fs09-pose-wave-v2\850cb0006b406c7176eeda8d711cd065\events.jsonl `
  --manual-events .\data\annotations\scoring-truth-pack-v1\compiled\by-video\850cb0006b406c7176eeda8d711cd065\manual-events.jsonl `
  --manual-keypoints .\data\annotations\scoring-truth-pack-v1\compiled\by-video\850cb0006b406c7176eeda8d711cd065\manual-keypoints.jsonl `
  --output .\reports\850cb0006b406c7176eeda8d711cd065-truth-evaluation.json
```

评测 CLI 默认使用当前 `metric-feasibility-pose-wave-v2.json`，并在报告中记录全部输入及其 SHA-256。`frames`、`primary-timeline` 和 `predicted-events` 必须来自同一个模型、同一个视频、同一个 v2 bundle；禁止把 `runs/full-test` 的 YOLO/COCO-17 帧与 RTMPose 时间线或事件混用。M49 已为真值包三段视频绑定同一 RTMPose-M/Halpe26 Pose contract 与当前主球员 timeline；事件误差评测仍须为每段另行提供同源 predicted events，不能把 measurement source manifest 冒充事件预测。

编译器已经按 `video_id` 自动拆分真值，避免其他视频的相同帧号混入当前序列。结果包含 Event F1、Segment IoU、事件起止 Boundary MAE/P95、关键阶段 Boundary MAE/P95 及逐阶段结果，以及特征 MAE/P95/Bias、分视角结果和误差预算。

## 5. 在人工边界上重算标定特征

候选事件 bundle 中的 `indicator-features.jsonl` 使用候选 `event_id`，不能与人工事件做 IoU 近似关联后直接进入标定。每个视频必须在该 Pose 模型自己的 frames/timeline 上，使用 accepted 人工 `event_id`、边界、phase 和 `person_track_id` 重新计算：

```powershell
$env:PYTHONPATH='src'
python .\scripts\build_manual_event_features.py `
  --frames .\runs\pose-ab\rtmpose-m-halpe26-256x192\850cb0006b406c7176eeda8d711cd065\frames.jsonl `
  --primary-timeline .\reports\pose-scoring-ab\rtmpose-m-halpe26-256x192\850cb0006b406c7176eeda8d711cd065\primary-player.jsonl `
  --manual-events .\data\annotations\scoring-truth-pack-v1\compiled\by-video\850cb0006b406c7176eeda8d711cd065\manual-events.jsonl `
  --feasibility-registry .\metric-feasibility-pose-wave-v2.json `
  --video-id 850cb0006b406c7176eeda8d711cd065 `
  --output-dir .\reports\manual-event-features\850cb0006b406c7176eeda8d711cd065
```

该命令不调用事件候选检测器；人工文件混入其他视频、人工事件沿用 `provisional_rule_baseline` 等候选标志或 Track 不存在时会拒绝。输出保留 frames、timeline、人工事件、注册表和每个特征函数的 SHA/版本。随后才可把各视频的 `indicator-features.jsonl` 重复传给 `scripts/compile_calibration_dataset.py`。编译器只按 `video_id + manual event_id + indicator_id` 精确关联；质量门禁 hard-fail 或 `feature_status=unavailable` 的记录即使保留诊断数值也不能进入拟合。

## 6. 当前安全验证

空白真值包已经实际编译并运行：

- `reports/scoring-truth-refresh/m53-empty-registry-17-v1/scoring-truth-evaluation.json`：`ground_truth_required`，目标方向上下文真值 0/不完整；
- `reports/truth-pack-empty-calibration.json`：`coach_labels_validated_calibration_required`；
- 没有事件真值、关键点真值、教练标签、阈值或非空 grade 被生成。

下一步需要真实标注者完成表格。标注完成后先审查误差和一致性，再预注册 F2→F3 接受标准；不能根据本批结果倒推门槛。

发布前必须运行 `scripts/run_tests.ps1` 并记录当次完整通过数量。标定契约 1.2.0 已强制要求独立测试凭证及特征单位/版本绑定；完成真值和内部标定仍只能推进到 F3，只有独立测试通过、明确批准正式评分且指标注册状态已经逐级晋级到 F4 后才能进入 `scored`。

## 7. 从最新真值刷新生成标定交接（M48）

人工 CSV 更新并成功发布 M47 后，不要继续把候选事件 bundle 的 `indicator-features.jsonl` 当作标定输入。运行：

```powershell
$env:PYTHONPATH='src'
python .\scripts\build_scoring_truth_calibration_handoff.py `
  --truth-refresh-latest .\reports\scoring-truth-refresh\latest.json `
  --output-root .\reports\scoring-truth-calibration-handoff `
  --handoff-id <新的不可变交接ID>
```

命令会从 M47 冻结的人工事件重算 manual-event features，再按精确 event ID 编译 13 项 calibration dataset，最后逐项检查 prepared dataset 是否 fit-ready。它不会运行阈值或序数拟合，不会生成 A～E。输出目录已存在时必须换新 ID，成功验证后才更新 `reports/scoring-truth-calibration-handoff/latest.json`。

当前 `m48-m47-empty-v2` 的人工事件、人工特征和 sample 都是 0；13 份 prepared 文件只证明合同齐全，0/13 可拟合。下一轮真实标注后，如 accepted event 属于尚未提供 frames/timeline 的其他视频，交接会将其记为缺少 measurement source，不能借用当前视频或候选边界补齐。

## 8. 为全部真值视频建立统一测量源并编译组合（M49）

M48 是单视频历史交接。当前操作入口改为先生成覆盖 truth manifest 全集的 measurement source set，再构建统一 portfolio。每个 `--source` 使用 `VIDEO_ID|FRAMES_JSONL|POSE_SUMMARY_JSON`，必须逐视频提供且不得遗漏或重复：

```powershell
$env:PYTHONPATH='src'
python .\scripts\build_calibration_measurement_sources.py `
  --truth-manifest .\data\annotations\scoring-truth-pack-v1\manifest.json `
  --feasibility-registry .\metric-feasibility-pose-wave-v2.json `
  --source '3ae77ee3271d67de171585a5c39ddd69|.\runs\pose-ab\rtmpose-m-halpe26-256x192\3ae77ee3271d67de171585a5c39ddd69\frames.jsonl|.\runs\pose-ab\rtmpose-m-halpe26-256x192\3ae77ee3271d67de171585a5c39ddd69\summary.json' `
  --source '850cb0006b406c7176eeda8d711cd065|.\runs\pose-ab\rtmpose-m-halpe26-256x192\850cb0006b406c7176eeda8d711cd065\frames.jsonl|.\runs\pose-ab\rtmpose-m-halpe26-256x192\850cb0006b406c7176eeda8d711cd065\summary.json' `
  --source '8d7754d0de6d315674013d5b69a0b6ba|.\runs\pose-ab\rtmpose-m-halpe26-256x192\8d7754d0de6d315674013d5b69a0b6ba\frames.jsonl|.\runs\pose-ab\rtmpose-m-halpe26-256x192\8d7754d0de6d315674013d5b69a0b6ba\summary.json' `
  --output-root .\reports\scoring-truth-measurement-sources `
  --source-set-id <新的不可变来源ID>
```

该命令复用已有 Pose frames，只重建当前 registry 要求的主球员 timeline。随后运行：

```powershell
python .\scripts\build_scoring_truth_calibration_portfolio.py `
  --truth-refresh-latest .\reports\scoring-truth-refresh\latest.json `
  --measurement-sources-latest .\reports\scoring-truth-measurement-sources\latest.json `
  --output-root .\reports\scoring-truth-calibration-portfolio `
  --portfolio-id <新的不可变组合ID>
```

当前发布组合 `m53-empty-registry-17-three-video-v1` 覆盖 3/3 视频、15,868 帧；人工 event/feature/sample 为 0/0/0，13 份 prepared 文件均非 fit-ready。它不会运行 GPU、候选事件检测、拟合或 A～E 评分。人工 CSV 更新后必须先发布新的 truth refresh，再使用新 ID 构建 measurement source/portfolio；不得覆盖现有不可变目录。

当前 source/portfolio manifest 已通过强校验。浏览入口为 `reports/scoring-truth-calibration-portfolio/m53-empty-registry-17-three-video-v1/index.html`。

M53 全仓回归为 521 tests OK（15 skipped optional jsonschema）；三套真实评分 bundle 均通过跨产物强校验。人工 event/keypoint/semantic/coach label 仍为 0，任何使用当前空白真值包的流程都必须保持 `annotation_required` 或 `ground_truth_required`，不得生成 A～E、阈值或 F3/F4 晋级。
