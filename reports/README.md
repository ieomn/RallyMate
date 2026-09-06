# 生成报告与精选证据

`reports/` 用于保存本地分析、验证和审计流程产生的输出。除本文件及下列精选证据外，目录内容默认不纳入 Git。

## 版本控制边界

以下内容保留在本地，不进入远程仓库：

- 原始视频、裁剪视频、标注视频和图像帧；
- 逐帧姿态、关键点、特征及中间事件候选数据；
- 完整运行目录、临时诊断结果和重复实验输出；
- 依赖本地模型权重、私有标注或受限测试数据生成的大体积文件。

这些产物通常可以由源码、配置和本地输入重新生成。将其排除可以控制仓库体积，并避免传播未经授权的视频、人体动作数据或人工标注。模型权重、运行环境和私有输入同样不在 Git 中提供。

## 精选证据

仓库仅保留能够说明接口合同、字段变更、实验边界和复核结果的小型证据：

- 当前用户端 Demo v1.2：[摘要](user-demo-v1.2/summary.md)、[接口合同](user-demo-v1.2/demo-contract.json)、[字段变更记录](user-demo-v1.2/field-change-record.json)；
- 用户端 Demo v1.1 历史兼容：[摘要](m96-user-demo-v1.1/summary.md)、[接口合同](m96-user-demo-v1.1/demo-contract.json)、[字段变更记录](m96-user-demo-v1.1/field-change-record.json)；
- M96 Halpe26 人工评测 pilot：[审计摘要](m96-pose-pilot/summary.md)、[pilot 合同](m96-pose-pilot/pilot-contract.json)、[字段变更记录](m96-pose-pilot/field-change-record.json)；
- M96 RTMPose M/L/X 同帧诊断：[摘要](m96-rtmpose-same-frame-diagnostic/summary.md)、[字段变更记录](m96-rtmpose-same-frame-diagnostic/field-change-record.json)、[诊断报告](m96-rtmpose-same-frame-diagnostic/diagnostic-report.json)；
- M97 RTMPose-X operational 测量恢复：[摘要](measurement-recovery-m97/summary/summary.md)、[结构化报告](measurement-recovery-m97/summary/report.json)、[字段变更记录](measurement-recovery-m97/field-change-record.json)。

精选证据只支持文件中明确陈述的结论。诊断输出、模型置信度或开发集覆盖率不能替代人工真值准确率、独立测试结论或正式模型晋级授权。

## 复现入口

请先按照项目根目录 [README](../README.md) 配置 Python 环境、模型权重和本地数据。常用入口如下：

```powershell
# 启动 API 与 RTMPose Worker
powershell -ExecutionPolicy Bypass -File scripts/run_api.ps1
powershell -ExecutionPolicy Bypass -File scripts/run_rtmpose_worker.ps1

# 校验 M96 人工评测 pilot
python scripts/build_m96_pose_pilot_audit.py --validate-existing

# 查看 M96 同帧诊断和字段记录生成器的参数
python scripts/run_m96_rtmpose_same_frame_diagnostic.py --help
python scripts/build_m96_same_frame_field_record.py --help

# 查看 M97 operational 扩展、摘要和字段记录生成器的参数
python scripts/run_m97_x_pose_operational_extension.py --help
python scripts/build_m97_x_pose_operational_summary.py --help
python scripts/build_m97_x_operational_field_record.py --help
```

各脚本的前置输入、固定参数、不可覆盖约束和验证口径以对应摘要、合同及脚本帮助信息为准。运行前应确认所用视频、人工标注和模型资产具有相应使用权限。
