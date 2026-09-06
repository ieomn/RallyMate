# RallyMate Score Lab

可运行的 GS 底线击球 + FS 步伐事件评分系统 Demo。

## Demo 包含

- 从两份源指标卡抽取的 298 条规则：GS 248 条，FS 50 条。
- 完整验收演示：298 项全部经过同一套证据门禁、四维评分、A—E 分级和模块聚合逻辑。
- 真实数据审慎模式：内置四组一期 FULL-TEST 摘要，也可在页面导入新的 `summary.json`。
- 可追溯报告：每项展示依赖、计算方式、等级原文和 AI 教练反馈，并可导出 JSON。
- HTTP 接口：`GET /api/scorecard` 查看能力；`POST /api/scorecard` 生成报告。

## 评分边界

完整演示模式使用可复现的合成特征，目的是验收评分系统，不代表真实球员表现。真实一期数据缺少事件切分和标定后的技术特征，所以只给证据就绪度，不生成技术等级。

## 本地运行

```bash
npm install
npm run dev
```

构建与验收：

```bash
npm test
```

规则注册表由 `scripts/extract_metric_cards.py` 从源 Word 文件生成到 `app/data/metric-cards.json`。
