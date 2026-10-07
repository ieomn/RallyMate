# 五份视觉定义与现有实现核验

五份文件足以建立可核验的定性技术规则和观测边界；不足以独立生成正式 100 分、数值扣分或技术评级。只有发球抛球“身高约 1.8 倍”是明确数值参考；抬起持拍手侧腿虽称“加分项目”，没有加分值。

覆盖 24 种技术、68 个源文阶段/事件块、139 个关键点段落，按分号拆为 246 条可定位视觉要求。KP/C 编号由本次审计生成，原文没有这些评分指标编号。所有“是什么/怎么识别/关键点”全文均在配套 JSON 中。

## 文件与实现来源

| 文档 | SHA256 | 阶段/事件块 |
|---|---|---|
| VIS-FS 步伐_精简视觉识别定义版.docx | `bbab99f521994420286820e60e8d8057729f0b53e7e0d0ce5ac5fd48bf8b47fe` | 10 |
| VIS-SV 发球技术_精简视觉识别定义版.docx | `a8b8228299471b23ae11b98ddefa691f677338195170b460fd298efea25f934d` | 6 |
| VIS-GS 底线基础_五阶段完整简化版.docx | `044d3643b0e9889d6c6c0c1fed9cf58ecdc1b61f93e09f513b7d205678c04f61` | 30 |
| VIS-RT 接发技术_精简视觉识别定义版.docx | `11ba48b880f260aa08f207e826497d13d9a5ffa7d4b615cf10489031a8d7443c` | 6 |
| VIS-NET 网前进攻技术_正手截击_反手截击_高压_精简视觉识别版.docx | `a7ddfe87a916301a2d0394cb0ec6e45d5a854c48f1198e64162e2a6d4c687388` | 16 |

后端 `src/rallymate_scoring/data/technique_metrics.json` 与前端 `scoring-demo-web/app/data/technique-catalog.json` 内容完全相同（SHA256：6b2229f69afccb8724db7da7adfe684981c0c71b78c438177d244bbcb7dd2046），五个文档 SHA256 均匹配。`technique_registry.py:111–115` 强制 `formal_coach_score=false`，符合来源边界。

## 可执行差异清单

### VIS-C01 网前与高压可选阶段未结构化（未实现）

原文：VIS-NET:word/document.xml:P0023；VIS-NET:word/document.xml:P0069；VIS-NET:word/document.xml:P0124。

实现：src/rallymate_scoring/data/technique_metrics.json /techniques/11/phases；src/rallymate_scoring/data/technique_metrics.json /techniques/12/phases；src/rallymate_scoring/data/technique_metrics.json /techniques/13/phases。

原文明确允许正/反截击稳定调整、高压蓄力阶段没有；当前 phases 仅字符串数组，无 optional/applicability。不能把阶段未检出当动作缺失扣分。

建议：为这三个阶段添加 conditional/optional 与 not_applicable；回放解释保留快速来球等上下文，不进入缺项分母。

### VIS-C02 GS 五阶段与评分卡十阶段并存（需标定）

原文：VIS-GS:word/document.xml:P0002；VIS-GS:word/document.xml:P0031 T003.R002.C002。

实现：src/rallymate_scoring/data/technique_metrics.json /default_phase_contracts/baseline；src/rallymate_scoring/data/metric_cards.json /cards/*/stageCode。

VIS-GS 为 observation/preparation/stability/strike/recovery；metric_cards 的每个 GS 事件有 M01 至 M10；且盯球贯穿全程而非互斥片段。不能静默合并、平均或互相替换。

建议：保留两套 contract；建立 many-to-many 来源映射。稳定调整跨引拍末到挥拍前；盯球是全程属性，不能按五个互斥等权片段评分。

### VIS-C03 1.8 倍参考与未定量加分保留为参考（一致）

原文：VIS-SV:word/document.xml:P0020 T002.R003.C002；VIS-SV:word/document.xml:P0048 T005.R003.C002。

实现：src/rallymate_scoring/data/technique_metrics.json /techniques/5/reference_constraints。

1.8 为约数、没有公差及评分分段；“加分项目”没有分值。registry 正确声明 visual_reference_only_not_scoring_threshold 与 optional_reference_only_no_numeric_bonus。

建议：显示“原文参考约 1.8×身高/落地抬腿为可选正向观察”；不可生成 100 分阈值或 +5 分。

### VIS-C04 每条来源定位不足（未实现）

原文：VIS-FS:word/document.xml:P0014 T001.R003.C002；VIS-GS:word/document.xml:P0024 T002.R003.C002；VIS-RT:word/document.xml:P0036。

实现：src/rallymate_scoring/data/technique_metrics.json /source_documents；src/rallymate_scoring/data/technique_metrics.json /techniques/*/core_visual_features。

source_documents 有五个原文件名和正确 SHA256；技术摘要没有段落/表格定位和每条规则证据引用。

建议：使用本交接 JSON 的稳定文档 ID、paragraph/table_cell/xpath 和规则 ID；在解释中显示原文与观测两侧依据。

### VIS-C05 证据完备参考范围不是原文 100 分技术评分（需标定）

原文：VIS-FS:word/document.xml:P0002；VIS-SV:word/document.xml:P0002；VIS-GS:word/document.xml:P0002；VIS-NET:word/document.xml:P0002。

实现：src/rallymate_scoring/data/technique_metrics.json /semantics。

registry score_range=[0,100] 与 evidence_readiness_reference 属工程合同；五视觉文档未给 0–100 的转换、阈值、权重、分段或总等级。formal_coach_score=false 正確保持边界。

建议：将观测完备度和技术质量分栏；不得称证据 100 分为动作 100 分，也不能用缺测反推扣分。

### VIS-C06 接发相对幅度规则需要所有子类继承（需标定）

原文：VIS-RT:word/document.xml:P0036。

实现：src/rallymate_scoring/data/technique_metrics.json /techniques/6/proxy_limits；src/rallymate_scoring/data/technique_metrics.json /techniques/7/proxy_limits；src/rallymate_scoring/data/technique_metrics.json /techniques/8/proxy_limits；src/rallymate_scoring/data/technique_metrics.json /techniques/9/proxy_limits；src/rallymate_scoring/data/technique_metrics.json /techniques/10/proxy_limits。

原文要求相对动作幅度和时间，而不是固定绝对阈值。registry 仅双反接发写了“无法确认发球速度时”使用相对时序，条件比原文更窄；其余子类未显式继承。

建议：在 return family 放共同约束并由全部五子类继承；速度可测也需按时间压力标定，不能自动启用统一固定阈值。

### VIS-C07 代理观测边界保留（一致）

原文：VIS-GS:word/document.xml:P0010 T001.R002.C002；VIS-GS:word/document.xml:P0040 T004.R002.C002；VIS-SV:word/document.xml:P0018 T002.R002.C002；VIS-SV:word/document.xml:P0037 T004.R002.C002；VIS-RT:word/document.xml:P0044；VIS-FS:word/document.xml:P0139 T010.R003.C002。

实现：src/rallymate_scoring/data/technique_metrics.json /semantics；src/rallymate_scoring/data/technique_metrics.json /techniques/*/proxy_limits。

registry 全局声明头部/连续响应仅代理、真实球拍触球需要可靠球/拍轨迹、缺证据 unavailable；与来源一致。

建议：运行结果逐项实现这些边界，不能仅有静态声明；视线、抛球、触球、战术最优等须单独 unavailable。

### VIS-C08 原文随挥速度措辞需时间窗口澄清（需标定）

原文：VIS-GS:word/document.xml:P0047 T005.R001.C002；VIS-GS:word/document.xml:P0051 T005.R003.C002；VIS-GS:word/document.xml:P0102 T010.R001.C002；VIS-GS:word/document.xml:P0106 T010.R003.C002；VIS-GS:word/document.xml:P0107 T010.R003.C002。

实现：src/rallymate_scoring/data/technique_metrics.json /techniques/0/core_visual_features。

定义要求随挥逐渐减速，关键点同时写“击球后没有明显降速”、正手“搞阶段甚至加速”；可能分别指即时延续与后程减速，但原文未给窗口。

建议：原文并列保留，待作者澄清“搞阶段”与时间窗口；不可静默改写成固定加速/减速分数。

### VIS-C09 源文复制/未完成语句（需标定）

原文：VIS-GS:word/document.xml:P0114 T011.R001.C002；VIS-GS:word/document.xml:P0160 T016.R001.C002；VIS-GS:word/document.xml:P0207 T021.R001.C002；VIS-FS:word/document.xml:P0081 T006.R003.C002；VIS-GS:word/document.xml:P0085 T008.R003.C002；VIS-RT:word/document.xml:P0045。

实现：src/rallymate_scoring/data/technique_metrics.json /techniques/1/core_visual_features；src/rallymate_scoring/data/technique_metrics.json /techniques/2/core_visual_features；src/rallymate_scoring/data/technique_metrics.json /techniques/3/core_visual_features；src/rallymate_scoring/data/technique_metrics.json /techniques/19/core_visual_features。

双反/单反/反手切削“盯球是什么”仍写“正手击球”；FS 开放支撑“重心压于击球”句子未完，GS 有“击球测”、RT 有“会拍”等字样。

建议：原样保留并标疑；可按对应章节关联技术身份，但不能把推定订正冒充源文或产生新数值规则。

### VIS-C10 高压专属阶段不能使用网前默认列表覆盖（未实现）

原文：VIS-NET:word/document.xml:P0097；VIS-NET:word/document.xml:P0106；VIS-NET:word/document.xml:P0115；VIS-NET:word/document.xml:P0124；VIS-NET:word/document.xml:P0133；VIS-NET:word/document.xml:P0142。

实现：src/rallymate_scoring/data/technique_metrics.json /default_phase_contracts/net_attack；src/rallymate_scoring/data/technique_metrics.json /techniques/13/phases。

默认 net_attack 为五阶段，overhead 已正確设置独立六阶段；数据可表达特例，但默认合同缺少“逐技术覆盖优先”的结构约束。

建议：消费者优先 technique.phases；高压保留定位→trophy→可选 stability，不能套截击五阶段。

### VIS-C11 GS/FS正式卡与视觉文档不可互相当作评分依据（未实现）

原文：VIS-FS:word/document.xml:P0002；VIS-GS:word/document.xml:P0002。

实现：src/rallymate_scoring/data/metric_cards.json /cards/*/grades；src/rallymate_scoring/data/metric_cards.json /cards/*/unavailable。

metric_cards 有298项(A–E、可评分状态及70%等条件)。这些值不能从五份视觉文档验证，只能回溯另两份 GS/FS 评分卡；本子审计未对两份卡片原文作判断。

建议：前端每条等级/不可用阈值必须附对应 CARD 来源；禁止给发球/接发/网前套用 GS/FS 通用 A–E 文案而声称视觉文档原定。

## 技术子项与阶段对应

| 技术 ID | 原文块 | 当前阶段 | 细项遗漏或注意点 |
|---|---|---|---|
| baseline_forehand（底线正手） | VIS-GS-T001, VIS-GS-T002, VIS-GS-T003, VIS-GS-T004, VIS-GS-T005, VIS-GS-T006, VIS-GS-T007, VIS-GS-T008, VIS-GS-T009, VIS-GS-T010 | observation → preparation → stability → strike → recovery | 非持拍手参与辅助（P0075）；重心在击球侧腿、肩髋连线指向来球（P0085/P0088）；击球后拍头继续向球飞行轨迹移动、利用身体转动减速（P0106/P0107） |
| baseline_two_hand_backhand（底线双手反手） | VIS-GS-T001, VIS-GS-T002, VIS-GS-T003, VIS-GS-T004, VIS-GS-T005, VIS-GS-T011, VIS-GS-T012, VIS-GS-T013, VIS-GS-T014, VIS-GS-T015 | observation → preparation → stability → strike → recovery | 开放/闭合均可及重心较正手更靠中线（P0136）；双手不过早分离、大臂水平、胸腔朝回球方向的收拍细节（P0154） |
| baseline_one_hand_backhand（底线单手反手） | VIS-GS-T001, VIS-GS-T002, VIS-GS-T003, VIS-GS-T004, VIS-GS-T005, VIS-GS-T016, VIS-GS-T017, VIS-GS-T018, VIS-GS-T019, VIS-GS-T020 | observation → preparation → stability → strike → recovery | 背部微微对向来球（P0173）；稳定阶段重心非惯用手侧（P0182）；先向前再后走的随挥顺序（P0200） |
| backhand_slice（反手切削） | VIS-GS-T001, VIS-GS-T002, VIS-GS-T003, VIS-GS-T004, VIS-GS-T005, VIS-GS-T021, VIS-GS-T022, VIS-GS-T023, VIS-GS-T024, VIS-GS-T025 | observation → preparation → stability → strike → recovery | 肩膀对向来球（P0219）；重心击球侧腿（P0228）；向前运动的重要性大于向下（P0237）；重心持续向回球方向及胸腔打开（P0246） |
| forehand_slice（正手切削） | VIS-GS-T001, VIS-GS-T002, VIS-GS-T003, VIS-GS-T004, VIS-GS-T005, VIS-GS-T026, VIS-GS-T027, VIS-GS-T028, VIS-GS-T029, VIS-GS-T030 | observation → preparation → stability → strike → recovery | 大臂离身并保持一定夹角（P0265）；对球飞行轨迹不作要求是本动作原文明示（P0283，registry 已有摘要） |
| serve（发球） | VIS-SV-T001, VIS-SV-T002, VIS-SV-T003, VIS-SV-T004, VIS-SV-T005, VIS-SV-T006 | setup → toss → trophy → strike → landing → recovery | 抛球释放高度在肩膀至头部、手臂继续上伸（P0020）；持拍大臂近肩膀水平、不能夹肘（P0030）；随挥大臂/小臂时序和身体中轴旋转（P0048） |
| return_forehand（正手接发） | VIS-RT-S01, VIS-RT-S02, VIS-RT-S03, VIS-RT-S04, VIS-RT-S05, VIS-RT-S06 | observation → split_step → first_step → compact_preparation → strike → recovery | 脚掌撑地、脚后跟不触地（P0019）；短引拍需使用相对幅度和时间，不能固定绝对阈值（P0036）；挥拍重心朝击球方向（P0045） |
| return_two_hand_backhand（双手反手接发） | VIS-RT-S01, VIS-RT-S02, VIS-RT-S03, VIS-RT-S04, VIS-RT-S05, VIS-RT-S06 | observation → split_step → first_step → compact_preparation → strike → recovery | 脚掌撑地、脚后跟不触地（P0019）；引拍完成后再稳定再挥拍的共同动作链（P0035/P0041） |
| return_one_hand_backhand（单手反手接发） | VIS-RT-S01, VIS-RT-S02, VIS-RT-S03, VIS-RT-S04, VIS-RT-S05, VIS-RT-S06 | observation → split_step → first_step → compact_preparation → strike → recovery | 脚掌撑地、脚后跟不触地（P0019）；相对幅度和时间适用于所有接发子类（P0036） |
| return_backhand_slice（反手切削接发） | VIS-RT-S01, VIS-RT-S02, VIS-RT-S03, VIS-RT-S04, VIS-RT-S05, VIS-RT-S06 | observation → split_step → first_step → compact_preparation → strike → recovery | 脚掌撑地、脚后跟不触地（P0019）；相对幅度和时间适用于所有接发子类（P0036） |
| return_forehand_slice（正手切削接发） | VIS-RT-S01, VIS-RT-S02, VIS-RT-S03, VIS-RT-S04, VIS-RT-S05, VIS-RT-S06 | observation → split_step → first_step → compact_preparation → strike → recovery | 脚掌撑地、脚后跟不触地（P0019）；相对幅度和时间适用于所有接发子类（P0036） |
| forehand_volley（正手截击） | VIS-NET-T001, VIS-NET-T002, VIS-NET-T003, VIS-NET-T004, VIS-NET-T005 | observation → compact_preparation → positioning → strike → recovery | 稳定短支撑可没有（P0023）；不要求固定一种站位（P0029）；球拍和重心同步前压（P0038） |
| backhand_volley（反手截击） | VIS-NET-T006, VIS-NET-T007, VIS-NET-T008, VIS-NET-T009, VIS-NET-T010 | observation → compact_preparation → positioning → strike → recovery | 稳定短支撑可没有（P0069）；身体重心与球拍同步（P0084） |
| overhead（高压） | VIS-NET-T011, VIS-NET-T012, VIS-NET-T013, VIS-NET-T014, VIS-NET-T015, VIS-NET-T016 | observation → positioning → trophy → stability → strike → recovery | 蓄力稳定阶段可没有（P0124）；移动时球拍在胸前（P0112）；持拍肘与肩平齐、非持拍手指球（P0121） |
| split_step（分腿垫步） | VIS-FS-T001 | trigger → support → direction_change | 双脚落地时间接近（P0012）；膝微弯、重心微前倾和脚掌站立（P0015） |
| first_step（第一步启动） | VIS-FS-T002 | trigger → direction_change → support | 胸腔转向移动方向、重心向移动方向倾倒（P0026/P0027） |
| crossover_step（交叉步） | VIS-FS-T003 | direction_change → support | 不能明显上下重心波动（P0043） |
| shuffle_step（并步移动） | VIS-FS-T004 | direction_change → support | 跟随脚及时跟进，不用于大范围追球（P0055/P0057） |
| adjustment_steps（小碎步调整） | VIS-FS-T005 | direction_change → support | 允许前后及左右调整（P0069）；不是单纯增加步数（P0070） |
| open_stance（开放式支撑） | VIS-FS-T006 | support | 身体中心处于可控支撑区域（P0082） |
| closed_stance（闭合式支撑） | VIS-FS-T007 | support | 原文重心压于后侧（P0096） |
| lunge_support（跨步支撑） | VIS-FS-T008 | direction_change → support | 高级技巧标签（P0101） |
| braking_stabilization（制动急停与稳定） | VIS-FS-T009 | braking → support | 高级技巧标签（P0115）；不能冲出支撑区域（P0124）；稳定不等同回位（P0125） |
| recovery_positioning（击球后回位） | VIS-FS-T010 | recovery → support | 回位不等于回中线，斜线球可站中线一侧（P0135） |

## 全部阶段与关键点定位

下列逐段保存关键点；配套 JSON 还保存定义与识别全文。除 1.8 倍身高参考外，单位、数值阈值、分数映射、权重、评级与聚合公式均未给出。“未实现”指尚未作为独立可核验指标结构化，不等于已经证明运行时识别失败。

### VIS-FS-T001 分腿垫步

适用：split_step；阶段：`event`；原文允许省略：未明示。

映射：一致；动作分类一致；trigger/support 等阶段词是工程归纳，视觉文档未定义这套编号阶段。

- 轻微下蹲而非深蹲；（VIS-FS:word/document.xml:P0010 T001.R003.C002）
- 离地幅度要小；（VIS-FS:word/document.xml:P0011 T001.R003.C002）
- 双脚分开且落地时间接近；（VIS-FS:word/document.xml:P0012 T001.R003.C002）
- 落地后身体稳定并能立即启动；（VIS-FS:word/document.xml:P0013 T001.R003.C002）
- 最关键的是时机：落地应与对方击球瞬间相匹配；（VIS-FS:word/document.xml:P0014 T001.R003.C002）
- 最佳站姿：膝盖微弯，中心微微前倾，脚掌站立。（VIS-FS:word/document.xml:P0015 T001.R003.C002）

### VIS-FS-T002 第一步启动

适用：first_step；阶段：`event`；原文允许省略：未明示。

映射：一致；动作分类一致；trigger/support 等阶段词是工程归纳，视觉文档未定义这套编号阶段。

- 启动要紧接分腿垫步落地；（VIS-FS:word/document.xml:P0025 T002.R003.C002）
- 胸腔转向要移动的方向；（VIS-FS:word/document.xml:P0026 T002.R003.C002）
- 重心向要移动的方向倾倒；（VIS-FS:word/document.xml:P0027 T002.R003.C002）
- 动作不能先明显向反方向晃动；（VIS-FS:word/document.xml:P0028 T002.R003.C002）
- 高级技巧：人体中心与启动脚向同一目标方向移动。（VIS-FS:word/document.xml:P0029 T002.R003.C002）

### VIS-FS-T003 交叉步移动

适用：crossover_step；阶段：`event`；原文允许省略：未明示。

映射：一致；动作分类一致；trigger/support 等阶段词是工程归纳，视觉文档未定义这套编号阶段。

- 必须出现明确的双脚交叉关系；（VIS-FS:word/document.xml:P0039 T003.R003.C002）
- 主要用于中远距离而不是小范围找点；（VIS-FS:word/document.xml:P0040 T003.R003.C002）
- 移动方向连续；（VIS-FS:word/document.xml:P0041 T003.R003.C002）
- 身体中心应随步伐快速移动；（VIS-FS:word/document.xml:P0042 T003.R003.C002）
- 交叉过程中仍需保持上肢保持稳定，不能因跨步出现明显上下重心波动。（VIS-FS:word/document.xml:P0043 T003.R003.C002）

### VIS-FS-T004 并步移动

适用：shuffle_step；阶段：`event`；原文允许省略：未明示。

映射：一致；动作分类一致；trigger/support 等阶段词是工程归纳，视觉文档未定义这套编号阶段。

- 双脚不交叉是与交叉步最重要的区别；（VIS-FS:word/document.xml:P0053 T004.R003.C002）
- 步幅通常较小；（VIS-FS:word/document.xml:P0054 T004.R003.C002）
- 跟随脚要及时跟进（螃蟹一样）；（VIS-FS:word/document.xml:P0055 T004.R003.C002）
- 身体重心相对稳定；（VIS-FS:word/document.xml:P0056 T004.R003.C002）
- 适合短距离调整和节奏控制，不用于大范围追球。（VIS-FS:word/document.xml:P0057 T004.R003.C002）

### VIS-FS-T005 小碎步调整

适用：adjustment_steps；阶段：`event`；原文允许省略：未明示。

映射：一致；动作分类一致；trigger/support 等阶段词是工程归纳，视觉文档未定义这套编号阶段。

- 核心不是移动得远，而是“短、快、连续的调整”；（VIS-FS:word/document.xml:P0067 T005.R003.C002）
- 通常出现在大范围移动结束、击球支撑建立之前；（VIS-FS:word/document.xml:P0068 T005.R003.C002）
- 既可以前后调整，也可以左右调整；（VIS-FS:word/document.xml:P0069 T005.R003.C002）
- 最终目的在于找到合适击球距离，而不是单纯增加步数。（VIS-FS:word/document.xml:P0070 T005.R003.C002）

### VIS-FS-T006 开放式支撑

适用：open_stance；阶段：`event`；原文允许省略：未明示。

映射：一致；动作分类一致；trigger/support 等阶段词是工程归纳，视觉文档未定义这套编号阶段。

- 双脚横向支撑关系是核心；（VIS-FS:word/document.xml:P0080 T006.R003.C002）
- 不能仅根据肩部朝向判断，击球侧腿应建立稳定支撑且膝部保持一定屈曲，重心压于击球；（VIS-FS:word/document.xml:P0081 T006.R003.C002）
- 身体中心处于可控支撑区域；（VIS-FS:word/document.xml:P0082 T006.R003.C002）
- 开放式与闭合式的判断重点是脚位关系和身体朝向的组合。（VIS-FS:word/document.xml:P0083 T006.R003.C002）

### VIS-FS-T007 闭合式支撑

适用：closed_stance；阶段：`event`；原文允许省略：未明示。

映射：一致；动作分类一致；trigger/support 等阶段词是工程归纳，视觉文档未定义这套编号阶段。

- 必须形成明显的前后脚支撑关系；（VIS-FS:word/document.xml:P0093 T007.R003.C002）
- 前脚跨出是重要事件；（VIS-FS:word/document.xml:P0094 T007.R003.C002）
- 身体侧身通常比开放式明显；（VIS-FS:word/document.xml:P0095 T007.R003.C002）
- 落地后支撑要稳定，重心压于后侧；（VIS-FS:word/document.xml:P0096 T007.R003.C002）
- 不能只用“身体侧身”判断，脚位是区分开放式和闭合式的主要依据。（VIS-FS:word/document.xml:P0097 T007.R003.C002）

### VIS-FS-T008 跨步支撑（高级技巧）

适用：lunge_support；阶段：`event`；原文允许省略：未明示。

映射：一致；动作分类一致；trigger/support 等阶段词是工程归纳，视觉文档未定义这套编号阶段。

- 大幅跨出是核心特征；（VIS-FS:word/document.xml:P0107 T008.R003.C002）
- 步幅应明显大于普通调整步；（VIS-FS:word/document.xml:P0108 T008.R003.C002）
- 常伴随身体伸展或重心降低；（VIS-FS:word/document.xml:P0109 T008.R003.C002）
- 跨步脚落地后仍要保持平衡；（VIS-FS:word/document.xml:P0110 T008.R003.C002）
- 它主要用于补偿距离，不应把普通闭合站位的小幅前跨误判为跨步支撑。（VIS-FS:word/document.xml:P0111 T008.R003.C002）

### VIS-FS-T009 制动急停/稳定（高级技巧）

适用：braking_stabilization；阶段：`event`；原文允许省略：未明示。

映射：一致；动作分类一致；trigger/support 等阶段词是工程归纳，视觉文档未定义这套编号阶段。

- 必须存在“移动→明显减速→稳定”的速度变化；（VIS-FS:word/document.xml:P0121 T009.R003.C002）
- 制动脚需要形成有效支撑；（VIS-FS:word/document.xml:P0122 T009.R003.C002）
- 膝部屈曲用于吸收惯性；（VIS-FS:word/document.xml:P0123 T009.R003.C002）
- 身体中心不能继续明显冲出支撑区域；（VIS-FS:word/document.xml:P0124 T009.R003.C002）
- 只判断制动稳定，不等同于回位。（VIS-FS:word/document.xml:P0125 T009.R003.C002）

### VIS-FS-T010 击球后回位

适用：recovery_positioning；阶段：`event`；原文允许省略：未明示。

映射：一致；动作分类一致；trigger/support 等阶段词是工程归纳，视觉文档未定义这套编号阶段。

- 回位不是简单“回到球场中线”，比如拉斜线时需要站在中线一侧；（VIS-FS:word/document.xml:P0135 T010.R003.C002）
- 核心是击球后主动恢复下一拍覆盖位置；（VIS-FS:word/document.xml:P0136 T010.R003.C002）
- 移动应具有明确目标和连续性；（VIS-FS:word/document.xml:P0137 T010.R003.C002）
- 到位后身体应重新稳定并能够进入下一次分腿垫步；（VIS-FS:word/document.xml:P0138 T010.R003.C002）
- 若没有可靠场地、球路和对手信息，第一版只判断“是否发生回位移动”，不评价战术位置是否最优。（VIS-FS:word/document.xml:P0139 T010.R003.C002）

### VIS-SV-T001 准备

适用：serve；阶段：`setup`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 站位稳定；身体不能完全松散；持球手和持拍手准备明确；准备结束后应自然进入抛球动作。（VIS-SV:word/document.xml:P0011 T001.R003.C002）

### VIS-SV-T002 抛球

适用：serve；阶段：`toss`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 抛球动作连续，匀速将球从低点托起，轨迹保持竖直，到达肩膀到头部的高度释放球，然后抛球手持续向上延伸；最佳抛球高度为此人身高的1.8倍左右。（VIS-SV:word/document.xml:P0020 T002.R003.C002）
- 手与球分开的抛球位置应与后续引拍协调；精确抛球高度和前后左右位置需要球轨迹才能判断。（VIS-SV:word/document.xml:P0021 T002.R003.C002）

### VIS-SV-T003 引拍蓄力

适用：serve；阶段：`trophy`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 引拍与抛球必须协调；非持拍臂上举有助于身体前侧腰腹部形成上下拉伸；肩髋与下肢共同蓄力而不是只有手臂后摆；，注意，持拍手大臂必须离开身体，几乎与肩膀水平，不能夹肘；Trophy Pose 是发球最关键的视觉状态之一。（VIS-SV:word/document.xml:P0030 T003.R003.C002）

### VIS-SV-T004 挥拍击球

适用：serve；阶段：`strike`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 动作核心是蓄力→上伸→加速→击球的连续释放；击球点位于头顶上方区域；不能把蹬地、转体、手臂加速拆成互不关联的动作；真实触球与拍面方向需球/球拍模型支持。（VIS-SV:word/document.xml:P0039 T004.R003.C002）

### VIS-SV-T005 随挥落地

适用：serve；阶段：`landing`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 击球后不能突然停拍；随挥轨迹应连续，球拍向前继续挥动，大臂围绕肩膀转动，实现随挥；小臂先随大臂转动，后围绕肘关节运动；身体围绕中轴线旋转，抛球手同时旋转；整个过程重心向前运动；落地时身体保持可控；加分项目：落地后持拍手侧的腿部抬起为最佳。（VIS-SV:word/document.xml:P0048 T005.R003.C002）

### VIS-SV-T006 恢复

适用：serve；阶段：`recovery`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 发球不是动作结束而是回合开始；落地后应尽快恢复平衡；恢复可直接衔接 步伐系统。（VIS-SV:word/document.xml:P0057 T006.R003.C002）

### VIS-GS-T001 盯球

适用：baseline_forehand, baseline_two_hand_backhand, baseline_one_hand_backhand, backhand_slice, forehand_slice；阶段：`observation`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 全程持续是核心；（VIS-GS:word/document.xml:P0012 T001.R003.C002）
- 从准备、引拍、稳定调整直到击球都要持续观察；（VIS-GS:word/document.xml:P0013 T001.R003.C002）
- 击球前不应明显提前转头；（VIS-GS:word/document.xml:P0014 T001.R003.C002）
- 击球瞬间视线应该位于击球点。（VIS-GS:word/document.xml:P0015 T001.R003.C002）

### VIS-GS-T002 准备（引拍/拉拍）

适用：baseline_forehand, baseline_two_hand_backhand, baseline_one_hand_backhand, backhand_slice, forehand_slice；阶段：`preparation`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 正手、双反、单反和切削的引拍形式不同，但是重点是身体转动与手臂/球拍引拍是否协调， 不能只看球拍有没有拉到后面， 核心的判断标准是利用身体转动将球拍拉到后方，还是只利用胳膊将球拍拽到后方。（VIS-GS:word/document.xml:P0024 T002.R003.C002）

### VIS-GS-T003 稳定调整（蓄力保持）

适用：baseline_forehand, baseline_two_hand_backhand, baseline_one_hand_backhand, backhand_slice, forehand_slice；阶段：`stability`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 核心是“稳定 + 蓄力”；引拍完成后不能马上失去身体控制；允许存在小范围调整，但真正挥拍前应形成相对稳定的支撑；这里的重心使用髋中心/人体中心作为视觉代理。（VIS-GS:word/document.xml:P0033 T003.R003.C002）

### VIS-GS-T004 挥拍击打

适用：baseline_forehand, baseline_two_hand_backhand, baseline_one_hand_backhand, backhand_slice, forehand_slice；阶段：`strike`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 整个动力链条的传导是，蹬地，重心移动，转体（先是髋关节，然后是胸腔），胳膊，球拍。（VIS-GS:word/document.xml:P0042 T004.R003.C002）

### VIS-GS-T005 随挥收拍

适用：baseline_forehand, baseline_two_hand_backhand, baseline_one_hand_backhand, backhand_slice, forehand_slice；阶段：`recovery`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 击球完成不等于动作结束；随挥应是挥拍击打的自然延续，不能突然停止；不同技术的收拍形式不同，但共同关注动作连续，击球后没有明显降速，球拍继续沿着挥拍轨迹向前、完整以及身体最终保持平衡。（VIS-GS:word/document.xml:P0051 T005.R003.C002）

### VIS-GS-T006 盯球（全程）

适用：baseline_forehand；阶段：`observation`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 盯球贯穿整个击球过程；击球前不能明显提前转头；击球瞬间视线应该位于击球点；真正视线判断需要球轨迹和更精细的头部/眼部模型。（VIS-GS:word/document.xml:P0064 T006.R003.C002）

### VIS-GS-T007 准备——转体引拍

适用：baseline_forehand；阶段：`preparation`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 身体转动带动引拍，而不是单纯手臂后拉；（VIS-GS:word/document.xml:P0073 T007.R003.C002）
- 肩髋参与转动；（VIS-GS:word/document.xml:P0074 T007.R003.C002）
- 非持拍手参与辅助（关键）；（VIS-GS:word/document.xml:P0075 T007.R003.C002）
- 引拍完成后进入稳定调整。（VIS-GS:word/document.xml:P0076 T007.R003.C002）

### VIS-GS-T008 稳定调整——建立支撑与保持蓄力

适用：baseline_forehand；阶段：`stability`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 重心落于击球测的腿上；（VIS-GS:word/document.xml:P0085 T008.R003.C002）
- 允许小范围调整，但挥拍前应建立稳定支撑；（VIS-GS:word/document.xml:P0086 T008.R003.C002）
- 重心使用髋中心/人体中心作为视觉代理；（VIS-GS:word/document.xml:P0087 T008.R003.C002）
- 两个髋关节的连线，两肩的连线指向来球。（VIS-GS:word/document.xml:P0088 T008.R003.C002）

### VIS-GS-T009 挥拍击打

适用：baseline_forehand；阶段：`strike`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 重点是完整动力释放，过程整个动力链条的传导是，蹬地，重心移动，转体（先是髋关节，然后是胸腔），胳膊，球拍；不能只观察手臂；（VIS-GS:word/document.xml:P0097 T009.R003.C002）

### VIS-GS-T010 随挥收拍

适用：baseline_forehand；阶段：`recovery`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 击球后球拍头继续向球飞行的轨迹移动，搞阶段甚至加速；（VIS-GS:word/document.xml:P0106 T010.R003.C002）
- 随挥必须连续自然，不能突然制动，利用身体转动完成减速；（VIS-GS:word/document.xml:P0107 T010.R003.C002）
- 身体重心保持稳定。（VIS-GS:word/document.xml:P0108 T010.R003.C002）

### VIS-GS-T011 盯球（全程）

适用：baseline_two_hand_backhand；阶段：`observation`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 盯球贯穿整个击球过程；击球前不能明显提前转头；击球瞬间视线应该位于击球点；真正视线判断需要球轨迹和更精细的头部/眼部模型。（VIS-GS:word/document.xml:P0118 T011.R003.C002）

### VIS-GS-T012 准备——双手转体引拍

适用：baseline_two_hand_backhand；阶段：`preparation`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 双手共同控制球拍；身体转动带动引拍；双腕之间不能出现明显脱节；不能只用双臂向后拉球拍，必须有明显的转肩动作。（VIS-GS:word/document.xml:P0127 T012.R003.C002）

### VIS-GS-T013 稳定调整——建立双反支撑

适用：baseline_two_hand_backhand；阶段：`stability`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 重点不是限定某一种站位（关闭，开放都行）但是重心必须位于人体中线微微靠击球侧，没有正手要求幅度大；双手仍应保持对球拍的共同控制，肩膀正对来球，甚至可以微微过一些。（VIS-GS:word/document.xml:P0136 T013.R003.C002）

### VIS-GS-T014 挥拍击打

适用：baseline_two_hand_backhand；阶段：`strike`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 双手共同加速是重要特征；身体重心移动同时旋转应能够带动双臂；不能主要依靠手臂硬推；真实触球仍需球和球拍轨迹。（VIS-GS:word/document.xml:P0145 T014.R003.C002）

### VIS-GS-T015 随挥收拍

适用：baseline_two_hand_backhand；阶段：`recovery`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 双手不能过早分离；随挥轨迹连续；身体继续完成剩余旋转；球拍从肩膀收于后背，最终恢复平衡，上身挺拔，胸腔正对击球飞行方向，惯用手大臂保持水平。（VIS-GS:word/document.xml:P0154 T015.R003.C002）

### VIS-GS-T016 盯球（全程）

适用：baseline_one_hand_backhand；阶段：`observation`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 盯球贯穿整个击球过程；击球前不能明显提前转头；击球瞬间视线应该位于击球点；真正视线判断需要球轨迹和更精细的头部/眼部模型。（VIS-GS:word/document.xml:P0164 T016.R003.C002）

### VIS-GS-T017 准备——侧身引拍

适用：baseline_one_hand_backhand；阶段：`preparation`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 单反需要比较充分的侧身，背部微微对向来球；非持拍手在引拍阶段参与球拍控制；不能过早放开；引拍主要由身体转动带动。（VIS-GS:word/document.xml:P0173 T017.R003.C002）

### VIS-GS-T018 稳定调整——侧身支撑与蓄力

适用：baseline_one_hand_backhand；阶段：`stability`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 核心是挥拍前身体稳定，同时保持侧身蓄力，重心位于非惯用手侧；非持拍手不宜过早脱离；重心以人体中心代理判断。（VIS-GS:word/document.xml:P0182 T018.R003.C002）

### VIS-GS-T019 挥拍击打

适用：baseline_one_hand_backhand；阶段：`strike`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 持拍手完成单手释放；非持拍手保持在释放位置；通过转体带动胳膊挥动；挥拍过程中非持拍臂逐渐展开帮助身体平衡。大忌讳，只有持拍手向外延伸。（VIS-GS:word/document.xml:P0191 T019.R003.C002）

### VIS-GS-T020 随挥收拍

适用：baseline_one_hand_backhand；阶段：`recovery`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 双臂展开是单反明显视觉特征；随挥必须连续，先向前再后走；胸肩继续打开；最终保持身体平衡。（VIS-GS:word/document.xml:P0200 T020.R003.C002）

### VIS-GS-T021 盯球（全程）

适用：backhand_slice；阶段：`observation`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 盯球贯穿整个击球过程；击球前不能明显提前转头；击球瞬间视线应该位于击球点；真正视线判断需要球轨迹和更精细的头部/眼部模型。（VIS-GS:word/document.xml:P0211 T021.R003.C002）

### VIS-GS-T022 准备——侧身高位引拍

适用：backhand_slice；阶段：`preparation`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 侧身 + 高位引拍是切削准备的重要特征；非持拍手继续辅助控制；没有可靠球拍关键点时只能粗略判断球拍高位，肩膀对向来球。（VIS-GS:word/document.xml:P0219 T022.R003.C002）

### VIS-GS-T023 稳定调整——高位蓄势

适用：backhand_slice；阶段：`stability`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 核心是身体稳定，同时保持重心在身体击球侧的腿上；不是要求完全静止。（VIS-GS:word/document.xml:P0228 T023.R003.C002）

### VIS-GS-T024 挥拍击打——向前下切削

适用：backhand_slice；阶段：`strike`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 重心向前移动；球拍向前下方切削运动，小臂随着大臂向前下方延展；击球后继续运动，向前的重要性大于向下。（VIS-GS:word/document.xml:P0237 T024.R003.C002）

### VIS-GS-T025 随挥收拍——低位结束

适用：backhand_slice；阶段：`recovery`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 切削收拍整体结束位置通常更低；重心持续向回球方向运动；会拍轨迹不能在击球后突然中断；最终仍要保持身体平衡，胸腔打开对向球飞向的轨迹方向。（VIS-GS:word/document.xml:P0246 T025.R003.C002）

### VIS-GS-T026 盯球（全程）

适用：forehand_slice；阶段：`observation`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 盯球贯穿整个击球过程；击球前不能明显提前转头；击球瞬间视线应该位于击球点；真正视线判断需要球轨迹和更精细的头部/眼部模型。（VIS-GS:word/document.xml:P0256 T026.R003.C002）

### VIS-GS-T027 准备——正手高位引拍

适用：forehand_slice；阶段：`preparation`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 身体转动与高位引拍同时建立，大臂离开身体保持一定夹角；不是单纯抬高手臂；球拍最终应形成能够向前下方释放的位置。（VIS-GS:word/document.xml:P0265 T027.R003.C002）

### VIS-GS-T028 稳定调整——高位蓄势

适用：forehand_slice；阶段：`stability`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 重点是挥拍前建立稳定而可释放的身体状态；允许小范围调整，但不能失去蓄力和支撑。（VIS-GS:word/document.xml:P0274 T028.R003.C002）

### VIS-GS-T029 挥拍击打——正手向前下切削

适用：forehand_slice；阶段：`strike`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 高位 → 前下方是最重要的动作方向，对球飞行轨迹不做要求，因为通常是救球或者放小球用。（VIS-GS:word/document.xml:P0283 T029.R003.C002）

### VIS-GS-T030 随挥收拍——正手低位结束

适用：forehand_slice；阶段：`recovery`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 随挥必须连续；收拍位置通常低于普通正手；不能击球后突然停拍；最终恢复身体平衡。（VIS-GS:word/document.xml:P0292 T030.R003.C002）

### VIS-NET-T001 盯球与网前准备

适用：forehand_volley；阶段：`observation`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 盯球贯穿整个截击过程；球拍保持在胸口前，不能提溜球拍；网前准备强调低重心、短反应和球拍在前。（VIS-NET:word/document.xml:P0011 T001.R003.C002）

### VIS-NET-T002 准备——正手侧转与短准备

适用：forehand_volley；阶段：`compact_preparation`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 截击准备要短；核心是“转体 + 球拍在前”，不是完整正手引拍；动作幅度明显小于底线正手。（VIS-NET:word/document.xml:P0020 T002.R003.C002）

### VIS-NET-T003 稳定调整——网前短支撑（可以没有，由于球员来不及反应）

适用：forehand_volley；阶段：`positioning`；原文允许省略：是。

映射：未实现；阶段名称已收录，但原文允许该阶段没有；当前 phases 数组无 optional/applicability 字段。

- 身体要先到合适位置；允许边前压边截击，但击球瞬间支撑必须可控；重点：不要求固定一种站位。（VIS-NET:word/document.xml:P0029 T003.R003.C002）

### VIS-NET-T004 挥拍击打——短促前送

适用：forehand_volley；阶段：`strike`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 截击不是大挥拍；球拍和重心扑向球，球拍前送与身体前压协调；不能明显向大幅挥动。（VIS-NET:word/document.xml:P0038 T004.R003.C002）

### VIS-NET-T005 短收拍与恢复

适用：forehand_volley；阶段：`recovery`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 收拍短而可控；不能因随挥过大失去下一拍；恢复后重新形成低重心、球拍在前的网前准备。（VIS-NET:word/document.xml:P0047 T005.R003.C002）

### VIS-NET-T006 盯球与网前准备

适用：backhand_volley；阶段：`observation`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 与正手截击共用网前准备逻辑；盯球贯穿整个动作；时刻保持拍在胸口前，不能等球已经到身体附近才开始准备。（VIS-NET:word/document.xml:P0057 T006.R003.C002）

### VIS-NET-T007 准备——反手侧转与短准备

适用：backhand_volley；阶段：`compact_preparation`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 反手截击同样强调短结构；转肩而不是大幅拉拍；球拍不能掉到身体后方。（VIS-NET:word/document.xml:P0066 T007.R003.C002）

### VIS-NET-T008 稳定调整——反手短支撑（可以没有）

适用：backhand_volley；阶段：`positioning`；原文允许省略：是。

映射：未实现；阶段名称已收录，但原文允许该阶段没有；当前 phases 数组无 optional/applicability 字段。

- 先解决距离和支撑，再完成截击；身体不能明显后仰；不限定开放、闭合或跨步中的单一形式。（VIS-NET:word/document.xml:P0075 T008.R003.C002）

### VIS-NET-T009 挥拍击打——反手短促前送

适用：backhand_volley；阶段：`strike`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 不能做完整反手挥拍；身体重心和球拍同步走，核心是拍面稳定和短促前送；身体支撑与球拍运动要同步。（VIS-NET:word/document.xml:P0084 T009.R003.C002）

### VIS-NET-T010 短收拍与恢复

适用：backhand_volley；阶段：`recovery`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 随挥幅度小；快速恢复球拍前置；为下一次正手截击、反手截击或高压做好准备。（VIS-NET:word/document.xml:P0093 T010.R003.C002）

### VIS-NET-T011 盯球与高球判断

适用：overhead；阶段：`observation`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 高压首先是空间判断；不要与高位截击混淆；球明显需要在头顶/身体前上方处理时才进入高压。（VIS-NET:word/document.xml:P0103 T011.R003.C002）

### VIS-NET-T012 移动定位

适用：overhead；阶段：`positioning`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 移动过程中持续盯球；先到位再组织击球；移动过程中保持球拍在胸前，不能提溜；后退高球也归入这一高压动作，不再单独建立后退高压类别。（VIS-NET:word/document.xml:P0112 T012.R003.C002）

### VIS-NET-T013 准备——侧身上举与引拍

适用：overhead；阶段：`trophy`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 侧身、球拍上举和非持拍手定位是高压的重要视觉特征； 持拍手肘部与肩膀平齐，非持拍手高举指球。（VIS-NET:word/document.xml:P0121 T013.R003.C002）

### VIS-NET-T014 稳定调整——高压蓄力（可以没有这个过程）

适用：overhead；阶段：`stability`；原文允许省略：是。

映射：未实现；阶段名称已收录，但原文允许该阶段没有；当前 phases 数组无 optional/applicability 字段。

- 核心仍是“稳定 + 蓄力”；不是完全静止；身体应具备向上伸展和旋转释放条件，不能明显失衡后仰。（VIS-NET:word/document.xml:P0130 T014.R003.C002）

### VIS-NET-T015 挥拍击打——头顶上方击球

适用：overhead；阶段：`strike`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 击球点应位于身体上方或前上方；强调完整连续释放，不只看手臂；（VIS-NET:word/document.xml:P0139 T015.R003.C002）

### VIS-NET-T016 随挥落地与恢复

适用：overhead；阶段：`recovery`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 随挥自然连续；高压后不能因前冲或后退失去平衡；恢复后可继续进入截击、底线或防守动作。（VIS-NET:word/document.xml:P0148 T016.R003.C002）

### VIS-RT-S01 观察准备（盯球全程存在）

适用：return_forehand, return_two_hand_backhand, return_one_hand_backhand, return_backhand_slice, return_forehand_slice；阶段：`observation`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 身体不能完全直立，应保持可快速启动的姿态。（VIS-RT:word/document.xml:P0008）
- 球拍提前处于身体前方，减少后续准备时间。（VIS-RT:word/document.xml:P0009）
- 注意力方向应持续面向发球与来球区域。（VIS-RT:word/document.xml:P0010）
- 因此“盯球”第一版采用头部朝向/稳定性作为视觉代理。（VIS-RT:word/document.xml:P0011）

### VIS-RT-S02 分腿垫步

适用：return_forehand, return_two_hand_backhand, return_one_hand_backhand, return_backhand_slice, return_forehand_slice；阶段：`split_step`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 最重要的是分腿垫步的时机。（VIS-RT:word/document.xml:P0016）
- 落地后应立即进入第一步启动，不能继续原地等待。（VIS-RT:word/document.xml:P0017）
- 动作幅度应小而快速，不应出现过高起跳。（VIS-RT:word/document.xml:P0018）
- 脚掌撑地，脚后跟不触地。（VIS-RT:word/document.xml:P0019）

### VIS-RT-S03 启动调整

适用：return_forehand, return_two_hand_backhand, return_one_hand_backhand, return_backhand_slice, return_forehand_slice；阶段：`first_step`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 分腿垫步与第一步启动必须连续。（VIS-RT:word/document.xml:P0024）
- 第一步方向应与来球方向和后续击球侧一致。（VIS-RT:word/document.xml:P0025）
- 接发强调快速、短距离调整，避免不必要的大幅移动。（VIS-RT:word/document.xml:P0026）
- 启动后应尽快建立后续击球所需的稳定支撑。（VIS-RT:word/document.xml:P0027）

### VIS-RT-S04 短引拍稳定

适用：return_forehand, return_two_hand_backhand, return_one_hand_backhand, return_backhand_slice, return_forehand_slice；阶段：`compact_preparation`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 这是接发区别于普通底线击球最重要的视觉特征之一。（VIS-RT:word/document.xml:P0032）
- 接发不是完整的大引拍，应强调短、快、稳定。（VIS-RT:word/document.xml:P0033）
- 引拍主要由身体转动带动，避免只有手臂大幅后拉。（VIS-RT:word/document.xml:P0034）
- 引拍完成后应形成稳定调整状态，再进入挥拍击打。（VIS-RT:word/document.xml:P0035）
- 发球越快，可用准备时间越短，因此模型应关注相对动作幅度与时间，而不是固定绝对阈值。（VIS-RT:word/document.xml:P0036）

### VIS-RT-S05 挥拍击打

适用：return_forehand, return_two_hand_backhand, return_one_hand_backhand, return_backhand_slice, return_forehand_slice；阶段：`strike`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 挥拍必须紧接短引拍稳定阶段，不能出现明显停顿。（VIS-RT:word/document.xml:P0041）
- 接发强调及时、简洁和稳定，不追求过大的挥拍幅度。（VIS-RT:word/document.xml:P0042）
- 身体转动与手臂/双手加速应形成连续动作。（VIS-RT:word/document.xml:P0043）
- 没有可靠球/球拍轨迹时，只识别“预计击球窗口”，不强行判断真实触球质量。（VIS-RT:word/document.xml:P0044）
- 重点：会拍时重心撞向击球方向。（VIS-RT:word/document.xml:P0045）

### VIS-RT-S06 收拍恢复

适用：return_forehand, return_two_hand_backhand, return_one_hand_backhand, return_backhand_slice, return_forehand_slice；阶段：`recovery`；原文允许省略：未明示。

映射：一致；技术/阶段存在并保持主要定性动作链；不表示逐条量化指标已经实现。

- 随挥应完整但不宜过大，以免影响下一拍恢复。（VIS-RT:word/document.xml:P0050）
- 击球后应快速重新建立身体平衡。（VIS-RT:word/document.xml:P0051）
- 恢复后应能够自然衔接下一动作。（VIS-RT:word/document.xml:P0052）
- 接发结束并不代表回合结束，因此“恢复能力”是接发动作链的重要组成部分。（VIS-RT:word/document.xml:P0053）

## 来源优先级及前端解释

- 五份视觉文档负责技术定义、阶段和证据边界；两份 CARD 文档负责正式指标和其明确评分语义，两者不能仅凭文件时间互相覆盖。
- 实施文件只证明当前工程行为，不能反过来填补原文缺失的分数或阈值。
- 本交接新增 ID、拆句、工程映射与观察要求归纳均为审计层，不是教练真值；存在歧义保留并列来源，等待明确确认。
- 短支撑/蓄力原文明示可缺的阶段视为条件适用；缺测 unavailable 与可观测但动作未发生是不同状态。

- 分别显示技术分类、观测结果/代理范围、指标依据、证据完备度和标定后技术评分；无标定不展示正式动作100分/扣分/评级。
- 每条结论附视频时间窗口、目标球员、观测通道、原始测量与单位、源文段落/表格定位、评分规则版本；目前只具备文档来源时显示待观测。
- 观测状态至少区分 observed / proxy_only / unavailable / not_applicable / needs_calibration；不得把缺测写成动作错误或扣分。
- GS 盯球显示为全程要求，不占互斥单独阶段；五阶段简化解释与十阶段正式卡可切换并展示映射依据。
- 显示24种子项：底线5、发球1、接发5、网前3、步伐10；高压包含后退来球，不能漏反手/正手切削或将高压合为截击。
- 发球1.8倍只展示约数参考和测量条件；抬腿仅可选正向观察，不能显示未经定义的加分数值。
- 没有球/球拍轨迹时显示预计击球窗口；无球路/对手/标定时回位只显示发生移动；头部稳定不称眼睛盯球真值。

本次仅审阅五份视觉定义。另两份正式评分卡的 298 个编号指标及其 A–E、质量门槛等，需要合并 CARD 子审计后才能判断原文明示与工程补充；不得把本报告中“视觉文档未给出”误写成“七份文档都没有”。
