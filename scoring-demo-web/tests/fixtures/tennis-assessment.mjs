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

export function tennisScopedFixture() {
  const report = tennisFixture();
  report.version = "tennis-action-assessment-v1.2.0";
  report.rubric.version = "tennis-action-rubric-v1.2.0";
  report.dimensions.forEach(d => { d.observation_count = 2; d.metrics.forEach(m => { m.observation_count = 2; }); });
  const follow = report.dimensions[4].metrics[0]; follow.id = "pattern_phase_continuity"; follow.value = .9;
  report.subject_scope = { version: "subject-scoped-assessment-v1", selected_subject_id: "subject-1", selection_basis: "first_locked_target", cross_subject_aggregation: false,
    subjects: [{ subject_id: "subject-1", identity_basis: "target_lock", start_ms: 0, end_ms: 5000, observed_frames: 150, source_track_ids: [1], measured_episode_count: 2, included_in_report: true },
      { subject_id: "legacy-track-2-epoch-0", identity_basis: "unlinked_source_track", start_ms: 6000, end_ms: 8000, observed_frames: 60, source_track_ids: [2], measured_episode_count: 1, included_in_report: false }] };
  report.episodes.forEach(e => {
    e.subject_id = "subject-1"; e.person_track_id = 1;
    e.metrics = Object.fromEntries(report.dimensions.flatMap(d => d.metrics.map(m => [m.id, { value: m.value, score: m.score }])));
    e.motion_pattern = { version: "tennis-motion-patterns-v1.0.0", profile_id: "generic", classification_status: "technique_unverified",
      classification_provenance: { method: "unverified" },
      requirements: [{ id: "phase_continuity", label: "挥出和随挥连续", status: "measured_proxy", metric_ids: ["pattern_phase_continuity"], reason: null },
        { id: "technique_classification", label: "具体技术类型未确认", status: "not_measured", metric_ids: [], reason: "no_technique_specific_rules_applied" }],
      coverage: { measured_requirements: 1, applicable_requirements: 2, fraction: .5, full_source_technique_covered: false } };
  });
  report.episode_validity = [...report.episodes.map(e => ({ episode_id: e.episode_id, subject_id: e.subject_id, status: "scored", reason_codes: [], person_track_id: 1, start_ms: e.start_ms, end_ms: e.end_ms })),
    { episode_id: "other-target", subject_id: "legacy-track-2-epoch-0", status: "other_subject", reason_codes: ["different_subject_not_aggregated"], person_track_id: 2, start_ms: 6000, end_ms: 8000 }];
  report.pattern_coverage = { measured_requirements: 2, applicable_requirements: 4, fraction: .5, verified_technique_episodes: 0, generic_episodes: 2, full_source_technique_covered: false, profiles: ["generic"] };
  return report;
}
