# RallyMate 真实标定数据集编译器

## 目标与安全边界

该编译器把下列人工真值和模型测量结果按 `video_id + manual event_id + indicator_id` 精确连接：

- 已验收的人工事件边界与关键阶段；
- 已验收的人工语义真值；
- 按同一人工事件边界重新计算的版本化指标特征；
- 经 scoring summary 和可信 run-bundle ledger 绑定的特征来源及质量资格快照；
- 多名教练的原始 A～E 或排序标签；
- 人工维护的 player/session/view 分组信息；
- 预注册的 train/validation/independent_test 分组分配。

候选事件的时间重叠、最近邻和顺序不会用于 join。这样会导致当前空白真值包正确输出 `annotation_required`，而不会把模型候选边界静默提升为真值。

编译器不生成阈值、不训练模型、不应用样本数或一致性经验门槛，也不提升 F3/F4。所有数值接受标准必须来自后续独立、版本化、预注册的协议。

从 M90 起，“文件能通过真值包编译”与“文件被授权用于标定”是两个独立条件。默认输入（包括状态为
`private_candidate_containing_intake_not_operator_authorized` 的 M89 私有 intake）只能生成
`artifact_scope=unverified_truth_diagnostic_input`、`source.kind=unverified_private_truth`；它可用于诊断覆盖率，但拟合器必定拒绝。显式 `--synthetic-test-only` 只生成
`synthetic_test_only_calibration_input`，同样没有生产权限。

真正的 `calibration_input` 首先必须在同一进程中持有不可 JSON 序列化的已验证真值对象。该对象把
M90 本地负责人放行记录、intake ID/version/content root、最终 A/B/C 修订链，以及 intake
manifest、truth manifest、validation report、manual events、manual semantics 和 coach labels 六类
文件的精确原始字节 SHA-256 绑在一起。仅复制持久化的 `truth_authorization` 字典或
`binding_sha256` 不构成权限。

M92 进一步要求每个 indicator-features JSONL 都有一个独立、不可序列化的
`VerifiedIndicatorFeatureSourceMetadata`。它把该 JSONL 与同次 scoring summary 的原始 bytes、summary
声明的路径/SHA，以及 trusted scoring run-bundle ledger 中的唯一 active entry 绑定；仅复制 sample 内的
`source_metadata` 或 `qualification_snapshot` 不能建立真实来源权限。真实编译同时需要真值授权对象和
与 feature 文件一一对应的 `verified_indicator_feature_sources`，两者互不替代。

M90 放行记录只代表“可以开始事件/阶段标注”：它不是身份认证、时间证明、人工标签认证，
也不等于标定、晋级或生产允许。当前仓库还没有可签发真实 `calibration_input` 的 intake verifier，
所以真实编译路径有意保持关闭；这保证 M89 技术交接和任何私有 intake 都不会被误当成标定数据。

## 数据流

1. 在 `scoring-truth-pack-v1` 完成人工事件、语义和教练标注，并运行真值包编译。
2. 对每个视频使用人工事件边界重新计算特征：

   ```powershell
   $env:PYTHONPATH='src'
   python scripts/build_manual_event_features.py `
     --frames runs/pose-ab/<pose-profile>/<video-id>/frames.jsonl `
     --primary-timeline reports/pose-scoring-ab/<pose-profile>/<video-id>/primary-player.jsonl `
     --manual-events data/annotations/scoring-truth-pack-v1/compiled/by-video/<video-id>/manual-events.jsonl `
     --feasibility-registry metric-feasibility-pose-wave-v2.json `
     --video-id <video-id> `
     --output-dir reports/manual-event-features/<pose-profile>/<video-id>
   ```

   此命令不调用事件候选检测器。它直接把人工 `event_id/start_ms/end_ms/key_phases_ms/person_track_id` 传给特征库，并输出 `events.jsonl`、`features.jsonl`、`indicator-features.jsonl`、`scores.jsonl` 和 `summary.json`。未标定时仍为 `calibration_required` 或 `unavailable`。

3. 从模板填写稳定身份和预注册拆分：

   - `examples/calibration-group-metadata.template.json`
   - `examples/calibration-split-policy.template.json`

   同一 player 或 session 的视频会合并为一个 `leakage_component_id`。如果身份未知，编译器保守地把所有未知身份视频绑定为同一组件；不会猜测它们属于不同球员。

4. 编译不可变数据集。每个视频的人工边界特征文件各传一次 `--indicator-features`：

   ```powershell
   python scripts/compile_calibration_dataset.py `
     --feasibility-registry metric-feasibility-pose-wave-v2.json `
     --indicator-features reports/manual-event-features/<profile>/<video-a>/indicator-features.jsonl `
     --indicator-features reports/manual-event-features/<profile>/<video-b>/indicator-features.jsonl `
     --indicator-features reports/manual-event-features/<profile>/<video-c>/indicator-features.jsonl `
     --manual-events data/annotations/scoring-truth-pack-v1/compiled/manual-events.jsonl `
     --manual-semantics data/annotations/scoring-truth-pack-v1/compiled/manual-semantics.jsonl `
     --coach-labels data/annotations/scoring-truth-pack-v1/compiled/coach-labels.jsonl `
     --truth-intake-manifest <private-intake>/intake-manifest.json `
     --truth-manifest data/annotations/scoring-truth-pack-v1/manifest.json `
     --truth-validation-report data/annotations/scoring-truth-pack-v1/compiled/validation-report.json `
     --group-metadata data/annotations/calibration-group-metadata.json `
     --split-policy data/annotations/calibration-split-policy.json `
     --output-dir reports/calibration-datasets/<immutable-dataset-version>
   ```

输出目录已存在时命令会拒绝覆盖。所有文件先写入同卷临时目录，全部成功后才原子改名，失败不会留下半成品数据集。
所有授权绑定输入均只读取一次；严格 JSON/JSONL 解析和 SHA-256 来自同一字节快照，并在原子改名前再次逐字节比较源文件，避免 parse/hash 重开和提交前替换竞态。

当前 CLI 仍不能自行签发真实 truth authorization 或 feature-source verifier。受控集成代码须先调用
`verify_indicator_feature_source_metadata(scoring_summary_path=..., indicator_features_path=...,
run_bundle_ledger=...)`，再把返回对象按 `indicator_feature_paths` 的顺序传给
`compile_calibration_dataset(..., verified_indicator_feature_sources=[...])`。verifier 拒绝 symlink、重复 JSON
键、NaN/Infinity、summary 与 JSONL 路径/SHA 不一致、video ID 漂移或 ledger entry 不匹配；提交前还会
再次核对 summary 与 JSONL 原始 bytes。

## 输出契约

### 全量 `samples.jsonl`

每条记录包含：

- `sample_id/dataset_version/video_id/event_id/event_code/indicator_id/person_track_id`；
- 严格按 registry `required_features` 排序的 `feature_vector`；
- `feature_vector_complete`、特征版本、单位、置信度、有效性和证据帧；
- `qualification_snapshot`：特征记录是否存在、`feature_status`、完整 v1.6 quality gate、目标方向是否已解析、
  源 feature canonical SHA，以及 summary/JSONL/run-bundle 来源 metadata；
- 人工事件边界、阶段、标注者和复核者；
- 必需语义、缺失语义和不可观察语义；
- 原始逐教练 grade/ranking 标签；
- `label_summary`；
- player/session/view/leakage component；
- split 和 readiness reason codes；
- 各输入记录的 SHA-256 与 exact-join key。

`label_summary.resolved_grade` 仅在至少两名独立教练对同一事件指标给出完全一致 A～E 时存在。冲突、单教练和缺失标签均保持 null；不做多数票、中位数或平均。完全一致只是保守的标签解析结果，不证明其真值正确，独立测试优先使用未来显式 adjudication。

### `split-manifest.json`

所有指标共用一次全局分组拆分，包含 train/validation/independent_test 的 sample IDs、leakage components、指标/等级/view/player/session 分布，以及冲突和未分配审计。任何组件不得跨 split。

### `prepared/by-indicator/<indicator-id>.json`

这是拟合器唯一可读的输入。它只包含 train 与 validation 的完整记录；independent test 仅包含 seal：

- `sample_ids`；
- `groups`；
- `record_count`；
- `content_sha256`；
- `labels_withheld=true`。

Seal 对该指标完整 independent-test samples 按 `sample_id` 排序后计算。规范为 UTF-8 JSON、`ensure_ascii=false`、`sort_keys=true`、紧凑分隔符、禁止 NaN/Infinity。拟合器不能读取独立测试值；独立 evaluator 从全量 `samples.jsonl` 读取并先复算 seal。

每个 prepared 文件还保存完整 `truth_authorization` lineage。只有 status 为
`verified_authorized_intake_for_calibration` 且调用方同时重放同一个进程内的已验证对象时，真实 scope
才可进入拟合。历史无 binding 的 prepared 文件仍可由只读审计器解析，但一律按未验证诊断材料处理，不能拟合。

版本策略：编译器版本为 `rallymate-calibration-dataset-v1.2.0`，正式 sample Schema 从 M92 起为
`1.1.0`，资格快照为 `calibration-feature-qualification-v1.0.0`，source metadata 为
`indicator-feature-source-metadata-v1.0.0`。授权 binding 使用独立且封闭的
`scoring-truth-calibration-authorization-binding-v1.0.0`，其上游事件/阶段放行 binding 为
`scoring-truth-event-authorization-binding-v2.0.0`。prepared/candidate 外层暂保留 1.0.0
字段版本以兼容既有 test-only 机械测试；生产 validator 不给无 binding 的旧 real-scope 文件任何拟合权限。未来若修改 binding 字段或授权语义，必须提升 binding 版本并同步提升外层 prepared/candidate 版本，不能静默接受未知字段。

`qualification_snapshot` 精确包含 `snapshot_version`、`source_status`、`feature_record_present`、
`feature_status`、`quality_policy_version`、`quality_gate`、`resolved_target_direction`、
`source_feature_canonical_sha256`、`source_metadata`。真实来源固定为
`source_status=verified_scoring_run_bundle_source`；diagnostic 与 synthetic/test-only 使用不同 status，且
run-bundle 字段为 null，不会被提升为真实权限。

`source_metadata` 精确包含 `metadata_version`、`canonicalization`、`video_id`、
`indicator_features_raw_sha256`、`indicator_feature_record_count`、`scoring_summary_raw_sha256`、
`run_bundle_root_sha256`、`run_bundle_entry_id`、`run_bundle_ledger_id`、`run_bundle_ledger_version`、
`run_bundle_ledger_canonical_sha256`、`run_bundle_authority_id`。dataset manifest 的 indicator-features source
项同时绑定文件 SHA 和这份 metadata，因此 dataset ID/source SHA 也随任一来源变化而变化。

### `readiness-report.json`

每项指标只报告可复算事实：样本数、有效向量数、语义覆盖、grade/rank 标签、多教练重叠、冲突、一致性以及 split/view/player/session/grade 分布。报告不内置 25/10、每等级最小样本数、QWK 0.6 等经验门槛。

## 机器契约

- `contracts/calibration-dataset.schema.json`
- `contracts/calibration-dataset-sample.schema.json`
- `contracts/calibration-dataset-readiness.schema.json`
- `contracts/calibration-split-manifest.schema.json`
- `contracts/calibration-group-metadata.schema.json`
- `contracts/calibration-split-policy.schema.json`
- `contracts/calibration-prepared-dataset.schema.json`
- `contracts/scoring-truth-calibration-authorization-binding.schema.json`

## 当前真实状态

`reports/calibration-dataset-pose-wave-2026-08-21.14-primary-v0.3-event-v0.4.1-quality-v1.6-loop-v0.4.1-empty` 是历史 `.14` 不可变审计快照（dataset ID `rallymate-calibration-21535d0a03c52856`），不得作为当前输入。当前 M42 空包为 `reports/calibration-dataset-pose-wave-2026-08-22.15-primary-v0.3-event-v0.4.1-quality-v1.6-loop-v0.6.0-empty`（dataset ID `rallymate-calibration-f0179d4e3dc40cb6`），绑定 registry `.15` 与源 `indicator-features.jsonl` SHA-256 `5801ECB4D04A9C9760AEDA758889B29DF3C614CCBB3EB627725B8D5C224DC7BD`。它包含 FS02-M02 的完整 5 项评分向量合同，但由于人工事件/语义/教练真值仍为空，没有任何候选记录被吸收为 calibration sample：

- status：`annotation_required`；
- accepted manual events：0；
- semantic records：0；
- coach labels：0；
- calibration samples：0；
- prepared indicator files：13；
- generated thresholds：false；
- F3/F4 promotion：false。

这不是失败回退，而是当前人工真值状态的准确表达。M89 三视频技术交接仍没有人工事件、
关键点、语义或教练等级标签；因此当前 13 项继续停留 F2，正式 A～E 数量为 0。

## 生产晋级前的独立测试记录

真实 production promotion 只接受 `schema_version=1.2.0` 的
`artifact_scope=independent_test_report`，且必须同时提供评测时使用的**完整原协议对象**，不能只给
报告内的协议摘要。验证会重新计算并逐项核对：

- 真实 independent-test sample 必须为 `schema_version=1.1.0`，携带
  `source_status=verified_scoring_run_bundle_source` 的 qualification snapshot；v1.0 sample 只兼容
  synthetic/test-only；
- sample 的 `feature_vector_complete` 与 readiness 资格必须从 snapshot、feature vector、阶段、语义和原始
  标签重新推导，不能只相信编译时布尔值；

- 报告的 `protocol_id`、`protocol_version`、`content_sha256`、`source_sha256`、`registered_at`；
- 独立测试 seal 的全部 sample IDs 是否按资格状态恰好分入：所有 `evaluation_eligible` 样本必须进入
  `predictions`，只有非资格样本可进入带 `reason_codes` 的结构化 `exclusions`；
- `coverage.sealed_record_count`、`evaluation_eligible_record_count`、`evaluated_record_count`、
  `valid_rate`、`evaluation_eligible_rate`、`eligible_evaluation_completion_rate`、
  `excluded_record_count` 和排除原因计数；
- overall、view、player、session 四组 `metrics`；
- 来自协议的八项 `acceptance.checks`：最少记录数、最少泄漏组数、最小有效率、最大 MAE、最大绝对 bias、最小 quadratic weighted kappa、必需等级覆盖和必需视角组。

生产晋级要求 `sealed/evaluation_eligible/evaluated` 都非零、`evaluated == evaluation_eligible`、
`eligible_evaluation_completion_rate == 1.0`，以及至少一条实际 prediction。三个比率为
`evaluated/sealed`、`eligible/sealed`、`evaluated/eligible`（零分母均为 `0.0`）。promotion 还会从
精确 sealed samples 和 indicator requirements 重跑 evaluator；重跑报告与提交报告必须
`canonical_sha256` 相等，即规范化 JSON 语义逐字段相同，原始键序或空白不影响。
空报告、只填汇总数、协议内容不一致、行与汇总对不上或八项检查不完整，都会在写入任何生产资产前拒绝。
`schema_version=1.0.0` 与 `1.1.0` 都只保留给明确的 synthetic/test-only 测试，不能用于 formal 或 production。

M92 的 qualification/source 与 promotion snapshot 联合核心回归为 79/79，关联运行时为 40/40；
120/120 份 Draft 2020-12 Schema 和全仓 872/872（617.382 秒）通过，Python compileall 通过。字段、
来源绑定和最终 artifact hashes 记录于
`reports/m92-qualification-promotion-snapshots/field-change-record.json`。这不改变当前 0 人工标签、
13 项 F2、0 正式 A～E 的真实状态。
