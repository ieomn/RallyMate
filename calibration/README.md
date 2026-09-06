# RallyMate 评分标定目录

本仓库不附带任何 A～E 阈值或序数模型参数。只有在教练真值、标注一致性、训练/验证拆分和独立测试完成后，才可按以下契约放入版本化产物：

## 生产加载信任边界

生产资产不会因为自身 JSON 声明 `passed`、`F4` 或
`approved_for_scoring=true` 而获得信任。生产评分还必须满足：

- 资产由 `promote_calibration_candidate.py` 输出并包含完整
  `promotion_lineage`；
- 资产 canonical SHA-256 与 lineage 必须精确匹配版本化可信晋级账本中的
  唯一 active entry；
- Worker 的账本路径只能由部署运维通过
  `RALLYMATE_SCORING_TRUSTED_PROMOTION_LEDGER` 配置，上传及普通任务请求不能指定；
- 账本不得位于服务的 `uploads`、`requests` 或 `runs`，并应使用仅运维可写的
  文件系统 ACL。

该账本是本地 allow-list，不是数字签名。能同时改写生产资产和已配置账本的主体
位于信任边界内，因此必须依赖操作系统 ACL、部署所有权和可审计发布流程。直接
CLI 可显式传入 `--trusted-promotion-ledger`；此时 CLI 调用者即信任边界。资产被
普通 JSON 反序列化后不会保留运行时授权，不能直接输出 A～E。

晋级账本授权后还必须经过第二层运行时绑定：标定资产、当前 F4 registry、实际
Pose backend/权重 SHA/原生拓扑、事件/主球员/质量/特征版本，以及当前视频的
人工机位证据必须精确一致。缺少任一 sidecar 时继续保持
`calibration_required`。完整契约与 CLI/Service 配置见
[`docs/RUNTIME_SCORING_PROFILE_BINDING.md`](../docs/RUNTIME_SCORING_PROFILE_BINDING.md)。
`trusted-runtime-profile-bindings.template.json` 与
`runtime-view-evidence.template.json` 故意不可直接加载。

- 阈值规则：`contracts/threshold-calibration.schema.json`
- 序数回归：`contracts/ordinal-model.schema.json`
- 教练标签：`contracts/coach-label.schema.json`

标定产物的 `source` 必须是 `coach_ground_truth_calibration`，并记录 `ground_truth_dataset_version`。当前评分资产契约为 `1.2.0`：阈值规则必须绑定 `primary_feature_version`，序数模型必须按 `feature_order` 完整绑定 `unit_by_feature` 与 `feature_version_by_feature`。评分时任何单位或特征版本不一致都会拒绝加载，防止特征算法升级后误用旧标尺。没有产物时统一输出 `calibration_required`，不得从当前视频分布或经验值自动生成阈值。

校验入口：

```powershell
python scripts/evaluate_calibration_labels.py --labels data/annotations/coach-labels.jsonl --output reports/calibration-evaluation.json
```

如需校验已有资产，可重复传入 `--calibration <file.json>`。该命令只校验标签一致性和资产契约，不从标签自动拟合阈值或训练模型。

## 从真值到生产资产的受控链路

当前实现将以下步骤拆开，避免训练、独立测试和发布互相读取或静默改写数据：

1. `scripts/build_manual_event_features.py` 只在 accepted 人工事件 ID、边界和 phase 上重算特征；不运行候选事件检测，也不做边界模糊匹配。
2. `scripts/compile_calibration_dataset.py` 精确关联人工事件、语义真值、教练标签和特征，并按球员/场次/视频连通分量生成无泄漏拆分；独立测试只以 SHA-256 seal 暴露给拟合器。
3. `scripts/fit_calibration_candidate.py` 只读取 train/validation 和预注册 fit protocol，输出不能评分的 candidate；协议中的样本、一致性和优化参数没有仓库默认值。
4. `scripts/evaluate_calibration_independent_test.py` 按另一份预注册验收协议打开 seal，报告结果但仍保持 `approved_for_scoring=false`。
5. `scripts/promote_calibration_candidate.py` 仅在人工决定、全部 lineage hash、独立测试报告和已为 F4 的注册表一致时复制候选参数为 production 资产；它不估计或修改任何切点、系数或门槛。

当前真实真值包为空，13 项注册表均为 F2，因此第 3～5 步会安全拒绝，仓库没有生成生产阈值或正式 A～E。操作细节见 `docs/CALIBRATION_CANDIDATE_FITTING.md` 与 `docs/SCORING_TRUTH_COLLECTION.md`。

生产 Pipeline 只接受 `artifact_scope=production` 的资产。单次任务可用请求字段 `scoring.calibration_assets` 显式传入；服务可通过 `RALLYMATE_SCORING_CALIBRATION_ASSETS` 配置多个路径。资产通过校验但指标不是 F4 时仍不会输出 grade。

## 生产信任模型

本链路中的 SHA-256、canonical seal 和 lineage 校验只证明“当前拿到的文件彼此一致”，并在已有可信锚点的前提下帮助发现内容被替换。它们不证明文件由谁签发，也不证明教练身份、标注真实性、协议确实在结果揭示前完成预注册，或某个 F4 注册表条目已经获得发布授权。拥有整套目录写权限的人可以同时替换文件和引用它们的哈希，仍然构造出链内一致的产物。当前仓库代码不验证签发者的密码学身份，不能把 hash 校验本身当成审批或签名。

生产环境必须在仓库契约之外建立可信发布边界：

- fit protocol 必须在拟合或候选选择前冻结；independent-test protocol 必须在独立测试标签、预测和结果对评测者揭示前冻结。预注册记录至少绑定协议 ID、版本、SHA-256、登记时间、负责人和审批记录；任何修改都创建新版本并重新执行受影响流程，禁止原地覆盖。
- 预注册协议、独立测试 seal/报告、人工 promotion decision、production 资产和作为发布依据的 F4 注册表必须发布到受控的只读目录或不可变制品库。运行时账号只有读取权限，写权限只授予发布角色；不得直接从普通工作区、上传目录、临时报告目录或任意请求路径信任同名 JSON。
- OS ACL、目录所有权和变更审计必须纳入部署基线。每次新增、替换、回滚或撤销都要记录操作者、时间、原因、对象哈希和审批凭证，并保证审计日志不由评分服务账号改写。
- 发布授权至少采用双人独立审批，或采用数字签名并在独立维护的可信签发者/公钥登记中验证；高风险部署可以同时要求两者。批准者不能只在待发布 JSON 内自我声明身份。
- F4 是受控注册表中的成熟度发布决定，不是任意 JSON 中的自证字段。生产只信任配置好的受控 F4 注册表及其匹配的独立测试证据、人工批准和发布登记；从其他位置复制或手工改成 F4 的条目没有授权效力。

如果协议预注册凭证、签发者/双人审批、受控注册表来源、权限状态或任一 lineage 校验不能确认，系统和运维流程都应 fail closed：隔离该资产，不输出 A～E，保持 `calibration_required` 或 `unavailable`。在数字签名和可信登记验证尚未由程序实现前，上述只读目录、OS 权限/审计和双人审批属于必需的外部运维控制，而不是可选建议。
