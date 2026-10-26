export const tennisHash = "a".repeat(64);
export function tennisFixture() {
  const ids = ["movement", "preparation", "coordination", "striking", "follow_through"];
  return { version: "tennis-action-assessment-v1.1.0", status: "scored", source_run_id: "tennis-test", source_video_sha256: tennisHash,
    source_timestamp_semantics: "original_decoder_pts", overall_score: 70,
    episodes: [
      { episode_id: "complete-swing-1", family: "baseline", start_ms: 1000, peak_ms: 1500, end_ms: 2200 },
      { episode_id: "complete-swing-2", family: "serve", start_ms: 3000, peak_ms: 3500, end_ms: 4200 },
    ],
    dimensions: ids.map((id, index) => ({ id, label: id, score: 50 + index * 10, observation_count: 4, metrics: [
      { id: `${id}_metric`, label: "动作测量", value: 1.5, unit: "body", score: 50 + index * 10, weight: 1, observation_count: 4, target: "保持平稳" },
    ] })), strengths: [{ title: "随挥连贯", observation: "随挥后能恢复准备姿势。" }],
    improvements: ids.slice(0, 2).map((dimension_id, index) => ({ dimension_id, title: index ? "提前准备" : "到位再挥拍", observation: index ? "准备阶段还有改善空间。" : "移动结束到挥拍之间衔接较快。",
      action: "先完成移动，再连续挥拍。", drill: "做 2 组，每组 5 次慢球移动挥拍。", retest: "下次录制相同动作 10 次，比较到位后的稳定性。", start_ms: 1000, end_ms: 2200 })),
    rubric: { version: "tennis-action-rubric-v1.1.0", semantics: "engineering_action_quality", coach_calibrated: false,
      identity_labels_used: false, confidence_used_in_score: false, aggregation: "mean_episode_dimension_scores_then_equal_dimension_mean" } };
}
export function tennisResult() { return { job_id: "tennis-test", input: { video: { sha256: tennisHash, duration_ms: 9000 } }, tennis_assessment: tennisFixture() }; }
