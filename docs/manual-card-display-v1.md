# 人工逐卡窗口与测量腕的前端契约

统一报告仍使用 `card-execution-v1.0.0`。本变更没有增加生产路由、自动步伐阶段识别或击球十阶段定位。原卡等级始终为空；复核和证据不足不补 E 或零分。

## FS 独立人工窗口

`scripts/build_fs_card_window_display_contract.py` 从 `fs_card_windows.supported_rules()`、原卡目录及注册测量函数生成 35 张卡的条件、来源摘要与关节并集。其中 20 张登记了视觉代理，15 张需要人工复核。登记不等于实际执行或真实视频验证。

运行版本为 `fs-card-windows-v1.0.0`，测量版本为 `card-kinematics-v1.0.0`。前端评分读取器要求同时存在完整的 `source_window_annotation` 和 `source_window_evidence`：

- 人工来源版本、语义、标注文档摘要、原帧闭区间、时间不确定性以及视频和球员绑定必须一致。
- `source_frames`、`source_pts` 和 `source_frame_bindings` 必须逐帧对应整个闭区间。原始 PTS、时基、视频摘要、选人段和相机段均保留，不通过删除边界帧缩小分母。
- 原始事件质量记录的时间范围必须覆盖全部窗口帧；前端重新核对原始轨迹来源、身份警告和阻断标记。无关关节警告只有在原始诊断完整、关节名已注册且与所需关节不相交时才能排除。其他明确轨迹的质量观测不冒充所选球员的质量证据。
- 条件必须与生成契约完全一致，重算布尔条件、完成度等级和分数；人工规则及缺少完整步周期的四张卡不能因附带分数或 `step_cycle_bound` 标志而得分。

完整周期缺失的四张卡由实际动态踝指标依赖生成，不由前端补齐周期。前端保留原始证据用于展示和备份；它不会从报告里的姿态摘要重新运行模型，也不声称浏览器已重新解码原视频。

## GS 测量腕

人工观察协议为 `manual-racket-side-observation-v1.0.0`。`measurement_wrist_side` 只称为测量腕；`observed_racket_hands` 描述采样画面的单手或双手观察。GS02 必须观察到双手，不能改称优势手或连续持拍证明。

读取器保留并比对 `source_annotation.racket_side_observation` 和 `window.racket_side_binding` 两份完整记录，验证动作、技术、阶段、视频、球员、PTS、原帧、标注文档、选人段和相机段。样本来源有效与阶段连续适用分别处理：只有后端明确核验阶段连续性且采样绑定一致，测量腕才可用于腕速指标；采样端点相同不会自行把连续性失败改为成功。旧 GS 报告没有测量腕观察时，已有非腕侧几何代理仍兼容。

## 验证样例及边界

`fs-card-window-contract-unit.json` 来自明确标注的受控重复姿态和单测质量上下文，由实际后端重新计算，得到 16 个代理、15 个复核、4 个周期缺失。这是契约单测，不是真实技术或视频验证，也没有媒体核验标志。

`gs-racket-side-real.json` 保留三个正式原视频 GS artifact 输出的全部 15 个实例：1 个代理、5 个复核、9 个证据不足；三个腕速指标都没有分数。为了单独测试统一读取器，fixture 明确记录了将独立服务版本替换为测试统一外壳的操作；这不能冒称 HTTP 端到端验证。上游原始报告 SHA 保留供定位，数值和证据不修改。

`fs-card-window-real.json` 从正式统一 artifact 报告摘取三个真实原视频人工 development 窗口。FS01-M01 为证据不足，FS01-M04 和 FS09-M02 为复核，全部没有分数；窗口分别保留 6、6、11 帧。原始左右交换或关节跳变警告仍在，不能把这些人工候选称为已完成技术行为。此摘录同样不声称 HTTP 验证。

后续独立 HTTP 核验记录在本地证据目录 `.codex_tmp/fs-card-windows-release-20261022/actual-api-validation.json` 和 `actual-api-card-report.json`：正式请求返回 200，完整 298 行与服务 artifact 报告结构一致，仍是两个复核和一个证据不足。本批历史 fixture 的来源声明保持不变。质量事件覆盖使用与后端相同的已核验原记录毫秒时间；小数 PTS 与毫秒取整只允许既有 0.51 毫秒误差，不因取整误差丢弃首尾质量覆盖。

定向测试包括错来源、错阶段、删首尾或中间帧、篡改 PTS、跨身份或相机分段、短质量覆盖、伪造无关关节、删除警告后伪造评分、四张周期卡补标志、腕侧证明错绑及旧 GS 兼容。显示与导出保留人工不确定性、非专家语义和完整原始绑定；备份再次读取仍重新校验评分。

可运行 `python scripts/build_fs_card_window_display_contract.py --check` 校验生成契约。前端定向测试为 `manual-card-evidence.test.mjs` 与 `manual-card-export.test.mjs`；后者必须使用本批最小 `report-export.ts` 改动。现有 FS 时间窗、FS03/FS04 周期、GS 准备期读取器测试共同防止回归。
