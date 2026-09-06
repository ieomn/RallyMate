# 标定候选资产拟合

拟合层只负责把已经准备且已授权的真实教练真值拟合为非生产候选资产。它不会把候选注册为正式评分资产，不会读取独立测试标签，也不会推进 F3/F4。

M90 新增了不可序列化的同进程 truth-authorization 门禁：真实 prepared dataset 即使携带完整且自洽的
`truth_authorization` JSON，也必须同时向 Python API 传入 authorized-intake verifier 刚签发的运行时对象，并逐字段匹配完整 binding。复制 JSON 或仅复制 `binding_sha256` 会被拒绝。候选保存完整 binding，且 prepared provenance 另存 binding SHA-256。

当前仓库还没有生产 authorized-intake verifier，因此文件型 `scripts/fit_calibration_candidate.py`
无法从磁盘 JSON 恢复这项权限，真实输入会 fail closed 且不写候选。下面的命令只说明未来 verifier 接入后的文件参数形态，不表示当前可执行生产拟合。显式 synthetic scope 仍只供内存单元测试使用。

如果教练只提供组内排序而没有 A～E 绝对锚点，必须使用独立的
`docs/CALIBRATION_RANKING_ONLY.md` 路径。该路径只能拟合
`relative_order_only` 候选，强制 `grade=null`，不能被此处的 A～E 晋级器加载。

## 输入边界

拟合必须同时提供：

1. `calibration-prepared-dataset.schema.json` 对应的单指标数据集；
2. `calibration-fit-protocol.schema.json` 1.1.0 对应的预注册单指标协议。

fit protocol 必须显式声明 `indicator_id`，且必须与 prepared dataset 的
`indicator_id` 完全一致；协议不能因为主特征同名而跨指标复用。该字段也进入完整
protocol 内容 SHA-256，因此候选 lineage 会绑定“哪一个指标的哪一版协议”。

prepared 数据由全量标注数据自动生成，训练与验证记录可见；独立测试只留下样本数、分组和内容 SHA-256。独立测试的规范化载荷是按唯一 `sample_id` 排序后的完整 sample 对象数组，JSON 参数为 `sort_keys=True, separators=(',', ':'), ensure_ascii=False`，编码为 UTF-8，并禁止 NaN/Infinity。

训练 grade 不能由程序对冲突教练标签进行多数票、均值或中位数处理，只允许：

- `unanimous_multi_coach_only_v1`：至少两名教练对该样本给出完全一致的 A～E；
- `external_adjudicated_v1`：保留至少两个原始标签，并提供可追溯的人工裁决记录。

样本数、各等级覆盖数、一致性下限和优化参数没有仓库默认值。它们必须由预注册协议明确给出并随协议 SHA-256 进入候选资产。缺少协议或任何门禁不满足时，命令退出并且不创建候选文件。

`examples/calibration-fit-protocol.template.json` 只提供字段结构；所有需要研究设计决定的值都保留为不可通过 Schema 的 `REPLACE_WITH_...` 占位符，避免模板被误当成正式协议或经验阈值。

## 输出边界

拥有同进程已验证授权的真实输入只能产生 `artifact_scope=calibration_candidate`：

- 阈值规则候选保存主特征、单位、特征版本、方向和四个拟合切点；
- 序数回归候选保存特征顺序、每特征单位/版本、系数、截距和四个有序切点；
- 两类候选均保存训练/验证诊断，但 `independent_test_metrics` 固定为 `null`；
- `scoring_allowed`、`F3_promoted` 和 `F4_promoted` 均固定为 `false`。

候选来源记录按 scope 强制绑定版本身份：`prepared_dataset` 必须包含
`dataset_id/dataset_version`，`fit_protocol` 必须包含
`protocol_id/protocol_version`，两者同时保存内容哈希和原始来源哈希。Python
校验器与 JSON Schema 使用相同的条件必填规则；缺失身份字段会在独立评测前以受控校验错误拒绝，不会进入报告组装。
完整授权 binding 会继续进入 candidate、production promotion lineage、promotion report 和可信账本快照；晋级和可信账本创建均重新要求同一个实时验证对象并比较完整 binding。持久化 lineage 只用于一致性重放，不能自行提升为权限。

候选预测函数返回 `candidate_prediction_not_scored`，不能作为正式评分结果。正式资产仍需由独立测试评测、明确批准和独立 promotion 流程生成，随后还必须满足指标 F4 门禁。

```powershell
$env:PYTHONPATH='src'
python scripts/fit_calibration_candidate.py `
  --prepared-dataset <prepared/by-indicator/FS01-M02.json> `
  --fit-protocol <preregistered-fit-protocol.json> `
  --output-directory <candidate-output-directory>
```

仓库不附带任何带经验数值的生产 fit protocol，也不生成伪造的 A～E 阈值。合成数据只用于内存单元测试，文件输出 CLI 会拒绝合成输入。

## 封存独立测试与显式晋级

候选生成后，独立评测者使用另一份符合 `calibration-independent-test-protocol.schema.json` 的预注册协议运行：

```powershell
python scripts/evaluate_calibration_independent_test.py `
  --candidate <candidate.json> `
  --samples <calibration-dataset/samples.jsonl> `
  --protocol <preregistered-independent-test-protocol.json> `
  --evaluated-at <ISO-8601> `
  --output <independent-test-report.json>
```

评测器先复算 test sample IDs、泄漏分组、数量和完整对象 SHA-256 seal，再验证完整 sample 契约并读取标签。它不信任 `label_summary`：完全一致策略会从原始逐教练 grade 重新计算唯一教练、共识等级、冲突和 contributing IDs；显式裁决策略要求至少两名唯一教练，并将裁决者、决定、全部 source label IDs 和原始标签内容哈希严格绑定。任一汇总或裁决字段与原始标签不一致时整次评测拒绝。特征单位/版本与标签解析策略也必须与候选完全一致。

M92 起，真实 independent-test sample 必须为 v1.1，并带
`qualification_snapshot=calibration-feature-qualification-v1.0.0`。评测器从该 snapshot 重新验证完整 v1.6
quality gate、源 feature canonical SHA、run-bundle 来源 metadata 和目标方向解析状态，再重新推导
`feature_vector_complete` 与 readiness；修改资格布尔值不能把缺 feature、非 measured、hard fail 或未验证
来源的记录变成 eligible。旧 v1.0 sample 只兼容 synthetic/test-only。正式**报告**版本不变，仍为 v1.2：
所有 `evaluation_eligible` seal sample 必须各有一条 `prediction`；只有不具评测资格的 sample 可以进入结构化
`exclusions`，并带 `classification=not_evaluation_eligible` 和非空 `reason_codes`。coverage、分组 metrics 和
八项 acceptance checks 均从这些行与完整原协议精确复算。所有数值验收门槛均来自外部协议。即使报告通过，
`approved_for_scoring` 仍固定为 `false`。

`examples/calibration-independent-test-protocol.template.json` 只提供协议字段结构；其中所有数值和来源哈希均为故意不能通过校验的占位符，必须在查看独立测试标签或结果前完成预注册。

只有负责人完成可追溯的人工 decision，且指标已经通过独立流程逐级登记为 F4 后，才可进入 promotion。production 晋级还必须提供完整的 F0→F4 证据包；晋级器校验证据链、外部协议/人工来源、每段 canonical hash、最终 registry，以及实时 truth authorization，仅逐值复制候选参数；它不会修改注册表、切点、系数或验收门槛。当前 13 项均为 F2，没有 production maturity bundle，也没有 authorized-intake verifier，所以真实 production 晋级必然拒绝且不落文件。合成候选最多只能产生 `test_only` 资产，不能授权生产评分。

人工决定结构可从 `examples/calibration-promotion-decision.template.json` 开始填写。production decision 必须复制 CLI 将要验证的同一份 maturity-evidence binding，并明确设置 `maturity_evidence_reviewed=true`。`COPY_FROM_*`/`REPLACE_WITH_*` 均为不可签发占位符；必须复制实际候选/报告/已为 F4 的注册表 lineage，并对外部签署记录计算 SHA-256，不能手工改写候选参数。

真实 production 晋级还必须同时生成一个由运维托管的版本化可信账本；下面命令仅适用于上述真实证据已经齐全、指标已经受控晋级为 F4 的情况。任一输出路径已存在都会拒绝，不会覆盖旧资产：

```powershell
python scripts/promote_calibration_candidate.py `
  --candidate <candidate.json> `
  --independent-test-report <independent-test-report.json> `
  --independent-test-protocol <the-exact-protocol-payload-used-by-evaluation.json> `
  --independent-test-samples <the-exact-sealed-samples.jsonl> `
  --indicator-requirements <the-exact-indicator-requirements.json> `
  --decision <signed-human-promotion-decision.json> `
  --feasibility-registry <controlled-F4-registry.json> `
  --maturity-evidence <controlled-F0-to-F4-maturity-evidence.json> `
  --promoted-at <ISO-8601> `
  --asset-output <production-calibration.json> `
  --promotion-report-output <promotion-report.json> `
  --trusted-ledger-output <trusted-calibration-promotion-ledger.json> `
  --ledger-id <operator-ledger-id> `
  --ledger-version <immutable-ledger-version> `
  --ledger-authority-id <release-authority-id> `
  --ledger-registered-at <ISO-8601>
```

production promotion 不信任报告内嵌的协议摘要。它必须重新接收评测所用的完整协议对象，复算其 canonical SHA-256，并核对协议 ID、版本、来源 SHA 与登记时间；随后从报告行重新计算 coverage、metrics 和全部 acceptance checks。正式路径只接受 v1.2；v1.0 与 v1.1 都只保留给显式 synthetic/test-only 测试兼容，不能进入 production。

v1.2 coverage 固定记录 `sealed_record_count`、`evaluation_eligible_record_count`、
`evaluated_record_count`、`valid_rate`、`evaluation_eligible_rate`、
`eligible_evaluation_completion_rate`、`excluded_record_count`、`exclusion_reason_counts`。三个比率分别是
`evaluated/sealed`、`eligible/sealed`、`evaluated/eligible`（分母为零时为 `0.0`）。正式晋级要求
`evaluated + excluded = sealed`、`evaluated == eligible > 0` 且完成率为 `1.0`：不得通过 exclusion
跳过本应评测的样本。promotion 还必须读取精确的 sealed samples 和 indicator requirements，重新调用
independent evaluator；重跑报告与提交报告必须 `canonical_sha256` 相等，即规范化 JSON 语义逐字段相同，
原始键序或空白不影响。空报告、聚合数与行不一致、
未覆盖全部 seal sample IDs、协议内容漂移或八项检查不一致，都会在创建任何资产、报告或账本文件之前拒绝。

M92 将 promotion lineage/report Schema 从 v1.2 升为 v1.3，并新增 production-only
`promotion_input_snapshots`。它与 asset 的 `promotion_lineage` 和 promotion report 同值，精确绑定：

- `candidate`、`independent_test_report`、`independent_test_protocol`、`decision`、
  `maturity_evidence`；
- `registry_lifecycle_authority`。

前五项来自文件时精确保存 `source_kind=file_bytes`、`source_path`、`raw_sha256`、`canonical_sha256`；
direct API 内存对象保存 `source_kind=in_memory_canonical_json` 和 canonical SHA。registry 的正式文件来源
固定为 `registry_lifecycle_verified_file_bytes`，保存 manifest path/raw SHA、authority version、
`authority_slot=roles.runtime_feasibility`、artifact path/raw/canonical SHA 和 embedded version。
sealed samples 与 indicator requirements 已由 `independent_test_source_replay` 独立绑定，不在这里重复。

CLI 参数不变；candidate/report/protocol/decision/maturity 现在也采用同一次严格 UTF-8 bytes 解析与 raw hash，
拒绝嵌套重复键、NaN/Infinity、`1e309`/`1e9999` 一类指数溢出、非 UTF-8 bytes 和 JSON 转义产生的
孤立 Unicode surrogate；领域错误以 CLI 退出码 2 返回且不泄漏 traceback。所有实际提供文件在写出
asset/report/ledger 前再次逐字节核对，production
registry 同时重新解析 lifecycle authority。source path 只作追溯，不能从持久化 lineage 重新授权。
trusted promotion ledger Schema 同步从 v1.1 升为 v1.2，并要求每个 entry 完整内嵌 lineage v1.3。
synthetic/test-only 不仅不持久化生产快照，而且明确拒绝 samples、requirements、source replay 和
`promotion_input_snapshots`。

M92 最终验证为核心资格/晋级 79/79、关联运行时 40/40、Draft 2020-12 Schema 120/120、全仓
872/872（617.382 秒），Python compileall 通过。机器字段与 artifact hashes 位于
`reports/m92-qualification-promotion-snapshots/field-change-record.json`；这些机械验证没有生成真实
candidate、independent-test report、F3/F4、production asset、阈值或正式 A～E。

运行时不会信任 production 资产自身的 `passed` 或 `approved_for_scoring` 声明。资产 canonical SHA-256、包含 `maturity_evidence` binding 的完整 `promotion_lineage` 快照及 promotion report SHA-256 必须命中可信账本中的唯一 active entry；随后还必须把晋级时记录的 registry version、canonical SHA-256、indicator ID 与本次运行实际加载的注册表逐项精确匹配，并再次确认该指标当前为 F4。同 version 异内容、不同 version、指标缺失或非 F4 均 fail closed。服务只能通过运维环境变量 `RALLYMATE_SCORING_TRUSTED_PROMOTION_LEDGER` 选择账本；普通上传/任务请求没有该字段。账本、注册表和资产应以只读挂载提供，并且不能位于 `RALLYMATE_DATA_ROOT` 下的 `uploads`、`requests` 或 `runs`。离线批评分 `scripts/score_calibrated_indicators.py` 使用 production 资产时必须同时传入 `--trusted-promotion-ledger` 与 `--feasibility-registry`；输入 record 自报的 `feasibility_level=F4` 不是授权来源。

## 预注册、人工批准与 F4 信任边界

预注册不是“协议文件里有时间字段”。fit protocol 应在拟合、候选比较或参数选择前登记；independent-test protocol 应在独立测试标签、预测或结果被揭示前登记。登记记录应在评分工作区之外的可信系统中固定协议 ID、版本、内容 SHA-256、登记时间、负责人和审批状态。协议变更必须生成新版本，并重新运行所有受影响步骤；不得在看到验证或独立测试结果后覆盖原协议。仅比较 JSON 中的时间和 hash 可以验证链内顺序，不能证明这些时间由可信第三方见证。

同理，candidate、prepared dataset、协议、test seal、独立测试报告、人工 decision、F0→F4 maturity evidence、F4 注册表和 production 资产之间的 hash 一致，只表示所提供的一组文件能够相互引用。hash 不验证签发者身份，也不证明人工批准真实有效；同时拥有这些文件写权限的操作者可以整体替换内容和哈希。当前 CLI 验证契约、lineage 和时间顺序，但不实现签名者身份认证或可信时间戳验证。

生产晋级因此还必须满足以下外部发布控制：

1. 预注册协议和待批准证据从不可变制品库或受控只读目录读取，不从普通工作区、临时报告目录或任意用户路径读取。
2. 评分运行账号对协议、F4 注册表和 production 资产只有读取权限；发布角色使用最小 OS ACL 写入，所有写入、回滚和撤销均进入独立审计日志。
3. promotion decision 采用双人独立审批，或由已登记的可信签发者数字签名并通过独立的签发者/公钥登记验证。记录应绑定批准者、角色、时间、决定、理由以及 candidate、报告、协议、注册表和目标资产的 SHA-256。
4. F4 条目只有在受控注册表来源、匹配的独立测试证据和上述批准凭证同时可信时才有发布效力。在任意本地副本中把 `feasibility_level` 改为 `F4` 不构成授权。
5. 任一可信来源、权限、审批/签名或 lineage 校验失败时，停止晋级并隔离资产；生产评分保持 `calibration_required` 或 `unavailable`，不得以人工复制文件绕过门禁。

M90 现验证由本地操作员复核的 release record，并把它绑定到事件/阶段标注计划的原始字节及完整 M89 技术 handoff。该记录只是启动标注工作流的本地操作门禁，不是数字签名或可信时间戳，不认证随后产生的人工 JSONL/CSV，也不授权标定、晋级或生产评分。authorized-intake verifier 尚未实现前，真实编译、拟合、晋级和账本创建保持关闭；hash 只证明所读字节的一致性，不证明操作员身份、外部批准或 F4 正确性。
