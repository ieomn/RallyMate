# M94 RTMPose-L 本地影子候选

## 当前结论

`rtmpose-l-halpe26-analysis-shadow` 已登记为可显式选择的本地离线/影子分析候选。它不参与自动路由，不改变默认模型，不代表准确率已提高，也不获得 F4 或 A～E 评分资格。

生产默认仍是 `rtmpose-m-halpe26-online`（RTMPose-M Halpe26 256×192）。影子候选使用仓库中已经存在的 RTMPose-L Halpe26 384×288 配置与 checkpoint：

- checkpoint：`models/rtmpose/rtmpose-l_halpe26_384x288.pth`；
- checkpoint SHA-256：`734182CE2409BA84E96EA2A6361AEDB0EB40722F7AB52BB69331C73477D6A650`；
- config：`runtime/rtmpose/.venv/Lib/site-packages/mmpose/.mim/configs/body_2d_keypoint/rtmpose/body8/rtmpose-l_8xb512-700e_body8-halpe26-384x288.py`；
- 原生拓扑：Halpe26；输入尺寸：384×288；运行档位：`analysis`；
- 部署角色：`offline_shadow_analysis_candidate_not_accuracy_validated_not_F4`；
- 晋级状态：`offline_shadow_only_not_accuracy_validated_not_F4_promoted`。

## 已有证据与不能推出的结论

M71 在固定事件边界、Track、时间戳、特征函数和质量门禁下，只对三段开发视频的 258 个残差帧运行了 RTMPose-L 候选。相对 M70：

- 完整特征实例由 2,338 增至 2,344，增加 6；
- 门禁后可测实例由 2,327 增至 2,333，增加 6；
- 回退实例为 0；
- 增益集中在前两段视频，第三段没有 L 帧通过严格超集选择。

这证明的只是当前固定边界上的“可观测字段变多且本次选择规则未丢字段”。M71 没有人工校正关键点、逐视角特征误差或未参与选择的视频发布测试，因此不能据此声称关键点更准、事件识别更准、动作评分更准或应替换默认模型。原始机器证据为 `reports/measurement-recovery-m71/summary/report.json`。

[OpenMMLab MMPose 的 Halpe26 RTMPose 模型表](https://github.com/open-mmlab/mmpose/blob/main/configs/body_2d_keypoint/rtmpose/body8/rtmpose_body8-halpe26.md)给出的当前官方背景值为：PCK@0.1 95.56、AUC 74.38、参数量 28.24M、计算量 9.40G。这些是外部数据集上的公开模型背景，不是 RallyMate 四段视频上的测量结果，也不满足 RallyMate 的发布门禁。

M71 的历史 `models/rtmpose/model-candidates.json` 已被三份实验报告按原始字节 SHA-256 `8E53F978AC0D178056515629FF7AEF0C2486D1400451B24A943574EA4F152DC0` 绑定；其中保留当时录入的四舍五入值 95.60/74.40。为保证 M71 可以重放，本轮不原地改写该历史注册表。95.56/74.38、28.24M/9.40G 只作为 M94 的当前官方背景记录，不用于修改旧证据或支持准确率声明。

## 四段视频的固定分工

从 M94 起，四段素材按视频级隔离，不允许把同一视频的相邻帧拆到训练与测试两边：

- 开发/标注/候选选择池：`3ae77ee3271d67de171585a5c39ddd69.mp4`、`850cb0006b406c7176eeda8d711cd065.mp4`、`8d7754d0de6d315674013d5b69a0b6ba.mp4`；
- 独立保留视频：`c235227fffcd3290b60572d0c3f9cc85.mp4`，SHA-256 为 `41AE2B5C12A92E8883B159F0963741451D42A515BB82F11194DEC1F4FB1C05D6`。

`c235…` 只在候选、配置、阈值与训练过程全部冻结后用于一次预注册比较；不得根据其结果反向调参。它在早期第一阶段曾经运行过通用推理，因此不是“从未被任何人看过”的全新外部测试集。若要形成正式独立发布证据，还需要在冻结协议下密封标签和评测结果，并最终加入来源独立的新视频测试集。

## 为什么本轮没有执行微调

当前 `data/annotations/scoring-truth-pack-v1/compiled/validation-report.json` 状态为 `annotation_required`，其中人工事件 0、已接受关键点帧 0、已接受关键点关节行 0、语义真值 0、教练标签 0。现有模型预测、候选事件和“可测实例”都不能冒充训练标签。

因此当前可用于受控 Pose 微调的已接受真人真值为 0。本轮若启动训练，只能得到不可审计的伪微调，所以明确不执行训练、不生成新权重，也不登记任何准确率或晋级结论。

## 后续本地微调与独立评估路径

1. **先冻结协议与视频分组。** 固定三段开发视频、`c235…` 保留视频、Halpe26 关节定义、可见性规则、视角分层、评测指标和成功/回退门槛；按源视频分组，禁止帧级泄漏。
2. **采集人工关键点真值。** 从开发池按动作阶段、远近景、遮挡和足部缺失分层取样；两名标注者独立标 Halpe26，第三人裁决分歧。保存原视频 SHA、帧号、时间戳、标注者和裁决 lineage。
3. **转换并校验训练集。** 将裁决结果转换为 MMPose 可读的 COCO 风格关键点数据；执行关节数量、左右语义、边界框、visibility、空标注、重复帧和视频级 split 校验。只有通过校验的开发池标注可进入训练。
4. **适配 RTMPose 训练。** 以现有 RTMPose 官方 Halpe26 配置和 checkpoint 为初始化，单独版本化 dataset manifest、配置、随机种子、依赖、日志与输出权重。训练/验证切分只来自开发池，`c235…` 不得参与早停、模型选择或超参数决定。
5. **先评关键点，再评下游。** 在冻结的开发验证集上按视角报告必需关节归一化 MAE/P95/PCK、缺失率、左右交换与抖动；再以相同人工事件边界比较 13 项特征误差。必须同时记录吞吐、显存和失败率，和默认 RTMPose-M 做同输入、同裁剪、同门禁比较。
6. **最后开启独立保留集。** 在 checkpoint 与评测脚本 SHA 均冻结后，对 `c235…` 执行一次预注册评估，报告全部指标与置信区间，不能只挑改善项。若保留集退化、样本量不足或标签协议未完成，候选继续保持 shadow。
7. **发布门禁分开处理。** Pose 候选通过关键点评估，也只说明可考虑更换 F2 测量入口；F3/F4、正式阈值和 A～E 仍需人工事件、特征真值、教练标定及来源独立的发布验证，不能由 Pose 模型升级自动获得。

## 显式本地运行

影子档位只能由操作者主动选择：

```powershell
.\scripts\run_local_inference.ps1 -PosePreset rtmpose-l-halpe26-analysis-shadow
.\scripts\run_rtmpose_service.ps1 -Profile Shadow

# 分进程运行时，API 与 Worker 必须显式传入同一个 preset
.\scripts\run_api.ps1 -PosePreset rtmpose-l-halpe26-analysis-shadow
.\scripts\run_worker.ps1 -PosePreset rtmpose-l-halpe26-analysis-shadow

# 等价的 RTMPose Profile 写法（Worker 需与上面的 Shadow API 配对）
.\scripts\run_rtmpose_worker.ps1 -Profile Shadow
```

`run_api.ps1` 会把 `-PosePreset` 写入 API 进程的 `RALLYMATE_POSE_PRESET`；API 随后把同一 preset 及其模型、配置、backend、profile 和原生关键点格式固化进每个新任务请求。`run_worker.ps1` 以同一参数预载对应模型，因此分进程不会再出现“Worker 预载 L384、API 却生成默认 M256 请求”的错配。两个终端不能混用不同 preset；若要改档位，应先停止两端，再以同一值重启。

省略参数时，`run_api.ps1` 与 `run_worker.ps1` 都仍加载 `rtmpose-m-halpe26-online`。`run_rtmpose_service.ps1 -Profile Shadow` 是 API 与 Worker 同进程的一体化入口，天然共享同一 Shadow 设置。当前没有自动 fallback、自动 A/B 分流或后台默认启用 L 候选的路径。
