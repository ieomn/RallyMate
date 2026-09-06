# 正式评分运行时模型与机位绑定

这层门禁解决一个与“模型能输出关键点”不同的问题：即使某项指标已经有教练标定并通过独立测试，标定结论也只适用于被验证过的姿态模型、拓扑、事件/主球员/质量版本和机位条件。换模型、换权重、换关键点拓扑或换机位后，不得继续套用旧 A～E 资产。

## 三个独立信任输入

生产评分必须同时具备：

1. `trusted-calibration-promotion-ledger.json`：证明标定资产经过成熟度证据、独立测试和人工晋级；
2. `trusted-runtime-profile-bindings.json`：由运维侧登记单个标定资产允许使用的精确运行配置；
3. 每个视频的 `runtime-view-evidence.json`：人工确认该视频满足注册表全部固定机位约束，并绑定视频 SHA-256。

普通上传、任务请求或运行目录不能选择或改写前两项。Service 还会拒绝把受信文件放在 `uploads/`、`requests/` 或 `runs/` 下。JSON 中的哈希只能证明链内一致性，不能认证签发人；真实部署仍需只读挂载、OS ACL 和独立审批。

## 精确绑定字段

运行时 Profile 逐项核对：

- Pose backend、runtime、profile、模型文件 SHA-256；
- 原生关键点拓扑、点数和关键点 Schema 版本；
- 指标定义、事件契约、事件检测器、阶段契约、主球员算法、质量策略和该指标特征契约；
- view profile、view group、预注册审核协议与注册表要求的全部 `view_constraints`；
- 当前输入视频的稳定 `video_id` 和内容 SHA-256。

任一字段缺失或不一致时，入口在 GPU 推理前拒绝，或评分层返回 `calibration_required` 且 `grade=null`。只通过 promotion ledger 和 F4 registry、但没有 runtime/view 绑定，也不能输出等级。

## CLI

在线推理：

```powershell
python -m rallymate_vision.cli `
  --request examples/request.json `
  --trusted-promotion-ledger calibration/trusted-calibration-promotion-ledger.json `
  --trusted-runtime-profile-bindings calibration/trusted-runtime-profile-bindings.json `
  --runtime-view-evidence calibration/runtime-view-evidence/<VIDEO_SHA256>.json
```

离线重评分使用同样的四个受信输入：

```powershell
python scripts/score_calibrated_indicators.py `
  --indicator-features <RUN>/indicator-features.jsonl `
  --scoring-summary <RUN>/scoring-loop-summary.json `
  --calibration calibration/<INDICATOR>.json `
  --trusted-promotion-ledger calibration/trusted-calibration-promotion-ledger.json `
  --feasibility-registry metric-feasibility-pose-wave-v2.json `
  --trusted-runtime-profile-bindings calibration/trusted-runtime-profile-bindings.json `
  --runtime-view-evidence calibration/runtime-view-evidence/<VIDEO_SHA256>.json `
  --output <RUN>/calibrated-scores.jsonl
```

模板文件故意不可直接加载：

- `calibration/trusted-runtime-profile-bindings.template.json`
- `calibration/runtime-view-evidence.template.json`

当前仓库没有真实教练真值、F4 指标或生产标定资产，因此不会生成任何可加载的 active binding，也不会借模板输出伪等级。
