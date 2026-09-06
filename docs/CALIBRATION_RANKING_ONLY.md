# 教练纯排序标注的安全标定路径

## 结论

RallyMate 现在支持把多名教练的 `ranking` 标注拟合成一个可复算的
`relative_order_only` 候选。该候选只回答“同一指标下，哪一个事件相对更好”，
不能回答它是 A、B、C、D 还是 E。

排序没有绝对等级锚点，因此本路径在数据准备、拟合、预测和独立测试四层均强制：

- `grade=null`；
- `threshold_version=null`；
- 不允许 `thresholds`、`cutpoints` 或 A～E 数值映射；
- `production_scoring_allowed=false`；
- 即便独立排序测试通过，也只能写为 `passed_relative_order_test`，不能批准正式评分。

若后续需要 A～E，必须另行导入教练 A～E 锚点并进入现有阈值规则或序数回归标定、
F0→F4 证据和 production promotion 流程。不能把相对名次按五等分映射成 A～E。

## 输入与无泄漏约束

数据准备入口消费 `compile_calibration_dataset.py` 的完整 `samples.jsonl`。每个样本必须：

- 来自人工事件边界且必需阶段完整；
- 具备完整、有效、版本一致的指标特征；
- 具备完整语义真值；
- 包含原始逐教练 `ranking` 对象，而不是聚合后的平均名次；
- 包含 `player_id`、`session_id`、`view_group` 和 `leakage_group_id`；
- 已由预注册 group-holdout 策略分为 train、validation、independent_test。

数据准备器逐教练、逐 `rank_group_id` 生成 Bradley–Terry pairwise 样本。约定
`rank=1` 最好，相同 rank 表示 tie。它会拒绝：

- 任一 leakage group 跨 split；
- 任一 `rank_group_id` 跨 split；
- 排序标签与 sample 的 video/event/indicator 不精确一致；
- 同一教练在同一排序组内重复标同一事件；
- 排序专用路径中混入 A～E 标签；
- 缺少两名教练共享可比较 pair，或 Kendall tau 无法计算。

独立测试的完整样本对象按 `sample_id` 排序后，以
`rallymate-canonical-json-v1` 计算 SHA-256。拟合数据集仅保存 seal、样本 ID 和组 ID，
不保存测试事件、特征或标签。测试执行器必须重新提交完整测试对象并精确匹配 seal。

## 版本化协议和后端

拟合协议必须在拟合前注册，并绑定以下不可变内容：

- dataset ID/version/canonical SHA-256；
- independent-test seal ID/content SHA-256；
- 数据量、教练数、共享 pair 和 Kendall tau 的研究者预注册门槛；
- 优化器、学习率、L2、收敛容差和 tie 策略。

后端为 `pairwise_logistic_ranker`，使用 pairwise Bradley–Terry logistic loss。
候选保存每项特征的系数、单位、特征函数版本以及方向解释。预测输出包括：

- `relative_score`；
- `relative_rank`；
- 每项特征的线性 contribution；
- 特征单位和特征函数版本必须与候选精确一致；
- `status=candidate_relative_order_not_scored`；
- `grade=null`。

`relative_score` 只在同一候选版本和同一指标内有意义，不能跨指标当作总分，也没有
“80 分”“A级”之类绝对语义。

## 命令

先从完整编译样本生成 ranking-only 数据集：

```powershell
python scripts/prepare_ranking_calibration_dataset.py `
  --samples reports/calibration-datasets/<version>/samples.jsonl `
  --indicator-id FS01-M02 `
  --source-dataset-id <dataset-id> `
  --source-dataset-version <dataset-version> `
  --source-kind human_coach_ranking_ground_truth `
  --prepared-at <ISO-8601-time> `
  --output reports/ranking-calibration/<version>/dataset.json
```

在未查看测试标签和结果前填写并登记拟合协议，然后拟合候选：

```powershell
python scripts/fit_ranking_calibration_candidate.py `
  --dataset reports/ranking-calibration/<version>/dataset.json `
  --protocol <registered-ranking-fit-protocol.json> `
  --fitted-at <ISO-8601-time-after-registration> `
  --output reports/ranking-calibration/<version>/candidate.json
```

独立评测者另行预注册测试协议，再解封测试样本：

```powershell
python scripts/evaluate_ranking_independent_test.py `
  --candidate reports/ranking-calibration/<version>/candidate.json `
  --protocol <registered-independent-ranking-test-protocol.json> `
  --samples <sealed-independent-test-samples.jsonl> `
  --evaluated-at <ISO-8601-time-after-registration> `
  --output reports/ranking-calibration/<version>/independent-test-report.json
```

协议中的最小样本量、Kendall tau、准确率和 log-loss 门槛必须由研究设计负责人在查看
对应标签或结果前预注册。仓库不提供经验默认值。

## 契约

- `contracts/calibration-ranking-dataset.schema.json`
- `contracts/calibration-ranking-fit-protocol.schema.json`
- `contracts/calibration-ranking-candidate.schema.json`
- `contracts/calibration-ranking-independent-test-protocol.schema.json`
- `contracts/calibration-ranking-independent-test-report.schema.json`

核心实现位于 `src/rallymate_scoring/calibration_ranking.py`。该 candidate 使用独立 backend
和 artifact scope，现有 A～E candidate validator、production promotion 和正式评分加载器均会拒绝它。
