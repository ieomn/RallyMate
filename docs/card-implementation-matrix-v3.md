# 298 卡执行与真实证据矩阵 v3

本版是提交 `eb61d4df00527158637db7ac4acb22e07f7f07a3` 的独立快照。历史 v1、v2 的文件、基线和数字保持不变。原视频重新解码，既有姿态、主球员选择、事件和人工注释重新送入正式适配器；未重新推理姿态模型，未读取保存分数作为计算输入，也没有合成视频实例。

本次共 348 个不重复逐卡实例：140 个 `proxy_scored`、157 个 `review_required`、51 个 `unavailable`。43 张不同卡有真实执行尝试，15 张至少一次取得工程代理分；255 张没有本次来源范围内的真实阶段窗口证据。原文完整技术等级自动评分仍为 0。

登记的 158 条视觉代理规则有对应数值函数，298 张卡有输入适配入口，140 张卡仍是人工或外部观察规则。这三项不能当作真实计分覆盖。`declared_dependency_contracts` 只声明运行时支持的输入契约；`contract_declaration_is_not_behavior_validation` 明确不把声明当作行为验证。`dependency_binding_gaps` 为 0 仅表示本版盘点的显式输入契约已接线，不代表原文不可观测行为有自动 verifier。实际窗口、持拍侧、完整步周期、辅助测量和分数各有独立计数。

九组执行保留原有证据范围并加入三个独立人工 FS 窗口：

- 原 FS 时序：200 个实例，109 分数、91 复核。
- MIT 三技术准备阶段：60 个实例，27 分数、16 复核、17 不可用。
- MIT 三技术 M07：55 个实例，4 分数、18 复核、33 不可用。
- USTA 六个 FS04 人工候选周期：30 个实例，全部复核。
- USTA 三个逐卡人工窗口：3 个实例，2 复核、1 不可用。

新持拍侧来源只并入三个既有 M07 动作，各自涉及 5 张卡；这 15 个实例已经包含在 55 个 M07 实例中，没有再相加。合并要求动作的人物、类别、起止边界和全部阶段与旧注释完全相同，只允许新增 `racket_side_observation`。其含义仍是少量原帧上的可见持拍腕，不是优势手或全程持拍真值。三个短窗仍是原来的 5、2、3 帧，腕速条件均不可用。旧八组 345 实例的条件、质量、原因、状态和分数与 v2 完全一致；来源信息的补充没有放宽门槛。

五张 GS 降重心卡已有局部联合运动辅助入口，但仍为 `manual_review`、空等级、空分数。本次没有可确认完整 M01 的源注释，因此辅助测量实际实例为 0。未把 M03 或其他阶段改名为 M01。新增四张 FS 迈步卡的 `step_motion_evidence` 已接入，采用声明移动脚、共同六关节、原 PTS 和完整低速—移动—低速验证；本次真实完整 FS4 周期例为 **0**，实际计分例亦为 **0**。入口存在与没有适当真实源窗口是两件事。未将静态交叉姿势、方向标签或人工布尔值当作已验证周期。

原 FS 内有 44 个尝试因没有定位到计算子窗而保留为无分数记录。它们单列于 `unlocated_measurement_window_attempts`；既不捏造阶段边界，也不从统计中删掉失败。逐卡 `observed_failure_reason_counts` 保留阶段缺失、连续帧不足、质量问题等实际原因。五张辅助卡、四张迈步卡的声明能力也分别与实际观测数并列。

视频清单按原视频 SHA-256 去重，仍为 11 个源视频，其中 5 个产生本次逐卡实例；另 6 个仅有识别或观察来源，不增加卡片样本数。同一原视频的不同截段不重复算视频。统一 API 报告仅用于集成复核，不再次汇入实例，以免重复计数。人工阶段或步型候选均不等于专家真值，工程代理也不声称接触、受力、真实视线或原文完整动作等级。

## 复算与来源闭包

`tests/fixtures/card-execution-inputs-v3.json` 指定九组原始输入和三份视频清单。`tests/fixtures/card-execution-results-v3.json` 是正式重放的数值副本。每个实例保留原条件、质量、原因、源阶段与来源绑定；完整实例、长序列及原证据另以 canonical SHA-256 和 JSON pointer 绑定本地完整报告。`docs/card-implementation-matrix-v3.json` 保留全部 298 行和重新统计的摘要。

执行命令需要原视频及大型本地 run 文件，输出目录必须尚不存在：

```powershell
python -B scripts/build_card_execution_matrix_v3.py replay --repository . --source-commit eb61d4df00527158637db7ac4acb22e07f7f07a3 --manifest tests/fixtures/card-execution-inputs-v3.json --output-directory .codex_tmp/card-execution-matrix-v3-new-replay
```

本次最终完整结果位于 `.codex_tmp/card-execution-matrix-v3-release-20261022/replay/`。运行先从指定 Git 提交导出干净目录，并在任何项目代码导入前核验全部 224 个源码、资源和 runner 文件。拒绝额外的源码影子文件；使用新的禁写字节码缓存路径，避免已核验 `.py` 被旧 `.pyc` 替代。计算后记录实际导入模块、全部打包 JSON 和两个明确调用的 runner，闭包共 96 个文件，并逐项对照同一提交的 Git 对象。资源是保守包含，不声称每个资源都被每张卡读取。

原视频、原 JSONL、原图和完整输出按原字节 SHA-256 校验。文本来源保留当次 raw SHA-256，同时记录 UTF-8 换行归一至 LF 的 SHA-256，以支持不同检出的换行；语义变化仍拒绝。目录和注释另用 `canonical_json_sorted_utf8_v1`，这三种摘要不互换。持拍侧注释的两个来源文档均记录摘要，重建时从其原始阶段与侧观察重新组合文档，不只相信报告保存的注释摘要。

从已保存的真实结果重建矩阵不会重新解码，不能称为再次视频验证：

```powershell
python -B scripts/build_card_execution_matrix_v3.py build --repository . --results tests/fixtures/card-execution-results-v3.json --runtime-checkout .codex_tmp/card-execution-matrix-v3-release-20261022/replay/checkout --output .codex_tmp/card-execution-matrix-v3-release-20261022/replay/rebuilt-matrix.json
```

重建同时核验全量 Git 导入面、实际闭包、三个来源清单、11 个原视频、原输入文件、完整报告和紧凑副本。按原视频、人物、卡 ID 与源时间窗去重；更换动作或分组名称不能使相同窗口重复计数。对没有定位到子窗的无分数尝试保留原事件身份，不虚构时间范围。可选 `gs_additional` 路由仅供后续独立冻结的真实阶段注释，本版没有该类实例。

## 验证边界

普通 CI 运行 `python -B -m unittest tests.test_card_execution_matrix_v3 -v`。30 项中 29 项无需媒体且不跳过；1 项是显式本地集成检查。三项测试使用真实临时 Git 仓库检查归档、闭包遗漏依赖篡改和额外源码影子文件，未操作工作区索引。其余测试验证固定真实数值副本、持拍侧合并不扩窗、不重复计数、FS 原帧/PTS/文档绑定、缺失不变 E/0、辅助值不能授权评分、清单篡改和完整报告字节变化。

设置 `RALLYMATE_MATRIX_V3_INTEGRATION=1` 才执行需要本地原媒体和运行目录的那项检查。本次还执行干净基线加仅本版六文件的普通测试，以及显式本地集成与 CLI 重建。测试用的小型伪造数据仅检验拒绝逻辑，不进入真实视频计数。后续新增真实阶段、完整步周期或改动运行时代码，应另换基线重放，不修改本版摘要冒充当前验证。
