# RallyMate 评分逻辑梳理、GitHub 代码对照审计与 Codex 实现优化指南

本指南基于上传的 10 份核心规格 Word 文档（涵盖底层感知数据字典、Lv1–Lv5 技术打分权重体系、GS/FS/DS/NS/OS/SS/TS 技术与战术系统，以及下一步产品规划），并与 GitHub 仓库 [ieomn/RallyMate](https://github.com/ieomn/RallyMate) 的最新主分支代码与审计基线（参见 [SCORING_FEASIBILITY_PLAN.md](https://github.com/ieomn/RallyMate/blob/main/SCORING_FEASIBILITY_PLAN.md) 与 [01-implementation-differences.md](https://github.com/ieomn/RallyMate/blob/main/docs/scoring-reference-20261004/01-implementation-differences.md)）进行逐模块、逐公式、逐阶段的交叉比对。文档最后提供了可直接复制给本机 Codex 执行的分阶段重构指令与架构蓝图。

## 一、 Word 文档核心评分逻辑全景梳理

### 1. 四层金字塔评分架构

整套 Word 文档构建了一个从底层视觉感知到顶层等级评定的 **四层递进式架构**：

1. **第一层：8 大底层感知与衍生数据系统（《基于视频评级的软件开发需要的数据.docx》）**
   - **J（Joint 关节系统）**：定义了 `J001–J168` 细粒度人体关键点，包括头部（`J001–J011`）、肩带（`J030–J035`）、胸腔与脊柱（`J050–J058`）、骨盆与身体重心（`J070–J077`，其中 `J077` 为身体重心肚脐位置）、左右上肢含掌心与指根（`J100–J139`）、左右下肢含足跟/足中/前脚掌/大脚趾/脚尖中心（`J140–J179`）。
   - **L（Length 长度系统）**：定义了 `L010–L116` 共 11 组人体骨骼长度与比例指标（全部由 `J` 系统自动计算），既用于跨身高归一化，也用于《下一步.docx》要求的**用户等比例理想人体模型构建**。
   - **RK（Racket 球拍系统）**：定义了拍头（`RK001–RK005`，`RK005` 为甜区中心）、三角区（`RK010–RK013`，`RK013` 为单反与切削非持拍手扶拍核心判定点）、拍柄（`RK020–RK023`）。
   - **BALL（Ball 球体系统）**：定义了球位置（`BALL-010–015` 含三维坐标 X/Y/Z）、球运动（`BALL-020–027` 速度、方向向量、轨迹与曲率）、球旋转（`BALL-030–035` 总转速、上旋、切削下旋、侧旋与旋转轴）。
   - **S（Spatial 空间系统）**：定义了身体空间（`S010–S013`）、球空间（`S020–S025`）、球拍空间（`S030–S035`）、击球空间（`S040–S045`）、ITF 标准球场坐标（`S050–S059`）、球网空间（`S060–S069`）、球员球场位置（`S070–S079`）与落点空间（`S080–S089`）。
   - **V（Vision 视觉系统）**：定义了头部跟踪（`V010–V013`）、眼部跟踪（`V020–V022`）与盯球系统（`V030–V034`，含击球前头部保持时间 `V034`）。
   - **K（Kinematics 运动学系统）**：定义了整体运动（`K010–K017`）、躯干（`K020–K027`）、左右上肢（`K030–K047`）、左右下肢（`K050–K067`）、球拍运动（`K070–K077`）、旋转运动学（`K080–K087`）与动力链传递系统（`K090–K097`）。
   - **T / TIME（Timing 时序系统）**：定义了 `T001–T020` 共 20 个时序指标，覆盖反应时间、分腿垫步时机、阶段启动与持续时间、人-拍-球同步性、提前/滞后判断、击球窗口与恢复时间。

2. **第二层：8 大技术与战术系统的时序阶段状态机（M01–M10 / M01–M05）**
   - **GS 底线基础技术（5 项）**：`GS-01` 正手、`GS-02` 双反、`GS-03` 单反采用 **动力链 10 阶段模型**（`M01 盯球准备` $\rightarrow$ `M02 启动移动` $\rightarrow$ `M03 转肩引拍` $\rightarrow$ `M04 蹬地` $\rightarrow$ `M05 转髋` $\rightarrow$ `M06 转胸` $\rightarrow$ `M07 加速挥拍` $\rightarrow$ `M08 击球` $\rightarrow$ `M09 收拍` $\rightarrow$ `M10 恢复准备`）；而 `GS-04` 反手切削与 `GS-05` 正手切削采用 **切削专用 10 阶段模型**（`M04 重心支撑` $\rightarrow$ `M05 高位引拍` $\rightarrow$ `M06 拍面打开` $\rightarrow$ `M07 切削挥拍` $\rightarrow$ `M08 切削击球` $\rightarrow$ `M09 低位切削收拍` $\rightarrow$ `M10 恢复准备`）。
   - **FS 步伐移动技术（10 项）**：`FS01` 分腿垫步 $\rightarrow$ `FS02` 第一步启动 $\rightarrow$ `FS03` 交叉步 / `FS04` 并步 $\rightarrow$ `FS05` 小碎步调整 $\rightarrow$ `FS06` 开放式 / `FS07` 闭合式 / `FS08` 跨步支撑 $\rightarrow$ `FS09` 制动急停 $\rightarrow$ `FS10` 击球后回位。每个 FS 子技术包含 **5 个阶段行为（M01–M05）**，并在 GS/DS/NS/OS/SS 的对应阶段被嵌套调用。
   - **SS 发球系统（5 项）**：`SS01` 平击、`SS02` 切削、`SS03` 上旋（Kick Serve）、`SS04` 二发稳定共用 **发球 10 阶段模型**（`M01 站位` $\rightarrow$ `M02 握拍准备` $\rightarrow$ `M03 抛球准备(未离手)` $\rightarrow$ `M04 抛球释放(离手上升)` $\rightarrow$ `M05 球拍引拍` $\rightarrow$ `M06 Trophy Pose蓄力` $\rightarrow$ `M07 蹬地上伸` $\rightarrow$ `M08 上方击球` $\rightarrow$ `M09 随挥落地` $\rightarrow$ `M10 恢复准备`）；`SS05` 发球落地恢复则作为独立 10 阶段模块被 `SS01–SS04` 共用调用。
   - **RS 接发系统（5 项）**：`RS01` 正手接发、`RS02` 双反接发、`RS03` 单反接发、`RS04` 反手切削接发、`RS05` 正手切削接发。
   - **NS 网前系统（5 项）**：`NS01` 正手截击、`NS02` 反手截击、`NS03` 半截击（球刚落地弹起处理）、`NS04` 低位截击（未落地低球）、`NS05` 高位截击（胸肩部短动作截击），均采用 **网前 10 阶段模型**（强调取消完整引拍、短支撑、短促前送/下沉/上举、短收拍）。
   - **DS 防守系统（6 项）**：`DS01` 正手挡回、`DS02` 反手挡回、`DS03` 跑动救球、`DS04` 挑高防守、`DS05` 低位切削防守、`DS06` 追身防守，均采用 **防守 10 阶段模型**（强调压迫识别、应急支撑、取消完整引拍、借力封挡/挑高/削挡、防守短收拍）。
   - **OS 高压系统（4 项）**：`OS01` 常规高压、`OS02` 后退高压、`OS03` 跳起高压、`OS04` 被动/应急高压，采用 **高压 10 阶段模型**。
   - **TS 战术系统（8 项共 64 子项）**：`TS01` 发球战术、`TS02` 接发战术、`TS03` 底线相持战术、`TS04` 进攻转换、`TS05` 防守转换、`TS06` 上网与网前战术、`TS07` 高压终结战术、`TS08` 落点与线路战术（空间落点层，供 `TS01–TS07` 共同调用）。

3. **第三层：Lv1–Lv5 动态权重矩阵与二选一互斥规则（《技术打分.docx》）**
   - 不同级别下，8 大技术系统的权重随能力进阶动态迁移（从 Lv1 的 `GS 70% + FS 30%`，逐步过渡到 Lv5 的全系统均衡与高战术权重 `GS 18% + FS 14% + SS 15% + RS 12% + DS 10% + NS 8% + OS 8% + TS 15%`）。
   - **反手路线互斥规则**：`GS-02`（双反）与 `GS-03`（单反）、`RS02`（双反接发）与 `RS03`（单反接发）严格执行**二选一互斥计分**，不重复计分。

4. **第四层：大级别晋级一票否决门槛（Gatekeeper Rules）**
   - **初级（Lv1–Lv2）晋级中级（Lv3）硬门槛**：必须**同时满足** `1. 非持拍手`、`2. 击球连贯`、`3. 身体协调`。
   - **中级（Lv3）晋级高级（Lv4–Lv5）硬门槛**：必须**同时满足** `1. 击球点是否合理（位置和时机 / 节奏把控能力）`、`2. 发球动作是否流畅`、`3. 球速转速和对拉落点是否一致（控球能力）`。

### 2. Lv1–Lv5 技术系统权重演进对照表

| 技术系统 | 编号 | Lv1 初学 | Lv2 入门 | Lv3 业余稳定 | Lv4 高水平业余 | Lv5 竞技级 | GitHub 代码现状 ([engine.ts](https://github.com/ieomn/RallyMate/blob/main/scoring-demo-web/app/scoring/engine.ts)) |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | :--- |
| 底线基础技术 | GS | 70% | 55% | 35% | 25% | 18% | 固定死为 `0.70`（仅相当于 Lv1 权重），且 F2 实测指标为 0 项（参见 [03-gs-audit.md](https://github.com/ieomn/RallyMate/blob/main/docs/scoring-reference-20261004/03-gs-audit.md)） |
| 步伐移动系统 | FS | 30% | 25% | 20% | 17% | 14% | 固定死为 `0.30`（仅相当于 Lv1 权重），50 项指标仅实现 13 项 F2（参见 [02-fs-audit.md](https://github.com/ieomn/RallyMate/blob/main/docs/scoring-reference-20261004/02-fs-audit.md)） |
| 发球系统 | SS | 0% | 8% | 12% | 14% | 15% | 未实现（0 项 F2 指标，未纳入引擎权重，参见 [01-implementation-differences.md](https://github.com/ieomn/RallyMate/blob/main/docs/scoring-reference-20261004/01-implementation-differences.md)） |
| 接发系统 | RS | 0% | 5% | 10% | 12% | 12% | 未实现（0 项 F2 指标，未纳入引擎权重，参见 [01-implementation-differences.md](https://github.com/ieomn/RallyMate/blob/main/docs/scoring-reference-20261004/01-implementation-differences.md)） |
| 网前系统 | NS | 0% | 4% | 6% | 8% | 8% | 未实现（0 项 F2 指标，未纳入引擎权重，参见 [01-implementation-differences.md](https://github.com/ieomn/RallyMate/blob/main/docs/scoring-reference-20261004/01-implementation-differences.md)） |
| 防守系统 | DS | 0% | 3% | 8% | 10% | 10% | 未实现（0 项 F2 指标，未纳入引擎权重，参见 [01-implementation-differences.md](https://github.com/ieomn/RallyMate/blob/main/docs/scoring-reference-20261004/01-implementation-differences.md)） |
| 高压系统 | OS | 0% | 0% | 4% | 5% | 8% | 未实现（0 项 F2 指标，未纳入引擎权重，参见 [01-implementation-differences.md](https://github.com/ieomn/RallyMate/blob/main/docs/scoring-reference-20261004/01-implementation-differences.md)） |
| 战术系统 | TS | 0% | 0% | 5% | 9% | 15% | 未实现（0 项 F2 指标，未纳入引擎权重，参见 [01-implementation-differences.md](https://github.com/ieomn/RallyMate/blob/main/docs/scoring-reference-20261004/01-implementation-differences.md)） |

## 二、 GitHub 代码尚未实现的评分逻辑清单（Unimplemented Features）

对照 [ieomn/RallyMate](https://github.com/ieomn/RallyMate) 仓库代码，以下核心模块和评分逻辑目前处于**完全未实现**或**仅注册卡片 Schema 但无事件/特征计算实现**的状态：

### 1. 技术与战术系统实现缺口（8 大系统仅局部实现 FS 的 3 个子项）

| 系统与模块 | Word 文档规格要求 | GitHub 代码当前状态 | 缺失的核心逻辑与文件定位 |
| :--- | :--- | :--- | :--- |
| **GS 底线基础系统**（`GS01–GS05`） | 5 个子技术、50 个阶段（M01–M10）、248 项正式指标 | **0 / 248 项实现**（参见 [03-gs-audit.md](https://github.com/ieomn/RallyMate/blob/main/docs/scoring-reference-20261004/03-gs-audit.md)） | 虽然在 [card_rules_v1.json](https://github.com/ieomn/RallyMate/blob/main/src/rallymate_scoring/data/card_rules_v1.json) 和 [technique_metrics.json](https://github.com/ieomn/RallyMate/blob/main/src/rallymate_scoring/technique_registry.py) 中注册了卡片文本，但所有 GS 指标的 `event_localization.status` 均为 `not_implemented`（参见 [01-implementation-differences.md](https://github.com/ieomn/RallyMate/blob/main/docs/scoring-reference-20261004/01-implementation-differences.md) 中的 `GS-IMPL-01` 至 `GS-IMPL-06`），没有任何一个 GS 指标进入 [indicator-scoring-requirements-pose-wave-v2.json](https://github.com/ieomn/RallyMate/blob/main/indicator-scoring-requirements-pose-wave-v2.json)。 |
| **FS 步伐系统其余 7 项**（`FS03–FS08`, `FS10`） | 10 个子技术、50 个阶段行为指标 | **仅实现 13 / 50 项**（参见 [02-fs-audit.md](https://github.com/ieomn/RallyMate/blob/main/docs/scoring-reference-20261004/02-fs-audit.md)） | 目前仅在 [fs01_fs02_features.py](https://github.com/ieomn/RallyMate/blob/main/src/rallymate_features/fs01_fs02_features.py) 和 [fs09_features.py](https://github.com/ieomn/RallyMate/blob/main/src/rallymate_features/fs09_features.py) 中实现了 `FS01`（M02–M05 共 4 项）、`FS02`（M02–M05 共 4 项）、`FS09`（M01–M05 共 5 项）共 13 个 F2 指标；`FS01-M01`、`FS02-M01` 以及 `FS03 交叉步`、`FS04 并步`、`FS05 小碎步`、`FS06 开放式支撑`、`FS07 闭合式支撑`、`FS08 跨步支撑`、`FS10 击球后回位` 共 37 项均未实现。 |
| **SS 发球系统**（`SS01–SS05`） | 4 类发球各 10 阶段 + `SS05` 落地恢复 10 阶段 | **0 项实现**（参见 [01-implementation-differences.md](https://github.com/ieomn/RallyMate/blob/main/docs/scoring-reference-20261004/01-implementation-differences.md) `CORE-11`） | 缺少抛球释放点检测（`SS01-M04`）、Trophy Pose 识别（`SS01-M06`）、平击/切削/上旋/二发稳定分类器（`SS01–SS04`）以及发球落地恢复模块（`SS05`）。 |
| **RS 接发系统**（`RS01–RS05`） | 5 类接发技术（正手、双反、单反、反手切削、正手切削） | **0 项实现**（参见 [04-visual-audit.md](https://github.com/ieomn/RallyMate/blob/main/docs/scoring-reference-20261004/04-visual-audit.md)） | 仅在视觉审计文档中提及 `VIS-RT`，代码中无接发事件分割、无接发反应时间（`T001`）与压缩引拍判定实现；且用户本地 Word 文档集中缺失 `3.RS接发.docx`。 |
| **NS 网前系统**（`NS01–NS05`） | 正截、反截、半截击（刚弹起）、低位截击（未落地）、高位截击 | **0 项实现**（参见 [01-implementation-differences.md](https://github.com/ieomn/RallyMate/blob/main/docs/scoring-reference-20261004/01-implementation-differences.md) `CORE-11`） | 无网前站位检测（`S073`）、无球落地反弹时刻与触球时刻差值判定（无法区分 `NS03 半截击` 与 `NS04 低位截击`）。 |
| **DS 防守系统**（`DS01–DS06`） | 正反手挡回、跑动救球、挑高防守、低位切削防守、追身防守 | **0 项实现**（参见 [01-implementation-differences.md](https://github.com/ieomn/RallyMate/blob/main/docs/scoring-reference-20261004/01-implementation-differences.md) `CORE-11`） | 无来球压迫度判定（`DS01-M01` 时间/距离/重心失控识别）、无“取消完整引拍/短收拍”反向评分规则（正常 GS 要求完整随挥，而 DS 要求短收拍制动）。 |
| **OS 高压系统**（`OS01–OS04`） | 常规高压、后退高压、跳起高压、被动/应急高压 | **0 项实现**（参见 [01-implementation-differences.md](https://github.com/ieomn/RallyMate/blob/main/docs/scoring-reference-20261004/01-implementation-differences.md) `CORE-11`） | 无高球轨迹触发器、无后退步伐定位（`OS02`）、无双脚离地空中触球检测（`OS03`）与失衡应急高压识别（`OS04`）。 |
| **TS 战术系统**（`TS01–TS08`） | 8 大战术模块 × 8 子项 = 64 个战术识别子项 | **0 项实现**（参见 [SCORING_FEASIBILITY_PLAN.md](https://github.com/ieomn/RallyMate/blob/main/SCORING_FEASIBILITY_PLAN.md)） | 当前仓库为单人 Pose-only 架构，完全未实现基于球落点（`S080–S089`）、球方向（`BALL-025/026`）与对手站位的战术意图识别状态机。 |

### 2. 感知系统与等级评定逻辑实现缺口

- **感知层缺口（Pose-only 局限）**：根据 [SCORING_FEASIBILITY_PLAN.md](https://github.com/ieomn/RallyMate/blob/main/SCORING_FEASIBILITY_PLAN.md)，当前管线仅运行在 **单人 2D Pose-only** 模式下（COCO-17 / Halpe-26 / WholeBody-133 关键点）。以下底层数据系统尚未在生产特征管线中打通：
  - **`BALL` 球体系统**：缺少球三维坐标（`BALL-013` 高度 Z）、球速（`BALL-020`）、飞行轨迹曲率（`BALL-027`）以及基于马格努斯方程的转速估算（`BALL-030–035` 上旋/下旋/侧旋）。
  - **`RK` 球拍关键点系统**：虽然有 [racket\_geometry.py](https://www.google.com/search?q=https://github.com/ieomn/RallyMate/blob/main/src/rallymate_scoring/racket_geometry.py) 接口契约，但未实现 `RK001–RK023`（尤其是甜区 `RK005`、三角区 `RK013`、拍面法向量 `S032`）的实时视觉检测与跟踪。
  - **`S05–S08` 球场与球网空间映射**：缺少球场单应性矩阵（Homography）标定与 ITF 坐标系（`S050–S059`）转换，因此无法计算真实距底线距离（`S071`）、距网距离（`S073`）、过网高度（`S066`）和落点深浅/宽度（`S082–S089`）。
  - **`V` 视觉盯球系统**：因为缺乏 `BALL-010` 与头部/眼部朝向的空間交汇计算，所有 M01 阶段的“观察来球/持续盯球”（如 `FS01-M01`、`FS02-M01`、`GS01-M01`）全部被跳过。
- **等级晋级门槛与业务模块缺口**：
  - **两道晋级硬门槛未实现**：《技术打分.docx》规定的“初级 $\rightarrow$ 中级三项同时满足门槛（非持拍手、击球连贯、身体协调）”与“中级 $\rightarrow$ 高级三项门槛（击球点合理性、发球流畅度、球速转速与落点一致性）”在 [scoring.py](https://github.com/ieomn/RallyMate/blob/main/src/rallymate_scoring/scoring.py) 与 [engine.ts](https://github.com/ieomn/RallyMate/blob/main/scoring-demo-web/app/scoring/engine.ts) 中完全没有对应逻辑。
  - **单反/双反二选一动态权重路由未实现**：代码中未实现根据用户主反手类型（`GS-02` vs `GS-03`、`RS02` vs `RS03`）动态切换并归一化权重的逻辑。
  - **F3/F4 校准闭环未完成**：当前 13 个已实现的 FS 指标全部停留在 `F2`（特征可计算但未经教练真值校准），`F4`（正式获批参与等级计分）指标数量为 **0**（参见 [02-fs-audit.md](https://github.com/ieomn/RallyMate/blob/main/docs/scoring-reference-20261004/02-fs-audit.md)）。
  - **《下一步.docx》规划的扩展业务未实现**：训练自动剪辑（剔除捡球/喝水等无效片段）、比赛技术统计（一二发得分率/Ace、Return Ace、非受迫性失误）、基于用户 `L` 系统的等比例理想人体模型对比指导、3.0 分界比赛心理评分、关节灵活度与运动康复模块均未实现。

## 三、 现有代码实现与 Word 文档的出入及逻辑错误（Discrepancies & Bugs）

根据仓库内的代码实现与 [01-implementation-differences.md](https://github.com/ieomn/RallyMate/blob/main/docs/scoring-reference-20261004/01-implementation-differences.md) 的审计记录，现有代码存在以下 **12 处核心出入与逻辑错误**：

### 1. 评分引擎、权重与等级映射层面的错误

| 错误/出入编号 | 涉及代码文件 | 代码当前实现逻辑 | Word 文档规范要求 | 冲突性质与错误影响 |
| :--- | :--- | :--- | :--- | :--- |
| **ERR-01**<br>（对应 `CORE-01` / `CORE-04`） | [scoring-demo-web/app/scoring/engine.ts](https://github.com/ieomn/RallyMate/blob/main/scoring-demo-web/app/scoring/engine.ts) | 硬编码 `DIMENSION_WEIGHTS = [0.40, 0.25, 0.20, 0.15]` 和 `MODULE_WEIGHTS = { GS: 0.70, FS: 0.30 }`，且模块内指标采用等权平均（Equal-weighted average）。 | 《技术打分.docx》定义了 Lv1–Lv5 随等级变化的 8 大系统权重矩阵（GS 从 70% 降至 18%，FS 从 30% 降至 14%），且每个系统内部子技术有精确占比（如 Lv1 中 `FS05 小碎步 25%`、`FS01 20%`、`FS03 5%`）。 | **严重逻辑错误**：前端引擎把 Lv1 的大类占比（70/30）当成了全局固定权重，且抹平了子技术内部权重差异（把占比 25% 的 `FS05` 和占比 5% 的 `FS03` 等权平均）。 |
| **ERR-02**<br>（对应 `CORE-02`） | [scoring-demo-web/app/scoring/engine.ts](https://github.com/ieomn/RallyMate/blob/main/scoring-demo-web/app/scoring/engine.ts) & [technical_review_rules.json](https://github.com/ieomn/RallyMate/blob/main/src/rallymate_scoring/data/technical_review_rules.json) | 使用 `gradeFor(score)` 按 `90 / 80 / 70 / 60` 硬切 `A / B / C / D / E` 五档，或按指标达标率 `<40% / 40-59% / 60-74% / 75-89% / >=90%` 映射 Lv1–Lv5。 | 《技术打分.docx》定义的是 **3 大级 5 小级（Lv1 初学、Lv2 入门、Lv3 业余稳定、Lv4 高水平业余、Lv5 竞技级）**，各级别考察的技术范围与权重完全不同，且必须通过两道硬门槛（Gatekeepers）才能跨大级晋升。 | **概念混淆错误**：代码将“单个动作的质量分档（A–E）”与“球员综合网球水平等级（Lv1–Lv5）”混为一谈，未实现跨级门槛拦截。 |
| **ERR-03**<br>（对应 `CORE-03`） | [scoring-demo-web/app/scoring/engine.ts](https://github.com/ieomn/RallyMate/blob/main/scoring-demo-web/app/scoring/engine.ts) | Demo 模式下维度得分由 `scenario.baseScore` + 文本匹配加分 + ID 哈希扰动（deterministic ID hash perturbation）合成，再用 `0.85 * required_coverage + 0.15 * enhanced_coverage` 缩放。 | 所有技术得分必须由 `J/L/RK/BALL/S/V/K/T` 的真实物理与时序特征驱动计算。 | **伪评分逻辑**：哈希扰动与覆盖率加权（`0.85/0.15` 及 [technique_assessment.py](https://github.com/ieomn/RallyMate/blob/main/src/rallymate_scoring/technique_assessment.py) 中的 `0.70/0.30`）属于工程占位符，会把“关键点可见度高（Evidence Readiness）”误当成“动作技术好”。 |
| **ERR-04**<br>（对应 `FS-COVERAGE-GATE` / `CORE-13`） | [src/rallymate_scoring/scoring.py](https://github.com/ieomn/RallyMate/blob/main/src/rallymate_scoring/scoring.py) | 工程控制设置了特征有效率门槛 `0.50`、事件运动学覆盖率门槛 `0.75`、依赖覆盖率 `0.65`、就绪门槛 `0.72`（参见 [01-implementation-differences.md](https://github.com/ieomn/RallyMate/blob/main/docs/scoring-reference-20261004/01-implementation-differences.md)）。 | 原始 GS（248 项）与 FS（34 项）规范明确要求数据质量底线为 **`>= 70%` 有效关键点帧（Valid Keypoint Frames）**（参见 [02-fs-audit.md](https://github.com/ieomn/RallyMate/blob/main/docs/scoring-reference-20261004/02-fs-audit.md) 与 [03-gs-audit.md](https://github.com/ieomn/RallyMate/blob/main/docs/scoring-reference-20261004/03-gs-audit.md)）。 | **阈值不一致**：代码中分散了 `0.50 / 0.65 / 0.72 / 0.75` 多套硬编码阈值，与文档统一的 `0.70` 有效帧率标准不一致。 |
| **ERR-05**<br>（对应 `CORE-14`） | [src/rallymate_scoring/technique_assessment.py](https://github.com/ieomn/RallyMate/blob/main/src/rallymate_scoring/technique_assessment.py) | `CONTACT_POLICY_VERSION` 将正向特征覆盖率检查（Positive coverage check）严格限制在 `contact_window_proxy` 或 `impact_window`（触球窗口）内。 | Word 文档中，每个技术有 10 个阶段（M01–M10），其中 M01–M07（准备、启动、引拍、蹬地、转髋、转胸、挥拍）和 M09–M10（收拍、恢复）均发生在触球窗口之外。 | **时间窗口截断错误**：仅检查触球窗口会导致引拍（M03）、垫步启动（M01–M02）、收拍恢复（M09–M10）的阶段证据被错误丢弃或降权。 |
| **ERR-06**<br>（对应 `CORE-12` / `FS-PLANS-STALE`） | [indicator-scoring-requirements-pose-wave-v2.json](https://github.com/ieomn/RallyMate/blob/main/indicator-scoring-requirements-pose-wave-v2.json) | `metric-measurement-plans.json` 引用的契约版本停留在 `pose-wave-2026-08-21.8`，而 `indicator-scoring-requirements-pose-wave-v2.json` 已升级为 `2026-10-01.2`（参见 [01-implementation-differences.md](https://github.com/ieomn/RallyMate/blob/main/docs/scoring-reference-20261004/01-implementation-differences.md)）。 | 测量计划与指标需求契约必须保持版本同步。 | **契约版本断层**：导致测量计划与最新 v2 指标需求存在版本失配（Stale plans）。 |

### 2. 已实现的 13 项 FS 特征算法与 Word 文本逻辑的具体出入

在目前已实现的 [fs01_fs02_features.py](https://github.com/ieomn/RallyMate/blob/main/src/rallymate_features/fs01_fs02_features.py)、[fs09_features.py](https://github.com/ieomn/RallyMate/blob/main/src/rallymate_features/fs09_features.py) 及 [indicator-scoring-requirements-pose-wave-v2.json](https://github.com/ieomn/RallyMate/blob/main/indicator-scoring-requirements-pose-wave-v2.json) 中，存在以下 6 处与《2.FS 步伐（优先级高）.docx》文本逻辑的偏差（对应 [01-implementation-differences.md](https://github.com/ieomn/RallyMate/blob/main/docs/scoring-reference-20261004/01-implementation-differences.md) 中的专项审计项）：

1. **`FS01-M02`（重心预加载）静态绝对值 vs 动态下压变化量偏差（`FS01-M02-DELTA`）**：
   - **文档逻辑**：《2.FS 步伐（优先级高）.docx》定义 `FS01-M02` 为“身体重心下压，双膝和踝关节进入弹性准备状态”，强调的是从观察姿态到预加载的**重心下压变化量（Delta）与膝角屈曲增量**。
   - **代码实现**：[indicator-scoring-requirements-pose-wave-v2.json](https://github.com/ieomn/RallyMate/blob/main/indicator-scoring-requirements-pose-wave-v2.json) 抽取的特征是 `preload_ms` 时刻的静态帧特征（`hip_center_y_body`、`left_knee_flexion_deg`、`right_knee_flexion_deg`），未把重心下压位移量（$\Delta y$）和膝关节屈曲变化角（$\Delta \theta_{\text{knee}}$）作为核心判定特征。
2. **`FS01-M04`（双脚分开落地）尺度归一化分母偏差（`FS01-M04-SCALE`）**：
   - **文档逻辑**：《基于视频评级的软件开发需要的数据.docx》规定双脚站距 `L102 / S013` 由 `J146`（左前脚掌）与 `J166`（右前脚掌）计算，且应与肩宽 `L030`（或身高 `L110`）建立比例参考。
   - **代码实现**：`post_slowdown_stance_width_body` 使用躯干/身体尺度代理（body scale proxy）进行归一化，在相机透视畸变下与真实脚掌间距/肩宽比存在尺度偏差。
3. **`FS01-M05`（重心重新分配并准备启动）跨事件越界耦合（`FS01-M05-CROSS-EVENT`）**：
   - **文档逻辑**：《2.FS 步伐（优先级高）.docx》严格界定 `FS01` 的结束标志性动作为“双脚落地完成垫步，重心重新分配”，而“支撑脚发力、启动脚迈出”属于下一个独立模块 `FS02 第一步启动`。
   - **代码实现**：[indicator-scoring-requirements-pose-wave-v2.json](https://github.com/ieomn/RallyMate/blob/main/indicator-scoring-requirements-pose-wave-v2.json) 在 `FS01-M05` 中绑定了 `initiation_ms`（启动时刻）与 `body_center_speed_body_s`，将 `FS02` 的启动特征提前混入了 `FS01-M05`，破坏了 `FS01` 与 `FS02` 的阶段边界独立性。
4. **`FS02-M02`（重心向来球方向转换）来球目标方向缺失（`FS02-M02-TARGET`）**：
   - **文档逻辑**：《2.FS 步伐（优先级高）.docx》要求 `FS02` 根据**来球方向（`BALL-025`）**判断启动方向是否一致（调用 `BALL-010/020/025` 与 `S020`）。
   - **代码实现**：由于当前是 Pose-only 模式，代码中 `target_direction_alignment_error_deg` 无法获取真实的 `BALL-025` 来球向量，只能使用人体后续移动轨迹方向作为伪目标方向（Self-trajectory proxy）——这意味着**如果球员判断错方向跑反了，代码也会认为他“启动方向与目标方向一致”**！
5. **`FS09-M01 / M02` 事件时间窗缺失与 `FS09-M05` 跨事件耦合（`FS09-M05-CROSS-EVENT`）**：
   - **文档逻辑**：`FS09 制动稳定` 有明确的阶段边界（开始于“身体惯性需要被控制”，结束于“身体惯性降低并重新建立稳定支撑”），且文档明确强调：“**不等于回位：FS09 只负责制动稳定，FS10 负责回到场地位置**”。
   - **代码实现**：在 [indicator-scoring-requirements-pose-wave-v2.json](https://github.com/ieomn/RallyMate/blob/main/indicator-scoring-requirements-pose-wave-v2.json) 中，`FS09-M01` 和 `FS09-M02` 的绑定事件列表为空 `[]`，缺乏明确的相位触发器；同时 `FS09-M05` 混入了后续恢复阶段的跨事件支撑代理指标。
6. **2D 姿态代理与文档真实物理量的偏差（`FS-PROXY-LIMITS`）**：
   - **文档逻辑**：要求识别“双脚轻微离地（`FS01-M03`）”、“双脚分开落地（`FS01-M04`）”、“制动脚落地（`FS09-M02`）”。
   - **代码实现**：[fs01_fs02_features.py](https://github.com/ieomn/RallyMate/blob/main/src/rallymate_features/fs01_fs02_features.py) 使用 2D 像素空间中脚部关键点的垂直速度极值（`takeoff_proxy_ms`、`landing_proxy_ms`、`first_step_slowdown_proxy_ms`）代替真实的地面接触检测（Ground Contact Detection）。在单目斜视角视频中，脚步前后移动（Y轴深度变化）会被误投影为像素垂直高度变化，容易产生假阳性垫步或假落地事件。

## 四、 Word 文档自身存在的内部矛盾与漏洞（写入代码前必须修正）

在让 Codex 照着 Word 文档写代码之前，必须先修正 **Word 文档自身的 4 处内部定义冲突**，否则会导致代码 Schema 报错或总分无法凑齐 100 分：

| 文档内部问题 | 所在 Word 文档 | 具体矛盾描述 | 建议 Codex 采用的修正标准 |
| :--- | :--- | :--- | :--- |
| **DOC-BUG-01：Lv2 与 Lv3 二选一后权重总和不满 100%** | 《技术打分.docx》 | 在 `Lv2` 的 `GS` 表中：`GS-01(40%) + GS-02(30%) + GS-03(30%) + GS-04(0%) + GS-05(0%) = 100%`。但规则规定 **`GS-02` 与 `GS-03` 二选一不重复计分**，选定一种反手后，GS 内部权重之和仅剩 **`70%`**（最终总占比仅剩 `38.5%`，比规定的 `55%` 少了 `16.5%`）！同理，`Lv2 RS` 内部之和仅剩 **`70%`**，`Lv3 GS` 内部之和仅剩 **`75%`**，`Lv3 RS` 内部之和仅剩 **`75%`**（而在 Lv4 和 Lv5 中文档作者已将其修正为二选一后等于 100%）。 | 在评分引擎中引入**动态权重归一化算子（Active Weight Normalization）**：当用户选定单反或双反后，将该系统内激活项的内部权重按 $\tilde{w}_i = \frac{w_i}{\sum_{j \in \text{Active}} w_j}$ 自动归一化到 `100%`，再乘以该系统在当前 Level 的大类权重（如 Lv2 GS 的 `55%`）。 |
| **DOC-BUG-02：空间系统 `S013` 编号重复冲突** | 《基于视频评级的软件开发需要的数据.docx》 | 在 `S01 身体空间系统` 表中，连续两行使用了同一个编号 **`S013`**：第一行 `S013 = Base Width 双脚站距 (J146 J166)`，第二行 `S013 = Weight Distribution 重心分布 (J077 J146 J166)`。而在后续 GS/FS/DS/NS/OS/SS 文档中调用 `S013` 时，有时指站距，有时指重心分布。 | 在代码 Schema 中将两者拆分为独立特征键：`S013_BASE_WIDTH`（双脚站距，等价于 `L102`）与 `S014_WEIGHT_DISTRIBUTION`（重心相对于双脚支撑域的分布偏移比），并在兼容层中让旧规则的 `S013` 同时挂载这两个特征。 |
| **DOC-BUG-03：视觉系统 `V001` 未定义** | 《基于视频评级的软件开发需要的数据.docx》 vs 其余技术文档 | 在 GS/FS/DS/NS/OS/SS/TS 所有阶段调用矩阵中，均高频调用了 **`V001`**；但在《基于视频评级的软件开发需要的数据.docx》的 `V 视觉系统` 表中，编号直接从 `V010`（Head Direction）开始，漏掉了 `V001` 的定义。 | 在特征字典中正式补充注册 `V001: Gaze / Visual Attention Readiness（视觉注视与观察准备状态，由 J002/J004/J008/J009 综合判定）`。 |
| **DOC-BUG-04：`BALL` 子项在 `S` 系统中的引用别名不一致** | 《基于视频评级的软件开发需要的数据.docx》 | `BALL` 系统正式编号为 `BALL-010~015`、`BALL-020~027`、`BALL-030~035`；但在 `S02 球空间` 和 `S04 击球空间` 的“来源”列中，却写成了 `BALL-P01`、`BALL-P04`、`BALL-M01`、`BALL-M06`、`BALL-M07`。 | 建立标准别名映射表：`BALL-P01` $\mapsto$ `BALL-010`（球中心位置），`BALL-P04` $\mapsto$ `BALL-013`（球 Z 坐标/高度），`BALL-M01` $\mapsto$ `BALL-020`（球速），`BALL-M06` $\mapsto$ `BALL-025`（方向向量），`BALL-M07` $\mapsto$ `BALL-026`（球运动轨迹）。 |

## 五、 给本机 Codex 的详细开发建议与分阶段执行 Prompt

既然你本机上已经有 Codex 的项目文件与上下文记忆，建议不要让 Codex 一次性重写整个仓库，而是采用 **“先修底座契约与评分引擎 $\rightarrow$ 再打通 GS/FS 核心特征与事件分割 $\rightarrow$ 接着扩展球/球拍/球场多模态感知以解锁 SS/RS/NS/DS/OS/TS $\rightarrow$ 最后落地产品级训练/比赛/康复功能”** 的四阶段路线。

下面为你整理了 **4 个可直接复制粘贴给本机 Codex 的详细任务指令（Prompts）**，每个指令都精确到了你仓库中的现有文件路径、函数职责和 Word 文档对照逻辑：

### 阶段一：修复特征字典冲突与重构 Lv1–Lv5 动态权重及门槛评分引擎

你可以直接将以下文本发送给本机 Codex：

```text
请基于我们的 Word 规格文档（《基于视频评级的软件开发需要的数据.docx》与《技术打分.docx》），对仓库中的特征注册表与前后端评分引擎进行第一阶段重构：

1. 统一底层 8 大感知特征字典（新建或更新 `src/rallymate_features/schemas.py` 与 `contracts/`）：
   - 完整枚举 J (J001-J168)、L (L010-L116，由 J 自动计算)、RK (RK001-RK023)、BALL (BALL-010~035)、S (S010-S089)、V (V001, V010-V034)、K (K010-K097)、T (T001-T020) 的标准 ID。
   - 修复文档内部编号冲突：将 S013 拆分为 `S013_BASE_WIDTH`（双脚站距，J146-J166）和 `S014_WEIGHT_DISTRIBUTION`（重心分布，J077 相对 J146-J166 支撑区间位置），并在规则解析器中向后兼容 `S013`。
   - 补充注册 `V001`（Visual Attention / Gaze Readiness）。
   - 建立 BALL 别名兼容映射：`BALL-P01 -> BALL-010`, `BALL-P04 -> BALL-013`, `BALL-M01 -> BALL-020`, `BALL-M06 -> BALL-025`, `BALL-M07 -> BALL-026`。
   - 将数据质量有效关键点帧率门槛统一对齐到文档标准的 `>= 0.70`（替换目前分散在 `src/rallymate_scoring/scoring.py` 中的 0.50/0.65/0.72/0.75 等工程启发式阈值）。

2. 重构等级评定与动态权重引擎（新建 `src/rallymate_scoring/level_grading.py` 并同步更新前端 `scoring-demo-web/app/scoring/engine.ts`）：
   - 废除前端 `engine.ts` 中硬编码的 `MODULE_WEIGHTS = { GS: 0.70, FS: 0.30 }`、等权平均逻辑以及 `scenario.baseScore` 哈希扰动伪分数（对应 `01-implementation-differences.md` 中的 CORE-01 ~ CORE-04）。
   - 严格按照《技术打分.docx》实现 Lv1（初学）、Lv2（入门）、Lv3（业余稳定）、Lv4（高水平业余）、Lv5（竞技级）的五级权重配置表（包含 8 大系统大类权重 + 每个系统内部子技术权重）。
   - 实现“单反/双反二选一互斥与动态归一化”：提供参数 `backhand_style: Literal["two_handed", "one_handed"]`。当为 `two_handed` 时禁用 `GS-03` 和 `RS03`；当为 `one_handed` 时禁用 `GS-02` 和 `RS02`。针对 Lv2 和 Lv3 中二选一后内部权重之和为 70% 或 75% 的情况，在系统内部按 `w_i / sum(active_w)` 自动归一化为 100%，再乘以系统大类权重。
   - 实现两道跨大级晋级一票否决硬门槛（Gatekeepers）：
     * 初级 -> 中级（Lv2 -> Lv3）门槛检查器：必须同时满足 `non_dominant_hand_ok`（非持拍手：如 GS01-M03/M04/M05/M09、GS03-M01~M05 托拍三角区 RK013）、`stroke_continuity_ok`（击球连贯：T005/T007 相位顺序与节奏连续性）、`body_coordination_ok`（身体协调：K090-K097 动力链传递）。若任一不达标，最高等级锁定在 Lv2。
     * 中级 -> 高级（Lv3 -> Lv4）门槛检查器：必须同时满足 `contact_point_reasonable`（击球点位置 S040-S045 与时机 T008/T009/T012 合理）、`serve_fluency_ok`（发球动作 SS01-SS04 动力链流畅 T007/T013/T014）、`ball_control_consistency_ok`（球速 BALL-020、转速 BALL-030 与落点 S085/S086 离散度一致）。若任一不达标，最高等级锁定在 Lv3。
```

### 阶段二：修正已实现的 FS 特征偏差并打通 GS 底线 5 大技术与 FS 其余 7 项（Pose + Racket 基础层）

完成评分引擎重构后，可将以下第二阶段指令发送给 Codex，重点解决 `GS01–GS05` 和 `FS01–FS10` 的事件分割与特征实现：

```text
请根据《1.GS底线基础（优先级高）(4).docx》、《2.FS 步伐（优先级高）.docx》以及 `docs/scoring-reference-20261004/01-implementation-differences.md`，修复现有 FS 特征偏差并实现 GS 与 FS 的完整事件分割与特征提取：

1. 修复现有 13 项 FS 指标（`src/rallymate_features/fs01_fs02_features.py`、`fs09_features.py` 与 `indicator-scoring-requirements-pose-wave-v2.json`）的 5 处逻辑偏差：
   - `FS01-M02`（修复 FS01-M02-DELTA）：除了静态膝角与重心高度外，新增计算从 M01 观察帧到 M02 预加载帧的重心下压增量 `hip_center_y_drop_delta_body` 与双膝屈曲角度增量 `knee_flexion_increase_deg`。
   - `FS01-M04`（修复 FS01-M04-SCALE）：使用前脚掌关键点（J146, J166）计算落地双脚间距，并同时输出相对于肩宽 `L030`（J033-J034）和身高 `L110` 的归一化比值。
   - `FS01-M05`（修复 FS01-M05-CROSS-EVENT）：移除混入 `FS01-M05` 的 `FS02` 启动位移特征，严格将 `FS01-M05` 限制在“落地后双脚支撑重分配与静止/微调平衡”窗口内，将启动加速移交 `FS02-M02/M03`。
   - `FS02-M02`（修复 FS02-M02-TARGET）：当存在球轨迹数据（`BALL-025`）时，使用真实来球方向计算 `target_direction_alignment_error_deg`；若处于 Pose-only 模式无球轨迹，必须将该子项标记为 `proxy_mode: "self_motion_only"` 且不计入方向判断扣分，防止跑反方向被误判为高分。
   - `FS09`（修复 FS09-M01/M02 事件绑定与 FS09-M05 跨事件耦合）：在 `rallymate_events` 中为 `FS09-M01`（惯性方向判断）和 `FS09-M02`（制动脚落地 `braking_foot_strike_ms`）补齐显式事件时间戳，并将 `FS09-M05`（进入稳定控制）与 `FS10`（回位移动启动）解耦。

2. 实现 FS 其余 7 个子技术（`FS03 交叉步`、`FS04 并步`、`FS05 小碎步`、`FS06 开放式支撑`、`FS07 闭合式支撑`、`FS08 跨步支撑`、`FS10 击球后回位`）：
   - 复用并扩展 `src/rallymate_features/foot_crossing.py`，利用双脚 X 坐标交叉检测区分 `FS03 交叉步`（移动脚跨过支撑脚，步幅大）与 `FS04 并步`（双脚不交叉跟进，步幅小）。
   - 利用高频短步周期与脚踝微位移方差识别 `FS05 小碎步调整`。
   - 利用击球支撑建立时刻（M04）双脚连线矢量与底线/球网矢量的夹角及步幅，自动分类 `FS06 开放式支撑`、`FS07 闭合式支撑` 与 `FS08 跨步支撑`（跨步距离 > 1.5 倍肩宽且重心大幅下沉）。

3. 实现 `GS01–GS05` 的 10 阶段（M01–M10）时序分割器与特征提取（在 `src/rallymate_events/` 与 `src/rallymate_features/` 下新增 `gs_phase_segmentation.py` 和 `gs_features.py`）：
   - 修正 `src/rallymate_scoring/technique_assessment.py` 中的 `CORE-14` 限制（不要只评估 `contact_window_proxy`），支持对 M01–M10 全阶段窗口进行特征覆盖率与质量评估。
   - 实现两套不同的 M01–M10 状态机：
     * 抽球状态机（`GS01 正手`、`GS02 双反`、`GS03 单反`）：按文档标志性动作切分 `M01 盯球准备(垫步落地)` -> `M02 启动移动(第一转肩)` -> `M03 转肩引拍(重心加载支撑腿)` -> `M04 蹬地(骨盆角速度启动 K080)` -> `M05 转髋(肩髋分离角峰值 K082-K080)` -> `M06 转胸(持拍手开始加速 K041)` -> `M07 挥拍释放(拍头滞后最大到轨迹收敛)` -> `M08 击球(触球到离拍)` -> `M09 收拍(双反双手随挥 / 单反展翅收拍 J103-J123 展开)` -> `M10 恢复准备`。
     * 切削状态机（`GS04 反手切削`、`GS05 正手切削`）：切分 `M01–M03` -> `M04 重心支撑` -> `M05 高位引拍(RK005 高于预计击球点 S040)` -> `M06 拍面打开(S032 后仰打开，非持拍手脱离三角区 RK013)` -> `M07 切削挥拍(由高向低前下轨迹 K073)` -> `M08 切削击球` -> `M09 低位切削收拍` -> `M10 恢复准备`。
```

### 阶段三：补齐球拍（RK）、球轨迹（BALL）与球场标定（S05–S08），实现 SS / RS / NS / DS / OS / TS

当 Pose + 基础 GS/FS 跑通后，可将以下第三阶段指令发送给 Codex，补齐其余 6 大系统：

```text
请基于《4.DS防守.docx》、《5.NS网前.docx》、《6.OS高压.docx》、《7.SS发球.docx》和《8.TS战术.docx》，扩展多模态视觉感知层并实现 SS、RS、NS、DS、OS、TS 六大系统的阶段识别与评分逻辑：

1. 打通球拍（RK）、球（BALL）与球场坐标系（S05–S08）计算模块：
   - 在 `src/rallymate_vision/` 与 `src/rallymate_tracking/` 中接入球拍关键点检测（至少输出 `RK001-RK005` 拍头/甜区、`RK013` 三角区、`RK020-RK023` 拍柄，用于计算拍面倾角 `S032` 与非持拍手扶拍距离）和网球轨迹追踪（输出 `BALL-010~027` 及落地反弹帧 `bounce_frame`）。
   - 在 `src/rallymate_features/coordinates.py` 中增加基于球场关键点（底线、边线、发球线、T点、球网柱）的单应性变换（Homography），将像素坐标映射到 ITF 标准球场坐标系（`S050-S059`），实时计算 `S071`（距底线距离）、`S073`（距网距离）、`S066`（过网高度）与 `S082-S089`（落点深度、宽度与区域）。

2. 实现专项技术系统的核心阶段状态机（`src/rallymate_scoring/`）：
   - `SS 发球系统（SS01-SS05）`：重点区分 `M03 抛球准备（BALL-010 随 J103/J104 运动，未离手）` 与 `M04 抛球释放（球手距离突变分离，进入独立抛物线）`；识别 `M06 Trophy Pose`；在 `M08 上方击球` 中根据球拍轨迹方向（`K073`）与出球曲率/弹跳（`BALL-026/013`）自动分类 `SS01 平击`、`SS02 切削（侧向摩擦+横向弯曲）`、`SS03 上旋（向上刷球+高过网高弹跳）`、`SS04 二发稳定（高容错安全落区）`，并挂载 `SS05 发球落地恢复` 模块。
   - `NS 网前系统（NS01-NS05）`：要求 `S073` 处于网前/中前场区间。核心边界是利用球落地反弹帧 `bounce_frame` 与触球帧 `contact_frame` 的时序关系，严格区分 `NS03 半截击`（`0 < contact_frame - bounce_frame <= delta_t`，球刚落地弹起，球拍下沉到反弹点）与 `NS04 低位截击`（未落地，但触球高度 `S040` 低于腰/膝舒适区）；当触球点位于胸肩部且无完整引拍蓄力时判定为 `NS05 高位截击`。
   - `OS 高压系统（OS01-OS04）`：与 `NS05` 的边界在于存在完整侧身、举拍与引拍蓄力链（`M04-M06`）。根据移动方向与双脚离地状态细分：`OS01 常规高压`、`OS02 后退高压（M03 向后/侧后方移动定位且到位）`、`OS03 跳起高压（M07 起跳上伸双脚离地，M08 空中最高点触球）`、`OS04 被动/应急高压（到位不足、后仰失衡、引拍压缩、触球点偏后）`。
   - `DS 防守系统（DS01-DS06）`：在 `M01` 通过来球速度（`BALL-020`）、人球距离失控（`S020`）与重心被动状态（`S013/S014`）触发防守模式。注意防守评分规则与 GS 相反：在 DS 中，“取消完整引拍（M04）”、“稳定拍面封挡借力（M05-M06）”和“小幅短收拍控制惯性（M07）”是加分项，大幅度随挥反而会导致失衡扣分！同时根据场景细分 `DS01/02 正反手挡回`、`DS03 跑动救球（调 FS03/FS08）`、`DS04 挑高防守（拍面打开向上托送高弧线）`、`DS05 低位切削防守`、`DS06 追身防守（S020 过近，让位收缩空间近身封挡）`。
   - `RS 接发系统（RS01-RS05）`：建立与 `DS` 类似的短引拍、借力控制与分腿垫步时机（`T002`）评价模型。

3. 实现 `TS 战术系统（TS01-TS08 共 64 子项）` 规则推理器（新建 `src/rallymate_scoring/tactical_analyzer.py`）：
   - 先实现底座 `TS08 落点与线路战术（TS08-01 ~ TS08-08）`：基于击球点位置（`S070`）、对手位置、出球方向（`BALL-025`）与落点坐标（`S082-S084`），自动为每一拍打上空间标签（斜线、直线、中路、深区、浅区/短区、大角度、追身、空档）。
   - 在 `TS08` 标签基础上，结合当前拍所属技术系统（SS/RS/GS/NS/OS/DS）与攻防状态转换（`S013/S070/S073` 前压或后退），自动推理 `TS01 发球战术`、`TS02 接发战术`、`TS03 底线相持战术`、`TS04 进攻转换`、`TS05 防守转换`、`TS06 上网与网前战术`、`TS07 高压终结战术`。
```

### 阶段四：落地《下一步.docx》规划的训练/比赛双模式、等比例理想人体模型与心理/康复模块

当核心技术与战术评分闭环完成后，可将以下第四阶段指令发送给 Codex：

```text
请根据《下一步.docx》的产品演进规划，在 `src/rallymate_service/` 与前端 `scoring-demo-web/` 中扩展以下业务模块：

1. 训练模式 vs 比赛模式双管线（`src/rallymate_service/`）：
   - 训练模块：支持单一技术精细诊断、回合分析、落点/球速/转速/过网高度统计，并新增“有效回合自动剪辑器”（通过检测无击球姿态、捡球弯腰、喝水、站立走动等非回合帧段，自动输出高光训练剪辑时间戳）。
   - 比赛模块：新增计分与赛况统计引擎，自动统计发球项（一发/二发得分率、一二发 Ace、落点/球速/转速）、接发项（破发/保发率、Return Ace 率）与回合项（非受迫性失误 Unforced Errors 统计）。

2. 基于用户 `L 长度系统` 的等比例理想人体模型对比与关节灵活度诊断（`src/rallymate_visualization/` & `src/rallymate_scoring/`）：
   - 读取用户视频初始化阶段提取的 `L010-L116` 骨骼长度比例，等比例缩放标准动作模板骨架（Ideal Skeleton Model），在前端与用户实际视频骨架进行同屏叠加渲染，直观标出引拍角度、击球点距离和转体幅度偏差。
   - 新增关节活动度（ROM）与体态评估规则：识别发球肩部外展/外旋受限（长期伏案圆肩）、转体腰髋分离度不足、直臂/曲臂大小臂夹角与握拍匹配关系，并自动映射输出针对性体能训练建议（如圆肩开肩训练、俄罗斯转体增强核心旋转力量等）。

3. 比赛心理评估模块（按 Lv3 / 3.0 分界）：
   - 当球员综合评级 `< Lv3 (3.0)` 时：输出轻量化娱乐心理评分与基础习惯提醒（如发球紧张、击球后缺乏回位和下一拍准备意识）。
   - 当球员综合评级 `>= Lv3 (3.0)` 时：对比同一球员在“比赛模式”与“训练模式”下的节奏（`T007`）、球速（`BALL-020`）、挥拍完整性（`M09` 随挥幅度）、脚步移动积极性（`FS01` 垫步率与 `FS05` 碎步频率）以及非受迫性失误率的变化差值，量化评估比赛心理波动。
```
