# M90 字段变更与当前状态记录

更新时间：2026-09-02  
范围：人工事件/阶段标注放行、独立测试报告、正式评分发布前检查

## 1. 当前事实

- M89 三视频技术交接 manifest 原始 SHA-256：
  `D6EF62986DEB550F6847073ADF28EC702C35F98CD3EFDA66F573594132E345DB`。
- M90 零标签计划：
  `data/analysis-plans/scoring-truth-event-m89-operator-plan-v2.json`。
- 计划版本：`scoring-truth-event-annotation-plan-v2.0.0`。
- 计划原始 SHA-256：
  `BE1A8BDB73DB48982F3737C53EBD5C116A2B413CDA05B5EBD057163B112BDC3F`。
- 当前没有实际负责人放行记录、人工事件标签、教练等级、正式阈值或生产评分资产。
- 13 项指标仍为 F2；正式 A～E 数量仍为 0。

## 2. 标注放行字段迁移

旧路线已停止使用：

- `plan_version`：`scoring-truth-event-annotation-plan-v1.0.0`；
- binding version：`scoring-truth-event-authorization-binding-v1.0.0`；
- binding 字段：`receipt`、`trust_anchor`、`signature_verified`；
- 两份旧契约和模板：`scoring-truth-external-protocol-receipt`、
  `scoring-truth-receipt-trust-anchor`；
- 项目依赖中的 `cryptography` 与 `trust` extra。

当前路线：

- plan version：`scoring-truth-event-annotation-plan-v2.0.0`；
- local release version：`scoring-truth-operator-reviewed-local-release-v1.0.0`；
- event authorization binding version：
  `scoring-truth-event-authorization-binding-v2.0.0`；
- binding status：`operator_reviewed_local_release_verified`；
- decision：`operator_released_for_independent_event_phase_annotation`；
- reviewer role：`annotation_release_operator`。

放行记录顶层字段固定为：

```text
schema_version
release_version
artifact_scope
release_id
released_at
reviewed_by
decision
plan
technical_handoff
scope_digest_sha256
role_protocol_digest_sha256
attestations
safety
```

嵌套字段固定为：

```text
reviewed_by = {reviewer_id, role}
plan = {plan_id, plan_version, raw_sha256}
technical_handoff = {
  manifest_raw_sha256,
  bundle_version,
  bundle_id,
  content_root_sha256,
  source_projection_sha256
}
attestations = {
  exact_plan_reviewed: true,
  exact_handoff_reviewed: true,
  blind_role_protocol_reviewed: true,
  annotation_only_scope_reviewed: true
}
safety = {
  annotation_workflow_release_only: true,
  digital_signature_authority: false,
  trusted_timestamp_authority: false,
  calibration_authorized: false,
  promotion_authorized: false,
  production_scoring_authorized: false
}
```

验证后 binding 顶层字段固定为：

```text
binding_version
status
canonicalization
plan
release_record
technical_handoff
scope_digest_sha256
role_protocol_digest_sha256
operator_release_record_verified
annotation_workflow_release_only
binding_sha256
```

这份记录只允许启动 A/B/C 事件和阶段标注。它不证明负责人身份或填写时间，也不允许标定、
晋级或生产评分。模板故意不可执行，不能代替实际放行记录。

## 3. M90 历史记录：独立测试报告 v1.1 字段迁移

- 正式报告 Schema：`1.0.0` → `1.1.0`。
- 在 M90 当时，`artifact_scope=independent_test_report` 只允许
  `schema_version=1.1.0`；当前正式版本以第 5 节的 M91 v1.2 契约为准。
- 旧 `1.0.0` 仅保留给明确的 synthetic/test-only 回归。
- 新增顶层 `exclusions`。
- `coverage` 从宽松对象收紧为：
  `{eligible_record_count, evaluated_record_count, valid_rate,
  excluded_record_count, exclusion_reason_counts}`。
- `metrics` 从宽松对象收紧为：
  `{overall, by_view_group, by_player, by_session}`。
- 每个 sealed sample ID 必须恰好出现在 `predictions` 或 `exclusions`。
- 正式通过至少需要一条 sealed record、一条 evaluated record 和一条 prediction。
- `acceptance.checks` 必须恰好包含以下八项并由完整协议重新计算：

```text
minimum_record_count
minimum_leakage_group_count
minimum_valid_rate
maximum_mean_absolute_grade_error
maximum_absolute_bias_grade_steps
minimum_quadratic_weighted_kappa
required_grade_coverage
required_view_groups
```

生产 promotion API 新增必需参数 `independent_test_protocol`，CLI 新增必需选项
`--independent-test-protocol`。空报告、只有汇总没有行记录、样本遗漏、覆盖数不一致、指标不一致
或检查项不完整，都会在写出生产资产前停止。

## 4. 机器契约

- `contracts/scoring-truth-event-annotation-plan.schema.json`
- `contracts/scoring-truth-operator-release-record.schema.json`
- `contracts/calibration-independent-test-report.schema.json`
- `src/rallymate_annotation/scoring_truth_authorization.py`
- `src/rallymate_scoring/calibration_independent_test.py`
- `src/rallymate_scoring/calibration_promotion.py`

最终机器可读字段快照与验证结果保存在：
`reports/m90-scoring-release-contract/field-change-record.json`。

## 5. M91 当前记录：正式独立测试报告 v1.2

本节是 M91 的新增记录；第 3 节保留 M90 v1.1 的历史字段，不把历史字段改写为当前字段。

- 正式 `artifact_scope=independent_test_report` 现在只允许
  `schema_version=1.2.0`。
- `schema_version=1.0.0` 和 `1.1.0` 都只可作为明确的
  `synthetic_test_only_independent_test_report` 测试材料，不能进入正式或 production 路径。
- 顶层 `predictions` 只容纳 `evaluation_eligible` 的 sealed sample；每个此类 sample 必须有且仅有一条 prediction。
- 顶层 `exclusions` 只容纳非 `evaluation_eligible` 的 sealed sample，固定
  `classification=not_evaluation_eligible`，并以非空、去重的结构化 `reason_codes` 说明原因；不能用自由文本排除应评测样本。

v1.2 的 `coverage` 固定为以下八个字段：

```text
sealed_record_count
evaluation_eligible_record_count
evaluated_record_count
valid_rate
evaluation_eligible_rate
eligible_evaluation_completion_rate
excluded_record_count
exclusion_reason_counts
```

令 `sealed=sealed_record_count`、`eligible=evaluation_eligible_record_count`、
`evaluated=evaluated_record_count`。三个比率按 10 位小数重算；相应分母为 0 时均为 `0.0`：

```text
valid_rate = evaluated / sealed
evaluation_eligible_rate = eligible / sealed
eligible_evaluation_completion_rate = evaluated / eligible
```

同时必须满足 `evaluated + excluded = sealed` 与 `evaluated <= eligible <= sealed`。`reason_counts`
是所有 exclusion 的每个 `reason_code` 的计数汇总，不能由报告自行填写。正式 production promotion
还要求 sealed、eligible、evaluated 均非零，且 `evaluated == eligible`、
`eligible_evaluation_completion_rate == 1.0`；因此所有符合评测资格的样本都必须实际产生 prediction，
只有不具资格的样本可以作为结构化 exclusion 留下。

`metrics` 继续固定为 `overall`、`by_view_group`、`by_player`、`by_session` 四组；
`acceptance.checks` 仍必须是原协议重新计算的八项，而不是报告自带的阈值或结果。M91 production
晋级会再读取完整的 sealed samples 与 indicator requirements，并重新调用评测器；重跑结果与提供报告
必须满足 `canonical_sha256` 相等，即规范化 JSON 的逐字段语义相同，原始 JSON 的空白和键序不参与比较。
promotion lineage/report 记录
`independent_test_source_replay={samples, indicator_requirements}`：每项保存 `source_kind` 和
`canonical_sha256`，samples 还保存 `source_record_count` 与 `sealed_record_count`；来自文件时另保存
`source_path`、`raw_sha256`，并在写出前复核原始文件 bytes。
`source_path` 只用于追溯说明，不是权限或真实性根。正式放行只信任本次进程从冻结 bytes 解析后的
raw/canonical SHA 绑定、写出前原始 bytes 复核与 evaluator 重放；之后仅凭 lineage 中的路径不能再次授权。
任一不一致均在写出 production 资产前拒绝。M91 没有生成实际独立测试、
F3/F4 或 A～E：13 项仍为 F2，正式 A～E 仍为 0。

M91 的机器可读字段与验证快照保存在
`reports/m91-independent-test-coverage/field-change-record.json`；M90 快照保持不变。

## 6. M92 当前记录：特征资格来源与晋级输入快照

本节只追加 M92 当前合同，不改写第 1～4 节的 M90 历史，也不改写第 5 节的 M91 v1.2 覆盖记录。
M92 没有签发负责人放行、人工标签、真实独立测试、F3/F4、阈值或正式 A～E。

### 6.1 正式 sample 的资格来源

标定编译器升为 `rallymate-calibration-dataset-v1.2.0`；正式 sample 从
`schema_version=1.0.0` 升为 `1.1.0`。dataset manifest、prepared dataset、candidate、test seal 和
independent-test report 的外层 Schema 不变；report 仍为 v1.2。v1.0 sample 只保留给
synthetic/test-only，不能进入真实 independent evaluation。

v1.1 sample 新增 `qualification_snapshot`，精确字段为：

```text
snapshot_version
source_status
feature_record_present
feature_status
quality_policy_version
quality_gate
resolved_target_direction
source_feature_canonical_sha256
source_metadata
```

`snapshot_version=calibration-feature-qualification-v1.0.0`；真实来源只允许
`source_status=verified_scoring_run_bundle_source`。另外两个互斥状态为
`synthetic_test_only_source` 和 `unverified_diagnostic_source`，不能提升为真实来源。
`quality_gate` 保存 quality policy v1.6 的完整输出，不是 passed 布尔值；
`source_feature_canonical_sha256` 必须与原有 `lineage.indicator_feature_sha256` 相同。

`source_metadata` 版本为 `indicator-feature-source-metadata-v1.0.0`，精确字段为：

```text
metadata_version
canonicalization
video_id
indicator_features_raw_sha256
indicator_feature_record_count
scoring_summary_raw_sha256
run_bundle_root_sha256
run_bundle_entry_id
run_bundle_ledger_id
run_bundle_ledger_version
run_bundle_ledger_canonical_sha256
run_bundle_authority_id
```

真实编译先调用
`verify_indicator_feature_source_metadata(scoring_summary_path, indicator_features_path,
run_bundle_ledger)`，再把每个不可序列化的进程内结果按 feature 文件顺序传给
`compile_calibration_dataset(..., verified_indicator_feature_sources=[...])`。verifier 从同一 bytes 解析并
计算 summary/JSONL 摘要，核对 summary 声明的路径和 SHA，再通过 trusted run-bundle ledger 选择唯一
entry；原子提交前两份源文件都会再次逐字节复核。CLI 本轮不新增参数，当前仓库也仍没有可签发真实
truth authorization 的 intake verifier。

完整 snapshot 只持久化在 sample；manifest 还在每个
`source_files.indicator_features[i].source_metadata` 保存对应来源，并计入 source/content seed。
seal 与 report 不复制 snapshot，而是通过完整 sample canonical bytes 的 seal SHA 间接封存和重放。
编译器与独立 evaluator 都从 snapshot + feature vector 重算 feature completeness，再与阶段、语义和原始
标签共同重算 readiness。缺记录、非 `measured`、quality hard fail、measurement/scoring 被禁止、无效
必需特征、未验证来源或 qualification/lineage SHA 不一致均 fail closed。

### 6.2 Production promotion 的全部对象输入快照

M92 的版本变化为：

- promotion lineage：`1.2.0` → `1.3.0`；
- promotion report：`1.2.0` → `1.3.0`；
- trusted calibration promotion ledger：`1.1.0` → `1.2.0`；
- decision 仍为 v1.1，independent report 仍为 v1.2，candidate/protocol/maturity/calibration asset 不变。

production-only `promotion_input_snapshots` 以同值写入 asset 的 `promotion_lineage` 与 promotion report，
顶层精确六键为：

```text
candidate
independent_test_report
independent_test_protocol
decision
maturity_evidence
registry_lifecycle_authority
```

前五项来自文件时为
`{source_kind=file_bytes, source_path, raw_sha256, canonical_sha256}`；direct API 内存对象为
`{source_kind=in_memory_canonical_json, canonical_sha256}`。registry 的 production 文件来源为：

```text
source_kind=registry_lifecycle_verified_file_bytes
manifest_path
manifest_raw_sha256
authority_version
authority_slot=roles.runtime_feasibility
artifact_path
artifact_raw_sha256
artifact_canonical_sha256
embedded_version
```

direct API 的 registry 内存对象只保存
`{source_kind=in_memory_canonical_json, artifact_canonical_sha256}`。sealed samples 与 indicator
requirements 继续由 M91 `independent_test_source_replay` 独立绑定，不在这里重复。

promotion CLI 不新增参数，但所有实际提供的 candidate/report/protocol/decision/maturity/samples/
requirements 输入都使用严格 UTF-8 同字节解析和 raw SHA，拒绝嵌套重复键、NaN/Infinity、指数溢出为
非有限值的 JSON number、非 UTF-8 bytes 与孤立 Unicode surrogate；领域错误固定为 CLI exit 2 且无
traceback。写出
asset/report/ledger 前再次复核 bytes。production registry 还重新解析 lifecycle manifest 和授权 artifact。
任何路径都只用于追溯，不能从旧 lineage 重新授权。synthetic promotion 不持久化生产快照，但它读取的
实际文件同样执行提交前复核；synthetic/test-only 还明确拒绝 samples、requirements、source replay 和
`promotion_input_snapshots`，对应 report Schema 禁止后两项。ledger v1.2 的 entry 必须完整内嵌 lineage
v1.3，不能删除 replay 或 snapshots 后继续作为可信条目。

M92 的机器可读字段与验证快照保存在
`reports/m92-qualification-promotion-snapshots/field-change-record.json`；M89/M90/M91 记录保持不变。

M92 最终验证为核心资格/晋级 79/79、关联运行时 40/40、Draft 2020-12 Schema 120/120、全仓
872/872（617.382 秒），Python compileall 通过。通过只代表上述字段和 fail-closed 行为按合同运行，
不代表已经产生真实标签、独立测试准确率、F3/F4、阈值、正式 A～E 或生产放行。

## 7. M93 当前记录：放行后的角色执行、裁决与私有 intake

本节只追加 M93 的执行合同，不改写 M89 技术交接、M90 放行、M91 独立测试或 M92 晋级输入记录。
M93 没有创建实际负责人放行记录；所有正向构建只在临时合成测试中使用测试 release。当前仓库没有
实际 A/B/C bundle、提交、intake 或人工标签。

M93 新增五个版本对象：

```text
A/B execution bundle = scoring-truth-event-execution-bundle-v1.0.0
A/B annotation submission = scoring-truth-event-annotation-submission-v1.0.0
C adjudication bundle = scoring-truth-event-adjudication-bundle-v1.0.0
C adjudication submission = scoring-truth-event-adjudication-submission-v1.0.0
PRIVATE intake = scoring-truth-event-phase-intake-v1.0.0
```

A/B execution manifest 顶层精确字段为：

```text
schema_version
bundle_version
bundle_id
execution_id
generated_at
status
role
event_authorization
source_files
scope
revision_contract
entrypoint
server_launcher
artifacts
content_root_sha256
manifest_binding_sha256
safety
```

C manifest 使用同一公共字段，但以 `execution_sources` 和 `annotation_submissions` 代替
`source_files`。每份 bundle ref 精确为 `{bundle_id,manifest_binding_sha256}`；binding 是 manifest
去掉 `artifacts`、`content_root_sha256` 和自身后的规范 JSON 投影摘要。A/B 包精确声明 16 个 artifact，
C 包精确声明 20 个 artifact；包外 validator snapshot 另保存 manifest raw SHA 与 content root，避免
在 `review.html` 中制造自引用。

A/B submission 顶层精确字段为：

```text
schema_version
submission_version
status
artifact_scope
execution_id
execution_bundle
authorization_binding_sha256
submission_id
role_slot
annotator_id
videos
submitted_at
submission_revision_sha256
exported_at
```

每个 video 固定 `{task_id,video_id,full_video_review,events,video_revision_sha256}`，每个 event 固定
`{annotation_id,event_id,event_code,start_ms,end_ms,phase_observations,confidence_milli,
boundary_uncertainty_ms,notes,annotated_at,annotation_revision_sha256}`。三段视频无论是否发现事件都必须
完成 full-video review；A/B 不能导入同伴提交。

C submission 顶层精确字段为：

```text
schema_version
adjudication_version
status
artifact_scope
execution_id
adjudication_bundle
authorization_binding_sha256
reviewer_slot
reviewer_id
source_submissions
video_adjudications
decisions
adjudicated_at
adjudication_submission_revision_sha256
exported_at
```

`video_adjudications` 必须按三任务顺序恰好包含三项，每项精确
`{task_id,video_id,completed,notes,adjudicated_at,review_revision_sha256}`。每个 decision 精确保存
adjudication ID、video ID、状态、A/B 视频 revisions、来源 annotation revisions、最终 event 或 null、
原因、时间和 revision。来源 event 必须全覆盖；merge/split/reject 与 C-added event 都以结构化字段表达。
任一来源视频 revision 改变都会清除对应视频确认并令相关 decision 过期。

私有 intake manifest 顶层精确字段为：

```text
schema_version
intake_version
intake_id
generated_at
status
classification
event_authorization
source_bundles
revision_lineage
raw_submissions
compilation
directories
artifacts
content_root_sha256
safety
```

其中 `source_bundles={execution:[A,B],adjudication:C}`；`revision_lineage` 保存三名参与者、A/B export
roots、C adjudication root、角色互异和最终 revision；`raw_submissions` 按 A/B/C 保存原始字节摘要、
participant、revision 与 export 时间。intake 精确保留 plan/release/M89、三份 bundle manifest、三份原始
提交和四份 compiled 输出，并在重放时重新编译核对。参与者 ID 经 trim + NFKC + casefold 后必须
三者互异；事件和裁决并列项采用不依赖 locale 的 Unicode codepoint 顺序；时间只按同一链内先后
核对，不构成身份或受信时间证明。

M93 字段快照保存于 `reports/m93-event-phase-execution/field-change-record.json`。execution/UI/intake
定向验证为 37/37（13.242 秒）；联合 M90 authorization 与 M89 handoff 为 63/63（21.427 秒，0 跳过）；
M93 新增 5 份 Schema，全仓 Draft 2020-12 Schema 为 125/125。M93 的所有
safety 字段继续固定 annotation-only，禁止 calibration、promotion、production、grade、threshold 和
maturity promotion；最终冻结字节的全仓回归为 909/909（779.863 秒，0 失败/错误）。当前 13 项仍为
F2，正式 A～E 仍为 0。
