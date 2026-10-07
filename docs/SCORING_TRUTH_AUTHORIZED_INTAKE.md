# 人工真值受控 intake 验证器

2026-10-07 的实现补齐了 Python API 的内容重放和接受门禁；没有创建实际接受账本、负责人放行、教练标签、F3/F4 或生产评分资产。当前真实材料仍不满足调用条件。软件测试中的标签、接受记录和未来时间全部是临时合成 fixture，不能进入真值。

`verify_scoring_truth_calibration_intake` 位于 `src/rallymate_scoring/scoring_truth_authorized_intake.py`。它返回同进程、不可序列化的 `VerifiedScoringTruthCalibrationAuthorization`，供现有真实数据编译、拟合和独立晋级流程使用。该对象只确认一批人工输入被受控接受；它不代表测量准确率、研究样本量充足、教练意见一致、已通过独立测试或允许生产评分。

## 必须提供的独立材料

1. 完整 M93 event/phase intake。验证器重新执行 M90 放行、A/B 独立提交、C 裁决、完整视频确认、当前 revision、所有来源字节和编译投影的校验。
2. 五 CSV 私有 truth intake。它必须保留原始 source pack 和五份原始导出位置；验证器调用既有 `validate_scoring_truth_intake`，从保留原始 CSV 重编译并精确对照语义、教练标签及 validation report。仅提供随填的 compiled JSONL 或 report 不可接受。
3. 运维托管、双人接受的独立 ledger，遵守 `contracts/scoring-truth-calibration-acceptance-ledger.schema.json`。其中必须绑定完整 calibration authorization、当前 M93 manifest 原始 SHA、最终 revision、两份 intake，以及两份不同的外部人工审批记录 SHA。
4. 由可信部署配置提供 ledger 路径和原始字节 SHA-256 pin。二者不能来自上传请求、待验证 intake、自报 JSON 或同目录配置；不能为了“通过”而在调用处对刚收到的 ledger 自动算 pin。ledger 不能放在两个 intake 或保留 source pack 内。

M93 的 `compiled/event-annotations.csv` 和 `compiled/full-video-review-completion.csv` 是到五 CSV intake 的明确桥接：两份 CSV 必须逐字节相等。事件边界不能重新手填、按时间近邻关联，或由模型候选替换。truth manifest 的全部视频 ID/SHA 必须与 M93 计划相同。原始私有模板依然不能分发给标注者；中央保存/编译 CSV 不等于允许教练在候选信息可见的模板中标注。

语义和教练输入仍需要独立采集、人工裁定及来源审阅。每条 accepted semantic 必须有与标注者不同的 reviewer；每个已有标签的 video/event/indicator/label_type 必须保留至少两名不同教练的原始标签，不合并成平均或多数票。身份比较执行 trim、NFKC 和 casefold。缺少语义、教练标签或人工事件时验证器拒绝；不可观察语义可保留明确 null/reason，不补模型值。少量数据通过来源验收也不代表完整数据集就绪，后续覆盖、特征资格、分组隔离及标定协议门禁仍独立执行。

## 外部接受的边界

ledger 条目必须是唯一 active、`scope=calibration_input_only`，且两个不同的接受者分别担任 `truth_data_reviewer` 和 `calibration_release_operator`。他们不得同时是该批事件、语义或教练标签的采集/裁决人员；审批时间必须不早于两份 intake 的完成时间、不晚于 ledger 登记时间。两人须在外部系统审阅独立采集、原始来源、语义裁决、原始教练标签和投影默认值。`promotion_authorized`、`production_scoring_authorized` 固定为 false。

这些记录及时间是可核对的声明，不是签发者身份认证、数字签名或可信时间戳。可信根来自独立受控的账本及其 pin：部署账号只读、发布账号受限写入，双人审批原件、pin 更新、撤销及回滚必须在工作区外留存审计。仅把任意 JSON 放到另一个文件夹不建立信任；本 API 不能检查组织的审批真实性或 OS ACL 是否按要求部署。代码不提供签发账本/审批的命令，也不从待验证包中恢复权限。

两个原 intake 的 annotation-only/diagnostic 标记不会被改写。接受账本是新的外部材料；它只授权这些精确字节作为标定输入，不能把旧来源整体变成生产资产。M93 的 `person_track_id=1`、`source-view-unclassified` 仍是投影默认值，不是观察真值；后续身份、视角和特征来源必须在独立 metadata/feature verifier 中处理。旧注册表及旧报告来源哈希保持原值，不能重算旧来源来消除版本冲突。

## 受控集成 API

```python
from rallymate_scoring.scoring_truth_authorized_intake import (
    verify_scoring_truth_calibration_intake,
)

# trusted_config 由运维独立提供，不从提交材料或上传请求解析。
authorization = verify_scoring_truth_calibration_intake(
    event_phase_intake_dir=m93_intake,
    truth_intake_dir=private_truth_intake,
    trusted_acceptance_ledger_path=trusted_config.acceptance_ledger_path,
    trusted_acceptance_ledger_sha256=trusted_config.acceptance_ledger_pin,
    authorization_id=accepted_authorization_id,
)

# compile_calibration_dataset(...,
#   truth_intake_manifest_path=private_truth_intake / "intake-manifest.json",
#   truth_manifest_path=private_truth_intake / "manifest.json",
#   truth_validation_report_path=private_truth_intake / "compiled/validation-report.json",
#   manual_events_path=private_truth_intake / "compiled/manual-events.jsonl",
#   manual_semantics_path=private_truth_intake / "compiled/manual-semantics.jsonl",
#   coach_labels_path=private_truth_intake / "compiled/coach-labels.jsonl",
#   verified_truth_authorization=authorization,
#   verified_indicator_feature_sources=independently_verified_feature_sources,
# )
```

该 API 不运行模型、不写任何输入、不生成 candidate，也不启动服务。每次下游 `require_verified_scoring_truth_calibration_authorization` 都重新验证 ledger 和完整来源，包括保留的原始 CSV/source pack、两份目录的精确拓扑及编译重放；数据集编译在原子提交前也再校验。ledger 撤销或替换、原始文件改动、旧 revision、源缺失均使已有运行对象失效。完整重放含媒体校验，因此应在离线受控流程中使用，不放入普通上传/报告请求。

持久化的 `.binding` 只用于 lineage，不可恢复运行权限。现有文件型 compile/fit/promotion CLI 仍不接受用户自报 ledger/pin，也不签发这个对象；真实受控调用使用上述 Python API。候选拟合、独立测试、F0→F4、另行人工晋级决定和可信 promotion ledger 的条件未放松。

## 验证

`tests/test_scoring_truth_authorized_intake.py` 使用临时合成材料，经真实 M93 JavaScript 导出、Python A/B/C intake 和 CSV 编译重放，检查对象签发、原始和编译后字节篡改、错误 pin/作用域、旧 revision、撤销、重复 JSON/非有限数、身份归一化、自审、空标签及封闭 Schema。它不使用真实教练标签，也不证明动作识别或评分准确。

```powershell
$env:PYTHONPATH = 'src;tests'
& runtime/rtmpose/.venv/Scripts/python.exe -m unittest tests.test_scoring_truth_authorized_intake -v
```
