# M96 Halpe26 人工评测 pilot 审计摘要

- 审计时间：`2026-09-04T16:21:28Z`
- 状态：空白 pilot 已验证，仍需真人 A/B 标注；不得声称真人标注完成。
- pilot manifest 原始字节 SHA-256：`5995F83285607E64EBA0CC9BBDA137CE46A891938198B9FB8CE270A83DDCAE4C`
- task contract SHA-256：`1248575A51D469539D2F5CAFD5A6D204BD5FC493C04886D4CDB7FD4432342175`
- 范围：3 个开发视频，每个 8 帧，共 24 帧；每帧 Halpe26 全部 26 点，共 624 个关节点任务。
- 当前人工行数：`0`
- 当前准确率：`null`

## 角色与治理边界

A 与 B 使用不同角色包独立完成，各自不能看到另一方结果或模型值。只有两份完整、绑定正确且角色 ID 不复用的 A/B 原始 CSV 通过原子 intake 后，才能生成 C 入口。C 只看到需要裁决的 A/B 人工分歧和必要视频帧，不看到模型值、密封 holdout 或仓库其他文件。

治理模板只保存已知 video ID 与 `REPLACE_WITH_*` 占位；真实 consent、subject、session、split、usage scope 和 retention policy 均未推断、未填写。

## 安全结论

- `training_allowed=false`
- `dataset_export_allowed=false`
- `promotion_allowed=false`
- `accuracy_claim_generated=false`
- 本审计没有生成标签、准确率、等级、阈值或晋级结论。

## 完整性

- `pilot-contract.json` 原始字节 SHA-256：`B8472CEF9F622FBF21A4ABEFB3D6F36A872BA5237D5503027A836CBD8C5B682F`
- `field-change-record.json` 原始字节 SHA-256：`877851936E18B03A8088AD0E4DDE9BA6B5C0D11B516FAF0893D586EBDB6DD465`
- 已保存源码、脚本、UI、测试和空白 pilot 关键文件的 bytes + SHA-256：`23` 个文件。

构建脚本拒绝覆盖既有目录：

```powershell
python scripts/build_m96_pose_pilot_audit.py
python scripts/build_m96_pose_pilot_audit.py --validate-existing
```
