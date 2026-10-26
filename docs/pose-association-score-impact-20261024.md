# 人体姿态归属修复后的真实评分影响

本次从纯提交 `577343ccb50670e1f2ea1d38aa9e18a865c08fa7` 归档执行已提交的 v5 runner，以完全相同的 13 组冻结输入重新解码原视频、读取原始轨迹并计算评分。比较基准是 `b1314b8edf3c5e01bea6725afc51261265ebc49f` 产生、现已提交的 v5 结果。没有重新推理姿态模型，没有替换原始观测，也没有修改历史 v5 结果。

372 个实例的状态、等级、分数和原始窗口全部保持不变：154 个 `scored`，218 个 `unavailable`；实际尝试 52 张卡，有分 21 张卡。本次没有新增分数或撤回已有分数，不合并跨来源总分。

但不能据此声称所有数值都未变化。USTA 原始窗口 11961–11996 中的第 11986 帧存在所选 track 2 姿态与 track 1 姿态重复、且原人框归属矛盾。新硬门将此窗的 5 个 FS04 实例明确标为 `selected_pose_person_association_conflict`。它们原本就无分；修复进一步撤回了其中 7 个不可信条件值，非空条件条目总数由 619 降至 612：

- FS04-M02：髋位移 `2.888830120420549`、躯干倾斜变化范围 `24.202940793466112`。
- FS04-M03：最大踝位移 `3.3128965299600615`。
- FS04-M04：踝间距收缩 `0.3591856762793175`。
- FS04-M05：髋速度下降 `-1.1992941116301488`、髋位于双踝支撑区比例 `0.6111111111111112`、躯干倾斜变化范围 `24.202940793466112`。

以上值在新结果中均为 `null`。其他条件值以及全部阈值、单位、运算符和 passed 标记保持不变。5 个受影响实例均属于 `manual-recovery-03-fs04-projection`，36 个原始帧完整保留，拒绝帧为 `[11986]`；没有删帧、平滑、扩窗、插值或换人。这证明该窗口缺少可信的所选人体几何，不证明真实人员已经切换。

这 5 个实例的整块派生 `measurements` 和 `evidence.series` 也被清空。每个旧 measurements 有 10 个键（其中 9 个非空），旧 series 有 19 个顶层键；总计清除 50 个测量条目（45 个非空）和 95 个序列顶层条目。这些是同一来源窗口在 5 张卡中的重复派生证据，不是 5 个新动作。收据列出全部旧测量值、序列键名及旧序列 canonical SHA；原始帧、PTS、coverage 等来源证据仍保留。除上述清空、7 个条件值和列明的 reason_codes 变化外，5 个实例的所有非审计字段逐字段相等。

完整实例比较中，186 个实例完全相同；181 个实例只新增 `pose_person_association`、`phase_pose_person_association` 或 `decision_support_source_frames` 来源审计字段；其余 5 个就是上述已无分窗口。比较逐一认证了两侧完整报告的原始字节 SHA，以及 372 对完整实例的 canonical JSON SHA，随后比较评分字段、全部条件合同与数值、原始窗口及帧清单。新审计中尚未评估的 SAM 轨迹仍标为 unassessed，不能把审计字段存在解释为已验证人体身份。

## 可追溯产物

小型机器可读收据为 [pose-association-score-impact-20261024.json](../tests/fixtures/pose-association-score-impact-20261024.json)，包含 13 对完整报告路径与 SHA、372 对完整实例 SHA/JSON pointer、7 条数值差异和 5 条冲突来源证据。大型完整报告留在 ignored 目录，未再复制进仓库。

- 基准 bundle：`tests/fixtures/card-execution-results-v5.json`，原始字节 SHA `45813e937baaac37daceb19044ba1ccb883724d8ce9a2f9301f71d8513d438fe`。
- 本次 bundle：`.codex_tmp/pose-association-score-impact-20261024/actual-replay/results.json`，原始字节 SHA `df10a3d03808670570ba930b7ea597841156213ce23a8deffff0a9f2d95ef344`。
- 本次 matrix 及独立进程重建的 `rebuilt-matrix.json` 逐字相等，原始字节 SHA 均为 `b9c3835436ea8094327b3aaf1e74ca3ca886a264a745676035c36069a07babb4`。
- 实际 runtime 闭包为 103 个源码文件，收据绑定完整闭包摘要；原先基准为 102 个文件。
- 26 个原始数据文件、5 个实际媒体文件以及 3 个冻结 v5 文件的执行前后 SHA 全部相同。

收据中的文件 SHA 均针对当次磁盘原始字节，实例和 runtime 闭包摘要使用 UTF-8 canonical JSON（排序键、紧凑分隔符、禁止 NaN）。这与 Git blob 或统一换行后的文本摘要不是同一种口径。

## 重放方法

先用 `git archive 577343ccb50670e1f2ea1d38aa9e18a865c08fa7` 提取纯提交到独立 launcher 目录。以下命令在仓库根目录执行；`--output-directory` 应为尚不存在的新目录。源码归档与所有输入 SHA 由 runner 核验，不能使用工作区未提交代码代替。

```powershell
C:/Python314/python.exe .codex_tmp/pose-association-score-impact-20261024/launcher-577343c/scripts/build_card_execution_matrix_v5.py replay --repository . --source-commit 577343ccb50670e1f2ea1d38aa9e18a865c08fa7 --manifest tests/fixtures/card-execution-inputs-v5.json --output-directory .codex_tmp/pose-association-score-impact-20261024/actual-replay
C:/Python314/python.exe .codex_tmp/pose-association-score-impact-20261024/launcher-577343c/scripts/build_card_execution_matrix_v5.py build --repository . --results .codex_tmp/pose-association-score-impact-20261024/actual-replay/results.json --runtime-checkout .codex_tmp/pose-association-score-impact-20261024/actual-replay/checkout --output .codex_tmp/pose-association-score-impact-20261024/actual-replay/rebuilt-matrix.json
```

两个命令均实际完成并退出 0。比较脚本位于 `.codex_tmp/pose-association-score-impact-20261024/compare_impact.py`，其 SHA 已写入小型收据。这是对已观察开发材料的修复影响验证，不是新 heldout 准确率试验。13 组仍是原来的 5 个实际媒体、6 个姿态导出来源；不因重放次数增加视频数量，也不将这次验证描述为新的模型推理。
