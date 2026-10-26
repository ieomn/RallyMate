# 网球动作评分验证记录 · 2026-10-26

本轮**没有通过职业与普通球员的等级区分验证**。在独立留出的有效来源组中，新版职业均分为 **87.16**，普通均分为 **87.57**；职业高于普通的两两排序比例从旧版 **50% 降至 25%**（2/4 → 1/4 对）。分数饱和有所减少，但排序没有改善。该评分目前只描述视频中部分可见动作质量，不能用作竞技等级、真实发力、击球效果或比赛能力排名。

机器可读证据见 [脱敏复评清单](evidence/tennis-score-20261026-manifest.json) 和 [结果、来源及性能证据](evidence/tennis-score-20261026-provenance.json)。后者保留每条记录的旧版/新版原始输出，包括未评分、跟踪失败及画质变体；没有发布原视频或姿态坐标。

## 样本与比较方法

共有 **25 条评测记录**：开发 2 条、既有回归 2 条、留出 14 条、重复/鲁棒性 7 条。其中 **19 条进行了新增视频推理**；另外 6 条复用既有任务的保存姿态，包括同源复测。25 条不是 25 个独立球员。新版产生 18 条原始分数，另外 7 条未评分；有分数也不等于其主目标归属有效。

开发来源固定为 `ordinary-850cb` 和 Sharapova；`ordinary-ece12` 与 Nadal 仅作既有回归。新增来源在查看新分数前锁定为留出，不用于公式选择。同一原片、相邻片段、压缩、镜像和同一摄影来源保持在同组。跨来源是否出现同一个人没有全部独立核验，因此统计单位是**来源组**，不是独立人员数。

每组先对有效视频分数取均值，再对来源组等权汇总；重复和鲁棒性记录不增加独立数。留出集尝试了 **11 个来源组**，只有 **5 个组**有可归属的评分：4 个普通组、1 个职业组。其余 6 组的未评分或跟踪失败均保留。职业有效组是 JC/demiace 拍摄的 Djokovic 与 Schiavone 两段，不能把这两段当作两个独立摄影来源。全体开发、回归、留出合计有 9 个可评分来源组。

普通标签来自用户说明，未按画面猜测职业身份，包含 `1012bc1e…` 和 `65408bed…`。公开职业身份依据原始文件说明、作者及赛事来源；文件标题不自动证明主框选中了该球员。Rola 的 NCAA 2013 片段保持 `unknown` / 拍摄时精英大学球员参考，不回溯套用之后的职业身份。身份标签仅供评测分组，不传入评分函数。

旧版为 Git `794ea15` / rubric `v1.0.0`，新版为本次冻结工作区 / rubric `v1.1.0`。两版读取相同的冻结 `summary.json`、`primary-player.jsonl` 与 `sam3d_frames.jsonl`，按真实 decoder PTS/time base 处理；复评本身不调用 GPU。保存快照是为了避免后台 3D 预览更新 `summary.json` 改变对照输入。文件和引擎 SHA-256 均在证据 JSON 中。

本次实现把屈膝准备、准备站距、准备后半段转体和挥拍手臂空间改为按实际 PTS 持续时间积分，减少瞬间达标代表整段达标的问题；发球仅屈膝准备使用这一积分。新增同侧下肢伸展连贯性，以及观察到真实腕速峰值和首次恢复边界时的随挥速度衔接，缺少边界时保持缺测。保留五维等权、动作间等权和缺测项目重归一化，公开项目权重能复算汇总值。页面新增有测量依据的优点、对应片段秒数与每组 8 次、共 2 组的具体练习，以 8 次中至少 6 次做到作为复测目标；高分项目显示巩固建议，完整原片、3D 回放和全部检测片段保留。

## 结果与失败边界

独立留出的旧版职业/普通组均分为 93.265 / 92.175，新版为 87.155 / 87.5725。4 对比较来自 1 个职业组乘以 4 个普通组；这只是小样本描述性排序，不是分类准确率，也不足以估计跨球员泛化。没有为使职业得分更高而再次调阈值。

全体 9 个有效来源组中，90 分以上从 7 组减为 4 组。同一动作中可一一对应的 439 项共有指标里，99 分以上从 358 项减为 323 项（81.5% → 73.6%）；比较没有把新版新增指标混入旧版分母。饱和仍明显，尤其不能把高姿态分解释为真实动力链或击球质量已被验证。

按同一标准人工检查主轨迹后，**8 条标为 `invalid_subject_tracking`**：ordinary night、ordinary indoor pink、Rola、Tsitsipas/Alcaraz、Davis Cup 两条、Federer/Cilic、Dimitrov/Tiafoe。具体时间与原因保留在清单中：包括黄衣击球者切到另一侧白衣人员、粉衣击球者切到另一半场搭档、场边线审/椅旁人员/观众被当作球员等。没有截掉失败部分再选择有利窗口；身份标签与原始数值不改变。

审核范围有明确限制：职业补充视频主要按每秒及末帧查看，部分单人及双视角视频只查首、中、尾；这不是逐帧身份认证。`65408bed…` 在 24.00–26.67 秒把片尾头像识别为人体，但最终评分窗为 2.033–3.500、5.100–6.233、7.333–8.767、15.100–16.533 秒，均早于片尾，因此保留评分窗 `valid` 并记录质量警告。其他整段归属失败中，误选是否恰好落入每个评分动作，仍需更细的窗口核对，当前采取保守归属判断。

另有 1 条压缩变体标为 `invalid_measurement_consistency`。25 条记录因此为 16 条 `valid`、8 条跟踪无效、1 条测量一致性无效；`valid` 也可能因动作不足而未评分。

严格有效性结果之外，还保留了将跟踪失败原始分数按原标签计入的敏感性结果：留出职业高于普通为 **33.3%（4/12 对）**，涉及 2 个职业组和 6 个普通组。该结果含混合人物的输出，只披露有效性排除的影响，不能用于评价对应球员。

## 真实画面鲁棒性

- 同源复测：14 个动作全部配对，最大动作分差 0.00；这是重复性检查，不增加独立证据。
- 同窗 360p / CRF35 压缩：原片 5 动作变为 1 动作，只配对 1 个，4 个原片动作丢失或无法匹配。已匹配动作分差 2.59，挥拍手识别改变 1 个，结束边界提前 0.80 秒。不能因整片分数接近就声称压缩稳定。
- 水平镜像：原片 5 动作，镜像 6 动作；5 个配对动作最大绝对分差 **11.48**，结束边界最多提前 0.60 秒。5 个挥拍手均对调符合镜像预期，但动作计数与分数仍不稳定。
- Mendeley 两组真实双机位：预先选定室外与室内 CrossCourt 01 侧视/俯视配对，4 段均实际推理。侧视分别得到 1 动作、73.29 与 80.90 分，俯视均 0 动作、未评分。作者的同编号配对规则、相同持续时间和可见动作进程支持同次动作配对，但绝对时间同步未独立标定；片长仅约 1.37 / 1.70 秒，机位与动作完整度的影响不能分离。

动作配对使用同 family、时间窗 IoU ≥ 0.35、峰值时间差 ≤ 500 ms 的一对一贪心匹配。保存姿态的刚体/尺度不变性检查只验证几何计算，不能替代上述重新经过人体估计的真实视频实验。

## 来源、许可与获取失败

本轮新增公开媒体使用来源明确的许可材料，记录作者、文件页、许可页、原始文件与提交文件 SHA、截取和转码。Commons 与 Mendeley 的媒体原件及衍生文件均留在本机，没有随本报告分发。主要来源如下：

- [Djokovic](https://commons.wikimedia.org/wiki/File:2012_US_Open_Djokovic_point.webm)、[Schiavone](https://commons.wikimedia.org/wiki/File:2012_US_Open_Schiavone_rally.webm)、[Federer/Cilic](https://commons.wikimedia.org/wiki/File:Federer_v._Clilic_Practice_with_Crowd.webm)：JC（Flickr: demiace），CC BY-SA 2.0；原始 Flickr 链接及 Commons 许可审核记录保留在证据 JSON。三条作为一个摄影来源组，最后一条跟踪失败仍保留。
- [Dimitrov/Tiafoe](https://commons.wikimedia.org/wiki/File:2024_US_Open._Grigor_Dimitrov_vs._Frances_Tiafoe.webm)：署名 **Oleg Yunakov / CC BY-SA 4.0**。实际推理后发现比赛中与结束摇镜阶段均会选错主目标。
- [Tsitsipas/Alcaraz](https://commons.wikimedia.org/wiki/File:Wave_at_Alcaraz%E2%80%93Tsitsipas_practice_at_the_2023_French_Open.webm)：Hameltion，CC BY-SA 4.0；[Davis Cup 04](https://commons.wikimedia.org/wiki/File:2018_Davis_Cup_Americas_Zone_-_Uruguay_vs_Mexico_-_04.webm) 和 [05](https://commons.wikimedia.org/wiki/File:2018_Davis_Cup_Americas_Zone_-_Uruguay_vs_Mexico_-_05.webm)：NaBUru38，CC BY-SA 4.0，按命名比赛群体记录，未强行归到某个主框人物。
- [Rola NCAA 2013](https://commons.wikimedia.org/wiki/File:Bla%C5%BE_Rola_NCAAs_2013.webm)：ZooTennis.com / tenniskalamazoo，CC BY 3.0；[Mendeley 双机位数据 v4](https://data.mendeley.com/datasets/75m8vz7jr2/4)：Kalin Guanlun Lai 等，CC BY 4.0，DOI `10.17632/75m8vz7jr2.4`，下载文件与发布方 SHA 匹配。

既有 Sharapova 来源为 javajoba 的 CC BY 2.0 文件。既有 Nadal 为 ATP 版权来源，本轮只复用本地保存姿态，没有新增下载或分发。用户 FULL-TEST 与用户视频没有公开再分发授权，不随证据提交。

获取阶段的拒绝也保留：Djokovic 采访缺少击球动作；Dailymotion Federer 候选只有音乐 CC 描述、未建立画面许可；MIT OCW 候选具有课程级非商业许可及外部媒体提示，本轮未纳入；历史新闻片、动画及未命名球员不满足本轮身份/内容条件。另有 BNP 2013 候选虽有 CC 许可但人物未命名，以及 Alcaraz/Wu、Lu/Monfils 文件页不可读取导致许可未核验，均未新增推理。这些是未纳入候选，不计作已分析的 25 条记录。

初次 Commons 请求遇到 HTTP 403、429 后停止并行请求、等待，之后以描述研究用途的 User-Agent 顺序重试，选定四条文件返回 200；未绕过认证。获取拒绝清单来自补充记录，原冻结主清单的 `acquisition_attempts` 为空；本次补充 provenance 保留该事实，没有补写原文件或重算替换其历史 hash。

## 成本与验证

本机硬件为 RTX 5070 Ti / Core Ultra 7 265K。19 个新增视频完整分析耗时 **16.0–294.4 秒/条**，任务运行时长合计约 1857 秒（31.0 分钟）；不含排队，包含解码、人体推理和产物生成，不能理解为新增评分函数耗时。逐任务原始姿态记录的推理中位耗时约 77.6–84.3 ms，CUDA allocated 峰值约 3.44–3.47 GiB；该值包含已加载模型，并非总显存占用。

保存姿态的 25 条 CPU 评分耗时中位数范围为 0.001–0.888 秒；包含未评分的早退出。18 份产出分数的计算范围为 0.029–0.888 秒，含姿态文件读取的首次构建为 0.044–2.165 秒。同输入旧版/新版的跨样本中位数为 0.142 / 0.145 秒，逐样本增量中位数约 +0.003 秒。操作系统文件缓存未清空，测量时可能有后台 3D 预览并行，不能据微小差值宣称性能提升。

25/25 份保存姿态通过三次确定性重算、公开构建器/缓存结果与输入文件未变检查；75/75 份刚体尺度变换、静止反事实、错误来源变体检查通过。工程 QA：后端组合 91 项通过；前端完整 911 项中 910 通过、1 跳过；最后文案修改后的 17 项相关测试、production build、lint、typecheck 通过。25 份报告严格解析、原视频 SHA、备份和导航往返检查通过。以上验证证明具体实现与来源绑定检查成立，不证明职业等级区分或真实画面鲁棒性已达标。

2026-10-26 12:12:56 UTC 的只读服务验证中，本机与临时公网的首页、评测页、普通 A 评分接口共 6/6 次 HTTP 200；25 条记录在两端共 50 次 API 比较，分数、动作数、版本、任务 ID 与来源 SHA 均匹配冻结结果。25 份实际原视频重新读取 SHA 校验；夜间与镜像两份 mesh 均为 ready，两端访问均为 200。此次验证没有请求 GPU 推理。

最终浏览器检查中，本机全部通过且 console/page/HTTP 错误均为 0。公网在 12:29:38 UTC 完成本轮检查，完整报告首读成功、重试 0 次；88.36 分（界面 88.4）、五维、14 段导航、2.833 秒片段定位、原片/3D/侧面切换保持 12 秒、训练建议跳转到 43.733 秒、390px 无整页横向溢出，以及 25 条评测的筛选/详情/无效样本不展示训练均通过。公网记录到一次 `sam3d-mesh.mp4` 的 `net::ERR_HTTP2_PROTOCOL_ERROR`，因此**严格无错误门禁未通过**，不能声称公网视频传输已稳定。视觉检查还发现侧面 90° 将两人同时纳入画面，模型过小；主视角清晰，但侧面视角的动作复核体验仍需改进。

普通 A 的完整报告在本机传输时 gzip 约 3.40 MB、解压 JSON 约 51.36 MB，公网初次下载明显慢于小型评分接口。曾有浏览器测试在报告仍接收数据时于 90 秒提前结束，该次结果保留为测试等待不足，没有记成产品失败；最终按完整等待预算重新核验。另修复了新增评测页缺少 favicon 引用及静态文件替换后的旧 Content-Length 缓存问题，并通过维护脚本在空闲时刷新服务，保留既有 Cloudflare 进程。

## 复评与冻结摘要

原冻结清单 SHA-256 为 `1c67c4044cca505c076e72ec8541fbb4c21a302e666f9df6d6590e7013515bb6`。脱敏清单保留它为 `source_frozen_manifest_sha256`，并拥有自己的文件 hash；两者不能混同。媒体、冻结姿态与引擎 hash 按原证据逐字保留，未改写历史冻结文件。

在具有授权原始姿态输入、项目依赖和 Git `794ea15` 的本地仓库中，可运行以下 PowerShell 命令。`--run-root` 需要包含按 job ID 命名的冻结目录，且每个目录有上述三份输入文件；复评前应与清单的 `frozen_artifact_sha256` 核对。新版源文件也应与 provenance 中 `after.engine_sha256` 一致，否则是新实验。公开清单本身不能在缺少这些私有/未发布输入时重建历史分数。

```powershell
$evalManifest = "docs/evidence/tennis-score-20261026-manifest.json"
$poseSnapshot = ".codex_tmp/tennis-score-round2-20261026/frozen-runs"
python scripts/evaluate_tennis_assessment.py --manifest $evalManifest --run-root $poseSnapshot --output .codex_tmp/tennis-evidence-recheck/before --revision 794ea15 --repeats 3 --variants
python scripts/evaluate_tennis_assessment.py --manifest $evalManifest --run-root $poseSnapshot --output .codex_tmp/tennis-evidence-recheck/after --repeats 3 --variants
python -c "import json,pathlib; p=pathlib.Path('docs/evidence/tennis-score-20261026-provenance.json'); pathlib.Path('.codex_tmp/tennis-evidence-recheck/performance.json').write_text(json.dumps(json.loads(p.read_text(encoding='utf-8'))['supplement']['performance']),encoding='utf-8')"
python scripts/summarize_tennis_evaluation.py --before .codex_tmp/tennis-evidence-recheck/before/evaluation.json --after .codex_tmp/tennis-evidence-recheck/after/evaluation.json --performance .codex_tmp/tennis-evidence-recheck/performance.json --output .codex_tmp/tennis-evidence-recheck/supplement.json
```

如需从稳定的本地任务重新建立输入快照，使用 `scripts/freeze_tennis_evaluation_inputs.py --manifest <清单> --run-root <本地任务目录> --output <不存在的新目录>`；不要覆盖历史快照。脱敏清单重跑会得到新的 manifest hash 和计时，不能以其覆盖本次冻结 provenance。
