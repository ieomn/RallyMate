# 官方教学来源与评分卡关联 v2

2026-10-24 核验三份新增机构原文后，教学清单更新为 `technique-examples-v1.1.0`：7 个来源、16 组观察或练习、34 张不同原卡。原有 4 来源、9 组、27 卡关联保持不变。唯一清单仍是 `scoring-demo-web/app/lib/technique-examples.ts`，按完整卡号匹配。

## 新增关联

- [LTA 正手分步教学](https://www.lta.org.uk/play/tennis-tips-and-techniques/how-to-play-a-tennis-forehand/)：核验页面正文的 Step two 至 Step five 及 Bonus tip。准备姿态关联 GS01-M01-05，侧身引拍关联 GS01-M03-01/03，身前击球练习关联 GS01-M08-02，随挥关联 GS01-M09-03，恢复准备关联 GS01-M10-04。网页署名教学教练为 Matt Smith，未给出可确认的发布日期，记录为 null。
- [USTA 双反练习](https://www.usta.com/en/home/improve/tips-and-instruction/national/improve-your-tennis-game--the-driving-force-in-your-two-handed-b.html)：页面日期 2018-07-02，Karl Davies 介绍用非优势手正手与双反交替练习，体会非优势手参与挥拍。关联 GS02-M07-01/GS02-M09-01；网页的左右手练习描述以右手球员为例，界面明确保留这一条件。
- [USTA 单反启动时机](https://www.usta.com/en/home/improve/tips-and-instruction/national/improve-your-tennis-game--hitting-a-one-handed-backhand.html)：页面日期 2019-02-19，Ben Zaiser 说明身前击球位置与提前启动挥拍的关系，作为 GS03-M07-01 的相关练习。不据此给出加速度阈值或改变 M07 的来源边界。

关联只表达来源讨论相关动作。两份 USTA 页提供视频入口，但本批验证依据是页面正文，没有逐帧观看、截取或标注视频，也没有使用视频时间戳。LTA 页的入门姿态、击球位置和回位建议不等于所有比赛情景的唯一标准。

## 与评分的边界

没有为教学对象添加 grade、score、模型输入或真值标签；没有改变任何评分规则、实例或来源窗口。特别是 GS01-M09-04/05 的手拍接近与控制不由这批资料恢复分数；原页面未直接给出这两卡的连续观测，因此不添加关联。

历史 v1 文档保留其当时范围。新增条目复用现有教学组件与外链机制，不下载、复制或嵌入原站媒体。相关测试检查全部卡号存在于原 298 卡、官方域名、唯一映射，以及组件不改变正式分数和缺测结果。
