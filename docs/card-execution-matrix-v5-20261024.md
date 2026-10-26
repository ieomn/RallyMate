# 298 卡当前来源与执行矩阵 v5

本版从已提交的 `b1314b8edf3c5e01bea6725afc51261265ebc49f` 纯 Git 归档重新执行全部 13 组。适配器实际解码原视频，并读取不可变的原始姿态、选人、事件及冻结人工窗口；没有重跑模型推理，没有把旧报告改名当成新结果，也没有吸入工作区尚未提交的姿态关联修复。独立新进程重建的矩阵与首次矩阵逐字节相同。

实际得到 **372 个实例：154 个正式应用评分、218 个不可评分**。52 张不同卡有真实执行尝试，占 298 张的 17.45%；21 张至少有一个正式分，占 7.05%；246 张没有本次来源范围内的真实阶段证据。原文技术等级自动验证仍为 0，全部实例 `original_grade=null`、`source_grade_supported=false`。空分不计成零分或 E。

目录有 156 张已注册数值条件卡，另有 FS05-M02/M03/M04 三张独立应用规则；三张在原目录中仍是 `manual_review`，应用规则的版本、条件和来源单独登记，未重写原文定义。156 条规则与 3 条应用规则是实现数量，不是真实覆盖。没有注册评分规则的卡仍有明确清单状态，没有用合成实例填补覆盖，也没有新增人工评分接口。

## 原有十组与新增三组

旧十组来源、原窗和输入散列不变，本次全部重新计算：

- 原 FS 时序：200 实例，115 有分、85 不可评分。
- MIT 正手、单反、双反准备：分别 20/20/20 实例，有分 12/13/2，不可评分 8/7/18。
- MIT 正手、单反、双反 M07：分别 20/15/20 实例，有分 4/0/0，不可评分 16/15/20。
- USTA 六个 FS04 人工候选周期：30 实例，全部不可评分。
- USTA 三个人工逐卡窗口：3 实例，2 有分、1 不可评分。
- MIT FS07-M02 固定负例：1 实例，不可评分；原 29–38 帧未扩大。

旧十组共 349 实例，148 有分、201 不可评分。与冻结 v4 比较，**349 个完整实例的规范化 JSON SHA 全部相同**，601 个原有非空条件数值、状态、等级、分数和窗口均未变化。这是两次计算结果的核对，不是从 v4 导入分数。

新增三组共 23 实例，6 有分、17 不可评分：

- native e420 导出的 FS05-M03/M04：原帧 112–136，同一固定开发窗，两卡均为 C/60。髋中心沿目标轴的误差减少量分别为 -0.3850337076、-0.4490654930 个躯干尺度，该条件均失败；双踝整窗最大路径 0.3002623309 个躯干尺度，以及各自方向对齐条件通过。目标是原帧 112 观察到的球中心及人工场地轴，不能解释为预测触球点或最优击球位置。
- 同一 native e420 导出的 FS05-M02：原帧仍为 112–136，C/60。完整可见调整 episode 数为 1，未达到至少 2；双脚路径条件通过。边界截断的运动保留为证据但不计完整 episode，双脚重叠运动不会重复算成两次顺序调整。该应用条件分不证明自动识别出真实 FS05 技术。
- MIT 正手 M09：四个固定窗口 40–45、103–107、162–166、224–229，共 20 实例，3 有分、17 不可评分。有分实例均属于 GS01-M09-01。没有扩大窗口或补齐缺失阶段。

原视频库存仍为 11 个，只有 5 个视频产生本次卡实例；13 个执行组对应 6 份不同的姿态导出。native e420 与 production 0a3 使用同一媒体 SHA，却是不同原始 frames 导出，必须分开，不因视频相同混为同一条轨迹。新增三组没有增加原视频数量；未具备完整人工阶段的其余视频不增加评分覆盖。材料已被观察或用于开发，不能称为独立保留集准确率验证。

## M09 撤销与计数边界

GS01-M09-04/05 的 8 个实例本次全部为不可评分，条件列表为空，正式等级与分数为空。双腕几何只保存在 `auxiliary_observations`，`scoring_eligible=false`；它既不能证明非持拍手接近球拍，也不能证明重新控制球拍。首个窗口两卡仍有可计算辅助观测，其余窗口原有不足没有被补值。

`retirement_audit` 按原窗口、来源导出、实例 SHA 和历史 JSON pointer 保留 8 条对照。其中只有 **2 条历史实例曾有分**，现已撤销；8 条不是 8 次撤分，也不是额外 8 个执行实例。旧 follow-through 结果及生产者保持不变，旧十组 v4 原本不包含这批 M09 窗口，因此这里不会从旧十组的 148 个有分实例中再扣除 2。

本版没有跨来源总分：顶层 `overall_score=null`。235 个 `score_groups` 只在同视频、同姿态导出、同本地人物且完整 primary/derived/camera 连续性证据相同时汇合；primary 文件不同或 epoch 不同也分别处理。其余 230 个时序或周期实例没有可直接等同于 GS/FS 派生选人计数器的完整跨窗身份契约，各自保持实例范围，不能凭数字相同的 track/epoch 合并人物。这里的分组数不是视频数或运动员数。

来源和时钟闭合只证明文件、原帧、选人记录及工程连续性一致，不能证明姿态点属于画面中的正确人物。另一次只读开发审计在 USTA 原帧 11986 实证了重叠 ROI 将教练姿态赋给练习者的旧模型输出缺陷；本版继续使用冻结原始观测，不应用尚未提交的修复、不平滑或删帧，也不把稳定 track、完整 COMMON 或高置信度当成人物真值。该缺陷、未确认动作类型、非专家阶段边界、缺失球拍/球方向/接触/受力证据和未校准工程阈值，均限制这些应用分的解释。这里不报告动作识别准确率、运动员能力总分或专家技术等级。

## 来源、失败记录与复算

可提交输入仍是 `tests/fixtures/card-execution-inputs-v5.json`。实际输出逐字节复制为 `tests/fixtures/card-execution-results-v5.json`（25,188,735 字节）和 `docs/card-execution-matrix-v5.json`（953,104 字节）。两文件不包含媒体、图片、权重或机器绝对路径。原字节 SHA-256 分别为：

- results：`45813e937baaac37daceb19044ba1ccb883724d8ce9a2f9301f71d8513d438fe`。
- matrix：`a00bd7d49e02df91a97b2fb9cdda825fec25f1af5c29106d2588db61ec16fad3`。

完整报告、原媒体和运行收据留在 `.codex_tmp/card-execution-matrix-v5-current-20261024/actual-replay-r2/`。运行前核验纯提交的完整源码导入面，拒绝影子源码和旧字节码；本次实际导入模块、打包 JSON 及指定 runner 的闭包共 102 文件，全部绑定同一提交。26 个原始文件路径和 5 个执行媒体路径的前后 SHA 一致。打包 JSON 是保守闭包，不表示每张卡都读取每个资源。媒体/JSONL 使用原字节摘要，文本额外记录 UTF-8 LF 摘要，语义对象使用规范化 JSON 摘要，三种口径不混用。

初次纯 `3b1d74b` 重放在第 8 组遇到 runner 契约错误：周期适配器输出 `input.source_video_sha256`，runner 却读取 GS 路径的 `input.video_sha256`。原视频和四个 raw 摘要实际均匹配。失败的 `actual-replay/`、日志和 `failure-receipt.json` 永久保留，未生成完整矩阵。窄修复只明确该 route 的原生字段，并继续严格验证完整文件集合、摘要和所有 artifact/decoder 门槛；20 项测试通过、0 跳过，包含原 USTA 视频实际解码和 30 个无分周期实例。修复提交为上述 `b1314b8`，随后从新目录重跑全部 13 组，没有接着使用失败进程的旧结果。

实际命令的生产者来自纯提交归档 `launcher-b1314b8/`，该归档不覆盖工作区。运行需要清单中的本地媒体与大文件，输出目录必须尚不存在：

```powershell
python .codex_tmp/card-execution-matrix-v5-current-20261024/launcher-b1314b8/scripts/build_card_execution_matrix_v5.py replay --repository . --source-commit b1314b8edf3c5e01bea6725afc51261265ebc49f --manifest tests/fixtures/card-execution-inputs-v5.json --output-directory .codex_tmp/card-execution-matrix-v5-current-20261024/actual-replay-r2
```

再次运行须创建新的纯提交 launcher 及新的输出目录；不能用后来修改的 runner 冒充此版。也可以在与该提交字节一致的 runner 上执行，runner 会再次创建并核验独立运行时归档。随后以独立新进程执行：

```powershell
python .codex_tmp/card-execution-matrix-v5-current-20261024/launcher-b1314b8/scripts/build_card_execution_matrix_v5.py build --repository . --results .codex_tmp/card-execution-matrix-v5-current-20261024/actual-replay-r2/results.json --runtime-checkout .codex_tmp/card-execution-matrix-v5-current-20261024/actual-replay-r2/checkout --output .codex_tmp/card-execution-matrix-v5-current-20261024/actual-replay-r2/rebuilt-matrix.json
```

重建核验原文件、实际运行时闭包、完整报告、原帧/PTS/身份账本、逐实例来源、条件算术及汇总，并用原 raw 重新计算三个 FS05 应用实例。它不会再次解码全视频或执行模型推理，不能称为第二次模型验证；其输出与首次矩阵逐字节一致。`completion-receipt-r2.json` 记录前后来源散列、349 个旧实例的完整 SHA 对照、当前计数及独立重建结果。v3、v4 和旧 follow-through 报告均未改写。
