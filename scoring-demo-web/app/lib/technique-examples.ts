/** Curated primary-source teaching links. These are learning references, never scoring inputs. */
export const TECHNIQUE_EXAMPLES_VERSION = "technique-examples-v1.1.0";
export const techniqueSources = {
  ustaUnitTurn: {
    provider: "USTA", title: "Improve Your Game: Perfecting Your Forehand",
    url: "https://www.usta.com/en/home/improve/tips-and-instruction/national/improve-your-tennis-game--get-your-forehand-flowing.html",
    publishedOn: "2018-06-29", verifiedOn: "2026-10-24",
  },
  ltaBackhand: {
    provider: "LTA", title: "How to hit a tennis backhand for beginners",
    url: "https://www.lta.org.uk/play/tennis-tips-and-techniques/how-to-hit-a-tennis-backhand/",
    publishedOn: null, verifiedOn: "2026-10-24",
  },
  ltaFootwork: {
    provider: "LTA", title: "Improve your tennis footwork on court with these simple tips",
    url: "https://www.lta.org.uk/play/tennis-tips-and-techniques/improve-your-tennis-footwork-on-court-with-these-simple-tips/",
    publishedOn: null, verifiedOn: "2026-10-24",
  },
  ustaSlice: {
    provider: "USTA", title: "Tennis techniques: Hitting the slice backhand",
    url: "https://www.usta.com/en/home/improve/tips-and-instruction/national/tennis-techniques--hitting-the-slice-backhand.html",
    publishedOn: null, verifiedOn: "2026-10-24",
  },
  ltaForehand: {
    provider: "LTA", title: "How to hit a tennis forehand",
    url: "https://www.lta.org.uk/play/tennis-tips-and-techniques/how-to-play-a-tennis-forehand/",
    publishedOn: null, verifiedOn: "2026-10-24",
  },
  ustaTwoHandDrive: {
    provider: "USTA", title: "Improve Your Game: Driving the Two-Handed Backhand",
    url: "https://www.usta.com/en/home/improve/tips-and-instruction/national/improve-your-tennis-game--the-driving-force-in-your-two-handed-b.html",
    publishedOn: "2018-07-02", verifiedOn: "2026-10-24",
  },
  ustaOneHandTiming: {
    provider: "USTA", title: "Improve Your Game: Hitting a One-Handed Backhand",
    url: "https://www.usta.com/en/home/improve/tips-and-instruction/national/improve-your-tennis-game--hitting-a-one-handed-backhand.html",
    publishedOn: "2019-02-19", verifiedOn: "2026-10-24",
  },
} as const;

type Example = {
  id: string;
  sourceId: keyof typeof techniqueSources;
  cardIds: readonly string[];
  title: string;
  sourceSection: string;
  focus: string;
  scope: "related_movement" | "technique_practice";
};

export const techniqueExamples: readonly Example[] = [
  { id: "forehand-unit-turn", sourceId: "ustaUnitTurn", cardIds: ["GS01-M03-01"],
    title: "正手：髋与上身一起转动", sourceSection: "正文中 Rita Gladstone 的 unit turn 练习",
    focus: "观察准备时髋与上身共同转动，以及从非持拍手接球练习过渡到正手击球。", scope: "related_movement" },
  { id: "backhand-preparation", sourceId: "ltaBackhand", cardIds: ["GS02-M03-01", "GS02-M03-02", "GS02-M03-04", "GS02-M03-05", "GS03-M03-03", "GS03-M03-05"],
    title: "反手：转身与引拍", sourceSection: "Step three – get into position",
    focus: "观察侧身准备与球拍后引；单反和双反按各自持拍方式练习。", scope: "related_movement" },
  { id: "backhand-follow-through", sourceId: "ltaBackhand", cardIds: ["GS02-M09-01", "GS02-M09-02", "GS03-M09-01", "GS03-M09-03"],
    title: "反手：触球后的继续挥拍", sourceSection: "Step five – swing through the ball",
    focus: "比较双反向肩上方的随挥与单反随挥时胸部打开的动作。", scope: "related_movement" },
  { id: "backhand-ready-again", sourceId: "ltaBackhand", cardIds: ["GS02-M10-04", "GS03-M10-05"],
    title: "反手：恢复下一拍准备", sourceSection: "Bonus tip",
    focus: "观察击球后重新建立准备姿态；回位位置需结合实际球路。", scope: "related_movement" },
  { id: "movement-options", sourceId: "ltaFootwork", cardIds: ["FS03-M03", "FS04-M03", "FS04-M04"],
    title: "移动：交叉步与侧向并步", sourceSection: "Understanding different tennis movements",
    focus: "了解跑动、交叉步与侧向并步的组合用途，关注移动后保持平衡。", scope: "related_movement" },
  { id: "small-adjustment-steps", sourceId: "ltaFootwork", cardIds: ["FS05-M02", "FS05-M03", "FS05-M04"],
    title: "接近来球后的短步调整", sourceSection: "Big steps, little steps",
    focus: "观察从较大步接近来球，转为较小调整步的过程。", scope: "related_movement" },
  { id: "stance-options", sourceId: "ltaFootwork", cardIds: ["FS06-M02", "FS06-M03", "FS06-M05", "FS07-M03", "FS07-M05"],
    title: "站位：根据时间选择开放或侧身准备", sourceSection: "Closed and open stance",
    focus: "对比时间充足与跑动中来不及充分侧身时的站位选择。", scope: "related_movement" },
  { id: "recovery-start", sourceId: "ltaFootwork", cardIds: ["FS10-M02"],
    title: "击球后启动回位", sourceSection: "Closed and open stance",
    focus: "关注击球后向恢复位置移动的衔接。", scope: "related_movement" },
  { id: "slice-practice", sourceId: "ustaSlice", cardIds: ["GS04-M08-01", "GS04-M09-01"],
    title: "反手切削：自抛与对练", sourceSection: "Self-Slice Backhands; Slice Backhand Rallies and Depth Drills",
    focus: "从自抛切削过渡到双人对练，学习控制回球深度。", scope: "technique_practice" },
  { id: "forehand-ready", sourceId: "ltaForehand", cardIds: ["GS01-M01-05"],
    title: "正手：建立移动前的准备姿态", sourceSection: "Step two – be ready",
    focus: "观察身前持拍与随时移动的准备姿态。", scope: "related_movement" },
  { id: "forehand-backswing", sourceId: "ltaForehand", cardIds: ["GS01-M03-01", "GS01-M03-03"],
    title: "正手：及早转身与引拍", sourceSection: "Step three – beat the bounce",
    focus: "学习在来球落地前完成定位、侧身与引拍准备。", scope: "related_movement" },
  { id: "forehand-contact-region", sourceId: "ltaForehand", cardIds: ["GS01-M08-02"],
    title: "正手：学习在身前击球", sourceSection: "Step four – play the ball in front",
    focus: "以入门练习理解身前击球位置；具体位置仍取决于来球与击球任务。", scope: "technique_practice" },
  { id: "forehand-follow-through", sourceId: "ltaForehand", cardIds: ["GS01-M09-03"],
    title: "正手：连续挥拍与随挥", sourceSection: "Step five – the racket swing",
    focus: "从便于控制的短挥拍逐渐过渡到更完整的随挥。", scope: "related_movement" },
  { id: "forehand-ready-again", sourceId: "ltaForehand", cardIds: ["GS01-M10-04"],
    title: "正手：恢复下一拍准备", sourceSection: "Bonus tip",
    focus: "关注随挥后重新建立准备状态，回位位置结合实际球路选择。", scope: "related_movement" },
  { id: "two-hand-drive-practice", sourceId: "ustaTwoHandDrive", cardIds: ["GS02-M07-01", "GS02-M09-01"],
    title: "双反：体会非优势手参与挥拍", sourceSection: "正文中 Karl Davies 的交替练习",
    focus: "右手球员可交替练习左手正手与双反，体会非优势手在挥拍和随挥中的作用。", scope: "technique_practice" },
  { id: "one-hand-earlier-swing", sourceId: "ustaOneHandTiming", cardIds: ["GS03-M07-01"],
    title: "单反：提前启动挥拍", sourceSection: "正文中 Ben Zaiser 的击球位置说明",
    focus: "理解单反击球位置通常比正手更靠身前，并练习提前启动挥拍。", scope: "technique_practice" },
];

export function techniqueExamplesForCard(indicatorId: string) {
  return techniqueExamples.filter(example => example.cardIds.includes(indicatorId))
    .map(example => ({ ...example, source: techniqueSources[example.sourceId] }));
}
