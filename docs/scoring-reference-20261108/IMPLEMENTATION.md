# 2026-11-08 评分重构：实现、观测边界与验收方法

本轮以同目录 `rallymate_scoring_audit_and_codex_guide.md` 为开发依据。10 份 Word 按用户要求原样归档，没有为本轮重新读取正文。项目既有实现明显晚于 MD 中引用的早期审计，因此复用已有 SAM 原生人体、身份绑定、球检测、场地标定和动作评分管线，不以早期审计中的“未实现”描述覆盖当前代码。

## 交付入口与评分原则

功能接入当前项目的正式动作结果组件 `scoring-demo-web/app/TennisAssessmentSummary.tsx` 和 `src/rallymate_service/tennis_assessment.py`，不是另建一个演示评分页面。原结果页显示动作分、Lv1–Lv5 评估、动作阶段关节测量和训练片段信息。

- 从实际视频、实际人物、实际时间窗取得可计算的动作量，再应用明确的工程评分函数；不使用文件名、哈希扰动或固定兜底分。
- 缺少某个可选模态或某种子技术，只影响该项的可观测范围。保留同一视频已有的有效动作分，不把缺测当作零分或低技术水平。
- F0–F4、人工审批状态、`assessment_status`、`scoring_allowed` 不再作为有来源、有数值、有数学规则的通用否决条件。评分准确性是否经教练真值验证仍单独记录，不能因取消审批门槛而伪称已校准。
- 视频 SHA、原始 PTS、人物、轨迹、选择时期、相机参考和实际阶段边界的检查保留。混用视频、人物或错误时钟不是“缺少审批”，仍必须拒绝相应证据。
- 关键点有效帧覆盖统一使用含边界的 70% 规则。它与单点模型置信度不同；原有单点置信度规则不被改成 70%，SAM 缺少置信度时也不补造数值。

## 已接入的模块

| 实现 | 作用与实际范围 |
| --- | --- |
| `rallymate_features/perception_dictionary.py`、`contracts/perception-measurement.schema.json` | 注册 J/L/RK/BALL/S/V/K/T 的编号范围；共 551 个规范/兼容编号。修复 S013 双义、补充 V001、归一化 BALL 别名。J 范围包含 MD 下肢段明确提到的 J179。未被 MD 逐项定义的编号标为 `reserved`，不会因为注册就宣称已经检测到。 |
| `rallymate_scoring/level_grading.py`、`data/level_grading_v1.json`、前端 `app/scoring/level-grading.ts` | Lv1–Lv5 八系统大类权重、单反/双反互斥、激活权重归一化、晋级条件和覆盖情况。晋级条件未观察到时保留动作分，单独显示待观察。 |
| `rallymate_service/level_assessment.py` | 从同一人物的真实动作分生成五级结果。未知发球旋转子类仍能提供已观察发球动作类别分，不复制成四类发球。步伐类别分来自实际挥拍支撑，不冒充全部 FS01–FS10 子项。 |
| `rallymate_features/fs01_fs02_features.py` | 新增观察到预加载的髋纵向下降量、双膝屈曲增量，以及落地候选阶段的前脚掌站距/肩宽比。严格使用对应阶段；后续第一步不混入分腿垫步指标。细足点缺失时不把踝点冒充前脚掌。 |
| `rallymate_features/fs09_features.py`、`rallymate_events/rules.py` | 显式输出惯性方向观察和制动脚减速候选锚点。脚部减速候选不冒充足底压力或已经确认的落地接触。 |
| `rallymate_features/source_alignment.py` | FS01 稳定和 FS09 稳定控制只用本事件窗口；另列跨事件间隔，不因缺少后继 FS02/FS10 而删除本事件稳定性。 |
| `rallymate_events/gs_phase_segmentation.py`、`rallymate_features/gs_features.py` | 根据实际膝屈曲、肩髋转动、腕轨迹等转折产生十阶段候选；计算膝、肘、肩角度、肩髋分离、站距和原 PTS 角速度。抽球、切削、发球使用不同阶段语义，不做十等分，也不把挥拍峰叫作真实触球。 |
| `rallymate_scoring/modality_features.py` | 读取真实球框、球拍框、明确提供的 RK 关键点和同帧场地标定；计算相机补偿后的图像球速，保留单位。球拍框不会被推导成甜区、三角区或拍面法向量。 |
| `rallymate_scoring/tactical_analyzer.py` | 具备来源绑定落点时输出 TS08 八类空间标签与 TS01–TS07 场景归类。防守/截击/接发采用紧凑控制规则。没有落点、人物和触球关系时不编造战术意图。 |
| `rallymate_service/specialist_motion_assessment.py` | 已确认接发自动计算短引拍、挥出路径效率和短收拍贡献，替换冲突的完整转体/长随挥奖励；同一条人体路径不需要背景相机信息。DS/NS 分类仍需真实且绑定的来球、位置、触球/反弹关系，不能仅凭短挥拍改类。 |
| `rallymate_service/training_session_analysis.py` | 从已评分动作生成可回看的训练片段索引、关节活动范围摘要，以及有明确同视频比赛记录时的统计。心理比较和标准人体模板使用明确输入，不把动作紧张或关节病变当作单视频直接测量。 |

### 权重与术语的来源边界

MD 提供了完整的大类权重表，但没有给出所有等级下每个子技术的完整内部占比。配置保存 MD 中出现的实例，其余采用明确标注、可覆盖的工程初始权重。不能表述为“逐项还原未读取 Word 中的全部权重”。

MD 写出 TS01–TS08、共 64 子项的框架，但没有逐条给出全部 64 子项的精确条件。本轮实现明确的 TS08 八个空间条件及七个上层场景，并保留来源条件；不将 64 个名称占位视作 64 个已经可靠识别的战术规则。

新建的规范编号字典也是契约，不是 551 个已训练完成的视觉检测器。`reserved` 与 `measurement_available=false` 明确保留这一差别。

## 多模态来源与用户确认的拍摄限制

用户在本轮针对 **320×568、缺少完整球场视野** 的样本，已明确选择“按此验收，保留有效动作评分（推荐）”：转速、真实过网高度和完整比赛得分率等不能从该拍摄条件可靠取得的量，不伪造数值，已有有效人体动作评分继续交付。这项确认只覆盖明确说明的不可观测量，不代表用户允许遗漏可从现有视频修复的评分，也不代表允许没有有效动作分的视频通过验收。

现有单目结果仍遵守以下物理含义：

- 原生 3D 关节是单目模型估计；关节角度可以计算，不能因此称为实验室动作捕捉精度。
- 图像球速以画面长边/秒表示；未取得适当三维标定，不转写成 m/s。
- 地面单应性只能用于已确认地面点。空中球投影不叫落点，也不能用于真实过网高度。
- 落点标签要求同视频、同球轨迹、同人物触球、连续来源观测以及随后已确认反弹；其他人的下一次反弹不能关联到本人的挥拍。
- 真实旋转、眼睛注视、拍面法向量、足底受力和心理状态，不由人体姿态或球框虚构。

## 数学与代码验收

`reports/scoring-refactor-20261108/backend-final-regression.json` 和同名 `.log` 保存最终集中回归结果：**2026-11-08 23:44（Asia/Shanghai），35 个模块、414 项通过，0 失败、0 错误、0 跳过**。这轮覆盖字典/Schema、FS 测量、角度与十阶段、来源绑定、模态来源隔离、专项规则、权重、导航时钟、人物重新关联和服务接入；它不代替真实视频验收。

其中专门检查：

1. 70% 边界、退化骨段、缺失关键点、PTS 不等间隔、跨缺测不求角速度。
2. 反手互斥、Lv2/Lv3 激活项归一化、零分与缺测的区别、晋级条件不删除动作分。
3. 提高或删除成熟度/审批标签不会改变已绑定阶段及实际评分；错误 SHA、人物、时期或阶段仍拒绝。
4. 跑反目标方向产生 180° 偏差；无独立目标时只报告自身移动，不奖励“方向正确”。
5. 球框不能产生 RK005；空中球不能变落点；跨视频或另一人物的反弹不能产生战术标签。
6. 同样的长引拍/长收拍在接发专项下低于紧凑路径；静止姿态不能得到完美短挥拍分。缺一项只移除该项贡献。
7. 原有发球、来源阶段、前脚掌回退语义和同人物评分回归保留。

在项目根目录可重跑关键集合：

```powershell
$env:PYTHONPATH = "src"
& .\runtime\rtmpose\.venv\Scripts\python.exe -m unittest tests.test_perception_dictionary_contract tests.test_scoring_guide_features tests.test_phase_authority_metadata tests.test_tennis_candidate_binding tests.test_specialist_motion_assessment tests.test_multimodal_scoring tests.test_gs_phase_kinematics tests.test_level_grading tests.test_level_assessment_integration tests.test_training_session_analysis tests.test_direct_scoring_rules -v
npm.cmd --prefix scoring-demo-web run typecheck
npm.cmd --prefix scoring-demo-web run lint
npm.cmd --prefix scoring-demo-web run build
```

前端必须使用当前 `validatedTennisAssessment` 解析新报告，保留关节窗口和五级结果；只检查后端 JSON 中有数字不算页面验收。

最终全量前端回归见 `frontend-regression-final.json`：1072 项中 1071 通过、0 失败，1 项依赖外部 `FORMAL_V2_REPORT` 回放文件的可选测试跳过；本批真实 FULL-TEST 报告另行逐条验收。类型检查、全量 ESLint 和生产构建均通过。

实际网关交付检查另外发现并修复了长视频首次读取超时：浏览器原上限 20 秒、网关原上限 30 秒，而已核验长视频冷计算约需 32–48 秒。`/tennis-assessment` 专用请求改为网关 120 秒、浏览器 125 秒，其余读取预算保持原值。五项新增传输测试覆盖 46 秒冷读取、请求/响应阶段的主动取消、有界超时和原有一次重试上限。最终验收在重启服务清空内存缓存后重新读取全部 10 份报告，并完整比较对象、来源、时钟、分数和测量数据。

`frontend-lifecycle-regression.json` 和同名 `.log` 另外记录 **91 项前端生命周期/回放回归全部通过**。动作详情、概览和球路请求现在根据视频来源、任务、加载器和重试编号派生读取状态，不再在 effect 内同步重置状态；切换来源不会短暂显示旧动作结果，重试会立即清除对应错误。姿态预取缓存以带加载器身份的不可变状态参与渲染，保持跨窗口即时回放、来源隔离和请求取消。当前类型检查与这三处代码的 ESLint 检查通过，没有关闭规则。

`segmented-lifecycle-regression.json` 和同名 `.log` 记录 **24 项分段报告回归全部通过**。动作列表翻页时保留同来源的上一页；详情停止、重试和切换动作均维持取消语义；新报告来源立即清除旧列表与选中动作，旧动作不能请求新来源的证据。`SegmentedReport.tsx` 同样改用请求身份派生等待状态，移除了 effect 同步状态更新和遗漏依赖问题。

## FULL-TEST 的最终验收条件

`scripts/validate_full_test_scoring.py` 对 `FULL-TEST` 建立视频 SHA/帧数/分辨率清单。只在原视频 SHA、完整帧范围及原生 SAM 产物匹配时复用既有推理；评分使用当前工作树重新计算，不能复制旧报告分数。缺少完整推理的样本走实际服务补跑。

最终状态以以下运行产物为准，后续重跑会更新它们：

- `video-inventory.json`：10 个输入文件及完整来源指纹；重复文件可共用同一 SHA 的推理证据，但文件逐项列入验收。
- `selected-runs.json`：每个输入对应的真实完整推理记录。
- `validation-results.json`：逐视频有效动作数、评分、来源检查与失败列表；只有所有输入均有有效数值评分且 `all_passed=true` 才算视频评分验收通过。
- `frontend-validation.json`：当前前端解析器对实际报告的校验；与上一轮构建或旧缓存混用的结果必须重跑。

最终交付结果见 `reports/scoring-refactor-20261108/ACCEPTANCE.md`，服务返回值检查见 `api-delivery-validation.json`，对应前端解析见 `frontend-delivery-validation.json`。后续遇到没有有效分的视频，应继续定位识别、跟踪、阶段、原生姿态或报告问题；只有确认无法从拍摄内容恢复后，才按用户要求说明具体视频和原因。
