# F0→F4 成熟度证据包

`src/rallymate_scoring/maturity_evidence.py` 提供独立、版本化、fail-closed 的成熟度证据验证层；机器契约为 `contracts/maturity-evidence-bundle.schema.json`。它不生成 A～E 阈值，也不解释任何数值是否足以晋级。数值判定必须来自外部预注册协议和人工评审记录。

## 四段顺序证据

一个有效 bundle 必须恰好包含以下四段，不能跳级或补写单段：

1. `F0→F1`：人工事件真值下的 Event F1、mean Segment IoU、Boundary MAE，以及外部预注册协议的通过决定。
2. `F1→F2`：逐特征 MAE、P95、Bias、有效率、分视角结果；Pose、事件边界、平滑、缺失值四类误差预算；并由外部协议逐特征判定“特征误差显著小于潜在等级差异”。代码不包含该判定的经验阈值。
3. `F2→F3`：等级可分性、多教练一致性、内部验证及人工教练标签来源。
4. `F3→F4`：独立留出测试、可评分批准和训练/测试独立性人工证明。

每段都绑定：前后 registry 版本、registry canonical SHA-256、证据 payload canonical SHA-256、前一 transition SHA-256、当前 transition SHA-256、评审人、带时区时间戳、原始来源 SHA-256 和协议 SHA-256。任一字段、层级或链路缺失即拒绝。

## Scope 与来源

- `maturity_evidence`：生产 scope。事件、关键点、教练标签和独立测试必须绑定规定的人工来源；接受协议必须为 `external_preregistered_protocol`；每段必须有人类评审记录。
- `synthetic_test_only_maturity_evidence`：只允许 `synthetic_test_fixture`，用于契约与链路测试，不能授权生产评分。

`examples/maturity-evidence-bundle.template.json` 故意设置为无效模板，避免空模板被误当证据。真实 bundle 应由证据编译器按 JSON Schema 生成，并由以下接口复核：

```python
from rallymate_scoring.maturity_evidence import (
    PRODUCTION_SCOPE,
    maturity_evidence_binding,
    validate_maturity_evidence_for_registry,
)

audit = validate_maturity_evidence_for_registry(
    bundle,
    feasibility_registry,
    expected_scope=PRODUCTION_SCOPE,
)
binding = maturity_evidence_binding(bundle)
```

`validate_maturity_evidence_for_registry` 还会计算当前 registry 的 canonical SHA-256，并要求 bundle 最后一段与该精确内容、版本、指标 ID 和 `F4` 状态一致。

## Promotion 接入

`scripts/promote_calibration_candidate.py` 的 production 路径必须传入 `--maturity-evidence`；未提供、scope 不匹配、链路不完整或最终 registry 不能精确绑定时，在创建任何输出文件前 fail closed。synthetic test-only 路径可以省略；如提供，则必须是 `synthetic_test_only_maturity_evidence`。

生产 decision、lineage 和 report 都必须保存同一份 `maturity_evidence_binding(bundle)`，字段为：`bundle_id`、`bundle_version`、`artifact_scope`、`indicator_id`、bundle `content_sha256`、`final_transition_sha256`、`final_registry` 和 `canonicalization`；decision 还必须明确声明 `maturity_evidence_reviewed=true`。可信账本 Schema 强制完整 lineage 快照包含该 binding，并对 lineage 计算 SHA-256；test-only binding 不能进入 production decision/lineage/report/ledger。

命令示例见 `examples/promote-calibration-candidate.production.ps1`。该文件只有占位路径，不是可签发资产。

当前真实 registry 中 13 个 Pose-only 指标仍为 `F2`，仓库没有生产 maturity bundle；因此本契约不会改变现有 `calibration_required` 行为，也不会修改真实 registry。
