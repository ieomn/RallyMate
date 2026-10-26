# 298 卡执行与真实证据矩阵 v2

这是基于干净 Git 提交 `f430a2d42ea65e979531b26b1264950e13fbed52` 的历史快照。保留 `card-implementation-matrix-v1.json`，不把后续增加的入口或修复算作本次实现。所有 298 行都有原卡 rubric 摘要和本次有效规则摘要；规则注册、适配器存在、真实执行和获得代理分是不同字段。

本次从原视频重新解码，并从既有原始姿态、主球员选择和事件文件重新计算四组结果，没有重新推理姿态模型，也没有读取历史分数作为计算输入：原 FS 200 实例、GS 准备期 60 实例、GS M07 55 实例、正式 FS04 人工候选窗 30 实例。共 345 个不重复实例，140 个 `proxy_scored`、155 个 `review_required`、50 个 `unavailable`。覆盖 40 张不同卡，其中 15 张至少有一次有效工程代理分；258 张没有本快照的真实阶段执行证据。原文完整技术等级自动评分为 0。

登记的 158 条视觉代理规则均有对应数值计算注册，263 张卡有阶段/事件适配入口（248 GS、5 个原时序 FS、10 个 FS03/FS04）。这两个数都不是实测卡数，也不意味着全部原文行为可观测。140 张卡仍需要人工或外部观察。三张 `GS01/02/03-M07-01` 虽已实际尝试执行，但该基线没有可信持拍侧输入入口；五张降重心卡需要专用局部联合运动子窗。这 8 项依赖缺口与“缺少真实阶段窗口”分别列出。

视频 inventory 按原视频 SHA-256 去重，共 11 个视频，只有 5 个视频产生本次逐卡实例；另外 6 个仅保留识别/观察来源信息。两个 USTA shuffle 片段共享一个原视频，因此只算一个视频。USTA 混合步型的正式 6 个 FS04 人工候选窗共 30 实例，全部待复核；没有纳入原核心按 FS03、FS04 两种假设投影的 60 实例，也没有合成轨迹实例。人工窗口不等于专家步型或原卡阶段真值。第四份统一 API 报告中的旧 65 个 FS 实例不另加入，避免与指定证据范围混淆和重复计算。

`tests/fixtures/card-execution-inputs-v2.json` 指定 8 组运行输入及 3 份视频清单。`tests/fixtures/card-execution-results-v2.json` 是真实重算结果的数值副本，保留每个实例的条件、实值、等级、质量、原因、源窗口和原始时间摘要。长的运动序列留在完整本地报告，以完整实例/证据摘要和 JSON pointer 绑定。`docs/card-implementation-matrix-v2.json` 则保留全 298 行、每行的独立状态、实例引用与可复算统计；没有媒体、权重或本机绝对路径。

真实重算入口（目标目录必须不存在；需要原视频和未改动的大型本地 run 文件）：

```powershell
python scripts/build_card_execution_matrix_v2.py replay --repository . --source-commit f430a2d42ea65e979531b26b1264950e13fbed52 --manifest tests/fixtures/card-execution-inputs-v2.json --output-directory .codex_tmp/card-execution-matrix-v2-new-replay
```

该入口先导出指定提交，强制所有 `rallymate` 导入来自该干净目录，再执行正式生产适配器。原 FS 的帧/选择记录额外与重新解码的真实 PTS 核对。GS 和周期入口验证视频摘要、run/job 身份、源帧、PTS、人工注释和选择/相机边界。实际导入的 `rallymate` 模块、全部打包 JSON 资源与两个被调用的 runner 依赖逐项核对 Git 对象；资源是保守包含，并不声称每个资源都被每张卡读取。当前闭包共 92 个文件。原始文件摘要与 UTF-8 换行归一到 LF 的摘要分别记录；已提交清单/注释在跨检出时允许显式 LF 等价，保留当次原始摘要不改写。原视频、姿态输入和完整报告仍按原字节校验。规则/注释的 canonical JSON 摘要也有独立算法名称，不能互换。

本次完整输出位于 `.codex_tmp/card-execution-matrix-v2-repaired-20261022/`。此次在同一历史基线重新运行了全部 8 组原始证据，以更新验证脚本来源；345 个实例数值没有被手工修改。此前 release 目录保留为历史产物。从该次结果重建矩阵仅校验保存的执行证据，不会重新解码，也不能称为新视频验证：

```powershell
python scripts/build_card_execution_matrix_v2.py build --repository . --results tests/fixtures/card-execution-results-v2.json --runtime-checkout .codex_tmp/card-execution-matrix-v2-repaired-20261022/checkout --output .codex_tmp/card-execution-matrix-v2-repaired-20261022/rebuilt-matrix.json
```

重建会再次校验完整报告与紧凑副本、原视频、源文件、注释、清单和生产脚本摘要。它从已绑定的 3 份清单重新构建视频身份，拒绝 inventory 中额外、缺失或被改名的来源，也重新核验仅用于识别的视频字节。导入 checkout 代码前先逐文件核验历史来源闭包，两个 runner 依赖也必须在闭包中；导入后再次核对实际加载的源码和 runner 与 Git 基线。没有结果不能成功；未知卡、重复实例、跨来源/阶段错绑、错误分数算术、摘要计数篡改、合成或未验证文件的结果均拒绝。缺失值不补 E/0。

普通 CI 执行 `python -m unittest tests.test_card_execution_matrix_v2 -v`。16 项核心数值/完整性测试使用已提交的真实副本，不依赖媒体且不会跳过；其中两项检查视频 inventory 增删改名和 checkout 验证脚本篡改，后者必须在导入前拒绝。额外 1 项明确标注为本地 integration，需要设置 `RALLYMATE_MATRIX_V2_INTEGRATION=1`；它核对原始大文件和 Git 来源，不把人为制造的坏字节测试计入视频样本。本次已完成真实重算，CLI 重建和开启该 integration 的全部测试。

`build_indicator_acceptance_matrix.py` 仍是原文完整等级 verifier 的验收视角，不能用这里的代理覆盖替代。任何后续新增可信持拍侧、其余 FS 人工窗口或规则变更，必须换基线重算，不能只修改本文件的统计或来源摘要。
