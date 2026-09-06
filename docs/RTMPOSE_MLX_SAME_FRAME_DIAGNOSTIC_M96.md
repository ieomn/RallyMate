# M96 RTMPose M/L/X 同帧开发诊断

> 状态：`diagnostic_complete_ground_truth_required`  
> 日期：2026-09-04  
> 用途：在不改变默认模型、不使用人工真值的前提下，比较 M/L/X 在完全相同输入上的可观测运行特征。

## 1. 本轮得到的结论

M96 已在本机 NVIDIA GeForce RTX 5070 Ti 上，对 3 段开发视频执行 RTMPose-M、RTMPose-L 和
RTMPose-X 同帧诊断。三种模型读取完全相同的 45 个解码帧和冻结 Player ROI，均返回 45/45 组
Halpe26 Pose。

在本轮固定样本上：

| 模型 | Pose 返回率 | 置信度不低于 0.5 的关键点覆盖 | 单 ROI 延迟 P50 | 连续帧归一位移 P50 | 同输入重复坐标差 P50 |
|---|---:|---:|---:|---:|---:|
| RTMPose-M 256×192 | 1.000000 | 0.734188 | 9.1244 ms | 0.006323 | 0.000000 |
| RTMPose-L 384×288 | 1.000000 | 0.747863 | 10.6427 ms | 0.006017 | 0.000000 |
| RTMPose-X 384×288 | 1.000000 | 0.769231 | 11.7265 ms | 0.006166 | 0.000000 |

X 在这 45 帧上的模型内高置信关键点覆盖最高，M 的本机单 ROI 耗时最低。它们是开发诊断事实，
不是准确率排名。模型置信度可能校准不同；模型彼此接近也可能是共同犯错；连续帧位移同时包含真实
运动、ROI/相机变化和模型输出变化，不能单独解释成“抖动”。

因此当前产品决策保持不变：

- 默认仍为 `rtmpose-m-halpe26-online`；
- L/X 仍为离线候选；
- 不生成候选综合分或“赢家”；
- 不声明 RallyMate 准确率已测量或已经提高；
- 不打开密封 `c235...` holdout，不推进 F3/F4、正式 A～E 或模型晋级。

## 2. 输入协议

协议注册表为 `models/rtmpose/m96-same-frame-diagnostic.json`，版本
`rtmpose-same-frame-diagnostic-2026-09-04.1`。它逐字节绑定：

- 当前部署注册表及默认 M preset；
- M/L 候选来源注册表；
- M95 X 候选注册表；
- 三个模型各自的 checkpoint、config、输入尺寸和 Halpe26 拓扑；
- 3 段开发视频及其冻结 `frames.jsonl`；
- 明确禁止在开发诊断中使用的 `c235...` holdout 身份。

每段开发视频从连续、同一 Track 的候选段中确定性选择 3 个互不重叠窗口；每个窗口连续 5 帧，
每帧只取冻结检测中面积最大的 Player ROI。总样本数固定为 `3 × 3 × 5 = 45`。运行顺序为单卡
串行，三个模型统一使用 realtime/no-flip、0.15 ROI margin 和 32 px 最小 ROI。

样本清单 canonical SHA-256 为
`A2EA1CAEEBFE1274BECB7FDA4434C784FD0101E260152CECD27D97AEFA14413A`。

## 3. 报告字段怎样理解

完整报告 `reports/m96-rtmpose-same-frame-diagnostic/diagnostic-report.json` 保存每个模型、每个样本的
26 点坐标、置信度、耗时、来源帧和 ROI，并由原始 observations 重算汇总。

- `pose_return_rate`：模型是否为每个 ROI 返回一组 Pose；缺失 Pose 仍进入分母。
- `threshold_coverage_including_missing_poses`：模型输出置信度超过固定阈值的关键点比例；不是准确率。
- `observed_temporal_displacement`：相邻真实视频帧输出坐标的归一位移；包含真实运动。
- `same_input_repeat_delta`：相同解码帧和 ROI 重复调用的坐标差；本轮 P50 均为 0。
- `cross_model.pairwise`：两模型在同一关键点上的坐标距离；只是模型间一致性。
- `three_model_consensus`：三模型同时有足够置信度时的最大两两距离；不是多数表决真值。
- `comparative_orderings`：只按单一诊断字段排序，不产生综合模型分。

报告 SHA-256 为
`B6361C909CF483C06B19B7EDA62DB1221D29BE1004E8F4FFF046FE7C5A0CCF71`。

## 4. 可复核运行

真实 GPU 运行入口：

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe `
  .\scripts\run_m96_rtmpose_same_frame_diagnostic.py `
  --output .\reports\m96-rtmpose-same-frame-diagnostic\diagnostic-report.json
```

输出采用不可覆盖语义。已有正式报告时必须选择新的输出目录或新的协议版本，不能覆盖当前证据。

聚焦验证：

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m unittest `
  tests.test_pose_same_frame_diagnostic `
  tests.test_pose_shadow_smoke `
  tests.test_pose_deployment_smoke_v2 -v
```

M96 冻结记录中的聚焦结果为 17 项通过，Python 编译通过。完整字段与 22 个产物 SHA 保存在
`reports/m96-rtmpose-same-frame-diagnostic/field-change-record.json`；该记录本身 SHA-256 为
`EBAA52D305B963719093BE2663240C11F32146D1AEDA347E3D2977A0225ED1DD`。

## 5. 后续证据的当前状态

同帧诊断已经把“模型能不能运行、是否更容易给出高置信关键点、速度代价和模型间差异”量化，但不能
回答“坐标是否更接近真人标注”。原计划中的 development pilot 已落地为
`data/annotations/m96-halpe26-development-pilot-v1`：3 段开发视频各 8 帧，共 24 帧 × 26 点 = 624 个
关节任务。A/B 为相互独立的盲标角色，只有完整且验证通过的 A/B 输入后才创建 C 分歧裁决；标注者界面
不显示模型值。该 pilot 仅用于评测，`training_allowed=false`、`dataset_export_allowed=false`、
`promotion_allowed=false`。

当前 pilot 的人工标注行数和裁决行数仍均为 0，accuracy 为 `null`。治理模板只保存已知身份与
`REPLACE_WITH_*` 占位符，不能推定真实 consent、subject、session、split、usage scope 或 retention。
pilot 合同、摘要和字段记录 SHA-256 分别为
`B8472CEF9F622FBF21A4ABEFB3D6F36A872BA5237D5503027A836CBD8C5B682F`、
`2BE2E2C06A1AAA70F05F0FC98583AFAF5158DC802EF6D726A4BCAF0DFB8E7C2B` 和
`877851936E18B03A8088AD0E4DDE9BA6B5C0D11B516FAF0893D586EBDB6DD465`。

M97 又在 M68/M70/M71 已冻结的三视频 258 个残差帧上，以相同 ROI/上下文、`analysis` + flip-test 和
required-joint strict superset/no-regression 路由运行 X384。X 返回 254 个 Pose、路由选中 56 帧；
相对 M68，feature/operational 恢复为 13/11、回归均为 0，最终 feature complete/operational measured
为 2,342/2,331。它保留 M70 的恢复集合并各新增 4 项，但相对 M71 各新增 1 项同时丢失 3 项，净少 2，
所以不能取代 M71 的开发集恢复结论。完整集合、延迟口径和哈希见
`docs/RTMPOSE_X_OPERATIONAL_COMPARISON_M97.md`。

M97 是 X384 的 `analysis` + flip-test operational 重放；本文件 M96 延迟是 M/L/X 统一
`realtime`/no-flip 的同帧横向诊断。两者不能计算跨 scope 速度倍率。二者都没有人工真值，因此不构成
准确率或晋级证据，默认仍为 `rtmpose-m-halpe26-online`，密封 `c235…` holdout 仍未打开。

下一条有效证据是完成上述 24 × 26 pilot 的真实 A/B 独立标注和 C 分歧裁决，再在完全相同帧、ROI 和
人工点上按视频聚类报告 M/L/X 的配对 MAE、P95、Bias、PCK 与有效率；若样本不足再预注册扩样。

现有 1,976 个任务来自两个历史残差评测包，存在跨包同帧重叠且只覆盖 14/26 点；它们是历史评测队列，
不是训练清单或训练数据集。微调数据仍需另建去重、全 26 点并按真实 subject/session 隔离的 train/val
数据集。
