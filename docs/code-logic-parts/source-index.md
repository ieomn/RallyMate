# 全量源码定位索引

代码基线：`70a744d54be0d958ed241e6ead3259a759888882`，分支 `codex/frontend-remove-manual-scoring`。

统计范围为 Git 跟踪的代码文件；排除 docs/reports 中的生成文档，排除未跟踪文件、依赖、视频和模型权重。行数包含空行与注释，不是有效逻辑行数。Python 符号用 AST 提取；JS/TS 符号仅匹配文件顶层声明，不等同于完整调用图。此索引用于定位，业务解释请结合主文档与分篇。

- `deploy`：7 个文件，1,492 行。
- `examples`：1 个文件，16 行。
- `scoring-demo-web`：97 个文件，12,044 行。
- `scripts`：166 个文件，38,383 行。
- `src/rallymate_annotation`：27 个文件，17,004 行。
- `src/rallymate_evaluation`：32 个文件，24,416 行。
- `src/rallymate_events`：6 个文件，2,730 行。
- `src/rallymate_features`：12 个文件，6,608 行。
- `src/rallymate_pose_evaluation`：2 个文件，797 行。
- `src/rallymate_scoring`：45 个文件，28,685 行。
- `src/rallymate_service`：18 个文件，7,409 行。
- `src/rallymate_tracking`：2 个文件，755 行。
- `src/rallymate_training`：9 个文件，4,747 行。
- `src/rallymate_vision`：26 个文件，6,308 行。
- `src/rallymate_visualization`：5 个文件，1,095 行。
- `tests`：167 个文件，51,320 行。
- `training`：1 个文件，175 行。

## deploy

### deploy/autodl/check_remote.py

[打开源码](../../deploy/autodl/check_remote.py) · 336 行

Local, read-only SSH heartbeat. No password fallback or automatic deployment.

- `CheckFailure`：L29–L30。
- `safe_token`：L33–L34。
- `public_url`：L37–L38。
- `finite`：L41–L42。
- `safe_time`：L45–L52。
- `compact_alert`：L55–L59。
- `compact_event`：L62–L74。
- `compact_snapshot`：L77–L102。
- `fetch_snapshot`：L105–L161。
- `allowed_code_path`：L164–L175。
- `scan_code`：L178–L206。
- `read_state`：L209–L216。
- `plan_result`：L219–L289。
- `write_state`：L292–L303。
- `main`：L306–L332。
### deploy/autodl/clean_legacy_replays.py

[打开源码](../../deploy/autodl/clean_legacy_replays.py) · 224 行

One-time, opt-in repair of completed replay pixels; inference evidence is untouched.

- `probe`：L21–L30。
- `inside`：L33–L37。
- `ensure_idle`：L40–L45。
- `plan_job`：L48–L101。
- `faststart`：L104–L119。
- `check_pixels`：L122–L147。
- `repair`：L150–L190。
- `main`：L193–L220。
### deploy/autodl/manage.sh

[打开源码](../../deploy/autodl/manage.sh) · 118 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### deploy/autodl/monitor.py

[打开源码](../../deploy/autodl/monitor.py) · 446 行

Bounded AutoDL health collection and a separate read-only snapshot command.

- `utc_now`：L39–L40。
- `number`：L43–L48。
- `age_seconds`：L51–L58。
- `safe_job_id`：L61–L62。
- `read_json`：L65–L71。
- `atomic_json`：L74–L85。
- `NoRedirect`：L88–L90；方法：`redirect_request` L89。
- `probe_http`：L93–L115。
- `process_health`：L118–L132。
- `current_tunnel_url`：L135–L144。
- `collect_jobs`：L147–L223。
- `disk_health`：L226–L232。
- `gpu_health`：L235–L246。
- `collect_uploads`：L249–L279。
- `cleanup_uploads`：L282–L290。
- `clean_upload_parts_once`：L293–L305。
- `collect_snapshot`：L308–L376。
- `read_snapshot`：L379–L392。
- `main`：L395–L442。
### deploy/autodl/run-process.sh

[打开源码](../../deploy/autodl/run-process.sh) · 63 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### deploy/autodl/test_check_remote.py

[打开源码](../../deploy/autodl/test_check_remote.py) · 130 行

No-network verification of the local SSH heartbeat and event deduplication.

- `healthy`：L18–L19。
- `RemoteCheckTests`：L22–L126；方法：`test_initial_healthy_baseline_is_quiet_and_unchanged_poll_stays_quiet` L26、`test_offline_deduplication_preserves_last_good_state_and_announces_recovery` L33、`test_recent_failure_is_delivered_once_and_recovery_alert_is_meaningful` L45、`test_last_url_survives_a_reachable_snapshot_with_tunnel_down` L59、`test_success_event_keeps_completion_time_and_is_not_repeated` L68、`test_code_hash_changes_only_report_allowed_relative_paths` L80、`test_compact_snapshot_drops_private_fields_and_nonpublic_urls` L90、`test_ssh_rejects_unknown_hosts_disables_extra_keys_and_uses_only_snapshot_command` L97。
### deploy/autodl/test_monitor.py

[打开源码](../../deploy/autodl/test_monitor.py) · 175 行

Local stdlib-only tests. They do not contact or change a deployed service.

- `MonitorTests`：L20–L171；方法：`setUp` L21、`tearDown` L26、`database` L29、`add_job` L36、`test_missing_queue_is_not_created` L41、`test_stalled_progress_and_new_failure_cursor_are_read_only_and_private` L48、`test_upload_statistics_never_remove_bytes_or_publish_names` L66、`test_success_cursor_baselines_history_and_delivers_each_new_completion_once` L80、`test_fresh_heartbeats_do_not_hide_unchanged_frames_and_progress_resets_timer` L94、`test_atomic_snapshot_and_staleness_are_read_only` L116、`test_tunnel_hostname_changes_never_reuse_prior_start` L126、`test_internal_key_is_not_sent_to_public_probe_or_copied_from_health_body` L134、`test_forced_snapshot_entry_does_not_probe_cleanup_or_start_any_process` L150、`test_failure_events_remain_visible_for_fifteen_minute_external_reader` L157。

## examples

### examples/promote-calibration-candidate.production.ps1

[打开源码](../../examples/promote-calibration-candidate.production.ps1) · 16 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。

## scoring-demo-web

### scoring-demo-web/app/ActionTimeline.tsx

[打开源码](../../scoring-demo-web/app/ActionTimeline.tsx) · 23 行

- `ActionTimeline`：L7。
### scoring-demo-web/app/AdviceResult.tsx

[打开源码](../../scoring-demo-web/app/AdviceResult.tsx) · 22 行

- `AdviceResult`：L3。
### scoring-demo-web/app/AnimatedDisclosure.tsx

[打开源码](../../scoring-demo-web/app/AnimatedDisclosure.tsx) · 112 行

- `AnimatedDisclosureProps`：L5。
- `AnimatedDisclosure`：L14。
### scoring-demo-web/app/BallTrajectorySummary.tsx

[打开源码](../../scoring-demo-web/app/BallTrajectorySummary.tsx) · 41 行

- `BallTrajectorySummary`：L4。
### scoring-demo-web/app/BallTrajectoryViewer.tsx

[打开源码](../../scoring-demo-web/app/BallTrajectoryViewer.tsx) · 286 行

- `BallTrajectoryViewerProps`：L10。
- `clamp`：L31。
- `pointSource`：L35。
- `formatReplayTime`：L43。
- `setReplayRate`：L49。
- `toggleReplayFullscreen`：L55。
- `PlaybackIcon`：L61。
- `BallTrajectoryViewer`：L65。
### scoring-demo-web/app/FootworkReviewPanel.tsx

[打开源码](../../scoring-demo-web/app/FootworkReviewPanel.tsx) · 25 行

- `FootworkReviewPanel`：L7。
### scoring-demo-web/app/LiveResults.tsx

[打开源码](../../scoring-demo-web/app/LiveResults.tsx) · 144 行

- `familyRecognition`：L45。
- `techniqueStatusLabel`：L53。
- `TechniqueFamilyChip`：L62。
- `LiveStats`：L69。
- `LiveResults`：L88。
### scoring-demo-web/app/MeasurementEvidence.tsx

[打开源码](../../scoring-demo-web/app/MeasurementEvidence.tsx) · 12 行

- `MeasurementEvidence`：L5。
### scoring-demo-web/app/MotionAnalysisPanel.tsx

[打开源码](../../scoring-demo-web/app/MotionAnalysisPanel.tsx) · 35 行

- `MotionAnalysisPanel`：L10。
### scoring-demo-web/app/PoseSkeletonOverlay.tsx

[打开源码](../../scoring-demo-web/app/PoseSkeletonOverlay.tsx) · 14 行

- `PoseSkeletonOverlay`：L4。
### scoring-demo-web/app/ProductWorkspace.tsx

[打开源码](../../scoring-demo-web/app/ProductWorkspace.tsx) · 154 行

- `WorkspaceView`：L9。
- `IconName`：L10。
- `ProductIcon`：L12。
- `WorkspaceSurface`：L46。
- `ProductShell`：L64。
- `CourtArtwork`：L83。
- `dateText`：L93。
- `orderedReports`：L99。
- `ReportPreview`：L103。
- `ReportCard`：L113。
- `EmptyTraining`：L117。
- `TrainingHome`：L121。
- `SessionLibrary`：L134。
- `CaptureGuide`：L150。
### scoring-demo-web/app/ReportExportActions.tsx

[打开源码](../../scoring-demo-web/app/ReportExportActions.tsx) · 32 行

- `ReportExportActions`：L6。
### scoring-demo-web/app/RotationAnalysisPanel.tsx

[打开源码](../../scoring-demo-web/app/RotationAnalysisPanel.tsx) · 21 行

- `RotationAnalysisPanel`：L4。
### scoring-demo-web/app/RuleReviewPanel.tsx

[打开源码](../../scoring-demo-web/app/RuleReviewPanel.tsx) · 66 行

- `RuleReviewPanel`：L14。
### scoring-demo-web/app/ScoreExplanationPanel.tsx

[打开源码](../../scoring-demo-web/app/ScoreExplanationPanel.tsx) · 59 行

- `IndicatorDetail`：L13。
- `ScoreExplanationPanel`：L36。
### scoring-demo-web/app/ScoreLab.tsx

[打开源码](../../scoring-demo-web/app/ScoreLab.tsx) · 382 行

- `Source`：L23。
- `ImportState`：L24。
- `LiveEvidence`：L25。
- `formatBytes`：L28。
- `isSupportedVideo`：L33。
- `isStage1Summary`：L40。
- `isTechniqueAssessment`：L55。
- `extractTechniqueAssessment`：L63。
- `ReplayPanel`：L74。
- `ScoreLab`：L100。
### scoring-demo-web/app/SourceMeasurementPanel.tsx

[打开源码](../../scoring-demo-web/app/SourceMeasurementPanel.tsx) · 79 行

- `WindowMode`：L12。
- `SourceMeasurementPanel`：L14。
### scoring-demo-web/app/TrainingReportOverview.tsx

[打开源码](../../scoring-demo-web/app/TrainingReportOverview.tsx) · 26 行

- `TrainingReportOverview`：L7。
### scoring-demo-web/app/api/advice/route.ts

[打开源码](../../scoring-demo-web/app/api/advice/route.ts) · 246 行

- `Advice`：L4。
- `Input`：L12。
- `EvidenceContext`：L13。
- `RuntimeEnv`：L15。
- `env`：L39。
- `json`：L44。
- `originAllowed`：L47。
- `readBody`：L57。
- `validInput`：L81。
- `cleanText`：L97。
- `parseAdvice`：L104。
- `prompt`：L131。
- `providerAdvice`：L142。
- `evidenceAdvice`：L156。
- `providerFallback`：L159。
- `loadEvidenceContext`：L167。
- `callMimo`：L187。
- `POST`：L216。
- `GET`：L246。
### scoring-demo-web/app/api/scorecard/route.ts

[打开源码](../../scoring-demo-web/app/api/scorecard/route.ts) · 11 行

- `GET`：L5。
- `POST`：L9。
### scoring-demo-web/app/chatgpt-auth.ts

[打开源码](../../scoring-demo-web/app/chatgpt-auth.ts) · 90 行

- `ChatGPTUser`：L4。
- `getChatGPTUser`：L21。
- `requireChatGPTUser`：L42。
- `chatGPTSignInPath`：L51。
- `chatGPTSignOutPath`：L56。
- `safeRelativeReturnPath`：L61。
- `isReservedAuthPath`：L76。
- `safeDecodeURIComponent`：L84。
### scoring-demo-web/app/globals.css

[打开源码](../../scoring-demo-web/app/globals.css) · 765 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/app/layout.tsx

[打开源码](../../scoring-demo-web/app/layout.tsx) · 19 行

- `RootLayout`：L17。
### scoring-demo-web/app/lib/advice-display.ts

[打开源码](../../scoring-demo-web/app/lib/advice-display.ts) · 17 行

- `Advice`：L3。
- `AdviceResponse`：L4。
- `ScopedAdvice`：L5。
- `adviceNotice`：L9。
### scoring-demo-web/app/lib/advice-evidence.ts

[打开源码](../../scoring-demo-web/app/lib/advice-evidence.ts) · 136 行

- `AdviceEvidenceStatus`：L1。
- `EvidenceStatus`：L2。
- `AdviceEvidence`：L3。
- `trustedText`：L30。
- `captureSteps`：L38。
- `unavailableAdviceEvidence`：L47。
- `adviceEvidenceFromPayload`：L69。
- `mayExplainEvidence`：L134。
### scoring-demo-web/app/lib/analysis-progress.ts

[打开源码](../../scoring-demo-web/app/lib/analysis-progress.ts) · 61 行

- `formatElapsed`：L24。
- `analysisProgress`：L32。
### scoring-demo-web/app/lib/analysis-session.ts

[打开源码](../../scoring-demo-web/app/lib/analysis-session.ts) · 108 行

- `AnalysisResult`：L4。
- `pause`：L6。
- `retryable`：L15。
- `reconnectDelay`：L20。
- `watchAnalysis`：L25。
### scoring-demo-web/app/lib/api-client.ts

[打开源码](../../scoring-demo-web/app/lib/api-client.ts) · 223 行

- `requestId`：L68。
- `getApiConfig`：L73。
- `RallyMateApiClient`：L83。
- `createApiClient`：L111。
### scoring-demo-web/app/lib/api-types.ts

[打开源码](../../scoring-demo-web/app/lib/api-types.ts) · 629 行

- `ScorecardResult`：L4。
- `ScorecardRequest`：L25。
- `ScorecardResponse`：L31。
- `ScorecardCapabilities`：L53。
- `TechniqueCatalogItem`：L72。
- `TechniqueCatalogResponse`：L86。
- `RallyMateMetaResponse`：L98。
- `JobStatus`：L111。
- `JobSubmission`：L121。
- `JobProgress`：L140。
- `DemoResultResponse`：L156。
- `AnalysisReport`：L193。
- `IndependentFootworkMeasurement`：L205。
- `MotionEpisode`：L212。
- `RotationAnalysis`：L233。
- `RotationWindow`：L245。
- `FootworkReview`：L250。
- `MotionFamily`：L268。
- `MotionAnalysis`：L275。
- `IndicatorEvaluation`：L285。
- `ActionEvaluation`：L311。
- `TrainingEvaluation`：L320。
- `TrajectoryPoint`：L342。
- `PosePreviewFrame`：L355。
- `PosePreviewResponse`：L367。
- `BallReconstructionAnalysis`：L391。
- `BallReconstructionSegment`：L401。
- `BallTrajectoryReconstruction`：L422。
- `TrajectoryTrack`：L442。
- `TrajectoryPreviewResponse`：L458。
- `TechniqueAssessmentItem`：L502。
- `TechniqueAssessmentResponse`：L533。
- `EvidenceFrame`：L571。
- `BallTrajectory`：L579。
- `RacketSignal`：L587。
- `GripSignal`：L595。
- `SubmitVideoOptions`：L603。
- `RallyMateApiConfig`：L611。
- `RallyMateApiError`：L619。
### scoring-demo-web/app/lib/footwork-review.ts

[打开源码](../../scoring-demo-web/app/lib/footwork-review.ts) · 47 行

- `independentFootworkMeasurements`：L5。
- `footworkEpisodes`：L22。
- `footworkFeatureText`：L39。
- `footworkScoringStatus`：L45。
### scoring-demo-web/app/lib/live-stats.ts

[打开源码](../../scoring-demo-web/app/lib/live-stats.ts) · 122 行

- `ActionCandidateView`：L3。
- `summarizeActionCandidates`：L6。
- `TrajectoryDisplayModel`：L25。
- `LiveStatsModel`：L37。
- `finiteNumber`：L50。
- `validCount`：L54。
- `summarizeLiveStats`：L59。
- `analysisPresentation`：L89。
- `buildTrajectoryDisplayModel`：L103。
### scoring-demo-web/app/lib/measurement-evidence.ts

[打开源码](../../scoring-demo-web/app/lib/measurement-evidence.ts) · 91 行

- `evidenceReferenceScore`：L10。
- `measurementWarnings`：L15。
- `measurementIndicators`：L20。
- `measurementCounts`：L26。
- `measurementResultFromSummary`：L32。
- `normalizeMeasurementResult`：L53。
### scoring-demo-web/app/lib/mimo-config.ts

[打开源码](../../scoring-demo-web/app/lib/mimo-config.ts) · 47 行

- `MimoProvider`：L1。
- `MimoConfig`：L2。
- `MimoConfigResult`：L9。
- `resolveMimoConfig`：L20。
### scoring-demo-web/app/lib/motion-analysis.ts

[打开源码](../../scoring-demo-web/app/lib/motion-analysis.ts) · 104 行

- `motionAnalysisOf`：L8。
- `rotationAnalysisOf`：L63。
- `rotationWindowsOf`：L83。
- `motionMetricText`：L102。
### scoring-demo-web/app/lib/pose-playback.ts

[打开源码](../../scoring-demo-web/app/lib/pose-playback.ts) · 70 行

- `poseReplayTimingAllowed`：L13。
- `normalizePosePreview`：L18。
- `poseFrameAt`：L58。
- `poseEdges`：L67。
### scoring-demo-web/app/lib/report-export.ts

[打开源码](../../scoring-demo-web/app/lib/report-export.ts) · 379 行

- `ReportExportInput`：L10。
- `ReportSection`：L26。
- `PracticeReport`：L27。
- `RecordValue`：L37。
- `text`：L47。
- `actionFamilyName`：L67。
- `assessmentOf`：L75。
- `summaryOf`：L78。
- `recognitionOf`：L81。
- `hasAnalysis`：L86。
- `getReportExportGate`：L98。
- `requireReady`：L120。
- `interval`：L132。
- `candidateEvidence`：L139。
- `metricRows`：L157。
- `buildPracticeReport`：L174。
- `renderReportMarkdown`：L320。
- `renderReportHtml`：L334。
- `buildReportBackup`：L340。
- `reportDownloadName`：L377。
### scoring-demo-web/app/lib/resumable-upload.ts

[打开源码](../../scoring-demo-web/app/lib/resumable-upload.ts) · 150 行

- `UploadProgress`：L3。
- `UploadSession`：L8。
- `digest`：L10。
- `pause`：L14。
- `uploadResumable`：L24。
### scoring-demo-web/app/lib/rule-review.ts

[打开源码](../../scoring-demo-web/app/lib/rule-review.ts) · 50 行

- `RuleSource`：L6。
- `ReviewRule`：L7。
- `RuleReviewCatalog`：L15。
- `ruleReviewContext`：L23。
- `reviewRuleResult`：L35。
### scoring-demo-web/app/lib/score-explanation.ts

[打开源码](../../scoring-demo-web/app/lib/score-explanation.ts) · 207 行

- `ScoreComponentKey`：L13。
- `ScoreComponent`：L14。
- `EvidenceGap`：L18。
- `EvidenceBlocker`：L19。
- `IndicatorExplanation`：L20。
- `VerifiedScoreExplanation`：L24。
- `ScoreExplanationState`：L29。
- `ScoreEvidenceWindow`：L32。
- `validPythonRounding`：L51。
- `readComponents`：L57。
- `readBase`：L76。
- `readIndicator`：L97。
- `scoreExplanationOf`：L141。
- `scoreEvidenceWindows`：L194。
- `componentImpact`：L202。
- `IndicatorWithExplanation`：L207。
### scoring-demo-web/app/lib/technical-review.ts

[打开源码](../../scoring-demo-web/app/lib/technical-review.ts) · 208 行

- `TechnicalGrade`：L7。
- `SourceMeasurement`：L8。
- `SourceVisibility`：L9。
- `SourceReviewWindow`：L10。
- `TechnicalReviewInput`：L11。
- `TechnicalReviewEntry`：L19。
- `TechnicalReviewLedger`：L22。
- `parseTechnicalReview`：L43。
- `technicalReviewError`：L89。
- `sourceReviewWindows`：L120。
- `sourceReviewCoverage`：L166。
- `sourceWindowDisplay`：L179。
- `sourceAssessmentForExport`：L186。
- `reviewInterval`：L202。
### scoring-demo-web/app/lib/training-report.ts

[打开源码](../../scoring-demo-web/app/lib/training-report.ts) · 81 行

- `ReportMoment`：L5。
- `ReportFocus`：L6。
- `analysisReportOf`：L12。
- `reportMoments`：L36。
- `reportFocus`：L50。
- `replayMoment`：L68。
- `RecentReport`：L73。
- `recentReports`：L74。
### scoring-demo-web/app/lib/trajectory-viewer.ts

[打开源码](../../scoring-demo-web/app/lib/trajectory-viewer.ts) · 110 行

- `TrajectoryEdge`：L3。
- `TrajectoryDisplayMode`：L4。
- `trajectoryWindow`：L8。
- `visibleTrajectoryPaths`：L13。
- `trajectoryDisplayOpacity`：L55。
- `trajectoryAvailability`：L62。
- `trajectoryEdges`：L77。
- `trajectoryStrokes`：L94。
- `timestampForVideoTime`：L104。
- `videoTimeForTimestamp`：L108。
### scoring-demo-web/app/lib/use-pose-playback.ts

[打开源码](../../scoring-demo-web/app/lib/use-pose-playback.ts) · 41 行

- `PoseWindowLoader`：L5。
- `usePosePlayback`：L8。
### scoring-demo-web/app/lib/workspace-navigation.ts

[打开源码](../../scoring-demo-web/app/lib/workspace-navigation.ts) · 86 行

- `WorkspaceView`：L1。
- `WorkspaceLocation`：L3。
- `validJobId`：L11。
- `readWorkspaceLocation`：L16。
- `workspaceHref`：L27。
- `record`：L34。
- `timestamp`：L39。
- `ReportDate`：L46。
- `reportDateOf`：L49。
- `formatReportDate`：L62。
- `reportVideoDurationMs`：L71。
- `formatVideoDuration`：L78。
### scoring-demo-web/app/page.tsx

[打开源码](../../scoring-demo-web/app/page.tsx) · 20 行

- `Home`：L5。
### scoring-demo-web/app/product-analysis.css

[打开源码](../../scoring-demo-web/app/product-analysis.css) · 107 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/app/product-motion.css

[打开源码](../../scoring-demo-web/app/product-motion.css) · 120 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/app/product-report.css

[打开源码](../../scoring-demo-web/app/product-report.css) · 275 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/app/product-workspace.css

[打开源码](../../scoring-demo-web/app/product-workspace.css) · 285 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/app/rule-review.css

[打开源码](../../scoring-demo-web/app/rule-review.css) · 74 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/app/score-explanation.css

[打开源码](../../scoring-demo-web/app/score-explanation.css) · 88 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/app/scoring/engine.ts

[打开源码](../../scoring-demo-web/app/scoring/engine.ts) · 423 行

- `Domain`：L1。
- `Dependency`：L2。
- `Grade`：L3。
- `EvaluationStatus`：L4。
- `MetricCard`：L6。
- `Scenario`：L30。
- `DimensionScores`：L47。
- `CardResult`：L54。
- `GroupResult`：L69。
- `DomainResult`：L81。
- `ScoreReport`：L86。
- `hash01`：L118。
- `gradeFor`：L127。
- `isOptional`：L135。
- `evidenceFor`：L146。
- `dimensionScores`：L171。
- `evaluateCard`：L189。
- `aggregate`：L249。
- `aggregateDomain`：L278。
- `buildScoreReport`：L303。
- `scenarioFromStage1Summary`：L350。
### scoring-demo-web/app/scoring/scenarios.ts

[打开源码](../../scoring-demo-web/app/scoring/scenarios.ts) · 110 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/app/source-measurements.css

[打开源码](../../scoring-demo-web/app/source-measurements.css) · 41 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/app/welcome/MarketingLanding.tsx

[打开源码](../../scoring-demo-web/app/welcome/MarketingLanding.tsx) · 144 行

- `MarketingLanding`：L22。
### scoring-demo-web/app/welcome/TrainingExperience.tsx

[打开源码](../../scoring-demo-web/app/welcome/TrainingExperience.tsx) · 166 行

- `ballPosition`：L11。
- `ControlIcon`：L20。
- `TrainingExperience`：L29。
### scoring-demo-web/app/welcome/marketing.css

[打开源码](../../scoring-demo-web/app/welcome/marketing.css) · 174 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/app/welcome/page.tsx

[打开源码](../../scoring-demo-web/app/welcome/page.tsx) · 11 行

- `WelcomePage`：L9。
### scoring-demo-web/app/welcome/training-experience.css

[打开源码](../../scoring-demo-web/app/welcome/training-experience.css) · 87 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/db/index.ts

[打开源码](../../scoring-demo-web/db/index.ts) · 14 行

- `getDb`：L5。
### scoring-demo-web/db/schema.ts

[打开源码](../../scoring-demo-web/db/schema.ts) · 4 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/drizzle.config.ts

[打开源码](../../scoring-demo-web/drizzle.config.ts) · 7 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/eslint.config.mjs

[打开源码](../../scoring-demo-web/eslint.config.mjs) · 41 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/examples/d1/app/api/notes/route.ts

[打开源码](../../scoring-demo-web/examples/d1/app/api/notes/route.ts) · 58 行

- `toRouteErrorMessage`：L5。
- `GET`：L18。
- `POST`：L36。
### scoring-demo-web/examples/d1/db/schema.ts

[打开源码](../../scoring-demo-web/examples/d1/db/schema.ts) · 9 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/next-env.d.ts

[打开源码](../../scoring-demo-web/next-env.d.ts) · 5 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/next.config.ts

[打开源码](../../scoring-demo-web/next.config.ts) · 7 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/postcss.config.mjs

[打开源码](../../scoring-demo-web/postcss.config.mjs) · 7 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/scripts/extract_metric_cards.py

[打开源码](../../scoring-demo-web/scripts/extract_metric_cards.py) · 181 行

Build the browser-ready GS/FS rule registry from the two source DOCX files.

- `clean`：L18–L19。
- `field_map`：L22–L31。
- `dependency_tags`：L34–L50。
- `stage_index`：L53–L67。
- `extract_card`：L70–L109。
- `main`：L112–L177。
### scoring-demo-web/sites-vite-plugin.ts

[打开源码](../../scoring-demo-web/sites-vite-plugin.ts) · 230 行

- `exists`：L20。
- `sites`：L32。
- `removeHeader`：L193。
- `setHeader`：L202。
- `respond`：L212。
- `safeReturn`：L218。
### scoring-demo-web/tests/advice-display.test.mjs

[打开源码](../../scoring-demo-web/tests/advice-display.test.mjs) · 66 行

- `load`：L8。
### scoring-demo-web/tests/advice-evidence.test.mjs

[打开源码](../../scoring-demo-web/tests/advice-evidence.test.mjs) · 83 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/tests/advice-guardrails.test.mjs

[打开源码](../../scoring-demo-web/tests/advice-guardrails.test.mjs) · 163 行

- `route`：L6。
- `withMocks`：L66。
### scoring-demo-web/tests/analysis-progress.test.mjs

[打开源码](../../scoring-demo-web/tests/analysis-progress.test.mjs) · 47 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/tests/analysis-session.test.mjs

[打开源码](../../scoring-demo-web/tests/analysis-session.test.mjs) · 200 行

- `moduleFrom`：L6。
### scoring-demo-web/tests/api-client.test.mjs

[打开源码](../../scoring-demo-web/tests/api-client.test.mjs) · 126 行

- `compiledUrl`：L7。
- `nativeReport`：L22。
### scoring-demo-web/tests/footwork-review.test.mjs

[打开源码](../../scoring-demo-web/tests/footwork-review.test.mjs) · 61 行

- `compiledUrl`：L8。
### scoring-demo-web/tests/gateway-upload-body.test.mjs

[打开源码](../../scoring-demo-web/tests/gateway-upload-body.test.mjs) · 142 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/tests/live-stats.test.mjs

[打开源码](../../scoring-demo-web/tests/live-stats.test.mjs) · 84 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/tests/measurement-evidence.test.mjs

[打开源码](../../scoring-demo-web/tests/measurement-evidence.test.mjs) · 203 行

- `compiledUrl`：L8。
- `historical`：L23。
- `referenceResult`：L81。
### scoring-demo-web/tests/mimo-config.test.mjs

[打开源码](../../scoring-demo-web/tests/mimo-config.test.mjs) · 76 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/tests/motion-analysis.test.mjs

[打开源码](../../scoring-demo-web/tests/motion-analysis.test.mjs) · 153 行

- `measuredRotationEpisode`：L37。
- `deepFreeze`：L54。
### scoring-demo-web/tests/pose-playback.test.mjs

[打开源码](../../scoring-demo-web/tests/pose-playback.test.mjs) · 136 行

- `compiledUrl`：L8。
### scoring-demo-web/tests/product-workspace.test.mjs

[打开源码](../../scoring-demo-web/tests/product-workspace.test.mjs) · 143 行

- `compiledUrl`：L9。
- `elements`：L75。
### scoring-demo-web/tests/rendered-html.test.mjs

[打开源码](../../scoring-demo-web/tests/rendered-html.test.mjs) · 81 行

- `worker`：L5。
### scoring-demo-web/tests/replay-display.test.mjs

[打开源码](../../scoring-demo-web/tests/replay-display.test.mjs) · 294 行

- `compiledUrl`：L8。
- `rotationEpisode`：L217。
- `parsedRotationEpisode`：L232。
### scoring-demo-web/tests/report-export.test.mjs

[打开源码](../../scoring-demo-web/tests/report-export.test.mjs) · 291 行

- `compiledUrl`：L6。
- `completed`：L18。
- `rotationReportInput`：L191。
- `exportedRotationMetrics`：L215。
### scoring-demo-web/tests/resumable-upload.test.mjs

[打开源码](../../scoring-demo-web/tests/resumable-upload.test.mjs) · 332 行

- `moduleUrl`：L7。
- `browserStorage`：L26。
- `protocol`：L45。
- `video`：L107。
### scoring-demo-web/tests/rule-review.test.mjs

[打开源码](../../scoring-demo-web/tests/rule-review.test.mjs) · 66 行

- `compiledUrl`：L9。
### scoring-demo-web/tests/score-explanation.test.mjs

[打开源码](../../scoring-demo-web/tests/score-explanation.test.mjs) · 218 行

- `compiledUrl`：L9。
- `indicator`：L29。
- `resultOf`：L42。
- `episode`：L197。
### scoring-demo-web/tests/technical-review.test.mjs

[打开源码](../../scoring-demo-web/tests/technical-review.test.mjs) · 207 行

- `compiledUrl`：L9。
- `sourceResult`：L95。
### scoring-demo-web/tests/training-report.test.mjs

[打开源码](../../scoring-demo-web/tests/training-report.test.mjs) · 112 行

- `compiledUrl`：L8。
- `motionResult`：L27。
### scoring-demo-web/tests/trajectory-viewer.test.mjs

[打开源码](../../scoring-demo-web/tests/trajectory-viewer.test.mjs) · 125 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/tests/workspace-navigation.test.mjs

[打开源码](../../scoring-demo-web/tests/workspace-navigation.test.mjs) · 84 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/vite.config.ts

[打开源码](../../scoring-demo-web/vite.config.ts) · 82 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scoring-demo-web/worker/gateway.ts

[打开源码](../../scoring-demo-web/worker/gateway.ts) · 92 行

- `GatewayEnv`：L1。
- `readUploadChunk`：L6。
- `proxyAnalysis`：L42。
### scoring-demo-web/worker/index.ts

[打开源码](../../scoring-demo-web/worker/index.ts) · 58 行

- `Env`：L6。
- `ExecutionContext`：L21。

## scripts

### scripts/audit_feature_observation_gaps.py

[打开源码](../../scripts/audit_feature_observation_gaps.py) · 651 行

Audit unavailable required features without changing measurement or scoring gates.

直接引用的项目模块：`rallymate_scoring.feasibility`、`rallymate_scoring.quality_policy`。

- `_sha256`：L45–L50。
- `_read_jsonl`：L53–L64。
- `_canonical`：L67–L68。
- `_source`：L71–L72。
- `_recovery`：L75–L82。
- `_hard_fail_recovery`：L85–L99。
- `_raw_observation_counts`：L102–L139。
- `_selected_diagnostics`：L142–L160。
- `_registry_indicators`：L163–L173。
- `_comparison_maps`：L176–L215。
- `audit_feature_observation_gaps`：L218–L568。
- `validate_feature_observation_gap_audit`：L571–L623。
- `main`：L626–L647。
### scripts/audit_full_body_keypoint_coverage.py

[打开源码](../../scripts/audit_full_body_keypoint_coverage.py) · 309 行

直接引用的项目模块：`rallymate_vision.pose.metadata`。

- `_card_text`：L81–L82。
- `_semantic_coverage`：L85–L104。
- `build_report`：L107–L294。
- `main`：L297–L305。
### scripts/audit_pose_backends_legacy_metrics.py

[打开源码](../../scripts/audit_pose_backends_legacy_metrics.py) · 101 行

直接引用的项目模块：`rallymate_scoring.granularity`。

- `_frames`：L25–L28。
- `main`：L31–L97。
### scripts/audit_pose_finetune_readiness.py

[打开源码](../../scripts/audit_pose_finetune_readiness.py) · 56 行

Audit strict Halpe26 truth packs before any fine-tune dataset export.

直接引用的项目模块：`rallymate_training.pose_finetune_readiness`。

- `main`：L15–L52。
### scripts/audit_scoring_batch.py

[打开源码](../../scripts/audit_scoring_batch.py) · 613 行

Read-only verification of completed batch snapshots; never submits inference.

- `finite`：L47–L54。
- `equivalent`：L57–L70。
- `Checks`：L73–L85；方法：`__init__` L74、`expect` L77、`fail` L84。
- `safe_detail`：L88–L102。
- `identity`：L105–L111。
- `normalized_records`：L114–L136。
- `valid_features`：L139–L151。
- `repeatability`：L154–L165。
- `expected_indicator`：L168–L206。
- `audit_explanation`：L209–L227。
- `audit_training`：L230–L299。
- `sha256`：L302–L307。
- `resolve_path`：L310–L312。
- `read_json`：L315–L319。
- `iter_jsonl`：L322–L330。
- `audit_formal`：L333–L373。
- `audit_entry`：L376–L494。
- `audit_source_contract`：L497–L545。
- `audit_manifest`：L548–L588。
- `main`：L591–L609。
### scripts/audit_scoring_blockers.py

[打开源码](../../scripts/audit_scoring_blockers.py) · 335 行

Audit score blockers without changing gates, grades, or thresholds.

直接引用的项目模块：`rallymate_scoring.blocker_taxonomy`。

- `_sha256`：L33–L38。
- `_read_jsonl`：L41–L52。
- `_typed_reason_for_flag`：L55–L56。
- `audit_scoring_blockers`：L59–L274。
- `validate_scoring_blocker_audit`：L277–L304。
- `main`：L307–L331。
### scripts/audit_smoothing_counterfactual_coverage.py

[打开源码](../../scripts/audit_smoothing_counterfactual_coverage.py) · 42 行

Audit replayable raw-versus-smoothed feature calculations.

直接引用的项目模块：`rallymate_evaluation.smoothing_coverage`。

- `main`：L15–L38。
### scripts/backfill_source_assessments.py

[打开源码](../../scripts/backfill_source_assessments.py) · 267 行

Add source-aligned measurements to completed local jobs without re-inference.

直接引用的项目模块：`rallymate_features.coordinates`、`rallymate_scoring.source_assessment`、`rallymate_service.source_assessment`。

- `now`：L28–L29。
- `load_json`：L32–L35。
- `load_jsonl`：L38–L39。
- `save_json`：L42–L53。
- `relative`：L56–L60。
- `read_job`：L63–L71。
- `code_fingerprint`：L74–L81。
- `core_hashes`：L84–L86。
- `validate_baseline`：L89–L116。
- `summarize_artifact`：L119–L160。
- `process_job`：L163–L211。
- `main`：L214–L263。
### scripts/benchmark_rtmpose_candidates.py

[打开源码](../../scripts/benchmark_rtmpose_candidates.py) · 233 行

直接引用的项目模块：`rallymate_vision.pose`。

- `_percentile`：L28–L31。
- `_samples`：L34–L59。
- `_decode`：L62–L73。
- `_config_path`：L76–L78。
- `benchmark_candidate`：L81–L183。
- `main`：L186–L229。
### scripts/build_calibration_measurement_sources.py

[打开源码](../../scripts/build_calibration_measurement_sources.py) · 63 行

Build current primary-player timelines and a strict multi-video calibration source set.

直接引用的项目模块：`rallymate_scoring.calibration_measurement_sources`。

- `_source`：L15–L25。
- `main`：L28–L59。
### scripts/build_calibration_safe_scores.py

[打开源码](../../scripts/build_calibration_safe_scores.py) · 208 行

直接引用的项目模块：`rallymate_scoring.scoring`。

- `main`：L18–L204。
### scripts/build_current_product_technical_docs.ps1

[打开源码](../../scripts/build_current_product_technical_docs.ps1) · 140 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scripts/build_default_pose_routing_audit.py

[打开源码](../../scripts/build_default_pose_routing_audit.py) · 83 行

Build a fail-closed certificate for the normal service Pose default.

直接引用的项目模块：`rallymate_evaluation.default_pose_routing`。

- `_sha256`：L14–L19。
- `_source`：L22–L23。
- `_read`：L26–L27。
- `main`：L30–L79。
### scripts/build_deployment_docx.py

[打开源码](../../scripts/build_deployment_docx.py) · 618 行

- `set_run_font`：L46–L68。
- `shade`：L71–L77。
- `set_cell_fill`：L80–L86。
- `set_repeat_table_header`：L89–L93。
- `paragraph_bottom_border`：L96–L107。
- `add_page_field`：L110–L129。
- `configure_document`：L132–L211。
- `add_numbering`：L214–L265。
- `attach_numbering`：L268–L278。
- `add_inline`：L284–L306。
- `make_architecture_diagram`：L309–L383。
- `add_title_block`：L386–L424。
- `table_weights`：L427–L437。
- `add_table`：L440–L475。
- `parse_table`：L478–L490。
- `add_code_block`：L493–L529。
- `build`：L532–L614。
### scripts/build_f2_error_budget_readiness.py

[打开源码](../../scripts/build_f2_error_budget_readiness.py) · 73 行

Build a source-replayed F2 error-budget readiness report without scoring.

直接引用的项目模块：`rallymate_evaluation.f2_error_budget_readiness`。

- `main`：L18–L69。
### scripts/build_frontend_scoring_reference.py

[打开源码](../../scripts/build_frontend_scoring_reference.py) · 260 行

Build the display-only scoring reference from the immutable Word audit.

- `require`：L55–L57。
- `text_field`：L60–L63。
- `unique_by`：L66–L69。
- `source_reference`：L72–L84。
- `field_sources`：L87–L92。
- `build_visual_techniques`：L95–L136。
- `build_catalog`：L139–L230。
- `render_catalog`：L233–L234。
- `main`：L237–L256。
### scripts/build_indicator_calculation_readiness.py

[打开源码](../../scripts/build_indicator_calculation_readiness.py) · 85 行

Build per-upload, per-indicator F2 calculation readiness from run artifacts.

直接引用的项目模块：`rallymate_scoring.calculation_readiness`。

- `_read_json`：L16–L17。
- `_read_jsonl`：L20–L25。
- `_sha256`：L28–L33。
- `_source`：L36–L37。
- `main`：L40–L81。
### scripts/build_indicator_measurement_portfolio.py

[打开源码](../../scripts/build_indicator_measurement_portfolio.py) · 75 行

直接引用的项目模块：`rallymate_scoring.feasibility`、`rallymate_scoring.measurement_portfolio`。

- `_jsonl`：L20–L25。
- `_sha256`：L28–L29。
- `main`：L32–L71。
### scripts/build_indicator_scoring_requirements.py

[打开源码](../../scripts/build_indicator_scoring_requirements.py) · 69 行

直接引用的项目模块：`rallymate_annotation.truth_pack`、`rallymate_scoring.feasibility`、`rallymate_scoring.indicator_requirements`。

- `main`：L23–L65。
### scripts/build_m67_measurement_recovery_summary.py

[打开源码](../../scripts/build_m67_measurement_recovery_summary.py) · 302 行

Aggregate M66 small-ROI and M67 ROI-margin composition experiments.

- `_sha256`：L18–L23。
- `_source`：L26–L27。
- `_sum_impact`：L30–L40。
- `build_summary`：L43–L218。
- `validate_summary`：L221–L254。
- `main`：L257–L298。
### scripts/build_m68_pose_router_summary.py

[打开源码](../../scripts/build_m68_pose_router_summary.py) · 340 行

Aggregate three required-joint-superset router experiments.

直接引用的项目模块：`rallymate_evaluation.pose_observability_router`。

- `_sha256`：L22–L27。
- `_source`：L30–L31。
- `_sum`：L34–L35。
- `build_summary`：L38–L237。
- `validate_summary`：L240–L295。
- `validate_summary_sources`：L298–L307。
- `main`：L310–L336。
### scripts/build_m69_high_resolution_summary.py

[打开源码](../../scripts/build_m69_high_resolution_summary.py) · 330 行

Aggregate M69 residual high-resolution Pose experiments.

直接引用的项目模块：`rallymate_evaluation.pose_observability_residual`。

- `_sha256`：L31–L36。
- `_source`：L39–L40。
- `_verify_bindings`：L43–L48。
- `build_summary`：L51–L252。
- `validate_summary`：L255–L306。
- `main`：L309–L326。
### scripts/build_m70_anchored_pose_extension.py

[打开源码](../../scripts/build_m70_anchored_pose_extension.py) · 372 行

Extend M69 only where a context-matched Pose strictly dominates M69.

直接引用的项目模块：`rallymate_evaluation.fixed_boundary_ab`、`rallymate_evaluation.pose_observability_residual`、`rallymate_evaluation.pose_observability_router`、`rallymate_evaluation.pose_policy_composition`、`rallymate_evaluation.small_roi_recovery`。

- `_sha256`：L48–L53。
- `_source`：L56–L57。
- `_bound`：L60–L64。
- `_rows`：L67–L75。
- `_write_rows`：L78–L81。
- `_identity_set`：L84–L88。
- `validate_anchored_pose_extension_report`：L91–L147。
- `main`：L150–L368。
### scripts/build_m70_pose_profile_ablation_summary.py

[打开源码](../../scripts/build_m70_pose_profile_ablation_summary.py) · 497 行

Aggregate the M70 crop-context/Pose-profile ablation and safe extension.

直接引用的项目模块：`rallymate_evaluation.pose_observability_residual`。

- `_sha256`：L36–L41。
- `_source`：L44–L45。
- `_verify_bindings`：L48–L60。
- `_identity_set`：L63–L69。
- `_sum`：L72–L79。
- `build_summary`：L82–L404。
- `validate_summary`：L407–L471。
- `main`：L474–L493。
### scripts/build_m71_larger_pose_model_summary.py

[打开源码](../../scripts/build_m71_larger_pose_model_summary.py) · 357 行

Aggregate the three-video M71 RTMPose-L extension experiment.

- `_sha256`：L26–L31。
- `_source`：L34–L35。
- `_verify_binding`：L38–L43。
- `_sum`：L46–L53。
- `build_summary`：L56–L269。
- `validate_summary`：L272–L333。
- `main`：L336–L353。
### scripts/build_m72_additive_keypoint_fusion_summary.py

[打开源码](../../scripts/build_m72_additive_keypoint_fusion_summary.py) · 405 行

Aggregate M72 additive keypoint fusion and replay the remaining residual.

- `_sha256`：L27–L32。
- `_source`：L35–L36。
- `_verify`：L39–L43。
- `_identity_set`：L46–L50。
- `_sum`：L53–L60。
- `build_summary`：L63–L310。
- `validate_summary`：L313–L379。
- `main`：L382–L401。
### scripts/build_m73_wholebody_mapped_fusion_summary.py

[打开源码](../../scripts/build_m73_wholebody_mapped_fusion_summary.py) · 433 行

Aggregate M73 WholeBody133 mapped fusion and replay every residual.

- `build_summary`：L40–L321。
- `validate_summary`：L324–L410。
- `main`：L413–L429。
### scripts/build_m74_event_bounded_gap_audit.py

[打开源码](../../scripts/build_m74_event_bounded_gap_audit.py) · 365 行

Audit and replay short event-internal Pose gaps on the M73 residual set.

直接引用的项目模块：`rallymate_evaluation.event_bounded_pose_gaps`、`rallymate_features`、`rallymate_features.event_features`、`rallymate_scoring.feasibility`。

- `_sha256`：L29–L34。
- `_load_json`：L37–L38。
- `_load_jsonl`：L41–L46。
- `_source`：L49–L53。
- `_valid_fraction`：L56–L58。
- `_feature_snapshot`：L61–L72。
- `_event_interval`：L75–L83。
- `build_report`：L86–L338。
- `main`：L341–L361。
### scripts/build_m75_event_gap_keypoint_truth.py

[打开源码](../../scripts/build_m75_event_gap_keypoint_truth.py) · 26 行

直接引用的项目模块：`rallymate_evaluation.event_gap_keypoint_truth`。

- `main`：L11–L22。
### scripts/build_m77_fs09_phase_truth.py

[打开源码](../../scripts/build_m77_fs09_phase_truth.py) · 34 行

直接引用的项目模块：`rallymate_evaluation.fs09_phase_truth`。

- `main`：L11–L30。
### scripts/build_m78_blocker_taxonomy_audit.py

[打开源码](../../scripts/build_m78_blocker_taxonomy_audit.py) · 82 行

直接引用的项目模块：`rallymate_scoring.blocker_taxonomy_audit`。

- `main`：L16–L78。
### scripts/build_m79_pose_diagnostic_clip_truth.py

[打开源码](../../scripts/build_m79_pose_diagnostic_clip_truth.py) · 422 行

直接引用的项目模块：`rallymate_evaluation.fs09_phase_truth`、`rallymate_evaluation.pose_diagnostic_clip_truth`。

- `_render_clips`：L36–L91。
- `_safe_segment`：L94–L110。
- `_html`：L113–L117。
- `_review_html`：L120–L132。
- `_adjudicate_html`：L135–L147。
- `main`：L150–L418。
### scripts/build_m80_pose_diagnostic_clip_truth.py

[打开源码](../../scripts/build_m80_pose_diagnostic_clip_truth.py) · 8 行

Stable M80 entry point for the versioned pose-diagnostic clip pack builder.

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scripts/build_m88_fs09_phase_blind_handoff.py

[打开源码](../../scripts/build_m88_fs09_phase_blind_handoff.py) · 44 行

直接引用的项目模块：`rallymate_evaluation.fs09_phase_handoff`。

- `main`：L14–L40。
### scripts/build_m95_field_change_record.py

[打开源码](../../scripts/build_m95_field_change_record.py) · 623 行

- `M95RecordError`：L79–L80。
- `_sha256`：L83–L88。
- `_load_json`：L91–L95。
- `_require`：L98–L100。
- `_document_version`：L103–L107。
- `_drawio_validation`：L110–L130。
- `_artifact`：L133–L140。
- `_parse_args`：L143–L160。
- `main`：L163–L619。
### scripts/build_m96_pose_pilot.py

[打开源码](../../scripts/build_m96_pose_pilot.py) · 56 行

直接引用的项目模块：`rallymate_evaluation.m96_pose_pilot`。

- `main`：L18–L52。
### scripts/build_m96_pose_pilot_adjudication.py

[打开源码](../../scripts/build_m96_pose_pilot_adjudication.py) · 52 行

直接引用的项目模块：`rallymate_evaluation.m96_pose_pilot`。

- `main`：L15–L48。
### scripts/build_m96_pose_pilot_audit.py

[打开源码](../../scripts/build_m96_pose_pilot_audit.py) · 533 行

直接引用的项目模块：`rallymate_evaluation.m96_pose_pilot`。

- `M96PilotAuditError`：L50–L51。
- `_sha256`：L54–L55。
- `_canonical_bytes`：L58–L65。
- `_pretty_bytes`：L68–L78。
- `_now_utc`：L81–L82。
- `_snapshot`：L85–L97。
- `_parse_object`：L100–L122。
- `_relative_path`：L125–L131。
- `_inventory`：L134–L167。
- `_assert_frozen_pilot`：L170–L194。
- `_pilot_contract`：L197–L273。
- `_field_change_record`：L276–L362。
- `_summary`：L365–L409。
- `_expected_output`：L412–L443。
- `validate_existing`：L446–L472。
- `build`：L475–L504。
- `main`：L507–L529。
### scripts/build_m96_same_frame_field_record.py

[打开源码](../../scripts/build_m96_same_frame_field_record.py) · 364 行

Seal M96 same-frame diagnostic fields and artifact hashes without overwriting.

直接引用的项目模块：`rallymate_evaluation.pose_same_frame_diagnostic`。

- `_inside`：L32–L37。
- `_artifact`：L40–L49。
- `_recompute_samples`：L52–L64。
- `_validate_report_against_current_sources`：L67–L143。
- `build_field_change_record`：L146–L322。
- `main`：L325–L360。
### scripts/build_m96_user_demo_field_record.py

[打开源码](../../scripts/build_m96_user_demo_field_record.py) · 379 行

Seal the M96 user Demo field-change record without overwriting it.

- `FieldRecordError`：L30–L31。
- `_inside`：L34–L39。
- `_sha256`：L42–L47。
- `_artifact`：L50–L59。
- `_load_contract`：L62–L91。
- `_verify_source_contract`：L94–L148。
- `build_field_change_record`：L151–L334。
- `main`：L337–L375。
### scripts/build_m97_goal_progress_field_record.py

[打开源码](../../scripts/build_m97_goal_progress_field_record.py) · 1,675 行

Build or validate the immutable M97 goal-progress evidence bundle.

- `GoalProgressRecordError`：L327–L328。
- `_expect`：L331–L333。
- `_inside`：L336–L341。
- `_relative_path`：L344–L345。
- `_reject_holdout_path`：L348–L353。
- `_sha256_bytes`：L356–L357。
- `_sha256_file`：L360–L367。
- `_load_json`：L370–L380。
- `_required`：L383–L393。
- `_expect_equal`：L396–L400。
- `_artifact`：L403–L413。
- `_validate_pilot_source_snapshot`：L416–L497。
- `_load_immutable_field_records`：L500–L526。
- `_extract_demo_v1_1`：L529–L674。
- `_extract_m96_same_frame`：L677–L750。
- `_extract_m97_operational`：L753–L807。
- `_extract_pilot`：L810–L893。
- `_extract_documents`：L896–L952。
- `_tag_name`：L955–L956。
- `_extract_drawio`：L959–L1004。
- `_extract_service_contract`：L1007–L1068。
- `_extract_production_state`：L1071–L1122。
- `_extract_holdout_boundary`：L1125–L1166。
- `_artifact_specs`：L1169–L1245。
- `_build_artifact_inventory`：L1248–L1256。
- `_collect_snapshot`：L1259–L1295。
- `_full_suite_record`：L1298–L1320。
- `_render_summary`：L1323–L1363。
- `_summary_binding`：L1366–L1374。
- `_build_record`：L1377–L1429。
- `_json_bytes`：L1432–L1440。
- `_validated_generated_at`：L1443–L1456。
- `_write_new_bundle`：L1459–L1491。
- `build_goal_progress_bundle`：L1494–L1531。
- `_verify_recorded_artifacts`：L1534–L1552。
- `validate_existing_bundle`：L1555–L1634。
- `main`：L1637–L1671。
### scripts/build_m97_x_operational_field_record.py

[打开源码](../../scripts/build_m97_x_operational_field_record.py) · 445 行

Seal M97 operational fields, provenance, and artifact hashes without overwrite.

直接引用的项目模块：`rallymate_evaluation.pose_x_operational`。

- `_inside`：L44–L49。
- `_load_json`：L52–L59。
- `_load_jsonl`：L62–L73。
- `_artifact`：L76–L85。
- `_environment`：L88–L128。
- `_m96_speed_context`：L131–L158。
- `build_field_change_record`：L161–L407。
- `main`：L410–L441。
### scripts/build_m97_x_pose_operational_summary.py

[打开源码](../../scripts/build_m97_x_pose_operational_summary.py) · 63 行

Aggregate the immutable three-video M97 RTMPose-X operational experiment.

直接引用的项目模块：`rallymate_evaluation.pose_x_operational`。

- `main`：L16–L59。
### scripts/build_manual_event_features.py

[打开源码](../../scripts/build_manual_event_features.py) · 59 行

直接引用的项目模块：`rallymate_scoring.manual_event_features`。

- `main`：L10–L55。
### scripts/build_metric_measurement_plans.py

[打开源码](../../scripts/build_metric_measurement_plans.py) · 40 行

直接引用的项目模块：`rallymate_scoring.measurement_plans`。

- `main`：L10–L36。
### scripts/build_minimum_scoring_loop.py

[打开源码](../../scripts/build_minimum_scoring_loop.py) · 447 行

直接引用的项目模块：`rallymate_events`、`rallymate_features`、`rallymate_scoring.feasibility`。

- `_parse_args`：L39–L56。
- `_load_historical_registry`：L59–L78。
- `_historical_replay_metadata`：L81–L88。
- `_write_jsonl`：L91–L95。
- `_load_jsonl`：L98–L103。
- `_require_reused_historical_bundle`：L106–L180。
- `main`：L183–L443。
### scripts/build_multivideo_indicator_calculation_coverage.py

[打开源码](../../scripts/build_multivideo_indicator_calculation_coverage.py) · 47 行

Build a hash-bound multi-video Pose indicator calculation coverage report.

直接引用的项目模块：`rallymate_scoring.multivideo_coverage`。

- `main`：L15–L43。
### scripts/build_multivideo_measurement_recovery.py

[打开源码](../../scripts/build_multivideo_measurement_recovery.py) · 58 行

Build an immutable multi-video small-ROI measurement recovery audit.

直接引用的项目模块：`rallymate_evaluation.multivideo_measurement_recovery`。

- `main`：L16–L54。
### scripts/build_multivideo_scoring_readiness.py

[打开源码](../../scripts/build_multivideo_scoring_readiness.py) · 43 行

Build a replay-validated multi-video scoring-readiness decomposition.

直接引用的项目模块：`rallymate_scoring.multivideo_readiness`。

- `main`：L15–L39。
### scripts/build_pose_ab_report.py

[打开源码](../../scripts/build_pose_ab_report.py) · 381 行

- `_round`：L35–L36。
- `_diagnostic_metrics`：L39–L69。
- `_aggregate`：L72–L84。
- `_read_selected`：L87–L97。
- `_draw_pose`：L100–L122。
- `_evidence_sheet`：L125–L175。
- `_markdown`：L178–L249。
- `main`：L252–L377。
### scripts/build_pose_baseline.py

[打开源码](../../scripts/build_pose_baseline.py) · 346 行

直接引用的项目模块：`rallymate_pose_evaluation.baseline`。

- `_specs`：L31–L42。
- `_annotation_manifest`：L45–L74。
- `_fmt`：L77–L82。
- `_markdown`：L85–L231。
- `main`：L234–L342。
### scripts/build_pose_diagnostic_review_queue.py

[打开源码](../../scripts/build_pose_diagnostic_review_queue.py) · 61 行

直接引用的项目模块：`rallymate_evaluation.pose_diagnostic_review`。

- `main`：L14–L57。
### scripts/build_pose_diagnostic_truth_pack.py

[打开源码](../../scripts/build_pose_diagnostic_truth_pack.py) · 44 行

直接引用的项目模块：`rallymate_evaluation.pose_diagnostic_truth`。

- `main`：L13–L40。
### scripts/build_pose_observability_residual_audit.py

[打开源码](../../scripts/build_pose_observability_residual_audit.py) · 49 行

Build the M69 post-router residual observability audit.

直接引用的项目模块：`rallymate_evaluation.pose_observability_residual`。

- `main`：L15–L45。
### scripts/build_pose_profile_routing_audit.py

[打开源码](../../scripts/build_pose_profile_routing_audit.py) · 412 行

Build a fixed-boundary pose-profile routing audit.

- `_sha256`：L23–L28。
- `_load`：L31–L35。
- `_summary_counts`：L38–L68。
- `_fixed_counts`：L71–L121。
- `_event_agreement`：L124–L158。
- `build_pose_profile_routing_audit`：L161–L317。
- `validate_pose_profile_routing_audit`：L320–L359。
- `main`：L362–L408。
### scripts/build_pose_scoring_ab_report.py

[打开源码](../../scripts/build_pose_scoring_ab_report.py) · 4,156 行

直接引用的项目模块：`rallymate_annotation.scoring_action_readiness`、`rallymate_annotation.scoring_evidence_plan`、`rallymate_annotation.scoring_truth_refresh`、`rallymate_evaluation.default_pose_routing`、`rallymate_evaluation.event_bounded_pose_gaps`、`rallymate_evaluation.event_gap_feature_truth`、`rallymate_evaluation.event_gap_keypoint_truth`、`rallymate_evaluation.f2_error_budget_readiness`、`rallymate_evaluation.fs09_phase_truth`、`rallymate_evaluation.multivideo_measurement_recovery`、`rallymate_evaluation.pose_diagnostic_clip_truth`、`rallymate_evaluation.pose_diagnostic_review`、`rallymate_evaluation.pose_diagnostic_truth`、`rallymate_evaluation.smoothing_coverage`、`rallymate_scoring.blocker_taxonomy_audit`、`rallymate_scoring.calculation_readiness`、`rallymate_scoring.calibration_handoff`、`rallymate_scoring.calibration_portfolio`、`rallymate_scoring.cycle_measurement`、`rallymate_scoring.diagnostic_policy_review`、`rallymate_scoring.measurement_portfolio`、`rallymate_scoring.multivideo_coverage`、`rallymate_scoring.multivideo_readiness`、`rallymate_scoring.scoring_context`。

- `_percent`：L118–L119。
- `_production_calibration_asset_paths`：L122–L138。
- `_shared_model_indicator_ids`：L141–L150。
- `_pose_wave_summary`：L153–L187。
- `_same_window_scope`：L190–L209。
- `_event_counts_text`：L212–L218。
- `_report_href`：L221–L225。
- `_file_sha256`：L228–L233。
- `_pose_wave_intro`：L236–L246。
- `_event_disagreement_summary`：L249–L267。
- `_fixed_boundary_summary`：L270–L294。
- `_pose_profile_routing_summary`：L297–L330。
- `_scoring_blocker_audit_summary`：L333–L364。
- `_truth_priority_worklist_summary`：L367–L387。
- `_feature_observation_gap_summary`：L390–L421。
- `_truth_action_worklist_summary`：L424–L451。
- `_truth_action_readiness_summary`：L454–L459。
- `_truth_evidence_plan_summary`：L462–L467。
- `_truth_refresh_summary`：L470–L479。
- `_truth_calibration_handoff_summary`：L482–L499。
- `_truth_calibration_portfolio_summary`：L502–L520。
- `_multivideo_calculation_coverage_summary`：L523–L543。
- `_multivideo_readiness_summary`：L546–L573。
- `_small_roi_recovery_summary`：L576–L637。
- `_multivideo_measurement_recovery_summary`：L640–L684。
- `_m67_measurement_recovery_summary`：L687–L756。
- `_m68_pose_router_summary`：L759–L862。
- `_m69_high_resolution_summary`：L865–L980。
- `_m70_pose_profile_ablation_summary`：L983–L1089。
- `_m71_larger_pose_model_summary`：L1092–L1193。
- `_m72_additive_keypoint_fusion_summary`：L1196–L1278。
- `_m73_wholebody_mapped_fusion_summary`：L1281–L1383。
- `_m74_event_bounded_gap_summary`：L1386–L1398。
- `_m75_event_gap_truth_summary`：L1401–L1448。
- `_m76_event_gap_feature_truth_summary`：L1451–L1481。
- `_m77_fs09_phase_truth_summary`：L1484–L1520。
- `_m78_blocker_taxonomy_summary`：L1523–L1549。
- `_m79_pose_diagnostic_clip_truth_summary`：L1552–L1601。
- `_small_roi_truth_summary`：L1604–L1666。
- `_residual_computability_summary`：L1669–L1742。
- `_metric_disagreement_text`：L1745–L1754。
- `_deployment_smoke_summary`：L1757–L1795。
- `_default_pose_routing_summary`：L1798–L1815。
- `_calculation_readiness_summary`：L1818–L1828。
- `_measurement_portfolio_summary`：L1831–L1838。
- `_cycle_measurement_summary`：L1841–L1848。
- `_pose_diagnostic_review_summary`：L1851–L1876。
- `_pose_diagnostic_truth_summary`：L1879–L1918。
- `_pose_diagnostic_policy_review_summary`：L1921–L1975。
- `main`：L1978–L4152。
### scripts/build_pose_scoring_current_primary_inputs.py

[打开源码](../../scripts/build_pose_scoring_current_primary_inputs.py) · 210 行

直接引用的项目模块：`rallymate_tracking`、`rallymate_tracking.primary_player`。

- `_load_jsonl`：L17–L22。
- `_write_jsonl`：L25–L29。
- `_sha256`：L32–L33。
- `_detection_signature`：L36–L44。
- `main`：L47–L206。
### scripts/build_primary_player_baseline.py

[打开源码](../../scripts/build_primary_player_baseline.py) · 88 行

直接引用的项目模块：`rallymate_tracking`。

- `_markdown`：L17–L48。
- `main`：L51–L84。
### scripts/build_residual_computability_audit.py

[打开源码](../../scripts/build_residual_computability_audit.py) · 84 行

Build a fail-closed computability certificate for residual indicator gaps.

直接引用的项目模块：`rallymate_evaluation.residual_computability`。

- `_sha256`：L16–L21。
- `_source`：L24–L25。
- `_read_jsonl`：L28–L33。
- `main`：L36–L80。
### scripts/build_scoring_blocker_taxonomy.py

[打开源码](../../scripts/build_scoring_blocker_taxonomy.py) · 39 行

直接引用的项目模块：`rallymate_scoring.blocker_taxonomy`。

- `main`：L11–L35。
### scripts/build_scoring_cycle_measurement.py

[打开源码](../../scripts/build_scoring_cycle_measurement.py) · 73 行

直接引用的项目模块：`rallymate_scoring.cycle_measurement`。

- `_jsonl`：L11–L16。
- `_sha256`：L19–L24。
- `main`：L27–L69。
### scripts/build_scoring_loop_report.py

[打开源码](../../scripts/build_scoring_loop_report.py) · 254 行

直接引用的项目模块：`rallymate_scoring.feasibility`、`rallymate_scoring.scoring_loop_report`。

- `_parse_args`：L32–L47。
- `_load_historical_registry`：L50–L69。
- `_historical_replay_metadata`：L72–L79。
- `_load_jsonl`：L82–L83。
- `_require_historical_bundle`：L86–L180。
- `main`：L183–L250。
### scripts/build_scoring_reference_context.py

[打开源码](../../scripts/build_scoring_reference_context.py) · 65 行

直接引用的项目模块：`rallymate_scoring.scoring_context`。

- `_load_jsonl`：L10–L19。
- `main`：L22–L61。
### scripts/build_scoring_truth_action_worklist.py

[打开源码](../../scripts/build_scoring_truth_action_worklist.py) · 80 行

Build a hash-bound action worklist covering every unavailable score instance.

直接引用的项目模块：`rallymate_annotation.scoring_action_worklist`。

- `main`：L18–L76。
### scripts/build_scoring_truth_calibration_handoff.py

[打开源码](../../scripts/build_scoring_truth_calibration_handoff.py) · 64 行

Build an immutable M47 truth-refresh to calibration-dataset handoff.

直接引用的项目模块：`rallymate_scoring.calibration_handoff`。

- `main`：L17–L60。
### scripts/build_scoring_truth_calibration_portfolio.py

[打开源码](../../scripts/build_scoring_truth_calibration_portfolio.py) · 58 行

Compile all bound truth videos into one immutable calibration portfolio.

直接引用的项目模块：`rallymate_scoring.calibration_portfolio`。

- `main`：L17–L54。
### scripts/build_scoring_truth_event_adjudication.py

[打开源码](../../scripts/build_scoring_truth_event_adjudication.py) · 85 行

直接引用的项目模块：`rallymate_annotation.scoring_truth_event_execution`。

- `main`：L15–L81。
### scripts/build_scoring_truth_event_execution.py

[打开源码](../../scripts/build_scoring_truth_event_execution.py) · 89 行

直接引用的项目模块：`rallymate_annotation.scoring_truth_event_execution`。

- `main`：L15–L85。
### scripts/build_scoring_truth_event_handoff.py

[打开源码](../../scripts/build_scoring_truth_event_handoff.py) · 66 行

直接引用的项目模块：`rallymate_annotation.scoring_truth_event_handoff`。

- `main`：L15–L62。
### scripts/build_scoring_truth_evidence_plan.py

[打开源码](../../scripts/build_scoring_truth_evidence_plan.py) · 49 行

Collapse M45 work items into shared human-evidence acquisition units.

直接引用的项目模块：`rallymate_annotation.scoring_evidence_plan`。

- `main`：L16–L45。
### scripts/build_scoring_truth_pack.py

[打开源码](../../scripts/build_scoring_truth_pack.py) · 47 行

直接引用的项目模块：`rallymate_annotation`、`rallymate_annotation.truth_pack`。

- `main`：L11–L43。
### scripts/build_scoring_truth_priority_worklist.py

[打开源码](../../scripts/build_scoring_truth_priority_worklist.py) · 331 行

Build a video-first human-truth worklist from complete-feature score blocks.

直接引用的项目模块：`rallymate_scoring.blocker_taxonomy`。

- `_sha256`：L25–L30。
- `_json`：L33–L37。
- `_jsonl`：L40–L41。
- `_feature_complete`：L44–L46。
- `build_truth_priority_worklist`：L49–L220。
- `validate_truth_priority_worklist`：L223–L250。
- `_write_csv`：L253–L267。
- `_write_html`：L270–L298。
- `main`：L301–L327。
### scripts/build_scoring_visual_observer_m81.py

[打开源码](../../scripts/build_scoring_visual_observer_m81.py) · 127 行

直接引用的项目模块：`rallymate_visualization.scoring_observer`。

- `_write_json`：L30–L40。
- `_sha256`：L43–L48。
- `main`：L51–L123。
### scripts/build_small_roi_keypoint_truth_pack.py

[打开源码](../../scripts/build_small_roi_keypoint_truth_pack.py) · 48 行

Build a blind, independently adjudicated keypoint truth pack for small ROIs.

直接引用的项目模块：`rallymate_evaluation.small_roi_keypoint_truth`。

- `main`：L13–L44。
### scripts/build_technical_review_rules.py

[打开源码](../../scripts/build_technical_review_rules.py) · 99 行

Compile the immutable Word audit into a packaged human-review contract.

- `canonical_sha`：L18–L20。
- `build`：L23–L81。
- `main`：L84–L95。
### scripts/check_public_secrets.py

[打开源码](../../scripts/check_public_secrets.py) · 49 行

Fail without printing secret values when tracked/staged files contain keys.

- `main`：L16–L45。
### scripts/compare_event_candidates.py

[打开源码](../../scripts/compare_event_candidates.py) · 85 行

Compare two model-produced event JSONL files without treating either as truth.

直接引用的项目模块：`rallymate_events.disagreement`。

- `_load_jsonl`：L17–L22。
- `_sha256`：L25–L26。
- `main`：L29–L81。
### scripts/compare_pose_wave_same_window.py

[打开源码](../../scripts/compare_pose_wave_same_window.py) · 198 行

Compare two uncalibrated pose-wave reports over the exact same frame window.

- `_load`：L12–L13。
- `_indicator_row`：L16–L25。
- `_event_quality_flags`：L28–L36。
- `_score_reason_counts`：L39–L55。
- `_shared_indicator_ids`：L58–L83。
- `main`：L86–L194。
### scripts/compile_calibration_dataset.py

[打开源码](../../scripts/compile_calibration_dataset.py) · 70 行

直接引用的项目模块：`rallymate_scoring.calibration_dataset`。

- `main`：L10–L66。
### scripts/compile_m75_event_gap_keypoint_truth.py

[打开源码](../../scripts/compile_m75_event_gap_keypoint_truth.py) · 22 行

直接引用的项目模块：`rallymate_evaluation.event_gap_keypoint_truth`。

- `main`：L11–L18。
### scripts/compile_m77_fs09_phase_truth.py

[打开源码](../../scripts/compile_m77_fs09_phase_truth.py) · 76 行

直接引用的项目模块：`rallymate_evaluation.fs09_phase_truth`。

- `main`：L15–L72。
### scripts/compile_m79_pose_diagnostic_clip_truth.py

[打开源码](../../scripts/compile_m79_pose_diagnostic_clip_truth.py) · 51 行

直接引用的项目模块：`rallymate_evaluation.pose_diagnostic_clip_truth`。

- `main`：L14–L47。
### scripts/compile_m80_pose_diagnostic_clip_truth.py

[打开源码](../../scripts/compile_m80_pose_diagnostic_clip_truth.py) · 8 行

Stable M80 entry point for compiling two-stage pose-diagnostic truth.

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scripts/compile_scoring_truth_pack.py

[打开源码](../../scripts/compile_scoring_truth_pack.py) · 24 行

直接引用的项目模块：`rallymate_annotation`。

- `main`：L10–L20。
### scripts/compile_small_roi_keypoint_truth_pack.py

[打开源码](../../scripts/compile_small_roi_keypoint_truth_pack.py) · 24 行

Compile and validate independent small-ROI keypoint annotations.

直接引用的项目模块：`rallymate_evaluation.small_roi_keypoint_truth`。

- `main`：L13–L20。
### scripts/compose_pose_recovery_experiments.py

[打开源码](../../scripts/compose_pose_recovery_experiments.py) · 271 行

Compose disjoint small-ROI and ROI-margin experiments, then recompute once.

直接引用的项目模块：`rallymate_evaluation.fixed_boundary_ab`、`rallymate_evaluation.pose_policy_composition`、`rallymate_evaluation.small_roi_recovery`。

- `_sha256`：L26–L31。
- `_source`：L34–L35。
- `_read_jsonl`：L38–L46。
- `_validate_report`：L49–L78。
- `main`：L81–L267。
### scripts/download_assets.py

[打开源码](../../scripts/download_assets.py) · 121 行

- `_sha256`：L23–L28。
- `download`：L31–L57。
- `main`：L60–L117。
### scripts/enable_cuda.ps1

[打开源码](../../scripts/enable_cuda.ps1) · 42 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scripts/enrich_scoring_event_diagnostics.py

[打开源码](../../scripts/enrich_scoring_event_diagnostics.py) · 46 行

直接引用的项目模块：`rallymate_tracking`。

- `_load`：L16–L17。
- `main`：L20–L42。
### scripts/evaluate_calibration_independent_test.py

[打开源码](../../scripts/evaluate_calibration_independent_test.py) · 89 行

直接引用的项目模块：`rallymate_scoring.calibration_independent_test`。

- `_load_object`：L16–L23。
- `_atomic_write`：L26–L43。
- `main`：L46–L85。
### scripts/evaluate_calibration_labels.py

[打开源码](../../scripts/evaluate_calibration_labels.py) · 80 行

直接引用的项目模块：`rallymate_scoring.calibration`。

- `main`：L16–L76。
### scripts/evaluate_fixed_boundary_pose_ab.py

[打开源码](../../scripts/evaluate_fixed_boundary_pose_ab.py) · 113 行

Evaluate paired pose-feature disagreement on one immutable event file.

直接引用的项目模块：`rallymate_evaluation.fixed_boundary_ab`。

- `_model_input`：L15–L29。
- `main`：L32–L109。
### scripts/evaluate_m75_event_gap_keypoint_truth.py

[打开源码](../../scripts/evaluate_m75_event_gap_keypoint_truth.py) · 25 行

直接引用的项目模块：`rallymate_evaluation.event_gap_keypoint_truth`。

- `main`：L11–L21。
### scripts/evaluate_m76_event_gap_feature_truth.py

[打开源码](../../scripts/evaluate_m76_event_gap_feature_truth.py) · 39 行

直接引用的项目模块：`rallymate_evaluation.event_gap_feature_truth`。

- `main`：L14–L35。
### scripts/evaluate_m77_fs09_phase_truth.py

[打开源码](../../scripts/evaluate_m77_fs09_phase_truth.py) · 45 行

直接引用的项目模块：`rallymate_evaluation.fs09_phase_truth`。

- `main`：L15–L41。
### scripts/evaluate_pose_diagnostic_truth.py

[打开源码](../../scripts/evaluate_pose_diagnostic_truth.py) · 54 行

直接引用的项目模块：`rallymate_evaluation.pose_diagnostic_truth`。

- `main`：L13–L50。
### scripts/evaluate_pose_features_on_common_events.py

[打开源码](../../scripts/evaluate_pose_features_on_common_events.py) · 195 行

直接引用的项目模块：`rallymate_features`、`rallymate_scoring.feasibility`、`rallymate_scoring.loop`。

- `_parse_args`：L40–L58。
- `_load_historical_registry`：L61–L80。
- `_load`：L83–L84。
- `_frames`：L87–L90。
- `main`：L93–L191。
### scripts/evaluate_ranking_independent_test.py

[打开源码](../../scripts/evaluate_ranking_independent_test.py) · 67 行

直接引用的项目模块：`rallymate_scoring.calibration_ranking`。

- `_load_object`：L15–L22。
- `main`：L25–L63。
### scripts/evaluate_scoring_truth.py

[打开源码](../../scripts/evaluate_scoring_truth.py) · 58 行

直接引用的项目模块：`rallymate_evaluation.scoring_truth`。

- `main`：L10–L54。
### scripts/evaluate_scoring_truth_action_readiness.py

[打开源码](../../scripts/evaluate_scoring_truth_action_readiness.py) · 67 行

Evaluate M44 work-item evidence without changing score or quality state.

直接引用的项目模块：`rallymate_annotation.scoring_action_readiness`。

- `main`：L15–L63。
### scripts/evaluate_small_roi_keypoint_truth.py

[打开源码](../../scripts/evaluate_small_roi_keypoint_truth.py) · 29 行

Evaluate experimental small-ROI Pose against independently adjudicated truth.

直接引用的项目模块：`rallymate_evaluation.small_roi_keypoint_truth`。

- `main`：L13–L25。
### scripts/export_mmpose_halpe26_dataset.py

[打开源码](../../scripts/export_mmpose_halpe26_dataset.py) · 59 行

Export ready adjudicated Halpe26 truth to a development MMPose dataset.

直接引用的项目模块：`rallymate_training.mmpose_dataset`。

- `main`：L16–L55。
### scripts/fit_calibration_candidate.py

[打开源码](../../scripts/fit_calibration_candidate.py) · 110 行

直接引用的项目模块：`rallymate_scoring.calibration_fitting`。

- `_load_object`：L15–L22。
- `_atomic_write_json`：L25–L40。
- `main`：L43–L106。
### scripts/fit_ranking_calibration_candidate.py

[打开源码](../../scripts/fit_ranking_calibration_candidate.py) · 65 行

直接引用的项目模块：`rallymate_scoring.calibration_ranking`。

- `_load_object`：L14–L21。
- `main`：L24–L61。
### scripts/full_service_status.ps1

[打开源码](../../scripts/full_service_status.ps1) · 149 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scripts/ingest_scoring_truth_event_phase.py

[打开源码](../../scripts/ingest_scoring_truth_event_phase.py) · 96 行

直接引用的项目模块：`rallymate_annotation.scoring_truth_event_phase_intake`。

- `_parser`：L14–L38。
- `main`：L41–L92。
### scripts/ingest_scoring_truth_exports.py

[打开源码](../../scripts/ingest_scoring_truth_exports.py) · 102 行

直接引用的项目模块：`rallymate_annotation.scoring_truth_intake`。

- `_parser`：L15–L42。
- `main`：L45–L98。
### scripts/integration_service.py

[打开源码](../../scripts/integration_service.py) · 118 行

直接引用的项目模块：`rallymate_service.api`、`rallymate_service.config`、`rallymate_service.database`、`rallymate_service.worker`。

- `make_consecutive_clip`：L20–L43。
- `main`：L46–L114。
### scripts/prepare_ranking_calibration_dataset.py

[打开源码](../../scripts/prepare_ranking_calibration_dataset.py) · 67 行

直接引用的项目模块：`rallymate_scoring.calibration_ranking`。

- `main`：L15–L63。
### scripts/prepare_rtmpose_runtime.ps1

[打开源码](../../scripts/prepare_rtmpose_runtime.ps1) · 59 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scripts/promote_calibration_candidate.py

[打开源码](../../scripts/promote_calibration_candidate.py) · 534 行

直接引用的项目模块：`rallymate_scoring.calibration_fitting`、`rallymate_scoring.calibration_promotion`、`rallymate_scoring.maturity_evidence`、`rallymate_scoring.registry_lifecycle`。

- `_read_snapshot`：L31–L36。
- `_strict_json_loads`：L39–L91。
- `_load_object_snapshot`：L94–L108。
- `_load_jsonl_snapshot`：L111–L133。
- `_recheck_snapshot`：L136–L141。
- `_resolve_pinned_runtime_feasibility_registry`：L144–L168。
- `_recheck_runtime_registry_authority`：L171–L194。
- `_stage_json`：L197–L212。
- `_write_outputs`：L215–L243。
- `main`：L246–L530。
### scripts/range_http_server.py

[打开源码](../../scripts/range_http_server.py) · 344 行

- `_is_private_path`：L36–L47。
- `_public_request_path`：L50–L77。
- `_is_reparse_point`：L80–L89。
- `_contains_reparse_component`：L92–L101。
- `_resolve_request_target`：L104–L130。
- `_resolve_allow_entries`：L133–L170。
- `RangeRequestHandler`：L173–L292；方法：`__init__` L179、`_loopback_host_allowed` L190、`end_headers` L201、`list_directory` L208、`send_head` L212、`copyfile` L282。
- `main`：L295–L340。
### scripts/refresh_scoring_truth_evidence.py

[打开源码](../../scripts/refresh_scoring_truth_evidence.py) · 78 行

Compile truth and publish one immutable, fail-closed M45/M46 refresh bundle.

直接引用的项目模块：`rallymate_annotation.scoring_truth_refresh`。

- `main`：L17–L74。
### scripts/render_full_body_keypoint_video.py

[打开源码](../../scripts/render_full_body_keypoint_video.py) · 358 行

- `_point_color`：L84–L90。
- `_body_box`：L93–L112。
- `_draw_line`：L115–L126。
- `_draw_derived_body_center`：L129–L148。
- `_draw_pose`：L151–L182。
- `_draw_panel`：L185–L231。
- `main`：L234–L354。
### scripts/render_m77_fs09_phase_review_clips.py

[打开源码](../../scripts/render_m77_fs09_phase_review_clips.py) · 186 行

直接引用的项目模块：`rallymate_evaluation.fs09_phase_truth`、`rallymate_evaluation.small_roi_keypoint_truth`。

- `_metadata_audit`：L20–L74。
- `main`：L77–L182。
### scripts/render_pose_keypoint_focus_video.py

[打开源码](../../scripts/render_pose_keypoint_focus_video.py) · 459 行

- `_load_jsonl`：L59–L60。
- `_frames_path`：L63–L66。
- `_point_map`：L69–L70。
- `_valid`：L73–L74。
- `_xy`：L77–L78。
- `_selected_pose`：L81–L85。
- `_selected_bbox`：L88–L93。
- `_smooth_box`：L96–L109。
- `_full_body_box`：L112–L127。
- `_foot_box`：L130–L153。
- `_render_crop`：L156–L187。
- `_inside`：L190–L191。
- `_put_text`：L194–L203。
- `_draw_pose`：L206–L262。
- `_ankle_angle`：L265–L277。
- `_draw_header`：L280–L296。
- `main`：L299–L455。
### scripts/render_pose_scoring_comparison_video.py

[打开源码](../../scripts/render_pose_scoring_comparison_video.py) · 271 行

- `_load_jsonl`：L53–L54。
- `_frames_path`：L57–L60。
- `_point_map`：L63–L64。
- `_valid`：L67–L68。
- `_xy`：L71–L72。
- `_ankle_angle`：L75–L88。
- `_selected_pose`：L91–L95。
- `_draw_pose`：L98–L116。
- `_letterbox`：L119–L126。
- `_crop_focus`：L129–L149。
- `_active_event`：L152–L157。
- `_selected_bbox`：L160–L165。
- `main`：L168–L267。
### scripts/render_residual_computability_video.py

[打开源码](../../scripts/render_residual_computability_video.py) · 422 行

Render residual unavailable events with their fail-closed evidence.

- `_sha256`：L46–L51。
- `_read_jsonl`：L54–L59。
- `resolve_ffmpeg`：L62–L79。
- `merge_residual_ranges`：L82–L114。
- `_pose`：L117–L125。
- `_detection`：L128–L136。
- `_points`：L139–L148。
- `_draw_pose`：L151–L166。
- `_annotate`：L169–L247。
- `main`：L250–L418。
### scripts/render_small_roi_recovery_comparison.py

[打开源码](../../scripts/render_small_roi_recovery_comparison.py) · 454 行

Render a dynamic current-vs-small-ROI pose comparison for audited gap events.

- `_sha256`：L29–L34。
- `_canonical`：L37–L44。
- `_read_jsonl`：L47–L48。
- `resolve_ffmpeg`：L51–L69。
- `merge_event_ranges`：L72–L92。
- `_pose`：L95–L96。
- `_valid_points`：L99–L107。
- `_draw_pose`：L110–L118。
- `_bbox_for_track`：L121–L128。
- `_zoom_crop`：L131–L143。
- `_panel`：L146–L199。
- `main`：L202–L450。
### scripts/render_wholebody133_videos.py

[打开源码](../../scripts/render_wholebody133_videos.py) · 661 行

- `_valid`：L88–L93。
- `_xy`：L96–L97。
- `_point_map`：L100–L103。
- `_group_counts`：L106–L116。
- `_body_box`：L119–L140。
- `_detail_box`：L143–L176。
- `_draw_connection`：L179–L193。
- `_draw_chain`：L196–L208。
- `_hand_connections`：L211–L219。
- `_draw_wholebody`：L222–L261。
- `_draw_named_pose`：L264–L317。
- `_header`：L320–L332。
- `_wholebody_lines`：L335–L345。
- `main`：L348–L657。
### scripts/restart_preview_service.ps1

[打开源码](../../scripts/restart_preview_service.ps1) · 74 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scripts/review_pose_diagnostic_quality_gate.py

[打开源码](../../scripts/review_pose_diagnostic_quality_gate.py) · 71 行

直接引用的项目模块：`rallymate_scoring.diagnostic_policy_review`。

- `main`：L13–L67。
### scripts/run_api.ps1

[打开源码](../../scripts/run_api.ps1) · 81 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scripts/run_demo.ps1

[打开源码](../../scripts/run_demo.ps1) · 81 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scripts/run_dev_service.py

[打开源码](../../scripts/run_dev_service.py) · 20 行

直接引用的项目模块：`rallymate_service.cli`。

- `main`：L9–L16。
### scripts/run_fs09_pose_wave_v2.py

[打开源码](../../scripts/run_fs09_pose_wave_v2.py) · 330 行

直接引用的项目模块：`rallymate_scoring.loop`、`rallymate_scoring.scoring_loop_report`、`rallymate_vision.validation`。

- `_sha256`：L21–L26。
- `_feature_sample`：L29–L36。
- `_registry_indicator_scope`：L39–L57。
- `_frame_window`：L60–L86。
- `main`：L89–L326。
### scripts/run_full_service.ps1

[打开源码](../../scripts/run_full_service.ps1) · 68 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scripts/run_integration_tests.ps1

[打开源码](../../scripts/run_integration_tests.ps1) · 69 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scripts/run_local_inference.ps1

[打开源码](../../scripts/run_local_inference.ps1) · 80 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scripts/run_m71_larger_pose_model_extension.py

[打开源码](../../scripts/run_m71_larger_pose_model_extension.py) · 586 行

Try RTMPose-L only where it strictly extends the M70 Pose evidence.

直接引用的项目模块：`rallymate_evaluation.fixed_boundary_ab`、`rallymate_evaluation.pose_observability_residual`、`rallymate_evaluation.pose_observability_router`、`rallymate_evaluation.pose_policy_composition`、`rallymate_evaluation.small_roi_recovery`、`rallymate_vision.pose.adapters`、`rallymate_vision.pose.rtmpose_backend`。

- `_sha256`：L47–L52。
- `_source`：L55–L56。
- `_bound`：L59–L63。
- `_rows`：L66–L74。
- `_write_rows`：L77–L80。
- `_identity_set`：L83–L87。
- `_replace_selected_poses`：L90–L114。
- `_validate_model_candidate`：L117–L140。
- `validate_larger_pose_model_extension_report`：L143–L205。
- `main`：L208–L582。
### scripts/run_m72_additive_keypoint_fusion.py

[打开源码](../../scripts/run_m72_additive_keypoint_fusion.py) · 330 行

Fill M71-invalid Halpe26 joints from RTMPose-L without overwriting valid points.

直接引用的项目模块：`rallymate_evaluation.fixed_boundary_ab`、`rallymate_evaluation.pose_observability_router`、`rallymate_evaluation.pose_policy_composition`。

- `validate_additive_keypoint_fusion_report`：L59–L120。
- `main`：L123–L326。
### scripts/run_m73_wholebody_mapped_keypoint_fusion.py

[打开源码](../../scripts/run_m73_wholebody_mapped_keypoint_fusion.py) · 543 行

Use WholeBody133 only to fill explicitly mapped missing M72 keypoints.

直接引用的项目模块：`rallymate_evaluation.fixed_boundary_ab`、`rallymate_evaluation.pose_observability_router`、`rallymate_evaluation.pose_policy_composition`、`rallymate_vision.pose.adapters`、`rallymate_vision.pose.metadata`、`rallymate_vision.pose.rtmpose_backend`。

- `_validate_candidate`：L66–L89。
- `_semantic_map`：L92–L106。
- `validate_wholebody_mapped_keypoint_fusion_report`：L109–L190。
- `main`：L193–L539。
### scripts/run_m95_rtmpose_x_shadow_smoke.py

[打开源码](../../scripts/run_m95_rtmpose_x_shadow_smoke.py) · 52 行

Run an immutable RTMPose-X development smoke without opening the holdout.

直接引用的项目模块：`rallymate_evaluation.pose_shadow_smoke`。

- `main`：L13–L48。
### scripts/run_m96_rtmpose_same_frame_diagnostic.py

[打开源码](../../scripts/run_m96_rtmpose_same_frame_diagnostic.py) · 57 行

Run immutable same-frame RTMPose M/L/X development diagnostics.

直接引用的项目模块：`rallymate_evaluation.pose_same_frame_diagnostic`。

- `main`：L13–L53。
### scripts/run_m97_x_pose_operational_extension.py

[打开源码](../../scripts/run_m97_x_pose_operational_extension.py) · 606 行

Run RTMPose-X on the exact M71 residual scope and strict M70 router baseline.

直接引用的项目模块：`rallymate_evaluation.fixed_boundary_ab`、`rallymate_evaluation.pose_observability_residual`、`rallymate_evaluation.pose_observability_router`、`rallymate_evaluation.pose_policy_composition`、`rallymate_evaluation.pose_x_operational`、`rallymate_evaluation.small_roi_recovery`、`rallymate_vision.pose.adapters`、`rallymate_vision.pose.rtmpose_backend`。

- `_json`：L64–L71。
- `_rows`：L74–L82。
- `_selected_rows`：L85–L102。
- `_write_rows`：L105–L108。
- `_same_source`：L111–L116。
- `_replace_target_poses`：L119–L143。
- `_identity_sha256`：L146–L154。
- `run_video`：L157–L564。
- `main`：L567–L602。
### scripts/run_pose_deployment_smoke.ps1

[打开源码](../../scripts/run_pose_deployment_smoke.ps1) · 37 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scripts/run_pose_observability_router_experiment.py

[打开源码](../../scripts/run_pose_observability_router_experiment.py) · 309 行

Route a larger-ROI Pose only on strict required-joint validity dominance.

直接引用的项目模块：`rallymate_evaluation.fixed_boundary_ab`、`rallymate_evaluation.pose_observability_router`、`rallymate_evaluation.pose_policy_composition`、`rallymate_evaluation.small_roi_recovery`。

- `_sha256`：L27–L32。
- `_source`：L35–L36。
- `_read_jsonl`：L39–L47。
- `_balanced_counts`：L50–L58。
- `main`：L61–L305。
### scripts/run_pose_scoring_ab.py

[打开源码](../../scripts/run_pose_scoring_ab.py) · 262 行

直接引用的项目模块：`rallymate_features`、`rallymate_scoring.feasibility`、`rallymate_scoring.loop`、`rallymate_tracking`。

- `_parse_args`：L47–L66。
- `_load_historical_registry`：L69–L88。
- `_historical_replay_metadata`：L91–L98。
- `_load`：L101–L102。
- `_frames_path`：L105–L108。
- `_pose_metadata`：L111–L122。
- `_stats`：L125–L134。
- `main`：L137–L258。
### scripts/run_regression_suites.py

[打开源码](../../scripts/run_regression_suites.py) · 145 行

Run the complete regression suite with explicit historical-fixture accounting.

- `flatten`：L27–L32。
- `partition_tests`：L35–L60。
- `AccountingResult`：L63–L70；方法：`__init__` L64、`addSuccess` L68。
- `result_summary`：L73–L87。
- `parse_args`：L90–L96。
- `main`：L99–L141。
### scripts/run_residual_high_resolution_pose_experiment.py

[打开源码](../../scripts/run_residual_high_resolution_pose_experiment.py) · 481 行

Re-infer M69 residual events with a higher-resolution Pose profile.

直接引用的项目模块：`rallymate_evaluation.fixed_boundary_ab`、`rallymate_evaluation.pose_observability_residual`、`rallymate_evaluation.pose_observability_router`、`rallymate_evaluation.pose_policy_composition`、`rallymate_evaluation.small_roi_recovery`、`rallymate_vision.pose.adapters`、`rallymate_vision.pose.rtmpose_backend`。

- `_sha256`：L33–L38。
- `_source`：L41–L42。
- `_read_jsonl`：L45–L53。
- `_write_jsonl`：L56–L59。
- `_balanced`：L62–L70。
- `validate_residual_high_resolution_experiment_report`：L73–L123。
- `main`：L126–L477。
### scripts/run_residual_pose_profile_ablation.py

[打开源码](../../scripts/run_residual_pose_profile_ablation.py) · 623 行

Isolate a 384x288 Pose profile under the exact M68 crop context.

直接引用的项目模块：`rallymate_evaluation.fixed_boundary_ab`、`rallymate_evaluation.pose_crop_context`、`rallymate_evaluation.pose_observability_residual`、`rallymate_evaluation.pose_observability_router`、`rallymate_evaluation.pose_policy_composition`、`rallymate_evaluation.small_roi_recovery`、`rallymate_vision.pose.adapters`、`rallymate_vision.pose.rtmpose_backend`。

- `_sha256`：L35–L40。
- `_source`：L43–L44。
- `_read_jsonl`：L47–L55。
- `_write_jsonl`：L58–L61。
- `_bound`：L64–L68。
- `_balanced`：L71–L79。
- `_context_settings`：L82–L113。
- `_replace_selected_poses`：L116–L140。
- `validate_pose_profile_ablation_report`：L143–L221。
- `main`：L224–L619。
### scripts/run_roi_margin_pose_recovery_experiment.py

[打开源码](../../scripts/run_roi_margin_pose_recovery_experiment.py) · 434 行

Re-run gap-event ROIs with more crop context as a non-production experiment.

直接引用的项目模块：`rallymate_evaluation.fixed_boundary_ab`、`rallymate_evaluation.small_roi_recovery`、`rallymate_vision.pose.adapters`、`rallymate_vision.pose.rtmpose_backend`。

- `_sha256`：L30–L35。
- `_read_jsonl`：L38–L46。
- `_source`：L49–L50。
- `validate_roi_margin_experiment_report`：L53–L118。
- `main`：L121–L430。
### scripts/run_rtmpose_ab_artifacts.py

[打开源码](../../scripts/run_rtmpose_ab_artifacts.py) · 208 行

直接引用的项目模块：`rallymate_pose_evaluation.baseline`、`rallymate_vision.contracts`、`rallymate_vision.pose`、`rallymate_vision.validation`。

- `_config_path`：L25–L27。
- `_load_candidates`：L30–L46。
- `run_video`：L49–L193。
- `main`：L196–L204。
### scripts/run_rtmpose_finetune.py

[打开源码](../../scripts/run_rtmpose_finetune.py) · 75 行

直接引用的项目模块：`rallymate_training.mmpose_finetune`。

- `build_parser`：L19–L44。
- `main`：L47–L71。
### scripts/run_rtmpose_service.ps1

[打开源码](../../scripts/run_rtmpose_service.ps1) · 26 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scripts/run_rtmpose_worker.ps1

[打开源码](../../scripts/run_rtmpose_worker.ps1) · 55 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scripts/run_small_roi_pose_recovery_experiment.py

[打开源码](../../scripts/run_small_roi_pose_recovery_experiment.py) · 331 行

Re-run only guard-skipped primary-player ROIs as a non-production experiment.

直接引用的项目模块：`rallymate_evaluation.fixed_boundary_ab`、`rallymate_evaluation.small_roi_recovery`、`rallymate_vision.pose.adapters`、`rallymate_vision.pose.rtmpose_backend`。

- `_sha256`：L31–L36。
- `_read_jsonl`：L39–L43。
- `_source`：L46–L47。
- `validate_small_roi_experiment_report`：L50–L92。
- `main`：L95–L327。
### scripts/run_tests.ps1

[打开源码](../../scripts/run_tests.ps1) · 45 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scripts/run_wholebody133_video_artifact.py

[打开源码](../../scripts/run_wholebody133_video_artifact.py) · 270 行

直接引用的项目模块：`rallymate_vision.contracts`、`rallymate_vision.pose`、`rallymate_vision.pose.metadata`、`rallymate_vision.validation`。

- `_jsonl`：L39–L44。
- `main`：L47–L266。
### scripts/run_worker.ps1

[打开源码](../../scripts/run_worker.ps1) · 100 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scripts/score_calibrated_indicators.py

[打开源码](../../scripts/score_calibrated_indicators.py) · 761 行

直接引用的项目模块：`rallymate_scoring.batch`、`rallymate_scoring.calibration`、`rallymate_scoring.calibration_fitting`、`rallymate_scoring.registry_lifecycle`、`rallymate_scoring.run_bundle_binding`、`rallymate_scoring.runtime_profile_binding`、`rallymate_vision.pipeline`、`rallymate_vision.pose.metadata`。

- `_sha256_bytes`：L36–L37。
- `_load_json_document`：L40–L48。
- `_load_jsonl`：L51–L71。
- `_resolve_pinned_runtime_feasibility_registry`：L74–L93。
- `_recheck_runtime_registry_authority`：L96–L119。
- `_require_summary_registry_lifecycle_binding`：L122–L164。
- `_reject_output_path_overlap`：L167–L195。
- `_require_bound_production_artifact`：L198–L250。
- `_load_calibrations`：L253–L289。
- `main`：L292–L757。
### scripts/serve_current_docs.ps1

[打开源码](../../scripts/serve_current_docs.ps1) · 88 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scripts/serve_fs09_phase_blind_handoff.py

[打开源码](../../scripts/serve_fs09_phase_blind_handoff.py) · 1,354 行

Validate and serve an FS09 public blind-handoff bundle.

- `BundleValidationError`：L248–L249。
- `JsonArgumentParser`：L252–L257；方法：`error` L255。
- `_emit_json`：L260–L267。
- `_reject_json_constant`：L270–L271。
- `_unique_json_object`：L274–L280。
- `_load_json_object`：L283–L300。
- `_load_manifest`：L303–L304。
- `_require_nonempty_string`：L307–L310。
- `_require_rfc3339_timestamp`：L313–L327。
- `_require_sha256`：L330–L333。
- `_canonical_json_bytes`：L336–L346。
- `_json_equal`：L349–L350。
- `_require_exact_object`：L353–L358。
- `_expected_bootstrap_tasks`：L361–L389。
- `_validate_analysis_plan`：L392–L735。
- `_validate_public_review`：L738–L825。
- `_safe_bundle_path`：L828–L850。
- `_is_link_like`：L853–L857。
- `_snapshot_regular_file`：L860–L876。
- `_actual_bundle_files`：L879–L915。
- `_canonical_content_root`：L918–L926。
- `_expected_bundle_id`：L929–L943。
- `validate_and_snapshot`：L946–L1109。
- `_request_bundle_path`：L1112–L1128。
- `_content_type`：L1131–L1145。
- `_parse_range`：L1148–L1166。
- `make_request_handler`：L1169–L1279。
- `SnapshotHTTPServer`：L1282–L1284。
- `_parse_args`：L1287–L1300。
- `run`：L1303–L1339。
- `main`：L1342–L1350。
### scripts/serve_m96_pose_pilot.py

[打开源码](../../scripts/serve_m96_pose_pilot.py) · 76 行

直接引用的项目模块：`rallymate_evaluation.m96_pose_pilot`。

- `main`：L18–L72。
### scripts/serve_pose_scoring_ab.ps1

[打开源码](../../scripts/serve_pose_scoring_ab.ps1) · 82 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scripts/serve_scoring_truth_event_execution.py

[打开源码](../../scripts/serve_scoring_truth_event_execution.py) · 802 行

Validate and serve a portable M93 event/phase execution bundle.

- `BundleValidationError`：L136–L137。
- `JsonArgumentParser`：L140–L143；方法：`error` L141。
- `_emit_json`：L146–L153。
- `_reject_json_constant`：L156–L157。
- `_unique_json_object`：L160–L166。
- `_validate_unicode`：L169–L189。
- `_canonical_bytes`：L192–L203。
- `_sha256`：L206–L207。
- `_load_json_object`：L210–L224。
- `_bundle_ref`：L227–L231。
- `_expected_bootstrap`：L234–L321。
- `_validate_review_html`：L324–L352。
- `_require_exact_keys`：L355–L362。
- `_require_sha`：L365–L368。
- `_timestamp`：L371–L380。
- `_safe_path`：L383–L396。
- `_is_link_like`：L399–L411。
- `_path_identity`：L414–L415。
- `_snapshot_file`：L418–L453。
- `_tree_files`：L456–L484。
- `_manifest_projection`：L487–L492。
- `manifest_binding_sha256`：L495–L496。
- `_content_root`：L499–L500。
- `_manifest_name`：L503–L511。
- `_validate_manifest`：L514–L570。
- `validate_and_snapshot`：L573–L593。
- `_served_paths`：L596–L612。
- `_decode_target`：L615–L630。
- `_range`：L633–L651。
- `_handler`：L654–L740。
- `main`：L743–L785。
### scripts/serve_scoring_truth_event_handoff.py

[打开源码](../../scripts/serve_scoring_truth_event_handoff.py) · 822 行

Validate and serve a portable three-video event/phase technical handoff.

- `BundleValidationError`：L156–L157。
- `JsonArgumentParser`：L160–L163；方法：`error` L161。
- `_emit_json`：L166–L173。
- `_canonical_bytes`：L176–L186。
- `_sha256`：L189–L190。
- `_reject_json_constant`：L193–L194。
- `_unique_json_object`：L197–L203。
- `_load_json_object`：L206–L219。
- `_require_exact_keys`：L222–L225。
- `_require_sha256`：L228–L231。
- `_timestamp`：L234–L240。
- `_safe_path`：L243–L256。
- `_content_root`：L259–L260。
- `_bundle_id`：L263–L274。
- `_operator_readme`：L277–L308。
- `_bootstrap`：L311–L325。
- `_review_html`：L328–L395。
- `_is_link_like`：L398–L402。
- `_snapshot_file`：L405–L421。
- `_actual_files`：L424–L443。
- `_validate_text_boundary`：L446–L469。
- `validate_and_snapshot`：L472–L583。
- `_request_path`：L586–L603。
- `_content_type`：L606–L620。
- `_parse_range`：L623–L641。
- `make_request_handler`：L644–L750。
- `SnapshotHTTPServer`：L753–L765；方法：`__init__` L757。
- `_parse_args`：L768–L781。
- `run`：L784–L810。
- `main`：L813–L818。
### scripts/slice_pose_scoring_window.py

[打开源码](../../scripts/slice_pose_scoring_window.py) · 64 行

Create an exact processed-index window for a fair pose scoring comparison.

- `_slice_jsonl`：L11–L35。
- `main`：L38–L60。
### scripts/smoke_pose_deployment_profile.py

[打开源码](../../scripts/smoke_pose_deployment_profile.py) · 293 行

直接引用的项目模块：`rallymate_scoring.feasibility`、`rallymate_service.config`、`rallymate_service.database`、`rallymate_service.worker`、`rallymate_vision.contracts`、`rallymate_vision.pose.metadata`、`rallymate_vision.pose.presets`、`rallymate_vision.validation`。

- `_jsonl`：L23–L28。
- `_registry_scope`：L31–L42。
- `_validate_scoring_contract`：L45–L116。
- `main`：L119–L289。
### scripts/start_local.ps1

[打开源码](../../scripts/start_local.ps1) · 83 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scripts/start_preview_web.ps1

[打开源码](../../scripts/start_preview_web.ps1) · 46 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### scripts/validate_m96_pose_pilot_submission.py

[打开源码](../../scripts/validate_m96_pose_pilot_submission.py) · 57 行

直接引用的项目模块：`rallymate_evaluation.m96_pose_pilot`。

- `main`：L18–L53。
### scripts/validate_pose_diagnostic_review_decisions.py

[打开源码](../../scripts/validate_pose_diagnostic_review_decisions.py) · 77 行

直接引用的项目模块：`rallymate_evaluation.pose_diagnostic_review`。

- `_sha256`：L16–L17。
- `main`：L20–L73。
### scripts/validate_run.py

[打开源码](../../scripts/validate_run.py) · 21 行

直接引用的项目模块：`rallymate_vision.validation`。

- `main`：L10–L17。
### scripts/validate_wholebody133_videos.py

[打开源码](../../scripts/validate_wholebody133_videos.py) · 152 行

Validate the browser-facing WholeBody133 demonstration videos.

- `_sha256`：L26–L31。
- `_fourcc_text`：L34–L36。
- `_inspect`：L39–L88。
- `_write_atomic`：L91–L104。
- `main`：L107–L148。
### scripts/watch_full_service.ps1

[打开源码](../../scripts/watch_full_service.ps1) · 29 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。

## src/rallymate_annotation

### src/rallymate_annotation/__init__.py

[打开源码](../../src/rallymate_annotation/__init__.py) · 54 行

直接引用的项目模块：`rallymate_annotation.scoring_truth_authorization`、`rallymate_annotation.scoring_truth_event_execution`、`rallymate_annotation.scoring_truth_event_handoff`、`rallymate_annotation.scoring_truth_event_phase_intake`、`rallymate_annotation.scoring_truth_intake`、`rallymate_annotation.truth_pack`。

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_annotation/assets/event-gap-truth-workbench.css

[打开源码](../../src/rallymate_annotation/assets/event-gap-truth-workbench.css) · 18 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_annotation/assets/event-gap-truth-workbench.js

[打开源码](../../src/rallymate_annotation/assets/event-gap-truth-workbench.js) · 250 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_annotation/assets/fs09-phase-truth-workbench.css

[打开源码](../../src/rallymate_annotation/assets/fs09-phase-truth-workbench.css) · 1 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_annotation/assets/fs09-phase-truth-workbench.js

[打开源码](../../src/rallymate_annotation/assets/fs09-phase-truth-workbench.js) · 237 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_annotation/assets/m96-pose-pilot-workbench.css

[打开源码](../../src/rallymate_annotation/assets/m96-pose-pilot-workbench.css) · 36 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_annotation/assets/m96-pose-pilot-workbench.js

[打开源码](../../src/rallymate_annotation/assets/m96-pose-pilot-workbench.js) · 328 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_annotation/assets/scoring-truth-event-collection-core.js

[打开源码](../../src/rallymate_annotation/assets/scoring-truth-event-collection-core.js) · 1,146 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_annotation/assets/scoring-truth-event-collection-workbench.css

[打开源码](../../src/rallymate_annotation/assets/scoring-truth-event-collection-workbench.css) · 114 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_annotation/assets/scoring-truth-event-collection-workbench.html

[打开源码](../../src/rallymate_annotation/assets/scoring-truth-event-collection-workbench.html) · 204 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_annotation/assets/scoring-truth-event-collection-workbench.js

[打开源码](../../src/rallymate_annotation/assets/scoring-truth-event-collection-workbench.js) · 866 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_annotation/assets/scoring-truth-event-workbench.css

[打开源码](../../src/rallymate_annotation/assets/scoring-truth-event-workbench.css) · 156 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_annotation/assets/scoring-truth-event-workbench.js

[打开源码](../../src/rallymate_annotation/assets/scoring-truth-event-workbench.js) · 131 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_annotation/assets/small-roi-truth-workbench.css

[打开源码](../../src/rallymate_annotation/assets/small-roi-truth-workbench.css) · 18 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_annotation/assets/small-roi-truth-workbench.js

[打开源码](../../src/rallymate_annotation/assets/small-roi-truth-workbench.js) · 288 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_annotation/assets/truth-workbench.css

[打开源码](../../src/rallymate_annotation/assets/truth-workbench.css) · 124 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_annotation/assets/truth-workbench.js

[打开源码](../../src/rallymate_annotation/assets/truth-workbench.js) · 743 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_annotation/scoring_action_readiness.py

[打开源码](../../src/rallymate_annotation/scoring_action_readiness.py) · 978 行

直接引用的项目模块：`rallymate_annotation.scoring_action_worklist`、`rallymate_evaluation.feature_errors`、`rallymate_evaluation.ground_truth`、`rallymate_evaluation.pose_diagnostic_truth`、`rallymate_events`、`rallymate_events.schemas`、`rallymate_features`、`rallymate_scoring.feasibility`、`rallymate_scoring.scoring_context`。

- `sha256_file`：L60–L65。
- `canonical_hash`：L68–L77。
- `_read_json`：L80–L84。
- `_read_jsonl`：L87–L96。
- `_source`：L99–L100。
- `_resolved_path`：L103–L106。
- `_assert_truth_validation_bindings`：L109–L137。
- `_assert_truth_validation_counts`：L140–L158。
- `_assert_feature_evaluation_bindings`：L161–L218。
- `_recompute_scoring_truth_evaluation`：L221–L285。
- `_validate_semantic_record`：L288–L323。
- `_event_matches`：L326–L334。
- `_phase_name`：L337–L340。
- `_diagnostic_status`：L343–L372。
- `_matched_truth_event`：L375–L385。
- `_semantic_evidence`：L388–L402。
- `_evaluate_item`：L405–L609。
- `build_scoring_truth_action_readiness`：L612–L853。
- `validate_scoring_truth_action_readiness`：L856–L978。
### src/rallymate_annotation/scoring_action_worklist.py

[打开源码](../../src/rallymate_annotation/scoring_action_worklist.py) · 590 行

直接引用的项目模块：`rallymate_scoring.blocker_taxonomy`、`rallymate_scoring.feasibility`。

- `sha256_file`：L33–L38。
- `_read_json`：L41–L45。
- `_read_jsonl`：L48–L59。
- `_source`：L62–L63。
- `_phase_requirement`：L66–L67。
- `_review_type`：L70–L77。
- `_assert_source_bindings`：L80–L121。
- `build_scoring_truth_action_worklist`：L124–L423。
- `validate_scoring_truth_action_worklist`：L426–L506。
- `write_scoring_truth_action_csv`：L509–L546。
- `write_scoring_truth_action_html`：L549–L590。
### src/rallymate_annotation/scoring_evidence_plan.py

[打开源码](../../src/rallymate_annotation/scoring_evidence_plan.py) · 669 行

直接引用的项目模块：`rallymate_annotation.scoring_action_readiness`、`rallymate_annotation.scoring_action_worklist`。

- `sha256_file`：L29–L34。
- `_read_json`：L37–L41。
- `_unit_id`：L44–L45。
- `_status_from_rows`：L48–L53。
- `_evidence_status`：L56–L125。
- `_base_spec`：L128–L150。
- `_required_units`：L153–L299。
- `build_scoring_truth_evidence_plan`：L302–L449。
- `validate_scoring_truth_evidence_plan`：L452–L559。
- `validate_scoring_truth_evidence_plan_sources`：L562–L589。
- `write_scoring_truth_evidence_plan`：L592–L669。
### src/rallymate_annotation/scoring_truth_authorization.py

[打开源码](../../src/rallymate_annotation/scoring_truth_authorization.py) · 731 行

直接引用的项目模块：`rallymate_annotation.scoring_truth_event_handoff`。

- `ScoringTruthAuthorizationError`：L131–L132。
- `_canonical_json_bytes`：L135–L145。
- `_sha256`：L148–L149。
- `_reject_json_constant`：L152–L153。
- `_unique_json_object`：L156–L162。
- `_is_link_like`：L165–L177。
- `_path_identity`：L180–L181。
- `_snapshot_file`：L184–L227。
- `_parse_json_object`：L230–L245。
- `_snapshot_json`：L248–L250。
- `_require_exact_keys`：L253–L262。
- `_require_identifier`：L265–L268。
- `_require_sha`：L271–L274。
- `_require_exact_json`：L277–L279。
- `_parse_canonical_utc`：L282–L294。
- `_validate_technical_handoff`：L297–L329。
- `_validate_plan`：L332–L390。
- `_validate_release_record`：L393–L467。
- `_assert_not_template_path`：L470–L472。
- `_handoff_scope`：L475–L491。
- `_role_protocol`：L494–L500。
- `scoring_truth_authorization_binding_sha256`：L503–L514。
- `validate_scoring_truth_authorization_binding`：L517–L608。
- `verify_scoring_truth_event_authorization`：L611–L723。
### src/rallymate_annotation/scoring_truth_event_execution.py

[打开源码](../../src/rallymate_annotation/scoring_truth_event_execution.py) · 2,946 行

直接引用的项目模块：`rallymate_annotation.scoring_truth_authorization`、`rallymate_annotation.scoring_truth_event_handoff`。

- `ScoringTruthEventExecutionError`：L225–L226。
- `_validate_unicode`：L229–L251。
- `_canonical_json_bytes`：L254–L265。
- `_pretty_json_bytes`：L268–L274。
- `_canonical_file_bytes`：L277–L278。
- `_sha256`：L281–L282。
- `_reject_json_constant`：L285–L286。
- `_unique_json_object`：L289–L295。
- `_parse_json_object`：L298–L314。
- `_is_link_like`：L317–L329。
- `_path_identity`：L332–L333。
- `_snapshot_file`：L336–L381。
- `_snapshot_json`：L384–L386。
- `_require_exact_keys`：L389–L398。
- `_require_exact_json`：L401–L403。
- `_require_sha`：L406–L409。
- `_require_identifier`：L412–L415。
- `_require_int`：L418–L432。
- `_parse_utc`：L435–L452。
- `_safe_relative_path`：L455–L472。
- `_identity_key`：L475–L479。
- `_now_utc`：L482–L483。
- `_format_generated_utc`：L486–L489。
- `_repo_root`：L492–L493。
- `_asset_path`：L496–L497。
- `_approved_asset_snapshot`：L500–L504。
- `_server_source_path`：L507–L508。
- `_load_server_module`：L511–L524。
- `_manifest_projection`：L527–L533。
- `scoring_truth_event_manifest_binding_sha256`：L536–L543。
- `_content_root`：L546–L547。
- `_artifact_records`：L550–L554。
- `_paths_overlap`：L557–L564。
- `_write_new_file`：L567–L577。
- `_write_tree`：L580–L582。
- `_publish_staging`：L585–L591。
- `_probe_video`：L594–L618。
- `_authorization_snapshot`：L621–L659。
- `_authority_raw`：L662–L673。
- `_source_files`：L676–L693。
- `_execution_id`：L696–L706。
- `_bundle_id`：L709–L723。
- `_scope_from_staging`：L726–L761。
- `_inject_bootstrap`：L764–L780。
- `_extract_bootstrap`：L783–L817。
- `_bundle_ref`：L820–L826。
- `_bootstrap_tasks`：L829–L841。
- `_bootstrap_common`：L844–L887。
- `_execution_bootstrap`：L890–L891。
- `_source_execution_ref`：L894–L898。
- `_adjudication_bootstrap`：L901–L920。
- `_validate_role`：L923–L926。
- `_validate_source_files`：L929–L973。
- `_validate_scope_shape`：L976–L1014。
- `_verify_scope_media`：L1017–L1046。
- `_validate_scope_frame_rates_against_handoff`：L1049–L1058。
- `_validate_artifacts`：L1061–L1091。
- `_validate_manifest_binding`：L1094–L1099。
- `_server_snapshot`：L1102–L1115。
- `_validate_execution_manifest_shape`：L1118–L1180。
- `validate_scoring_truth_event_execution_bundle`：L1183–L1257。
- `build_scoring_truth_event_execution_bundle`：L1260–L1398。
- `_require_canonical_submission_file`：L1401–L1410。
- `_validate_bundle_ref`：L1413–L1418。
- `_annotation_submission_id`：L1421–L1439。
- `_validate_phase_observations`：L1442–L1490。
- `_validate_event_value`：L1493–L1546。
- `_annotation_revision_payload`：L1549–L1568。
- `_review_revision_payload`：L1571–L1590。
- `_validate_annotation_submission_object`：L1593–L1852。
- `validate_scoring_truth_event_annotation_submission`：L1855–L1877。
- `_execution_source_record`：L1880–L1892。
- `_annotation_source_record`：L1895–L1910。
- `_validate_adjudication_manifest_shape`：L1913–L2052。
- `_parse_source_manifest`：L2055–L2058。
- `validate_scoring_truth_event_adjudication_bundle`：L2061–L2241。
- `build_scoring_truth_event_adjudication_bundle`：L2244–L2459。
- `_source_submission_summary`：L2462–L2479。
- `_source_video_revisions`：L2482–L2496。
- `_source_event_index`：L2499–L2507。
- `_validate_adjudication_submission_object`：L2510–L2896。
- `validate_scoring_truth_event_adjudication_submission`：L2899–L2924。
### src/rallymate_annotation/scoring_truth_event_handoff.py

[打开源码](../../src/rallymate_annotation/scoring_truth_event_handoff.py) · 679 行

- `ScoringTruthEventHandoffError`：L112–L113。
- `_canonical_bytes`：L116–L126。
- `_sha256`：L129–L130。
- `_reject_json_constant`：L133–L134。
- `_unique_json_object`：L137–L143。
- `_read_json_object`：L146–L160。
- `_require_sha`：L163–L166。
- `_canonical_utc`：L169–L178。
- `_is_link_like`：L181–L185。
- `_snapshot_file`：L188–L204。
- `_source_projection`：L207–L224。
- `_collect_leaf_tokens`：L227–L241。
- `_sensitive_source_tokens`：L244–L275。
- `_scan_public_bytes`：L278–L292。
- `_video_directory`：L295–L318。
- `_source_snapshot`：L321–L403。
- `_repo_root`：L406–L407。
- `_asset_path`：L410–L411。
- `_server_source_path`：L414–L415。
- `_load_server_module`：L418–L433。
- `_scope`：L436–L446。
- `_safety`：L449–L469。
- `_bundle_id`：L472–L483。
- `_content_root`：L486–L487。
- `_manifest_bytes`：L490–L493。
- `_paths_overlap`：L496–L503。
- `_write_new_file`：L506–L514。
- `validate_scoring_truth_event_handoff`：L517–L572。
- `build_scoring_truth_event_handoff`：L575–L668。
### src/rallymate_annotation/scoring_truth_event_phase_intake.py

[打开源码](../../src/rallymate_annotation/scoring_truth_event_phase_intake.py) · 2,194 行

直接引用的项目模块：`rallymate_annotation.scoring_truth_authorization`、`rallymate_annotation.truth_pack`。

- `ScoringTruthEventPhaseIntakeError`：L124–L125。
- `_sha256`：L128–L129。
- `_canonical_json_bytes`：L132–L144。
- `_canonical_sha`：L147–L148。
- `_utc_now`：L151–L156。
- `_parse_float`：L159–L163。
- `_reject_constant`：L166–L167。
- `_unique_object`：L170–L176。
- `_strict_json_object`：L179–L196。
- `_is_link_like`：L199–L213。
- `_path_identity`：L216–L217。
- `_snapshot_file`：L220–L258。
- `_require_plain_directory`：L261–L271。
- `_snapshot_tree`：L274–L293。
- `_canonical_relative_path`：L296–L310。
- `_session_path`：L313–L320。
- `_path_contains`：L323–L328。
- `_paths_overlap`：L331–L332。
- `_require_exact_keys`：L335–L341。
- `_require_sha`：L344–L347。
- `_require_identifier`：L350–L353。
- `_require_timestamp`：L356–L365。
- `_timestamp_value`：L368–L370。
- `_latest_canonical_timestamp`：L373–L377。
- `_validate_intake_chronology`：L380–L401。
- `_require_nonnegative_int`：L404–L409。
- `_identity_key`：L412–L414。
- `_write_json`：L417–L423。
- `_jsonl_bytes`：L426–L427。
- `_csv_bytes`：L430–L440。
- `_content_root`：L443–L453。
- `_execution_api`：L456–L473。
- `_validated_inputs`：L476–L520。
- `_snapshot_bytes`：L523–L529。
- `_snapshot_object`：L532–L538。
- `_first_sha`：L541–L546。
- `_manifest_binding`：L549–L558。
- `_bundle_record`：L561–L613。
- `_submission_snapshot`：L616–L667。
- `_submission_binding_sha`：L670–L675。
- `_submission_manifest_binding`：L678–L694。
- `_plan_scope`：L697–L719。
- `_confidence_milli`：L722–L726。
- `_validate_phase_observations`：L729–L787。
- `_source_indexes`：L790–L877。
- `_assert_source_submission_summary`：L880–L902。
- `_compile_outputs`：L905–L1278。
- `_roles_and_lineage`：L1281–L1323。
- `_assert_lineage_bindings`：L1326–L1375。
- `_artifact`：L1378–L1379。
- `_copy_bytes`：L1382–L1385。
- `ingest_scoring_truth_event_phase`：L1388–L1658。
- `_walk_plain_tree`：L1661–L1682。
- `_expected_directories`：L1685–L1692。
- `_expected_artifact_role`：L1695–L1716。
- `_parse_artifacts`：L1719–L1758。
- `_validate_manifest_shape`：L1761–L1796。
- `_validate_source_records`：L1799–L1918。
- `_validate_raw_submission_records`：L1921–L2023。
- `_validate_revision_lineage`：L2026–L2055。
- `_validate_compilation_records`：L2058–L2097。
- `validate_scoring_truth_event_phase_intake`：L2100–L2184。
### src/rallymate_annotation/scoring_truth_intake.py

[打开源码](../../src/rallymate_annotation/scoring_truth_intake.py) · 1,032 行

直接引用的项目模块：`rallymate_annotation.truth_pack`。

- `ScoringTruthIntakeError`：L80–L81。
- `_sha256`：L84–L85。
- `_canonical_json_bytes`：L88–L98。
- `_content_root`：L101–L111。
- `_utc_now`：L114–L119。
- `_strict_json`：L122–L155。
- `_is_reparse`：L158–L165。
- `_require_plain_directory`：L168–L174。
- `_path_contains`：L177–L182。
- `_paths_overlap`：L185–L186。
- `_canonical_relative_path`：L189–L199。
- `_session_path`：L202–L207。
- `_require_exact_keys`：L210–L213。
- `_require_sha256`：L216–L219。
- `_require_nonnegative_int`：L222–L225。
- `_read_csv_snapshot`：L228–L270。
- `_coerce_exports`：L273–L303。
- `_walk_tree`：L306–L327。
- `_snapshot_source_pack`：L330–L383。
- `_source_snapshot_equal`：L386–L397。
- `_copy_source_snapshot`：L400–L410。
- `_normalize_compilation_report`：L413–L437。
- `_write_json`：L440–L444。
- `_artifact_role`：L447–L456。
- `_snapshot_session_artifacts`：L459–L477。
- `_source_file_record`：L480–L486。
- `_assert_inputs_unchanged`：L489–L501。
- `ingest_scoring_truth_exports`：L504–L655。
- `_validate_source_file_record`：L658–L681。
- `_replay_compilation`：L684–L721。
- `validate_scoring_truth_intake`：L724–L1021。
### src/rallymate_annotation/scoring_truth_refresh.py

[打开源码](../../src/rallymate_annotation/scoring_truth_refresh.py) · 734 行

直接引用的项目模块：`rallymate_annotation.scoring_action_readiness`、`rallymate_annotation.scoring_action_worklist`、`rallymate_annotation.scoring_evidence_plan`、`rallymate_annotation.truth_pack`、`rallymate_evaluation.pose_diagnostic_truth`、`rallymate_evaluation.scoring_truth`。

- `sha256_file`：L49–L54。
- `canonical_hash`：L57–L66。
- `_read_json`：L69–L73。
- `_write_json`：L76–L81。
- `_artifact`：L84–L85。
- `_preflight_truth_pack`：L88–L96。
- `_validated_compiled_truth_files`：L99–L138。
- `_snapshot_compiled_truth`：L141–L179。
- `_snapshot_pose_truth`：L182–L199。
- `_snapshot_worklist_inputs`：L202–L230。
- `_render_index`：L233–L237。
- `build_scoring_truth_refresh`：L240–L524。
- `validate_scoring_truth_refresh_manifest`：L527–L713。
- `load_latest_scoring_truth_refresh`：L716–L734。
### src/rallymate_annotation/truth_pack.py

[打开源码](../../src/rallymate_annotation/truth_pack.py) · 1,737 行

直接引用的项目模块：`rallymate_evaluation.ground_truth`、`rallymate_events.schemas`、`rallymate_scoring.calibration`、`rallymate_vision.pose.metadata`。

- `_read_jsonl`：L239–L244。
- `_write_jsonl`：L247–L251。
- `_write_csv`：L254–L258。
- `_read_csv`：L261–L265。
- `_select_evenly`：L268–L275。
- `_frame_metadata`：L278–L287。
- `_json_for_html`：L290–L296。
- `_write_workbench_assets`：L299–L305。
- `_review_html`：L308–L526。
- `build_truth_pack`：L529–L823。
- `_number`：L826–L828。
- `_required_boolean`：L831–L835。
- `_indicator_event_code`：L838–L842。
- `_semantic_value_from_row`：L845–L899。
- `_row_has_semantic_value`：L902–L916。
- `_has_exact_string_members`：L919–L925。
- `compile_truth_pack`：L928–L1737。

## src/rallymate_evaluation

### src/rallymate_evaluation/__init__.py

[打开源码](../../src/rallymate_evaluation/__init__.py) · 197 行

直接引用的项目模块：`rallymate_evaluation.default_pose_routing`、`rallymate_evaluation.event_bounded_pose_gaps`、`rallymate_evaluation.event_gap_feature_truth`、`rallymate_evaluation.event_gap_keypoint_truth`、`rallymate_evaluation.f2_error_budget_readiness`、`rallymate_evaluation.feature_errors`、`rallymate_evaluation.fixed_boundary_ab`、`rallymate_evaluation.fs09_phase_truth`、`rallymate_evaluation.ground_truth`、`rallymate_evaluation.multivideo_measurement_recovery`、`rallymate_evaluation.pose_crop_context`、`rallymate_evaluation.pose_diagnostic_clip_truth`、`rallymate_evaluation.pose_diagnostic_review`、`rallymate_evaluation.pose_diagnostic_truth`、`rallymate_evaluation.pose_observability_residual`、`rallymate_evaluation.pose_observability_router`、`rallymate_evaluation.residual_computability`、`rallymate_evaluation.small_roi_keypoint_truth`、`rallymate_evaluation.smoothing_coverage`。

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_evaluation/assets/pose-diagnostic-clip-adjudicate.js

[打开源码](../../src/rallymate_evaluation/assets/pose-diagnostic-clip-adjudicate.js) · 25 行

- `parseCsv`：L5。
- `loadFiles`：L6。
- `task`：L7。
- `taskSources`：L8。
- `sha256`：L9。
- `fetchEvidence`：L10。
- `targetNames`：L11。
- `keypointMap`：L12。
- `jointColor`：L13。
- `currentEvidenceFrame`：L14。
- `drawOverlay`：L15。
- `loadAndDraw`：L18。
- `seekTask`：L19。
- `render`：L20。
- `save`：L21。
- `csv`：L22。
- `download`：L23。
- `animationLoop`：L24。
### src/rallymate_evaluation/assets/pose-diagnostic-clip-review.js

[打开源码](../../src/rallymate_evaluation/assets/pose-diagnostic-clip-review.js) · 23 行

- `save`：L8。
- `coverageId`：L9。
- `setVideo`：L10。
- `setSegment`：L11。
- `currentFrame`：L12。
- `seekFrame`：L13。
- `coverageKey`：L14。
- `lockAnnotator`：L15。
- `markCoverage`：L16。
- `addPositive`：L17。
- `render`：L18。
- `csv`：L19。
- `download`：L20。
- `exportCoverage`：L21。
- `exportPositives`：L22。
### src/rallymate_evaluation/assets/pose-diagnostic-clip-truth.css

[打开源码](../../src/rallymate_evaluation/assets/pose-diagnostic-clip-truth.css) · 32 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_evaluation/default_pose_routing.py

[打开源码](../../src/rallymate_evaluation/default_pose_routing.py) · 369 行

- `DefaultPoseRoutingError`：L13–L14。
- `_indicator_counts`：L17–L50。
- `build_default_pose_routing_audit`：L53–L285。
- `validate_default_pose_routing_audit`：L288–L369。
### src/rallymate_evaluation/event_bounded_pose_gaps.py

[打开源码](../../src/rallymate_evaluation/event_bounded_pose_gaps.py) · 310 行

直接引用的项目模块：`rallymate_features.schemas`、`rallymate_features.validity`。

- `_validate_contract`：L20–L37。
- `_missing_runs`：L40–L49。
- `interpolate_event_bounded_pose_gaps`：L52–L243。
- `validate_event_bounded_pose_gap_report`：L246–L282。
- `validate_event_bounded_pose_gap_report_sources`：L285–L310。
### src/rallymate_evaluation/event_gap_feature_truth.py

[打开源码](../../src/rallymate_evaluation/event_gap_feature_truth.py) · 616 行

直接引用的项目模块：`rallymate_evaluation.event_bounded_pose_gaps`、`rallymate_evaluation.event_gap_keypoint_truth`、`rallymate_evaluation.ground_truth`、`rallymate_evaluation.small_roi_keypoint_truth`、`rallymate_features`、`rallymate_features.schemas`。

- `EventGapFeatureTruthError`：L36–L37。
- `_event`：L40–L48。
- `_snapshot`：L51–L61。
- `_same_interpolated_points`：L64–L84。
- `apply_adjudicated_gap_truth`：L87–L153。
- `_aggregate`：L156–L188。
- `_numeric_signed_error`：L191–L198。
- `_load_truth`：L201–L210。
- `evaluate_event_gap_feature_truth`：L213–L462。
- `validate_event_gap_feature_truth_report`：L465–L578。
- `validate_event_gap_feature_truth_report_sources`：L581–L605。
- `report_sha256`：L608–L616。
### src/rallymate_evaluation/event_gap_keypoint_truth.py

[打开源码](../../src/rallymate_evaluation/event_gap_keypoint_truth.py) · 882 行

直接引用的项目模块：`rallymate_evaluation.event_bounded_pose_gaps`、`rallymate_evaluation.ground_truth`、`rallymate_evaluation.small_roi_keypoint_truth`。

- `EventGapTruthError`：L39–L40。
- `_frame_index`：L43–L45。
- `_valid_bbox`：L48–L54。
- `_annotation_bbox`：L57–L105。
- `_source_records`：L108–L116。
- `validate_event_gap_truth_manifest`：L119–L147。
- `_candidate_points_from_m74`：L150–L197。
- `_verify_m74_task_replay`：L200–L264。
- `_verify_pack_sources`：L267–L287。
- `_workbench_html`：L290–L300。
- `build_event_gap_truth_pack`：L303–L509。
- `compile_event_gap_truth_pack`：L512–L682。
- `_verify_compilation`：L685–L709。
- `_aggregate`：L712–L748。
- `evaluate_event_gap_keypoints`：L751–L882。
### src/rallymate_evaluation/f2_error_budget_readiness.py

[打开源码](../../src/rallymate_evaluation/f2_error_budget_readiness.py) · 618 行

直接引用的项目模块：`rallymate_evaluation.scoring_truth`、`rallymate_evaluation.smoothing_coverage`、`rallymate_scoring.feasibility`、`rallymate_scoring.multivideo_coverage`。

- `_sha256`：L23–L28。
- `_canonical_sha256`：L31–L39。
- `_read_json`：L42–L46。
- `_artifact`：L49–L53。
- `_nonempty_jsonl_count`：L56–L57。
- `_truth_summary`：L60–L139。
- `_build_report`：L142–L516。
- `build_f2_error_budget_readiness`：L519–L538。
- `validate_f2_error_budget_readiness`：L541–L585。
- `validate_f2_error_budget_readiness_sources`：L588–L609。
### src/rallymate_evaluation/feature_errors.py

[打开源码](../../src/rallymate_evaluation/feature_errors.py) · 1,905 行

直接引用的项目模块：`rallymate_events.evaluation`、`rallymate_features.event_features`、`rallymate_features.fs01_fs02_features`、`rallymate_features.fs09_features`、`rallymate_features.kinematics`、`rallymate_features.schemas`、`rallymate_scoring.scoring_context`。

- `_interval`：L47–L55。
- `_numeric`：L58–L61。
- `_feature_definition`：L64–L70。
- `_feature_payload`：L73–L83。
- `_target_semantics_by_event`：L86–L124。
- `_target_reference`：L127–L155。
- `_is_series_payload`：L158–L161。
- `_series_map`：L164–L184。
- `_series_values`：L187–L192。
- `_vector_series_values`：L195–L219。
- `_aggregate_series`：L222–L267。
- `_series_sample_keys`：L270–L277。
- `_series_overlap`：L280–L288。
- `_series_grid_exact`：L291–L304。
- `_edge_drop`：L307–L311。
- `_braking_side`：L314–L323。
- `_peak_slowdown`：L326–L343。
- `_fs09_v2_braking_side`：L346–L360。
- `_series_timestamps`：L363–L373。
- `_aligned_scalar_series`：L376–L401。
- `_aligned_vector_series`：L404–L435。
- `_peak_sample`：L438–L455。
- `_raw_drive_peaks_from_flexion`：L458–L470。
- `_low_motion_duration_ms`：L473–L511。
- `compute_smoothing_counterfactual`：L514–L1392。
- `_aggregate_raw`：L1395–L1402。
- `_metric`：L1405–L1423。
- `evaluate_feature_errors`：L1426–L1905。
### src/rallymate_evaluation/fixed_boundary_ab.py

[打开源码](../../src/rallymate_evaluation/fixed_boundary_ab.py) · 1,001 行

直接引用的项目模块：`rallymate_features`、`rallymate_features.event_features`、`rallymate_scoring.feasibility`、`rallymate_tracking`。

- `_load_jsonl`：L47–L54。
- `_sha256`：L57–L62。
- `_canonical_sha256`：L65–L72。
- `_finite_scalar`：L75–L79。
- `_comparison_kind`：L82–L87。
- `_wrapped_angle_difference_deg`：L90–L93。
- `_validity_state`：L96–L103。
- `_semantic_signature`：L106–L115。
- `_boundary_provenance`：L118–L141。
- `_feature_payload`：L144–L159。
- `_compare_feature_pair`：L162–L222。
- `_paired_validity`：L225–L238。
- `_aggregate_feature_pairs`：L241–L338。
- `_indicator_validity_state`：L341–L350。
- `_validate_events`：L353–L371。
- `_registry_index`：L374–L397。
- `_model_source_for_report`：L400–L412。
- `compare_fixed_boundary_pose_features`：L415–L748。
- `_timeline_event_alignment`：L751–L760。
- `_observed_pose_formats`：L763–L771。
- `evaluate_fixed_boundary_pose_ab_files`：L774–L845。
- `_is_sha256`：L848–L853。
- `_walk_prohibited_scoring_keys`：L856–L885。
- `validate_fixed_boundary_ab_report`：L888–L993。
### src/rallymate_evaluation/fs09_phase_handoff.py

[打开源码](../../src/rallymate_evaluation/fs09_phase_handoff.py) · 898 行

直接引用的项目模块：`rallymate_evaluation.fs09_phase_truth`。

- `FS09PhaseBlindHandoffError`：L54–L55。
- `_canonical_bytes`：L58–L65。
- `_sha256_bytes`：L68–L69。
- `_require_sha256`：L72–L75。
- `_require_exact_keys`：L78–L81。
- `_reject_json_constant`：L84–L85。
- `_unique_json_object`：L88–L94。
- `_timestamp`：L97–L110。
- `_safe_relative_path`：L113–L121。
- `_content_root`：L124–L125。
- `_bundle_identity`：L128–L141。
- `_anonymous_operator_readme`：L144–L178。
- `_server_source_path`：L181–L182。
- `_parse_bootstrap`：L185–L199。
- `_public_review_html`：L202–L254。
- `_recover_canonical_review_html`：L257–L287。
- `_read_json_object`：L290–L304。
- `_plan_bindings`：L307–L336。
- `_source_snapshot`：L339–L438。
- `_is_link_like`：L441–L445。
- `_directory_tree`：L448–L469。
- `validate_fs09_phase_blind_handoff`：L472–L764。
- `_paths_overlap`：L767–L777。
- `build_fs09_phase_blind_handoff`：L780–L888。
### src/rallymate_evaluation/fs09_phase_truth.py

[打开源码](../../src/rallymate_evaluation/fs09_phase_truth.py) · 4,326 行

直接引用的项目模块：`rallymate_evaluation.event_bounded_pose_gaps`、`rallymate_evaluation.small_roi_keypoint_truth`、`rallymate_features`。

- `FS09PhaseTruthError`：L129–L130。
- `_utc_now`：L139–L140。
- `_parse_bool`：L143–L147。
- `_optional_int`：L150–L157。
- `_parse_confidence`：L160–L167。
- `_csv_snapshot`：L170–L191。
- `_json_snapshot`：L194–L202。
- `_jsonl_snapshot`：L205–L216。
- `_canonical_jsonl_bytes`：L219–L231。
- `_path_contains`：L234–L239。
- `_paths_overlap`：L242–L243。
- `_session_relative_path`：L246–L257。
- `_session_path`：L260–L265。
- `_session_source`：L268–L273。
- `_require_exact_keys`：L276–L279。
- `_strict_json_equal`：L282–L292。
- `_annotation_revision_sha256`：L295–L308。
- `_require_sha256`：L311–L318。
- `_identity_key`：L321–L322。
- `_require_safe_identity`：L325–L329。
- `_require_timestamp`：L332–L344。
- `_timestamp_value`：L347–L350。
- `_validate_handoff_export_row`：L353–L374。
- `validate_fs09_phase_analysis_plan`：L377–L751。
- `_validate_fs09_phase_analysis_plan_binding`：L754–L838。
- `_phase_items`：L841–L858。
- `_candidate_event`：L861–L869。
- `_snapshot`：L872–L882。
- `_sealed_candidate`：L885–L916。
- `_expected_contract`：L919–L977。
- `validate_fs09_phase_review_clip_manifest`：L980–L1088。
- `probe_fs09_phase_review_clip`：L1091–L1118。
- `validate_fs09_phase_truth_manifest`：L1121–L1280。
- `_source_records`：L1283–L1300。
- `_verify_pack_sources`：L1303–L1456。
- `_workbench_html`：L1459–L1470。
- `_operator_guide`：L1473–L1519。
- `build_fs09_phase_truth_pack`：L1522–L1738。
- `_parse_decision`：L1741–L1783。
- `validate_fs09_phase_truth_intake_manifest`：L1786–L2176。
- `_validate_fs09_phase_truth_exports`：L2179–L2369。
- `_compile_fs09_phase_truth_rows`：L2372–L2534。
- `_compile_fs09_phase_truth_pack`：L2537–L2718。
- `compile_fs09_phase_truth_pack`：L2721–L2725。
- `ingest_fs09_phase_truth_exports`：L2728–L3015。
- `_verify_compilation`：L3018–L3274。
- `_event_interval`：L3277–L3285。
- `_aggregate_numeric`：L3288–L3295。
- `_require_report_number_or_none`：L3298–L3306。
- `_validate_numeric_aggregate`：L3309–L3332。
- `evaluate_fs09_phase_truth`：L3335–L3650。
- `validate_fs09_phase_truth_report`：L3653–L4257。
- `validate_fs09_phase_truth_report_sources`：L4260–L4326。
### src/rallymate_evaluation/ground_truth.py

[打开源码](../../src/rallymate_evaluation/ground_truth.py) · 158 行

直接引用的项目模块：`rallymate_features.coordinates`、`rallymate_features.schemas`。

- `validate_keypoint_annotation`：L14–L62。
- `load_keypoint_ground_truth`：L65–L79。
- `apply_keypoint_corrections`：L82–L158。
### src/rallymate_evaluation/kinematic_continuity.py

[打开源码](../../src/rallymate_evaluation/kinematic_continuity.py) · 154 行

CPU-only, fixed-event diagnostic for the opt-in continuous derivative.

直接引用的项目模块：`rallymate_features.coordinates`、`rallymate_features.kinematics`、`rallymate_features.schemas`、`rallymate_features.smoothing`、`rallymate_features.temporal`。

- `_finite`：L31–L32。
- `_peak`：L35–L38。
- `_compare`：L41–L67。
- `evaluate_kinematic_continuity`：L70–L119。
- `evaluate_kinematic_continuity_files`：L122–L141。
- `main`：L144–L150。
### src/rallymate_evaluation/m96_pose_pilot.py

[打开源码](../../src/rallymate_evaluation/m96_pose_pilot.py) · 1,996 行

- `M96PosePilotError`：L97–L98。
- `_canonical_bytes`：L101–L108。
- `_pretty_bytes`：L111–L121。
- `_sha256`：L124–L125。
- `_now_utc`：L128–L129。
- `_require_utc`：L132–L139。
- `_require_sha`：L142–L145。
- `_require_role_id`：L148–L151。
- `_require_record_id`：L154–L157。
- `_identity_key`：L160–L161。
- `_safe_relative`：L164–L172。
- `_snapshot`：L175–L187。
- `_parse_json`：L190–L212。
- `_bound_workspace_file`：L215–L237。
- `_load_development_sources`：L240–L318。
- `_repo_root`：L321–L322。
- `_asset`：L325–L329。
- `_artifact_records`：L332–L336。
- `_content_root`：L339–L340。
- `_write_new`：L343–L349。
- `_atomic_publish`：L352–L383。
- `_read_joint_schema`：L386–L422。
- `_selected_frames`：L425–L494。
- `_contract`：L497–L575。
- `_tasks_jsonl`：L578–L579。
- `_governance_template`：L582–L609。
- `_field_contract`：L612–L642。
- `_protocol`：L645–L688。
- `_html`：L691–L739。
- `_annotation_role`：L742–L825。
- `build_m96_pose_pilot`：L828–L935。
- `_validate_artifact_tree`：L938–L971。
- `_read_tasks`：L974–L982。
- `_extract_bootstrap`：L985–L997。
- `_validate_role_manifest_shape`：L1000–L1051。
- `_validate_role_directory`：L1054–L1096。
- `_validate_governance`：L1099–L1122。
- `validate_m96_pose_pilot`：L1125–L1266。
- `_parse_csv`：L1269–L1288。
- `_validate_coordinate`：L1291–L1300。
- `_validate_point`：L1303–L1317。
- `_annotation_submission_bytes`：L1320–L1391。
- `validate_m96_annotation_submission`：L1394–L1408。
- `_semantic_point`：L1411–L1418。
- `_adjudication_role`：L1421–L1486。
- `build_m96_adjudication_bundle`：L1489–L1664。
- `_source_artifact_sha`：L1667–L1674。
- `validate_m96_adjudication_bundle`：L1677–L1866。
- `_adjudication_submission_bytes`：L1869–L1933。
- `validate_m96_adjudication_submission`：L1936–L1951。
- `role_service_contract`：L1954–L1996。
### src/rallymate_evaluation/multivideo_measurement_recovery.py

[打开源码](../../src/rallymate_evaluation/multivideo_measurement_recovery.py) · 636 行

直接引用的项目模块：`rallymate_evaluation.small_roi_recovery`。

- `_sha256`：L22–L27。
- `_source`：L30–L32。
- `_load_json`：L35–L39。
- `_load_jsonl`：L42–L50。
- `_canonical`：L53–L60。
- `_validate_bound_source`：L63–L68。
- `_validate_gap_audit`：L71–L173。
- `_validate_experiment`：L176–L251。
- `build_multivideo_measurement_recovery_report`：L254–L526。
- `validate_multivideo_measurement_recovery_report`：L529–L607。
- `render_multivideo_measurement_recovery_html`：L610–L627。
### src/rallymate_evaluation/pose_crop_context.py

[打开源码](../../src/rallymate_evaluation/pose_crop_context.py) · 122 行

- `_canonical`：L12–L19。
- `_selected_pose`：L22–L32。
- `classify_routed_pose_sources`：L35–L119。
### src/rallymate_evaluation/pose_diagnostic_clip_truth.py

[打开源码](../../src/rallymate_evaluation/pose_diagnostic_clip_truth.py) · 1,005 行

直接引用的项目模块：`rallymate_evaluation.fs09_phase_truth`、`rallymate_evaluation.pose_diagnostic_review`。

- `PoseDiagnosticClipTruthError`：L61–L62。
- `canonical_sha256`：L65–L73。
- `file_sha256`：L76–L81。
- `source_binding`：L84–L86。
- `_json`：L89–L93。
- `_jsonl`：L96–L109。
- `_parse_iso8601`：L112–L118。
- `_read_csv`：L121–L126。
- `_write_empty_csv`：L129–L131。
- `_median_frame_rate`：L134–L147。
- `_merge_task_windows`：L150–L176。
- `_timeline_frame_slice`：L179–L197。
- `build_video_clip_pose_evidence`：L200–L351。
- `build_clip_plan`：L354–L537。
- `validate_clip_plan`：L540–L600。
- `_split_ids`：L603–L604。
- `_positive_matches_task`：L607–L617。
- `compile_clip_truth`：L620–L853。
- `validate_clip_truth_manifest_sources`：L856–L970。
- `validate_empty_clip_truth_report`：L973–L994。
- `initialize_empty_annotation_csvs`：L997–L1001。
- `json_dump`：L1004–L1005。
### src/rallymate_evaluation/pose_diagnostic_review.py

[打开源码](../../src/rallymate_evaluation/pose_diagnostic_review.py) · 845 行

直接引用的项目模块：`rallymate_features.event_features`、`rallymate_vision.validation`。

- `PoseDiagnosticReviewError`：L38–L39。
- `_load_jsonl`：L42–L60。
- `_sha256`：L63–L68。
- `_canonical_hash`：L71–L79。
- `_required_joints`：L82–L97。
- `_frame_record_index`：L100–L119。
- `_timeline_index`：L122–L136。
- `_task_key`：L139–L144。
- `_add_task_candidate`：L147–L179。
- `_joint_observations`：L182–L244。
- `build_pose_diagnostic_review_queue`：L247–L537。
- `validate_pose_diagnostic_review_queue`：L540–L610。
- `validate_pose_diagnostic_review_queue_sources`：L613–L659。
- `build_pose_diagnostic_review_queue_from_run`：L662–L717。
- `render_pose_diagnostic_review_html`：L720–L764。
- `write_pose_diagnostic_review_artifacts`：L767–L782。
- `decisions_csv_header`：L785–L795。
- `validate_decisions_csv`：L798–L845。
### src/rallymate_evaluation/pose_diagnostic_truth.py

[打开源码](../../src/rallymate_evaluation/pose_diagnostic_truth.py) · 1,185 行

直接引用的项目模块：`rallymate_evaluation.pose_diagnostic_review`。

- `PoseDiagnosticTruthError`：L29–L30。
- `_sha256`：L33–L38。
- `_canonical_hash`：L41–L49。
- `_load_jsonl`：L52–L70。
- `_utc_now`：L73–L74。
- `coverage_csv_header`：L77–L91。
- `positives_csv_header`：L94–L110。
- `_timeline_from_queue`：L113–L143。
- `_blank_csv`：L146–L151。
- `build_pose_diagnostic_truth_pack_manifest`：L154–L242。
- `validate_pose_diagnostic_truth_pack_manifest`：L245–L276。
- `validate_pose_diagnostic_truth_pack_sources`：L279–L322。
- `_review_metadata`：L325–L347。
- `_read_csv`：L350–L354。
- `_parse_int`：L357–L368。
- `_parse_coverage`：L371–L468。
- `_truth_identity`：L471–L495。
- `_parse_optional_track`：L498–L501。
- `_parse_positives`：L504–L608。
- `_candidate_identities`：L611–L630。
- `_safe_ratio`：L633–L636。
- `evaluate_pose_diagnostic_truth`：L639–L858。
- `validate_pose_diagnostic_evaluation`：L861–L1056。
- `render_pose_diagnostic_truth_html`：L1059–L1107。
- `write_blank_pose_diagnostic_truth_pack`：L1110–L1185。
### src/rallymate_evaluation/pose_observability_residual.py

[打开源码](../../src/rallymate_evaluation/pose_observability_residual.py) · 474 行

直接引用的项目模块：`rallymate_evaluation.fixed_boundary_ab`、`rallymate_evaluation.pose_observability_router`、`rallymate_features.event_features`。

- `_sha256`：L20–L25。
- `_source`：L28–L29。
- `_read_jsonl`：L32–L40。
- `_bound_path`：L43–L48。
- `_counter_rows`：L51–L52。
- `operational_transitions_with_preserved_source_gate`：L55–L128。
- `build_pose_observability_residual_audit`：L131–L395。
- `validate_pose_observability_residual_audit`：L398–L448。
- `validate_pose_observability_residual_audit_sources`：L451–L465。
### src/rallymate_evaluation/pose_observability_router.py

[打开源码](../../src/rallymate_evaluation/pose_observability_router.py) · 446 行

直接引用的项目模块：`rallymate_features.event_features`、`rallymate_features.validity`。

- `pose_required_joints_from_registry`：L18–L53。
- `_canonical`：L56–L63。
- `_selected_pose`：L66–L74。
- `_point_valid`：L77–L88。
- `_valid_joint_set`：L91–L105。
- `select_required_joint_superset_frames`：L108–L235。
- `select_required_joint_dominating_candidates`：L238–L370。
- `validate_pose_observability_router_report`：L373–L435。
### src/rallymate_evaluation/pose_policy_composition.py

[打开源码](../../src/rallymate_evaluation/pose_policy_composition.py) · 505 行

直接引用的项目模块：`rallymate_features.validity`。

- `_pose_for_track`：L12–L20。
- `_canonical`：L23–L30。
- `_valid_keypoint`：L33–L44。
- `_keypoints_by_name`：L47–L59。
- `add_missing_keypoints_from_same_topology_candidate_rows`：L62–L206。
- `add_missing_keypoints_from_mapped_topology_candidate_rows`：L209–L386。
- `compose_disjoint_pose_policy_rows`：L389–L498。
### src/rallymate_evaluation/pose_same_frame_diagnostic.py

[打开源码](../../src/rallymate_evaluation/pose_same_frame_diagnostic.py) · 1,380 行

- `PoseSameFrameDiagnosticError`：L22–L23。
- `sha256_file`：L26–L31。
- `canonical_sha256`：L34–L42。
- `_inside`：L45–L54。
- `_bound_file`：L57–L79。
- `_require_false_fields`：L82–L89。
- `load_diagnostic_registry`：L92–L338。
- `_player_key`：L341–L349。
- `_evenly_spaced`：L352–L362。
- `select_frozen_same_frame_samples`：L365–L498。
- `_percentile`：L501–L511。
- `_distribution`：L514–L525。
- `_rate`：L528–L529。
- `_threshold_key`：L532–L533。
- `_pose_points`：L536–L548。
- `_roi_diagonal`：L551–L558。
- `_coverage_metrics`：L561–L623。
- `_temporal_continuity`：L626–L675。
- `summarize_model_observations`：L678–L700。
- `summarize_repeatability`：L703–L757。
- `compare_model_observations`：L760–L883。
- `_comparative_orderings`：L886–L936。
- `validate_diagnostic_report`：L939–L1004。
- `_decode_samples`：L1007–L1041。
- `_run_model`：L1044–L1184。
- `render_diagnostic_markdown`：L1187–L1230。
- `run_same_frame_diagnostic`：L1233–L1380。
### src/rallymate_evaluation/pose_shadow_smoke.py

[打开源码](../../src/rallymate_evaluation/pose_shadow_smoke.py) · 507 行

- `PoseShadowSmokeError`：L19–L20。
- `sha256_file`：L23–L28。
- `_inside`：L31–L40。
- `_bound_file`：L43–L65。
- `load_shadow_registry`：L68–L202。
- `_read_frozen_samples`：L205–L269。
- `_percentile`：L272–L282。
- `run_shadow_smoke`：L285–L507。
### src/rallymate_evaluation/pose_x_operational.py

[打开源码](../../src/rallymate_evaluation/pose_x_operational.py) · 1,138 行

- `PoseXOperationalError`：L21–L22。
- `sha256_file`：L25–L30。
- `_inside`：L33–L42。
- `_bound_file`：L45–L67。
- `source_binding`：L70–L78。
- `verify_source_binding`：L81–L91。
- `_json`：L94–L101。
- `_jsonl`：L104–L121。
- `_require_false`：L124–L129。
- `_summary_versions`：L132–L137。
- `load_x_operational_protocol`：L140–L401。
- `comparable_frame_identity`：L404–L412。
- `assert_m71_comparable_scope`：L415–L444。
- `identity_set`：L447–L451。
- `_identity_rows`：L454–L458。
- `compare_recovery_sets`：L461–L484。
- `_validate_transition_impact`：L487–L503。
- `_validate_reference_comparison`：L506–L544。
- `expected_per_video_status`：L547–L567。
- `validate_per_video_report`：L570–L670。
- `_sum`：L673–L680。
- `_aggregate_reference_comparison`：L683–L712。
- `build_multivideo_summary`：L715–L978。
- `validate_multivideo_summary`：L981–L1114。
- `latency_distribution`：L1117–L1138。
### src/rallymate_evaluation/residual_computability.py

[打开源码](../../src/rallymate_evaluation/residual_computability.py) · 435 行

直接引用的项目模块：`rallymate_features.event_features`。

- `ResidualComputabilityError`：L16–L17。
- `_frame_source_index`：L20–L22。
- `_selected_detection`：L25–L36。
- `_bbox_is_clipped`：L39–L48。
- `_classification`：L51–L80。
- `build_residual_computability_audit`：L83–L384。
- `validate_residual_computability_audit`：L387–L435。
### src/rallymate_evaluation/scoring_truth.py

[打开源码](../../src/rallymate_evaluation/scoring_truth.py) · 276 行

直接引用的项目模块：`rallymate_evaluation.feature_errors`、`rallymate_evaluation.ground_truth`、`rallymate_events`、`rallymate_events.schemas`、`rallymate_features`、`rallymate_scoring.feasibility`、`rallymate_scoring.scoring_context`。

- `_reject_duplicate_keys`：L24–L30。
- `_parse_json`：L33–L41。
- `_parse_jsonl`：L44–L62。
- `_freeze_input`：L65–L73。
- `build_scoring_truth_evaluation`：L76–L276。
### src/rallymate_evaluation/small_roi_keypoint_truth.py

[打开源码](../../src/rallymate_evaluation/small_roi_keypoint_truth.py) · 908 行

直接引用的项目模块：`rallymate_evaluation.ground_truth`。

- `SmallRoiTruthError`：L63–L64。
- `sha256_file`：L67–L72。
- `_canonical_sha256`：L75–L83。
- `_read_jsonl`：L86–L99。
- `_write_json`：L102–L106。
- `_write_jsonl`：L109–L117。
- `_write_csv`：L120–L125。
- `_read_csv`：L128–L135。
- `_source`：L138–L139。
- `_frame_index`：L142–L144。
- `_pose_for_track`：L147–L155。
- `_detection_for_track`：L158–L167。
- `_valid_keypoint`：L170–L180。
- `_quantile_edges`：L183–L186。
- `_stratum`：L189–L196。
- `validate_pack_manifest`：L199–L229。
- `build_small_roi_truth_pack`：L232–L446。
- `_workbench_html`：L449–L459。
- `_parse_bool`：L462–L466。
- `_parse_point`：L469–L485。
- `_verify_pack_sources`：L488–L500。
- `_verify_compilation_lineage`：L503–L531。
- `compile_small_roi_truth_pack`：L534–L720。
- `_aggregate_errors`：L723–L768。
- `evaluate_small_roi_keypoints`：L771–L908。
### src/rallymate_evaluation/small_roi_recovery.py

[打开源码](../../src/rallymate_evaluation/small_roi_recovery.py) · 360 行

直接引用的项目模块：`rallymate_vision.utils`。

- `_area`：L9–L13。
- `select_small_roi_targets`：L16–L115。
- `select_roi_margin_targets`：L118–L242。
- `feature_vector_transitions`：L245–L270。
- `operational_measurement_transitions`：L273–L352。
### src/rallymate_evaluation/smoothing_coverage.py

[打开源码](../../src/rallymate_evaluation/smoothing_coverage.py) · 684 行

直接引用的项目模块：`rallymate_evaluation.feature_errors`、`rallymate_features.event_features`、`rallymate_features.schemas`、`rallymate_scoring.scoring_context`。

- `_sha256`：L30–L35。
- `_read_json`：L38–L42。
- `_read_jsonl`：L45–L58。
- `_numeric`：L61–L62。
- `_counterfactual_difference`：L65–L73。
- `_feature_result`：L76–L93。
- `_registry_requirements`：L96–L139。
- `build_smoothing_counterfactual_coverage`：L142–L447。
- `validate_smoothing_counterfactual_coverage`：L450–L639。
- `validate_smoothing_counterfactual_coverage_sources`：L642–L674。

## src/rallymate_events

### src/rallymate_events/__init__.py

[打开源码](../../src/rallymate_events/__init__.py) · 25 行

直接引用的项目模块：`rallymate_events.disagreement`、`rallymate_events.evaluation`、`rallymate_events.rules`、`rallymate_events.schemas`。

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_events/associations.py

[打开源码](../../src/rallymate_events/associations.py) · 78 行

Index observed successor events without inventing missing event classes.

- `finite_time`：L11–L18。
- `event_identity`：L21–L26。
- `SuccessorEventIndex`：L29–L78；方法：`__init__` L32、`next_event` L49。
### src/rallymate_events/disagreement.py

[打开源码](../../src/rallymate_events/disagreement.py) · 402 行

直接引用的项目模块：`rallymate_events.evaluation`。

- `_MatchState`：L21–L24。
- `_canonical_event_sha256`：L27–L34。
- `_validate_events`：L37–L55。
- `_normalise_thresholds`：L58–L64。
- `_state_is_better`：L67–L75。
- `_ordered_optimal_matches`：L78–L109。
- `_mean`：L112–L113。
- `_event_stub`：L116–L123。
- `_group_key`：L126–L127。
- `_group_events`：L130–L138。
- `_rate`：L141–L142。
- `_aggregate_counts`：L145–L156。
- `_compare_at_threshold`：L159–L268。
- `compare_event_candidates`：L271–L350。
- `validate_event_disagreement_report`：L353–L402。
### src/rallymate_events/evaluation.py

[打开源码](../../src/rallymate_events/evaluation.py) · 524 行

- `_overlap_and_union`：L18–L29。
- `segment_iou`：L32–L34。
- `_resolved_video_id`：L37–L48。
- `_validate_events`：L51–L81。
- `_group_key`：L84–L92。
- `_group_events`：L95–L109。
- `_ResidualEdge`：L113–L117。
- `_add_residual_edge`：L120–L141。
- `_optimal_group_matches`：L144–L243。
- `_matching_metadata`：L246–L264。
- `_ground_truth_required_result`：L267–L302。
- `evaluate_events`：L305–L524。
### src/rallymate_events/rules.py

[打开源码](../../src/rallymate_events/rules.py) · 1,441 行

直接引用的项目模块：`rallymate_events.schemas`、`rallymate_features.coordinates`、`rallymate_features.kinematics`、`rallymate_features.schemas`、`rallymate_features.smoothing`。

- `_runs`：L79–L89。
- `_close_short_gaps`：L92–L103。
- `_motion_bout_diagnostics`：L106–L183。
- `_event_id`：L186–L190。
- `_confidence`：L193–L202。
- `_event_kinematic_coverage`：L205–L238。
- `_event_motion_reference`：L241–L322。
- `_event_motion_reference_provenance`：L325–L357。
- `_event_quality_flags`：L360–L367。
- `_finite_row_max`：L370–L381。
- `_adaptive_signal_threshold`：L384–L416。
- `_activity_onset_peak_and_slowdown`：L419–L453。
- `_right_censored_peak_only_phase`：L456–L494。
- `_low_sample_peak_only_phase`：L497–L525。
- `_right_boundary_speed_peak_confirmed_slowdown_phase`：L528–L628。
- `_adaptive_peak_candidate`：L631–L644。
- `_missing_phase_flags`：L647–L652。
- `_phase_signals`：L655–L742。
- `_make_event`：L745–L800。
- `_local_activity_thresholds`：L803–L830。
- `_offset_phase_indexes`：L833–L845。
- `detect_pose_events`：L848–L1441。
### src/rallymate_events/schemas.py

[打开源码](../../src/rallymate_events/schemas.py) · 260 行

- `_validate_event_local_kinematic_coverage`：L26–L105。
- `_validate_event_motion_reference`：L108–L179。
- `validate_event_record`：L182–L245。
- `load_event_annotations`：L248–L260。

## src/rallymate_features

### src/rallymate_features/__init__.py

[打开源码](../../src/rallymate_features/__init__.py) · 19 行

直接引用的项目模块：`rallymate_features.coordinates`、`rallymate_features.event_features`、`rallymate_features.schemas`。

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_features/coordinates.py

[打开源码](../../src/rallymate_features/coordinates.py) · 293 行

直接引用的项目模块：`rallymate_features.schemas`、`rallymate_features.validity`。

- `validated_camera_transform`：L16–L42。
- `_finite_number`：L45–L46。
- `_frame_dimensions`：L49–L53。
- `_isotropic_point`：L56–L82。
- `pose_sequence_from_records`：L85–L218。
- `point_series`：L221–L235。
- `center_series`：L238–L248。
- `shoulder_center`：L251–L252。
- `hip_center`：L255–L256。
- `body_center`：L259–L265。
- `body_scale`：L268–L293。
### src/rallymate_features/event_features.py

[打开源码](../../src/rallymate_features/event_features.py) · 1,248 行

直接引用的项目模块：`rallymate_features.coordinates`、`rallymate_features.fs01_fs02_features`、`rallymate_features.fs09_features`、`rallymate_features.geometry`、`rallymate_features.kinematics`、`rallymate_features.schemas`、`rallymate_features.smoothing`、`rallymate_features.temporal`。

- `_event_indexes`：L348–L352。
- `_event_scale`：L355–L365。
- `_series_payload`：L368–L394。
- `_feature_confidence`：L397–L413。
- `statistics_mean`：L416–L417。
- `_representative_frames`：L420–L448。
- `_aggregate`：L451–L472。
- `_hip_center_y`：L475–L483。
- `_body_speed`：L486–L491。
- `_knee_flexion`：L494–L498。
- `_torso_lean`：L501–L507。
- `_stance_width`：L510–L515。
- `_hip_support`：L518–L532。
- `_angular_velocity`：L535–L545。
- `_ankle_shank_foot_angle`：L548–L556。
- `_raw_series`：L559–L586。
- `_cached_series`：L589–L602。
- `clear_feature_cache`：L605–L618。
- `stability_duration_from_series`：L621–L686。
- `_stability_feature`：L689–L788。
- `_fs09_event_summary_feature`：L791–L882。
- `_fs01_fs02_event_summary_feature`：L885–L1034。
- `_compute_feature`：L1037–L1134。
- `_camera_window_for_event`：L1137–L1212。
- `_camera_blocked_feature`：L1215–L1229。
- `compute_feature`：L1232–L1236。
- `compute_event_features`：L1239–L1248。
### src/rallymate_features/fs01_fs02_features.py

[打开源码](../../src/rallymate_features/fs01_fs02_features.py) · 3,058 行

直接引用的项目模块：`rallymate_features.coordinates`、`rallymate_features.geometry`、`rallymate_features.kinematics`、`rallymate_features.schemas`、`rallymate_features.smoothing`、`rallymate_features.temporal`。

- `_definition`：L58–L73。
- `FootReference`：L228–L241。
- `FS01FS02EventSummary`：L245–L254。
- `_finite_positive_scale`：L257–L261。
- `_mask_rows`：L264–L267。
- `foot_reference_series`：L270–L371。
- `_knee_flexion`：L374–L381。
- `_event_edge_displacement`：L384–L401。
- `launch_direction_from_hip_motion`：L404–L442。
- `hip_acceleration_along_launch_direction_from_series`：L445–L509。
- `launch_foot_event_kinematics_from_series`：L512–L701。
- `first_step_phase_kinematics_from_series`：L704–L957。
- `_longest_true_run`：L960–L968。
- `_peak_index`：L971–L976。
- `_event_local_rise`：L979–L997。
- `_validated_event_vector_series`：L1000–L1053。
- `_bilateral_foot_rise_components`：L1056–L1109。
- `bilateral_foot_rise_from_position_series`：L1112–L1172。
- `bilateral_foot_rise_synchrony_from_velocity_series`：L1175–L1233。
- `bilateral_foot_rise_duration_from_position_series`：L1236–L1380。
- `hip_center_vertical_velocity_from_position_series`：L1383–L1420。
- `_vertical_slowdown_index`：L1423–L1444。
- `_bilateral_vertical_slowdown_components`：L1447–L1521。
- `bilateral_foot_vertical_slowdown_from_position_series`：L1524–L1573。
- `post_slowdown_stance_width_from_position_series`：L1576–L1617。
- `hip_center_lateral_variability_from_position_series`：L1620–L1767。
- `_speed_slowdown_index`：L1770–L1793。
- `_event_series`：L1796–L1918。
- `clear_fs01_fs02_feature_cache`：L1921–L1929。
- `_event_foot_reference_provenance`：L1932–L2039。
- `_summary`：L2042–L2070。
- `_side_selection`：L2073–L2085。
- `drive_measurement_candidate_side`：L2088–L2116。
- `drive_side_from_knee_extension_peaks`：L2119–L2130。
- `_event_phase_anchor_index`：L2133–L2213。
- `summarize_fs01_fs02_event_feature`：L2216–L3058。
### src/rallymate_features/fs09_features.py

[打开源码](../../src/rallymate_features/fs09_features.py) · 1,007 行

直接引用的项目模块：`rallymate_features.coordinates`、`rallymate_features.geometry`、`rallymate_features.kinematics`、`rallymate_features.schemas`、`rallymate_features.smoothing`、`rallymate_features.temporal`。

- `FS09EventSummary`：L89–L103。
- `_scale_value`：L106–L110。
- `_mask_rows`：L113–L116。
- `hip_center_velocity_body_s`：L119–L133。
- `hip_center_speed_body_s`：L136–L143。
- `hip_center_motion_direction_deg`：L146–L156。
- `hip_center_relative_to_ankle_midpoint_body`：L159–L171。
- `hip_center_speed_trend_body_s2`：L174–L180。
- `ankle_speed_body_s`：L183–L195。
- `knee_flexion_deg`：L198–L206。
- `knee_flexion_velocity_deg_s`：L209–L212。
- `hip_height_above_ankle_midpoint_body`：L215–L221。
- `torso_lean_deg`：L224–L230。
- `shoulder_hip_angular_velocity_deg_s`：L233–L244。
- `raw_fs09_series`：L247–L278。
- `_finite_event_indexes`：L281–L283。
- `event_edge_change`：L286–L315。
- `event_variability`：L318–L333。
- `braking_side_from_speed_drops`：L336–L387。
- `peak_slowdown_index`：L390–L397。
- `_positive_peak_index`：L400–L413。
- `_longest_true_run`：L416–L422。
- `double_support_low_motion_proxy`：L425–L460。
- `_validated_phase_timing_series`：L463–L516。
- `braking_ankle_slowdown_to_hip_deceleration_from_series`：L519–L608。
- `hip_deceleration_to_double_support_proxy_from_series`：L611–L669。
- `_base_event_series`：L672–L721。
- `clear_fs09_feature_cache`：L724–L732。
- `summarize_fs09_event_feature`：L735–L1007。
### src/rallymate_features/geometry.py

[打开源码](../../src/rallymate_features/geometry.py) · 44 行

- `angle_three_points_deg`：L6–L21。
- `orientation_deg`：L24–L28。
- `wrap_axis_angle_deg`：L31–L33。
- `unwrap_axis_angle_deg`：L36–L44。
### src/rallymate_features/kinematics.py

[打开源码](../../src/rallymate_features/kinematics.py) · 92 行

直接引用的项目模块：`rallymate_features.temporal`。

- `contiguous_irregular_derivative`：L11–L52。
- `irregular_derivative`：L55–L83。
- `speed`：L86–L88。
- `scalar_acceleration`：L91–L92。
### src/rallymate_features/schemas.py

[打开源码](../../src/rallymate_features/schemas.py) · 94 行

- `PoseSequence`：L10–L49；方法：`__post_init__` L25。
- `EventInterval`：L53–L63；方法：`__post_init__` L61。
- `FeatureResult`：L67–L94；方法：`to_dict` L80。
### src/rallymate_features/smoothing.py

[打开源码](../../src/rallymate_features/smoothing.py) · 90 行

- `interpolate_short_gaps`：L6–L33。
- `time_weighted_smooth`：L36–L77。
- `smooth_series`：L80–L90。
### src/rallymate_features/source_alignment.py

[打开源码](../../src/rallymate_features/source_alignment.py) · 531 行

Source-clause measurements with explicit image-plane operational definitions.

直接引用的项目模块：`rallymate_events.associations`、`rallymate_features.coordinates`、`rallymate_features.geometry`、`rallymate_features.schemas`、`rallymate_features.validity`、`rallymate_scoring.quality_policy`。

- `_mapping`：L40–L41。
- `SourceAlignedContext`：L45–L126；方法：`indexes` L58、`coverage` L62、`window_reason` L84、`subject_window_reason` L102。
- `prepare_source_aligned_context`：L132–L169。
- `clear_source_aligned_feature_cache`：L172–L175。
- `_event_quality_gate`：L178–L195。
- `_identity_reason`：L198–L218。
- `_phase`：L221–L230。
- `_row`：L233–L257。
- `_scale`：L260–L263。
- `_speed_summary`：L266–L289。
- `locate_source_preload`：L292–L353。
- `_preload`：L356–L393。
- `_post_slowdown_ratio`：L396–L414。
- `_transition`：L417–L481。
- `compute_source_aligned_features`：L484–L531。
### src/rallymate_features/temporal.py

[打开源码](../../src/rallymate_features/temporal.py) · 72 行

- `longest_contiguous_duration`：L12–L72。
### src/rallymate_features/validity.py

[打开源码](../../src/rallymate_features/validity.py) · 60 行

- `valid_point_mask`：L9–L21。
- `combined_valid_mask`：L24–L33。
- `valid_fraction`：L36–L38。
- `longest_false_run`：L41–L60。

## src/rallymate_pose_evaluation

### src/rallymate_pose_evaluation/__init__.py

[打开源码](../../src/rallymate_pose_evaluation/__init__.py) · 5 行

Pose backend evaluation and reproducible baseline utilities.

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_pose_evaluation/baseline.py

[打开源码](../../src/rallymate_pose_evaluation/baseline.py) · 792 行

- `sha256_file`：L50–L55。
- `_safe_percentile`：L58–L63。
- `_distribution`：L66–L94。
- `_point`：L97–L102。
- `_center`：L105–L108。
- `_angle_3pt`：L111–L118。
- `_body_scale`：L121–L138。
- `_wrap_180`：L141–L142。
- `_max_missing_run`：L145–L154。
- `_load_pose_tracks`：L157–L216。
- `_joint_temporal_diagnostics`：L219–L296。
- `_left_right_swap_diagnostics`：L299–L344。
- `_feature_diagnostics`：L347–L483。
- `analyze_pose_artifact`：L486–L625。
- `_benchmark_samples`：L628–L656。
- `benchmark_yolo_pose`：L659–L792。

## src/rallymate_scoring

### src/rallymate_scoring/__init__.py

[打开源码](../../src/rallymate_scoring/__init__.py) · 71 行

Model-granularity audit for the GS/FS scoring specification.

直接引用的项目模块：`rallymate_scoring.calculation_readiness`、`rallymate_scoring.cycle_measurement`、`rallymate_scoring.diagnostic_policy_review`、`rallymate_scoring.indicator_feature_qualification`、`rallymate_scoring.measurement_portfolio`、`rallymate_scoring.scoring_context`。

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_scoring/batch.py

[打开源码](../../src/rallymate_scoring/batch.py) · 318 行

直接引用的项目模块：`rallymate_events.schemas`、`rallymate_scoring.indicator_requirements`、`rallymate_scoring.quality_policy`、`rallymate_scoring.runtime_profile_binding`、`rallymate_scoring.scoring`、`rallymate_scoring.scoring_context`。

- `_required`：L25–L28。
- `_index_authoritative_records`：L31–L51。
- `_authoritative_production_quality_flags`：L54–L126。
- `score_indicator_records`：L129–L318。
### src/rallymate_scoring/blocker_taxonomy.py

[打开源码](../../src/rallymate_scoring/blocker_taxonomy.py) · 235 行

- `blocker_spec`：L100–L118。
- `typed_reason_for_flag`：L121–L123。
- `truth_requirement_for_flag`：L126–L141。
- `blocker_group_for_flag`：L144–L150。
- `typed_reason_supports_flag`：L153–L154。
- `known_typed_reason_codes`：L157–L161。
- `exact_typed_reason_support_flags`：L164–L168。
- `scoring_block_details`：L171–L212。
- `taxonomy_document`：L215–L235。
### src/rallymate_scoring/blocker_taxonomy_audit.py

[打开源码](../../src/rallymate_scoring/blocker_taxonomy_audit.py) · 417 行

- `BlockerTaxonomyAuditError`：L24–L25。
- `_sha256`：L28–L33。
- `_json`：L36–L40。
- `_jsonl`：L43–L56。
- `_row_index`：L59–L68。
- `_without_confidence`：L71–L80。
- `_confidence_leaves`：L83–L99。
- `_compare_indexed_rows`：L102–L145。
- `_source_binding`：L148–L149。
- `build_blocker_taxonomy_replay_audit`：L152–L359。
- `validate_blocker_taxonomy_replay_audit`：L362–L406。
- `write_blocker_taxonomy_replay_audit`：L409–L417。
### src/rallymate_scoring/calculation_readiness.py

[打开源码](../../src/rallymate_scoring/calculation_readiness.py) · 418 行

直接引用的项目模块：`rallymate_scoring.feasibility`。

- `CalculationReadinessError`：L16–L17。
- `_remediation`：L20–L59。
- `build_indicator_calculation_readiness`：L62–L289。
- `validate_indicator_calculation_readiness`：L292–L418。
### src/rallymate_scoring/calibration.py

[打开源码](../../src/rallymate_scoring/calibration.py) · 393 行

- `validate_coach_label`：L21–L50。
- `load_coach_labels`：L53–L65。
- `_quadratic_weighted_kappa`：L68–L86。
- `_kendall_tau`：L89–L103。
- `compute_annotator_agreement`：L106–L181。
- `_validate_independent_test_evidence`：L184–L247。
- `_require_non_empty_strings`：L250–L253。
- `_finite_number`：L256–L261。
- `calibration_scoring_readiness`：L264–L284。
- `validate_threshold_calibration`：L287–L333。
- `validate_ordinal_model`：L336–L393。
### src/rallymate_scoring/calibration_dataset.py

[打开源码](../../src/rallymate_scoring/calibration_dataset.py) · 1,983 行

直接引用的项目模块：`rallymate_events.schemas`、`rallymate_scoring.calibration`、`rallymate_scoring.indicator_feature_qualification`、`rallymate_scoring.indicator_requirements`、`rallymate_scoring.scoring_context`、`rallymate_scoring.scoring_truth_calibration_authorization`。

- `_FrozenInput`：L53–L57。
- `_reject_duplicate_keys`：L60–L66。
- `_parse_json_bytes`：L69–L83。
- `_parse_jsonl_bytes`：L86–L108。
- `_freeze_input`：L111–L131。
- `_assert_frozen_inputs_unchanged`：L134–L139。
- `canonical_json_bytes`：L142–L151。
- `canonical_sha256`：L154–L155。
- `file_sha256`：L158–L163。
- `_load_jsonl`：L166–L168。
- `_write_json`：L171–L176。
- `_write_jsonl`：L179–L187。
- `_event_code_for_indicator`：L190–L191。
- `_finite_number`：L194–L199。
- `_normalize_video_id`：L202–L210。
- `_load_semantic_requirements`：L213–L233。
- `_validate_manual_event`：L236–L258。
- `_validate_semantic`：L261–L282。
- `_load_group_metadata`：L285–L320。
- `_metadata_for_event`：L323–L338。
- `_UnionFind`：L341–L357；方法：`__init__` L342、`find` L345、`union` L351。
- `_build_leakage_components`：L360–L412。
- `_load_split_policy`：L415–L456。
- `_assign_splits`：L459–L490。
- `_manual_target_alignment_feature`：L493–L533。
- `_feature_vector`：L536–L590。
- `_label_summary`：L593–L630。
- `_distribution`：L633–L642。
- `recompute_independent_test_seal`：L645–L667。
- `_prepared_indicator`：L670–L829。
- `_compile_calibration_dataset_in_place`：L832–L1680。
- `_replace_path_prefix`：L1683–L1690。
- `_freeze_compiler_inputs`：L1693–L1740。
- `_authorization_input_hashes`：L1743–L1759。
- `compile_calibration_dataset`：L1762–L1971。
### src/rallymate_scoring/calibration_fitting.py

[打开源码](../../src/rallymate_scoring/calibration_fitting.py) · 1,285 行

直接引用的项目模块：`rallymate_scoring.calibration`、`rallymate_scoring.scoring_truth_calibration_authorization`。

- `CalibrationFitError`：L36–L37。
- `canonical_json_bytes`：L40–L47。
- `canonical_sha256`：L50–L51。
- `_legacy_nonproduction_truth_marker`：L54–L76。
- `independent_test_seal_sha256`：L79–L104。
- `_require_string`：L107–L111。
- `_require_sha256`：L114–L121。
- `_finite_number`：L124–L130。
- `_non_negative_integer`：L133–L136。
- `_string_list`：L139–L146。
- `_validate_feature_maps`：L149–L163。
- `_validate_label_resolution`：L166–L205。
- `validate_prepared_dataset`：L208–L504。
- `validate_fit_protocol`：L507–L618。
- `_apply_protocol_gates`：L621–L650。
- `_records_matrix`：L653–L660。
- `_metrics`：L663–L682。
- `_threshold_predictions`：L685–L689。
- `_fit_thresholds`：L692–L734。
- `_sigmoid`：L737–L742。
- `_ordinal_probabilities`：L745–L759。
- `_ordinal_loss_and_gradient`：L762–L797。
- `_project_cutpoints`：L800–L805。
- `_fit_ordinal`：L808–L848。
- `_candidate_base`：L851–L923。
- `fit_calibration_candidates`：L926–L1053。
- `validate_calibration_candidate`：L1056–L1234。
- `predict_candidate`：L1237–L1285。
### src/rallymate_scoring/calibration_handoff.py

[打开源码](../../src/rallymate_scoring/calibration_handoff.py) · 816 行

直接引用的项目模块：`rallymate_annotation.scoring_truth_refresh`、`rallymate_scoring.calibration_dataset`、`rallymate_scoring.calibration_fitting`、`rallymate_scoring.manual_event_features`。

- `_sha256_file`：L29–L34。
- `_canonical_sha256`：L37–L45。
- `_read_json`：L48–L52。
- `_read_jsonl`：L55–L68。
- `_write_json`：L71–L76。
- `_write_jsonl`：L79–L87。
- `_artifact`：L90–L91。
- `_copy_bound`：L94–L103。
- `_first_pose_topology`：L106–L121。
- `_fit_readiness`：L124–L179。
- `_render_index`：L182–L214。
- `build_scoring_truth_calibration_handoff`：L217–L485。
- `_verify_artifact`：L488–L493。
- `validate_scoring_truth_calibration_handoff`：L496–L788。
- `load_latest_scoring_truth_calibration_handoff`：L791–L806。
### src/rallymate_scoring/calibration_independent_test.py

[打开源码](../../src/rallymate_scoring/calibration_independent_test.py) · 2,043 行

直接引用的项目模块：`rallymate_scoring.calibration`、`rallymate_scoring.calibration_fitting`、`rallymate_scoring.indicator_feature_qualification`、`rallymate_scoring.indicator_requirements`。

- `IndependentTestError`：L42–L43。
- `_require_string`：L46–L50。
- `_require_sha256`：L53–L60。
- `_finite`：L63–L69。
- `_non_negative_integer`：L72–L75。
- `_parse_time`：L78–L85。
- `validate_independent_test_protocol`：L88–L183。
- `_string_array`：L186–L196。
- `_require_exact_keys`：L199–L217。
- `_positive_integer`：L220–L223。
- `_validate_feature_vector`：L226–L292。
- `_validate_manual_event`：L295–L354。
- `_validate_semantics`：L357–L441。
- `_validate_raw_labels`：L444–L489。
- `_validate_label_summary_shape`：L492–L546。
- `_validate_groups_readiness_lineage`：L549–L627。
- `validate_independent_test_sample`：L630–L844。
- `_sample_groups`：L847–L869。
- `_resolved_grade`：L872–L991。
- `_feature_values`：L994–L1039。
- `_metrics`：L1042–L1080。
- `_grouped_metrics`：L1083–L1089。
- `_acceptance`：L1092–L1171。
- `_validate_report_groups`：L1174–L1184。
- `_report_canonical_bytes`：L1187–L1193。
- `_validate_report_prediction`：L1196–L1262。
- `validate_independent_test_report`：L1265–L1703。
- `evaluate_independent_test`：L1706–L2023。
- `load_jsonl`：L2026–L2043。
### src/rallymate_scoring/calibration_measurement_sources.py

[打开源码](../../src/rallymate_scoring/calibration_measurement_sources.py) · 509 行

直接引用的项目模块：`rallymate_tracking`。

- `_sha256_file`：L24–L29。
- `_canonical_sha256`：L32–L41。
- `_read_json`：L44–L48。
- `_write_json`：L51–L56。
- `_artifact`：L59–L61。
- `_jsonl_metadata`：L64–L114。
- `_validate_timeline_against_frames`：L117–L150。
- `_pose_contract`：L153–L184。
- `build_calibration_measurement_sources`：L187–L346。
- `_verify_artifact`：L349–L353。
- `validate_calibration_measurement_sources`：L356–L484。
- `load_latest_calibration_measurement_sources`：L487–L500。
### src/rallymate_scoring/calibration_portfolio.py

[打开源码](../../src/rallymate_scoring/calibration_portfolio.py) · 734 行

直接引用的项目模块：`rallymate_annotation.scoring_truth_refresh`、`rallymate_scoring.calibration_dataset`、`rallymate_scoring.calibration_fitting`、`rallymate_scoring.calibration_handoff`、`rallymate_scoring.calibration_measurement_sources`、`rallymate_scoring.manual_event_features`。

- `_render_index`：L42–L78。
- `build_scoring_truth_calibration_portfolio`：L81–L378。
- `_verify`：L381–L385。
- `validate_scoring_truth_calibration_portfolio`：L388–L709。
- `load_latest_scoring_truth_calibration_portfolio`：L712–L725。
### src/rallymate_scoring/calibration_promotion.py

[打开源码](../../src/rallymate_scoring/calibration_promotion.py) · 2,083 行

直接引用的项目模块：`rallymate_scoring.calibration`、`rallymate_scoring.calibration_fitting`、`rallymate_scoring.calibration_independent_test`、`rallymate_scoring.feasibility`、`rallymate_scoring.maturity_evidence`、`rallymate_scoring.scoring_truth_calibration_authorization`。

- `CalibrationPromotionError`：L55–L56。
- `TrustedProductionCalibration`：L59–L94；方法：`__init__` L69、`__getitem__` L83、`__iter__` L86、`__len__` L89、`authorization` L93。
- `_require_object`：L97–L100。
- `_require_string`：L103–L107。
- `_require_sha256`：L110–L117。
- `_timestamp`：L120–L129。
- `_expect_equal`：L132–L134。
- `_verify_binding`：L137–L151。
- `_validate_maturity_binding`：L154–L226。
- `validate_promotion_decision`：L229–L310。
- `_validate_report`：L313–L472。
- `_build_source_replay_lineage`：L475–L555。
- `_validate_promotion_input_snapshots`：L558–L649。
- `_build_promotion_input_snapshots`：L652–L688。
- `_replay_production_independent_test`：L691–L733。
- `_find_registry_indicator`：L736–L750。
- `_validate_report_requirements_for_maturity_transition`：L753–L782。
- `_verify_decision_lineage`：L785–L879。
- `_validate_maturity_for_promotion`：L882–L989。
- `_asset_from_candidate`：L992–L1054。
- `_build_promotion_lineage`：L1057–L1127。
- `validate_promotion_lineage`：L1130–L1389。
- `promote_calibration_candidate`：L1392–L1635。
- `_calibration_version`：L1638–L1640。
- `_validate_promotion_report_against_asset`：L1643–L1754。
- `build_trusted_promotion_ledger`：L1757–L1868。
- `validate_trusted_promotion_ledger`：L1871–L1930。
- `verify_production_asset_with_trusted_ledger`：L1933–L1981。
- `authorize_production_asset_with_trusted_ledger`：L1984–L1992。
- `bind_trusted_production_calibration_to_registry`：L1995–L2079。
- `is_trusted_production_calibration`：L2082–L2083。
### src/rallymate_scoring/calibration_ranking.py

[打开源码](../../src/rallymate_scoring/calibration_ranking.py) · 1,491 行

直接引用的项目模块：`rallymate_scoring.calibration`。

- `RankingCalibrationError`：L36–L37。
- `canonical_json_bytes`：L40–L47。
- `canonical_sha256`：L50–L51。
- `_require_string`：L54–L58。
- `_require_sha256`：L61–L68。
- `_finite`：L71–L77。
- `_non_negative_integer`：L80–L83。
- `_parse_time`：L86–L95。
- `_string_list`：L98–L105。
- `_exact_feature_maps`：L108–L123。
- `_ranking_labels`：L126–L160。
- `_source_item`：L163–L230。
- `_pair_records`：L233–L297。
- `_agreement`：L300–L329。
- `_seal`：L332–L344。
- `prepare_ranking_dataset`：L347–L519。
- `validate_ranking_dataset`：L522–L703。
- `validate_ranking_fit_protocol`：L706–L767。
- `_protocol_gates`：L770–L806。
- `_matrix`：L809–L823。
- `_sigmoid`：L826–L828。
- `_fit_pairwise`：L831–L872。
- `_kendall_score_vs_rank`：L875–L890。
- `_fit_metrics`：L893–L936。
- `fit_ranking_candidate`：L939–L1054。
- `validate_ranking_candidate`：L1057–L1148。
- `rank_candidate_items`：L1151–L1211。
- `validate_ranking_test_protocol`：L1214–L1252。
- `evaluate_ranking_independent_test`：L1255–L1378。
- `validate_ranking_test_report`：L1381–L1468。
- `load_jsonl`：L1471–L1484。
- `write_json`：L1487–L1491。
### src/rallymate_scoring/cycle_measurement.py

[打开源码](../../src/rallymate_scoring/cycle_measurement.py) · 591 行

直接引用的项目模块：`rallymate_scoring.feasibility`。

- `CycleMeasurementError`：L20–L21。
- `_source_id`：L24–L25。
- `_phase`：L28–L30。
- `_cycle_id`：L33–L37。
- `_feature_confidences`：L40–L41。
- `_compact_feature_vector`：L44–L57。
- `_event_ref`：L60–L72。
- `_link_cycles`：L75–L150。
- `_cycle_rank`：L153–L163。
- `build_scoring_cycle_measurement`：L166–L416。
- `validate_scoring_cycle_measurement`：L419–L591。
### src/rallymate_scoring/diagnostic_policy_review.py

[打开源码](../../src/rallymate_scoring/diagnostic_policy_review.py) · 631 行

直接引用的项目模块：`rallymate_evaluation.pose_diagnostic_truth`、`rallymate_scoring.quality_policy`。

- `DiagnosticPolicyReviewError`：L30–L31。
- `_canonical_hash`：L34–L43。
- `_sha256`：L46–L51。
- `_exact_keys`：L54–L64。
- `_string`：L67–L70。
- `_sha`：L73–L80。
- `_timestamp`：L83–L92。
- `_rate`：L95–L103。
- `_positive_int`：L106–L109。
- `validate_diagnostic_gate_acceptance_protocol`：L112–L277。
- `_safe_ratio`：L280–L281。
- `_f1`：L284–L289。
- `_load_and_recompute_bundle`：L292–L345。
- `review_pose_diagnostic_quality_gate`：L348–L586。
- `validate_diagnostic_policy_review`：L589–L631。
### src/rallymate_scoring/feasibility.py

[打开源码](../../src/rallymate_scoring/feasibility.py) · 339 行

- `_non_empty`：L33–L34。
- `assess_feasibility_promotion`：L37–L136。
- `_validate_registry_header`：L139–L154。
- `_validate_indicator`：L157–L242。
- `measurement_feature_names`：L245–L265。
- `validate_feasibility_registry`：L268–L296。
- `load_feasibility_registry`：L299–L302。
- `indicator_ids_at_or_above`：L305–L325。
- `feasibility_event_codes`：L328–L339。
### src/rallymate_scoring/granularity.py

[打开源码](../../src/rallymate_scoring/granularity.py) · 793 行

- `_registry`：L37–L39。
- `_mapping`：L42–L43。
- `_registry_indicator_metadata`：L46–L63。
- `_minimum_loop_context`：L66–L220。
- `_max_missing_run`：L223–L232。
- `_longest_track_fraction`：L235–L240。
- `inspect_frame_observations`：L243–L321。
- `_is_optional`：L324–L333。
- `_required_joints`：L336–L337。
- `_dependency_coverage`：L340–L367。
- `_evidence_status`：L370–L375。
- `_model_granularity`：L378–L406。
- `_indicator_result`：L409–L549。
- `_capability_matrix`：L552–L619。
- `static_model_capability`：L622–L651。
- `analyze_scoring_readiness`：L654–L793。
### src/rallymate_scoring/indicator_feature_qualification.py

[打开源码](../../src/rallymate_scoring/indicator_feature_qualification.py) · 715 行

直接引用的项目模块：`rallymate_scoring.quality_policy`、`rallymate_scoring.run_bundle_binding`、`rallymate_scoring.scoring_context`。

- `IndicatorFeatureQualificationError`：L91–L92。
- `_reject_duplicate_keys`：L95–L101。
- `_parse_json`：L104–L125。
- `_parse_jsonl`：L128–L158。
- `_sha256_bytes`：L161–L162。
- `_canonical_sha256`：L165–L173。
- `_sha256`：L176–L185。
- `_string`：L188–L193。
- `_plain_file`：L196–L203。
- `_declared_indicator_feature_path`：L206–L220。
- `VerifiedIndicatorFeatureSourceMetadata`：L224–L239；方法：`__reduce__` L234、`metadata` L238。
- `verify_indicator_feature_source_metadata`：L245–L331。
- `require_verified_indicator_feature_source_metadata`：L334–L358。
- `assert_indicator_feature_source_metadata_unchanged`：L361–L375。
- `nonproduction_source_metadata`：L378–L399。
- `validate_source_metadata`：L402–L455。
- `validate_quality_gate`：L458–L475。
- `build_qualification_snapshot`：L478–L539。
- `_feature_values_complete`：L542–L560。
- `_resolved_target_direction_from_vector`：L563–L575。
- `validate_qualification_snapshot`：L578–L654。
- `derive_feature_qualification`：L657–L697。
### src/rallymate_scoring/indicator_requirements.py

[打开源码](../../src/rallymate_scoring/indicator_requirements.py) · 241 行

- `event_code_for_indicator`：L30–L33。
- `required_phase_keys`：L36–L60。
- `requirement_snapshot`：L63–L86。
- `canonical_sha256`：L89–L97。
- `build_indicator_requirements_snapshot`：L100–L154。
- `validate_indicator_requirements_snapshot`：L157–L241。
### src/rallymate_scoring/loop.py

[打开源码](../../src/rallymate_scoring/loop.py) · 843 行

直接引用的项目模块：`rallymate_evaluation.feature_errors`、`rallymate_events`、`rallymate_features`、`rallymate_features.coordinates`、`rallymate_features.event_features`、`rallymate_features.fs01_fs02_features`、`rallymate_features.fs09_features`、`rallymate_scoring.feasibility`、`rallymate_scoring.quality_policy`、`rallymate_scoring.runtime_profile_binding`、`rallymate_scoring.scoring`、`rallymate_scoring.scoring_context`、`rallymate_scoring.source_assessment`、`rallymate_tracking`。

- `_load_jsonl`：L79–L86。
- `_write_jsonl`：L89–L94。
- `_sha256`：L97–L102。
- `_implemented_feature_contract`：L105–L140。
- `_model_versions`：L143–L181。
- `run_minimum_scoring_loop`：L184–L843。
### src/rallymate_scoring/manual_event_features.py

[打开源码](../../src/rallymate_scoring/manual_event_features.py) · 429 行

直接引用的项目模块：`rallymate_events.schemas`、`rallymate_features`、`rallymate_scoring.calibration_dataset`、`rallymate_scoring.feasibility`、`rallymate_scoring.quality_policy`、`rallymate_scoring.scoring`、`rallymate_scoring.scoring_context`、`rallymate_tracking`。

- `_load_jsonl`：L41–L54。
- `_write_jsonl`：L57–L61。
- `_compact_feature`：L64–L74。
- `_pose_versions`：L77–L96。
- `build_manual_event_features`：L99–L426。
### src/rallymate_scoring/maturity_evidence.py

[打开源码](../../src/rallymate_scoring/maturity_evidence.py) · 922 行

直接引用的项目模块：`rallymate_scoring.calibration_fitting`、`rallymate_scoring.feasibility`。

- `MaturityEvidenceError`：L27–L28。
- `_require_object`：L31–L34。
- `_require_array`：L37–L40。
- `_exact_keys`：L43–L57。
- `_require_string`：L60–L64。
- `_require_sha256`：L67–L74。
- `_timestamp`：L77–L86。
- `_number`：L89–L97。
- `_ratio`：L100–L104。
- `_positive_int`：L107–L110。
- `_source_kind`：L113–L114。
- `_validate_reference`：L117–L146。
- `_validate_registry_binding`：L149–L170。
- `_validate_reviewer`：L173–L178。
- `_validate_error_stats`：L181–L201。
- `_validate_metric_summary`：L204–L218。
- `_validate_event_payload`：L221–L269。
- `_validate_feature_metric`：L272–L315。
- `_validate_feature_payload`：L318–L446。
- `_validate_f3_payload`：L449–L535。
- `_validate_f4_payload`：L538–L607。
- `canonical_transition_sha256`：L610–L617。
- `_validate_transition`：L620–L743。
- `validate_maturity_evidence_bundle`：L746–L854。
- `validate_maturity_evidence_for_registry`：L857–L892。
- `maturity_evidence_binding`：L895–L908。
### src/rallymate_scoring/measurement_plans.py

[打开源码](../../src/rallymate_scoring/measurement_plans.py) · 517 行

直接引用的项目模块：`rallymate_events.rules`、`rallymate_features.event_features`、`rallymate_scoring.feasibility`、`rallymate_scoring.indicator_requirements`。

- `_registry`：L120–L122。
- `_calculation_clauses`：L125–L127。
- `classify_clause`：L130–L248。
- `_requirement_status`：L251–L266。
- `_event_localization`：L269–L280。
- `compile_measurement_plan`：L283–L390。
- `_load_build_feasibility_registry`：L393–L399。
- `_next_pose_wave_indicator_ids`：L402–L416。
- `build_measurement_plan_registry`：L419–L495。
- `write_measurement_plan_registry`：L498–L517。
### src/rallymate_scoring/measurement_portfolio.py

[打开源码](../../src/rallymate_scoring/measurement_portfolio.py) · 423 行

直接引用的项目模块：`rallymate_scoring.feasibility`。

- `MeasurementPortfolioError`：L17–L18。
- `_feature_confidence_summary`：L21–L25。
- `_measurement_rank`：L28–L42。
- `_diagnostic_rank`：L45–L57。
- `_event_reference`：L60–L70。
- `_feature_vector`：L73–L86。
- `build_indicator_measurement_portfolio`：L89–L289。
- `validate_indicator_measurement_portfolio`：L292–L423。
### src/rallymate_scoring/multivideo_coverage.py

[打开源码](../../src/rallymate_scoring/multivideo_coverage.py) · 668 行

直接引用的项目模块：`rallymate_scoring.calibration_measurement_sources`、`rallymate_vision.validation`。

- `_read_json`：L24–L28。
- `_read_jsonl`：L31–L41。
- `_sha256_file`：L44–L49。
- `_canonical_sha256`：L52–L61。
- `_artifact`：L64–L66。
- `_verify_artifact`：L69–L75。
- `_write_json`：L78–L83。
- `_feature_vector`：L86–L103。
- `_replay_video`：L106–L292。
- `_assemble`：L295–L407。
- `_render_index`：L410–L450。
- `build_multivideo_indicator_calculation_coverage`：L453–L563。
- `validate_multivideo_indicator_calculation_coverage`：L566–L642。
- `load_latest_multivideo_indicator_calculation_coverage`：L645–L659。
### src/rallymate_scoring/multivideo_readiness.py

[打开源码](../../src/rallymate_scoring/multivideo_readiness.py) · 858 行

直接引用的项目模块：`rallymate_scoring.blocker_taxonomy`、`rallymate_scoring.multivideo_coverage`。

- `_read_json`：L41–L45。
- `_read_jsonl`：L48–L58。
- `_sha256_file`：L61–L66。
- `_canonical_sha256`：L69–L78。
- `_artifact`：L81–L83。
- `_verify_artifact`：L86–L92。
- `_write_json`：L95–L100。
- `_complete_feature_vector`：L103–L109。
- `_classify`：L112–L154。
- `_counter_dict`：L157–L158。
- `_base_counts`：L161–L215。
- `_metric_rows`：L218–L274。
- `_combination_rows`：L277–L295。
- `_typed_reason_rows`：L298–L336。
- `_scoring_block_recovery_rows`：L339–L405。
- `_load_records`：L408–L448。
- `_assemble`：L451–L523。
- `_render_index`：L526–L610。
- `build_multivideo_scoring_readiness_decomposition`：L613–L692。
- `validate_multivideo_scoring_readiness_decomposition`：L695–L830。
- `load_latest_multivideo_scoring_readiness_decomposition`：L833–L849。
### src/rallymate_scoring/quality_policy.py

[打开源码](../../src/rallymate_scoring/quality_policy.py) · 369 行

直接引用的项目模块：`rallymate_features.event_features`、`rallymate_scoring.blocker_taxonomy`、`rallymate_scoring.indicator_requirements`。

- `_indicator_required_joints`：L147–L172。
- `_joint_scoped_pose_diagnostic_flags`：L175–L223。
- `indicator_event_quality_flags`：L226–L249。
- `_is_confirmed_id_switch`：L252–L261。
- `evaluate_indicator_event_quality`：L264–L369。
### src/rallymate_scoring/registry_lifecycle.py

[打开源码](../../src/rallymate_scoring/registry_lifecycle.py) · 568 行

直接引用的项目模块：`rallymate_scoring.feasibility`、`rallymate_scoring.indicator_requirements`。

- `RegistryLifecycleError`：L43–L44。
- `ResolvedRegistryArtifact`：L48–L68。
- `_exact_keys`：L95–L105。
- `_non_empty_string`：L108–L111。
- `_sha256`：L114–L123。
- `_relative_path`：L126–L145。
- `_reject_duplicate_keys`：L148–L154。
- `_reject_nonfinite_constant`：L157–L158。
- `_parse_finite_float`：L161–L165。
- `_validate_canonical_utf8`：L168–L187。
- `_parse_json_bytes`：L190–L207。
- `_validate_binding`：L210–L248。
- `_validate_lifecycle_manifest`：L251–L313。
- `_read_manifest`：L316–L333。
- `load_registry_lifecycle`：L336–L347。
- `_resolve_under_manifest_root`：L350–L370。
- `_embedded_identity`：L373–L393。
- `_validate_artifact_payload`：L396–L423。
- `_resolve_binding_artifact`：L426–L484。
- `resolve_registry_role`：L487–L506。
- `resolve_runtime_feasibility_registry`：L509–L512。
- `resolve_current_scoring_requirements`：L515–L518。
- `validate_registry_lifecycle_artifacts`：L521–L553。
### src/rallymate_scoring/report.py

[打开源码](../../src/rallymate_scoring/report.py) · 439 行

- `_e`：L45–L46。
- `_pct`：L49–L53。
- `_number`：L56–L60。
- `_metric`：L63–L72。
- `_bar`：L75–L81。
- `build_analysis_report_html`：L84–L409。
- `write_analysis_report`：L412–L419。
- `main`：L422–L435。
### src/rallymate_scoring/rotation_analysis.py

[打开源码](../../src/rallymate_scoring/rotation_analysis.py) · 328 行

Replayable image-plane body-axis measurements, never a technique score.

- `_number`：L42–L48。
- `_axis_observation`：L51–L61。
- `_axis_angle`：L64–L65。
- `_metric_values`：L68–L75。
- `_filtered_segment`：L78–L86。
- `_metric_evidence`：L89–L92。
- `_availability_reasons`：L95–L104。
- `_axis_delta`：L107–L109。
- `_segments`：L112–L133。
- `_phase_measurements`：L136–L185。
- `analyze_rotation`：L188–L328。
### src/rallymate_scoring/run_bundle_binding.py

[打开源码](../../src/rallymate_scoring/run_bundle_binding.py) · 486 行

直接引用的项目模块：`rallymate_scoring.calibration_fitting`。

- `ScoringRunBundleBindingError`：L28–L29。
- `_object`：L32–L35。
- `_exact_keys`：L38–L48。
- `_string`：L51–L54。
- `_sha256`：L57–L66。
- `_timestamp`：L69–L79。
- `_artifact_hashes`：L82–L88。
- `scoring_run_bundle_identity`：L91–L140。
- `scoring_run_bundle_root_sha256`：L143–L145。
- `_validate_bundle_identity`：L148–L198。
- `build_scoring_run_bundle_entry`：L201–L232。
- `build_trusted_scoring_run_bundle_ledger`：L235–L262。
- `_validate_entry`：L265–L320。
- `validate_trusted_scoring_run_bundle_ledger`：L323–L406。
- `load_trusted_scoring_run_bundle_ledger`：L409–L422。
- `authorize_scoring_run_bundle`：L425–L472。
### src/rallymate_scoring/runtime_profile_binding.py

[打开源码](../../src/rallymate_scoring/runtime_profile_binding.py) · 792 行

直接引用的项目模块：`rallymate_events`、`rallymate_scoring.calibration_fitting`、`rallymate_scoring.calibration_promotion`、`rallymate_scoring.feasibility`。

- `RuntimeProfileBindingError`：L26–L27。
- `RuntimeProfileBoundProductionCalibration`：L30–L66；方法：`__init__` L40、`__getitem__` L55、`__iter__` L58、`__len__` L61、`authorization` L65。
- `is_runtime_profile_bound_calibration`：L69–L70。
- `_require_object`：L73–L76。
- `_exact_keys`：L79–L88。
- `_string`：L91–L94。
- `_sha256`：L97–L104。
- `_timestamp`：L107–L117。
- `_unique_strings`：L120–L129。
- `_calibration_version`：L132–L134。
- `_indicator_runtime_version`：L137–L154。
- `_find_indicator`：L157–L179。
- `_validate_pose_profile`：L182–L200。
- `_validate_pipeline_profile`：L203–L215。
- `_validate_view_profile`：L218–L228。
- `validate_runtime_scoring_profile`：L231–L261。
- `build_runtime_scoring_profile`：L264–L343。
- `validate_runtime_view_evidence`：L346–L394。
- `_validate_binding_entry`：L397–L471。
- `validate_trusted_runtime_binding_registry`：L474–L526。
- `_profile_matches_indicator`：L529–L552。
- `bind_trusted_production_calibration_to_runtime_profile`：L555–L671。
- `bind_production_calibrations_for_runtime`：L674–L760。
- `runtime_profile_versions`：L763–L778。
### src/rallymate_scoring/scoring.py

[打开源码](../../src/rallymate_scoring/scoring.py) · 621 行

直接引用的项目模块：`rallymate_scoring.blocker_taxonomy`、`rallymate_scoring.calibration`、`rallymate_scoring.calibration_promotion`、`rallymate_scoring.quality_policy`、`rallymate_scoring.runtime_profile_binding`。

- `_base_result`：L30–L55。
- `_trusted_registry_versions`：L58–L91。
- `_production_authorization`：L94–L102。
- `_runtime_profile_mismatch_reasons`：L105–L161。
- `_scoring_block_explanation`：L164–L171。
- `_append_concurrent_scoring_blocks`：L174–L189。
- `score_indicator`：L192–L621。
### src/rallymate_scoring/scoring_context.py

[打开源码](../../src/rallymate_scoring/scoring_context.py) · 694 行

- `canonical_sha256`：L35–L43。
- `file_sha256`：L46–L51。
- `bind_scoring_context_evidence_to_video`：L54–L93。
- `_finite_number`：L96–L102。
- `signed_angle_delta_deg`：L105–L111。
- `build_target_direction_alignment_feature`：L114–L186。
- `compact_target_direction_alignment_feature`：L189–L211。
- `validate_scoring_reference_context`：L214–L384。
- `build_scoring_reference_worklist`：L387–L452。
- `load_scoring_reference_context`：L455–L473。
- `resolve_target_direction_context`：L476–L545。
- `validate_resolved_target_direction_context`：L548–L694。
### src/rallymate_scoring/scoring_loop_report.py

[打开源码](../../src/rallymate_scoring/scoring_loop_report.py) · 662 行

- `_h`：L16–L17。
- `_json_text`：L20–L36。
- `_number`：L39–L46。
- `_series_summary`：L49–L86。
- `_payload_summary`：L89–L116。
- `_list_text`：L119–L122。
- `_key_value_table`：L125–L135。
- `_event_css_class`：L138–L140。
- `_event_details`：L143–L178。
- `_timeline`：L181–L225。
- `_evidence_frames`：L228–L245。
- `_feature_table`：L248–L327。
- `_legal_event_grade`：L330–L341。
- `_indicator_instance`：L344–L396。
- `_metric_value`：L399–L400。
- `_view_items`：L403–L418。
- `_feature_error_section`：L421–L485。
- `build_scoring_loop_report_html`：L488–L615。
- `_load_jsonl`：L618–L636。
- `_artifact_path`：L639–L645。
- `write_scoring_loop_report`：L648–L662。
### src/rallymate_scoring/scoring_truth_authorized_intake.py

[打开源码](../../src/rallymate_scoring/scoring_truth_authorized_intake.py) · 332 行

直接引用的项目模块：`rallymate_annotation.scoring_truth_event_phase_intake`、`rallymate_annotation.scoring_truth_intake`、`rallymate_scoring.scoring_truth_calibration_authorization`。

- `_error`：L60–L61。
- `_exact`：L64–L67。
- `_text`：L70–L73。
- `_identity`：L76–L77。
- `_sha`：L80–L81。
- `_digest`：L84–L87。
- `_time`：L90–L98。
- `validate_scoring_truth_acceptance_ledger`：L101–L150。
- `_jsonl`：L153–L154。
- `_read`：L157–L158。
- `_check_labels_and_semantics`：L161–L188。
- `verify_scoring_truth_calibration_intake`：L191–L329。
### src/rallymate_scoring/scoring_truth_calibration_authorization.py

[打开源码](../../src/rallymate_scoring/scoring_truth_calibration_authorization.py) · 328 行

- `ScoringTruthCalibrationAuthorizationError`：L34–L35。
- `_canonical_json_bytes`：L38–L45。
- `_sha256`：L48–L49。
- `scoring_truth_calibration_authorization_binding_sha256`：L52–L57。
- `_require_sha256`：L60–L67。
- `validate_scoring_truth_calibration_authorization_binding`：L80–L217。
- `VerifiedScoringTruthCalibrationAuthorization`：L224–L236；方法：`__reduce__` L231、`binding` L235。
- `_issue_verified_scoring_truth_calibration_authorization`：L239–L249。
- `require_verified_scoring_truth_calibration_authorization`：L252–L266。
- `build_nonproduction_truth_authorization_marker`：L269–L315。
### src/rallymate_scoring/source_aligned_features.py

[打开源码](../../src/rallymate_scoring/source_aligned_features.py) · 10 行

Public source-review adapter; existing registry/scoring paths are unchanged.

直接引用的项目模块：`rallymate_features.source_alignment`。

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_scoring/source_assessment.py

[打开源码](../../src/rallymate_scoring/source_assessment.py) · 66 行

Persist additive source-aligned observations without changing legacy scores.

直接引用的项目模块：`rallymate_features.schemas`、`rallymate_scoring.source_aligned_features`。

- `source_binding`：L22–L26。
- `file_sha`：L29–L34。
- `write_source_aligned_assessment`：L37–L66。
### src/rallymate_scoring/stroke_analysis.py

[打开源码](../../src/rallymate_scoring/stroke_analysis.py) · 255 行

Measured image-plane stroke motion; no contact events or calibrated scores.

- `_distance`：L29–L30。
- `racket_hand_evidence`：L33–L44。
- `_angle`：L47–L53。
- `_episode`：L57–L155。
- `_incoming_ball_context`：L158–L201。
- `build_motion_analysis`：L204–L255。
### src/rallymate_scoring/stroke_candidates.py

[打开源码](../../src/rallymate_scoring/stroke_candidates.py) · 478 行

Reviewable swing/serve motion candidates from tracked pose and racket boxes.

- `Sample`：L34–L50。
- `_number`：L53–L56。
- `_point`：L59–L65。
- `_distance`：L68–L69。
- `_midpoint`：L72–L73。
- `_normalization`：L76–L90。
- `_sample`：L93–L131。
- `_stabilize_partial_torso_windows`：L134–L169。
- `_associate_rackets`：L172–L200。
- `_smooth_segment`：L203–L211。
- `_segments`：L214–L226。
- `_candidate`：L229–L243。
- `_racket_support`：L246–L247。
- `_detect_segment`：L250–L319。
- `_detect_tracks`：L322–L345。
- `detect_stroke_candidates`：L348–L452。
- `recognize_strokes_from_artifacts`：L455–L478。
### src/rallymate_scoring/technique_assessment.py

[打开源码](../../src/rallymate_scoring/technique_assessment.py) · 866 行

Evidence-gated assessment for the qualitative technique registry.

直接引用的项目模块：`rallymate_scoring.technique_registry`。

- `TechniqueRegistrySnapshotError`：L25–L43；方法：`__init__` L28、`as_detail` L37。
- `_canonical_event_code`：L133–L151。
- `_number`：L208–L212。
- `_as_mapping`：L215–L216。
- `_coverage_with_detail`：L219–L278。
- `_coverage`：L281–L284。
- `_augment_coverage_from_records`：L287–L326。
- `_event_codes`：L329–L397。
- `_candidate_only`：L400–L404。
- `_action_recognition`：L407–L425。
- `_technique_observed`：L428–L437。
- `_family_recognition`：L440–L503。
- `_record_technique_ids`：L506–L522。
- `_phase_statuses`：L525–L555。
- `_status`：L558–L565。
- `_key_field_analysis`：L568–L608。
- `_has_strike_phase`：L611–L626。
- `_contact_status`：L629–L647。
- `_expected_registry_snapshot`：L650–L669。
- `_resolve_registry_snapshot`：L672–L704。
- `build_technique_assessment`：L707–L866。
### src/rallymate_scoring/technique_registry.py

[打开源码](../../src/rallymate_scoring/technique_registry.py) · 282 行

Versioned qualitative technique registry derived from the supplied DOCX rules.

- `TechniqueRegistryError`：L24–L25。
- `_reject_duplicate_keys`：L28–L34。
- `_reject_nonfinite_constant`：L37–L38。
- `_parse_finite_float`：L41–L45。
- `_decode_and_validate`：L48–L60。
- `_require_text`：L63–L66。
- `_require_string_list`：L69–L77。
- `_validate`：L80–L189。
- `_load_cached`：L193–L204。
- `_load_source`：L207–L214。
- `_packaged_source`：L217–L225。
- `_load_registry`：L228–L233。
- `load_technique_registry`：L236–L244。
- `load_technique_registry_snapshot`：L247–L259。
- `technique_registry_digest`：L262–L266。
- `technique_catalog`：L269–L282。
### src/rallymate_scoring/temporal_recognition.py

[打开源码](../../src/rallymate_scoring/temporal_recognition.py) · 343 行

Replaceable temporal recognition, with explicit observed-data contracts.

- `_distance`：L35–L36。
- `samples_are_continuous`：L39–L48。
- `TemporalPoseWindow`：L52–L71；方法：`summary` L64。
- `build_temporal_window`：L74–L136。
- `TemporalRecognitionBackend`：L139–L145；方法：`metadata` L142、`recognize` L144。
- `wrist_kinematics`：L148–L157。
- `_axis`：L160–L175。
- `_classification`：L178–L227。
- `_rule_recognition`：L231–L292。
- `RuleTemporalBackend`：L295–L305；方法：`metadata` L298、`recognize` L303。
- `validate_recognition`：L308–L343。

## src/rallymate_service

### src/rallymate_service/__init__.py

[打开源码](../../src/rallymate_service/__init__.py) · 1 行

Single-GPU deployable API and durable job worker for RallyMate.

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_service/analysis_report.py

[打开源码](../../src/rallymate_service/analysis_report.py) · 263 行

Compose a replay-first report without turning evidence into technique grades.

- `_object`：L20–L21。
- `_rows`：L24–L25。
- `_number`：L28–L34。
- `_interval`：L37–L41。
- `_focus`：L44–L51。
- `_footwork_observations`：L54–L74。
- `_motion_episodes`：L77–L92。
- `_rotation_metric_names`：L95–L108。
- `_rotation_local_windows`：L111–L122。
- `_no_candidate_reason`：L125–L144。
- `build_analysis_report`：L147–L263。
### src/rallymate_service/api.py

[打开源码](../../src/rallymate_service/api.py) · 1,374 行

直接引用的项目模块：`rallymate_scoring.granularity`、`rallymate_scoring.registry_lifecycle`、`rallymate_scoring.stroke_analysis`、`rallymate_scoring.stroke_candidates`、`rallymate_scoring.technique_assessment`、`rallymate_scoring.technique_registry`、`rallymate_service.analysis_report`、`rallymate_service.config`、`rallymate_service.database`、`rallymate_service.footwork_review`、`rallymate_service.report_compatibility`、`rallymate_service.source_assessment`、`rallymate_service.technical_review`、`rallymate_service.uploads`、`rallymate_service.user_demo`、`rallymate_vision.pipeline`、`rallymate_vision.pose_playback`、`rallymate_vision.quality`、`rallymate_vision.trajectory`。

- `_redact_public_value`：L122–L148。
- `_public_summary`：L151–L155。
- `_should_redact_public`：L158–L159。
- `_redact_readiness_reason`：L162–L165。
- `_public_error_detail`：L168–L194。
- `_production_api_key_reasons`：L220–L246。
- `_public_url`：L249–L252。
- `_safe_request_id`：L255–L260。
- `_public_job`：L263–L329。
- `_safe_filename`：L332–L336。
- `_parse_polygon`：L339–L363。
- `create_app`：L366–L1371。
### src/rallymate_service/assets/user-demo.css

[打开源码](../../src/rallymate_service/assets/user-demo.css) · 461 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_service/assets/user-demo.html

[打开源码](../../src/rallymate_service/assets/user-demo.html) · 172 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_service/assets/user-demo.js

[打开源码](../../src/rallymate_service/assets/user-demo.js) · 839 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_service/cli.py

[打开源码](../../src/rallymate_service/cli.py) · 89 行

直接引用的项目模块：`rallymate_service.api`、`rallymate_service.config`、`rallymate_service.database`、`rallymate_service.worker`。

- `api_main`：L15–L27。
- `worker_main`：L30–L45。
- `dev_main`：L48–L89。
### src/rallymate_service/config.py

[打开源码](../../src/rallymate_service/config.py) · 524 行

直接引用的项目模块：`rallymate_scoring.registry_lifecycle`、`rallymate_scoring.technique_registry`、`rallymate_vision.pose.presets`。

- `ServiceConfigError`：L22–L23。
- `_split_http_url`：L26–L59。
- `_validate_web_origins`：L62–L96。
- `ServiceSettings`：L100–L372；方法：`uploads_dir` L131、`requests_dir` L135、`runs_dir` L139、`resolved_scoring_feasibility_registry` L143、`resolved_scoring_registry_lifecycle_manifest` L147、`resolved_scoring_registry_authority` L156、`resolved_technique_registry` L171、`ensure_directories` L176、`validate_license` L183。
- `_int_env`：L375–L388。
- `load_settings`：L391–L524。
### src/rallymate_service/database.py

[打开源码](../../src/rallymate_service/database.py) · 294 行

- `utc_now`：L11–L12。
- `JobDatabase`：L15–L294；方法：`__init__` L18、`connect` L23、`initialize` L33、`create_job` L71、`get_job` L101、`list_jobs` L124、`claim_next` L132、`update_progress` L204、`mark_succeeded` L228、`mark_failed` L262、`count_by_status` L289。
### src/rallymate_service/footwork_review.py

[打开源码](../../src/rallymate_service/footwork_review.py) · 408 行

Read-only, event-bound footwork measurements for video review.

直接引用的项目模块：`rallymate_features.event_features`、`rallymate_service.training_evaluation`。

- `_empty`：L63–L80。
- `_identity`：L83–L84。
- `_integer`：L87–L88。
- `_finite`：L91–L97。
- `_bounded_rows`：L100–L107。
- `_unique_by_key`：L110–L122。
- `_feature_candidates`：L125–L138。
- `_features`：L141–L167。
- `_reason_text`：L170–L189。
- `_measurements`：L192–L250。
- `build_footwork_review`：L253–L361。
- `_strict_object`：L364–L370。
- `_reject_nonfinite`：L373–L374。
- `load_footwork_review`：L377–L405。
### src/rallymate_service/report_compatibility.py

[打开源码](../../src/rallymate_service/report_compatibility.py) · 111 行

Explicit read-only projections for the original web client's report contract.

- `_fields`：L20–L21。
- `_provenance`：L24–L31。
- `_footwork`：L34–L49。
- `_motion`：L52–L84。
- `project_report`：L87–L111。
### src/rallymate_service/score_explanation.py

[打开源码](../../src/rallymate_service/score_explanation.py) · 235 行

Reconcile existing evidence scores without introducing scoring decisions.

- `_number`：L41–L48。
- `_excluded`：L51–L54。
- `_base`：L57–L63。
- `explain_indicator_score`：L66–L107。
- `explain_aggregate_score`：L110–L161。
- `_blocker_group`：L164–L179。
- `explain_evidence_gaps`：L182–L235。
### src/rallymate_service/source_assessment.py

[打开源码](../../src/rallymate_service/source_assessment.py) · 233 行

Bounded public projection of the source-aligned measurement artifact.

直接引用的项目模块：`rallymate_features.source_alignment`、`rallymate_scoring.source_assessment`。

- `_number`：L58–L62。
- `_reason`：L65–L81。
- `_empty`：L84–L86。
- `_signature`：L89–L91。
- `_reject_constant`：L94–L95。
- `_bound_measurement`：L98–L121。
- `project_source_assessment`：L124–L204。
- `_load`：L208–L221。
- `load_source_aligned_assessment`：L224–L233。
### src/rallymate_service/technical_review.py

[打开源码](../../src/rallymate_service/technical_review.py) · 472 行

Human source-rule reviews, deliberately separate from runtime scores/calibration.

直接引用的项目模块：`rallymate_scoring.calibration`。

- `TechnicalReviewError`：L35–L41；方法：`__init__` L36、`detail` L40。
- `_require`：L44–L46。
- `_json`：L49–L50。
- `_sha`：L53–L54。
- `_pairs`：L57–L63。
- `_load_json`：L66–L69。
- `TechnicalReviewInput`：L72–L117；方法：`validate_uuid` L100、`validate_sha` L107、`validate_text` L114。
- `parse_review_input`：L120–L126。
- `_signature`：L129–L131。
- `TechnicalReviewCatalog`：L134–L157；方法：`__init__` L135、`public_rules` L154。
- `TechnicalReviewContext`：L160–L246；方法：`__init__` L162、`load` L166。
- `TechnicalReviewStore`：L258–L472；方法：`__init__` L259、`catalog` L266、`initialize` L281、`_connect` L299、`_history` L312、`_latest` L332、`_response` L336、`get` L351、`_label_candidates` L371、`save` L390。
### src/rallymate_service/training_evaluation.py

[打开源码](../../src/rallymate_service/training_evaluation.py) · 703 行

直接引用的项目模块：`rallymate_service.score_explanation`。

- `_technical_score_contract`：L27–L34。
- `_reference_score`：L37–L60。
- `_reference_level`：L63–L70。
- `_finite`：L276–L283。
- `_percent`：L286–L289。
- `_is_measured`：L292–L299。
- `_event_identity`：L302–L314。
- `_valid_feature_map`：L317–L344。
- `_load_metric_cards`：L347–L357。
- `_repeatability_for_feature`：L360–L381。
- `_display_value`：L384–L395。
- `_friendly_limitations`：L398–L426。
- `_representative_measurements`：L429–L466。
- `_indicator_evaluation`：L469–L606。
- `validated_training_records`：L609–L638。
- `build_training_evaluation`：L641–L703。
### src/rallymate_service/uploads.py

[打开源码](../../src/rallymate_service/uploads.py) · 213 行

Durable byte-range uploads. Chunks are transport units, never video clips.

- `exclusive_file`：L23–L43。
- `UploadStore`：L46–L213；方法：`__init__` L47、`directory` L52、`read` L60、`save` L69、`public` L79、`create` L89、`status` L126、`put` L130、`complete` L160、`remove_parts` L199、`cleanup` L203。
### src/rallymate_service/user_demo.py

[打开源码](../../src/rallymate_service/user_demo.py) · 725 行

直接引用的项目模块：`rallymate_scoring.technique_assessment`、`rallymate_service.analysis_report`、`rallymate_service.training_evaluation`。

- `UserDemoResultError`：L32–L33。
- `_post_analysis_capabilities`：L115–L167。
- `_duplicate_rejecting_object`：L170–L176。
- `_reject_nonfinite_constant`：L179–L180。
- `load_indicator_feature_records`：L183–L230。
- `_finite_number`：L233–L237。
- `_nonnegative_int`：L240–L245。
- `_is_measured_record`：L248–L258。
- `_friendly_amplitudes`：L261–L315。
- `_bounded_percent`：L318–L321。
- `_formation_assessment`：L324–L378。
- `build_user_demo_result`：L381–L725。
### src/rallymate_service/worker.py

[打开源码](../../src/rallymate_service/worker.py) · 292 行

直接引用的项目模块：`rallymate_service.config`、`rallymate_service.database`、`rallymate_vision.contracts`、`rallymate_vision.inference`、`rallymate_vision.pipeline`、`rallymate_vision.pose.metadata`、`rallymate_vision.utils`。

- `CpuThreadConfigurationError`：L24–L25。
- `_set_thread_pool`：L28–L61。
- `configure_cpu_thread_pools`：L64–L122。
- `default_worker_id`：L125–L126。
- `PersistentVisionRunner`：L129–L194；方法：`__init__` L132、`__call__` L163。
- `process_one`：L197–L256。
- `run_worker`：L259–L292。

## src/rallymate_tracking

### src/rallymate_tracking/__init__.py

[打开源码](../../src/rallymate_tracking/__init__.py) · 17 行

直接引用的项目模块：`rallymate_tracking.primary_player`。

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_tracking/primary_player.py

[打开源码](../../src/rallymate_tracking/primary_player.py) · 738 行

直接引用的项目模块：`rallymate_vision.utils`。

- `primary_timeline_algorithm_version`：L41–L83。
- `registry_required_primary_player_version`：L86–L100。
- `_pose_points`：L103–L123。
- `_valid_fraction`：L126–L131。
- `_frame_candidates`：L134–L172。
- `_normalize`：L175–L181。
- `_track_priors`：L184–L226。
- `_transition`：L229–L240。
- `_argmax_first`：L243–L246。
- `_select_segment`：L249–L375。
- `select_primary_player_timeline`：L378–L466。
- `_point`：L469–L473。
- `_body_scale`：L476–L487。
- `_longest_missing`：L490–L504。
- `diagnose_primary_timeline`：L507–L703。
- `build_primary_player_artifacts`：L706–L738。

## src/rallymate_training

### src/rallymate_training/__init__.py

[打开源码](../../src/rallymate_training/__init__.py) · 5 行

Reproducible data preparation, training, evaluation and model registration.

直接引用的项目模块：`rallymate_training.config`。

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_training/cli.py

[打开源码](../../src/rallymate_training/cli.py) · 135 行

直接引用的项目模块：`rallymate_training.config`、`rallymate_training.dataset`、`rallymate_training.engine`、`rallymate_training.registry`。

- `_class_names`：L16–L20。
- `_fingerprint_dataset`：L23–L38。
- `build_parser`：L41–L75。
- `main`：L78–L131。
### src/rallymate_training/config.py

[打开源码](../../src/rallymate_training/config.py) · 112 行

- `TrainingConfigError`：L10–L11。
- `QualityGates`：L15–L18。
- `TrainConfig`：L22–L41。
- `_resolve`：L44–L48。
- `_positive_int`：L51–L55。
- `load_train_config`：L58–L112。
### src/rallymate_training/dataset.py

[打开源码](../../src/rallymate_training/dataset.py) · 336 行

- `DatasetError`：L14–L15。
- `DatasetRow`：L31–L39。
- `_inferred_label_path`：L42–L50。
- `load_manifest`：L53–L115。
- `_assigned_split`：L118–L125。
- `assign_group_splits`：L128–L172。
- `_parse_label_line`：L175–L205。
- `audit_labels`：L208–L250。
- `prepare_dataset`：L253–L336。
### src/rallymate_training/engine.py

[打开源码](../../src/rallymate_training/engine.py) · 119 行

直接引用的项目模块：`rallymate_training.config`。

- `_metric_value`：L10–L16。
- `_extract_metrics`：L19–L38。
- `train`：L41–L70。
- `evaluate`：L73–L94。
- `export_model`：L97–L119。
### src/rallymate_training/mmpose_dataset.py

[打开源码](../../src/rallymate_training/mmpose_dataset.py) · 959 行

直接引用的项目模块：`rallymate_training.pose_finetune_readiness`、`rallymate_vision.pose.metadata`。

- `MMPoseDatasetExportError`：L34–L35。
- `_sha256`：L38–L39。
- `_decoded_pixel_sha256`：L42–L51。
- `_json_bytes`：L54–L64。
- `_read_jsonl`：L67–L82。
- `_read_governance`：L85–L104。
- `_finite_number`：L107–L116。
- `_nonnegative_int`：L119–L130。
- `_positive_int`：L133–L137。
- `_task_bbox`：L140–L155。
- `_manual_point`：L158–L186。
- `_source_videos`：L189–L209。
- `_assert_holdout_and_split_isolation`：L212–L241。
- `_load_supervision`：L244–L364。
- `_extract_video_frames`：L367–L431。
- `_artifact`：L434–L440。
- `export_mmpose_halpe26_dataset`：L443–L952。
### src/rallymate_training/mmpose_finetune.py

[打开源码](../../src/rallymate_training/mmpose_finetune.py) · 1,791 行

Fail-closed MMPose/RTMPose fine-tune planning and execution.

直接引用的项目模块：`rallymate_training.pose_finetune_readiness`。

- `MMPoseFineTuneError`：L160–L172；方法：`__init__` L163。
- `_require_exact_keys`：L175–L177。
- `_workspace_root`：L180–L183。
- `_sha256_file`：L186–L191。
- `_require_sha256`：L194–L200。
- `_read_object`：L203–L210。
- `_read_jsonl`：L213–L233。
- `_path_within`：L236–L256。
- `_bound_external_path`：L259–L275。
- `_read_governance`：L278–L316。
- `_validate_exact_model_inputs`：L319–L350。
- `_int_id`：L353–L356。
- `_positive_int`：L359–L363。
- `_nonempty_ids`：L366–L376。
- `_number`：L379–L385。
- `_decoded_pixel_sha256`：L388–L395。
- `_validate_coco_split`：L398–L732。
- `_artifact_index`：L735–L769。
- `_validate_dataset_manifest`：L772–L1204。
- `_resolved_config_text`：L1207–L1233。
- `_runtime_config_audit`：L1236–L1391。
- `_write_json`：L1394–L1399。
- `_utc_now`：L1402–L1403。
- `_transition_run_record`：L1406–L1445。
- `_validate_execute_environment`：L1448–L1452。
- `prepare_or_run_rtmpose_finetune`：L1455–L1622。
- `_build_mmengine_runner`：L1625–L1695。
- `_run_mmengine`：L1698–L1781。
### src/rallymate_training/pose_finetune_readiness.py

[打开源码](../../src/rallymate_training/pose_finetune_readiness.py) · 1,193 行

直接引用的项目模块：`rallymate_vision.pose.metadata`。

- `PoseFineTuneReadinessError`：L70–L71。
- `_sha256_file`：L74–L79。
- `_canonical_sha256`：L82–L90。
- `load_m95_sealed_holdout_guard`：L93–L136。
- `_read_json`：L139–L143。
- `_read_jsonl`：L146–L163。
- `_read_strict_csv`：L166–L176。
- `_bound_path`：L179–L183。
- `_check_file_binding`：L186–L206。
- `_check_declared_binding`：L209–L223。
- `_parse_source_ids`：L226–L227。
- `_parse_bool`：L230–L238。
- `_normalized_point`：L241–L251。
- `_point_is_valid`：L254–L264。
- `_extract_source_videos`：L267–L292。
- `_audit_source_videos`：L295–L373。
- `_audit_pack`：L376–L766。
- `_audit_governance`：L769–L896。
- `_source_alias_cross_split_issues`：L899–L952。
- `audit_pose_finetune_readiness`：L955–L1193。
### src/rallymate_training/registry.py

[打开源码](../../src/rallymate_training/registry.py) · 97 行

直接引用的项目模块：`rallymate_training.config`。

- `RegistryError`：L13–L14。
- `sha256_file`：L17–L22。
- `gates_passed`：L25–L35。
- `register_model`：L38–L97。

## src/rallymate_vision

### src/rallymate_vision/__init__.py

[打开源码](../../src/rallymate_vision/__init__.py) · 3 行

RallyMate first-stage video perception demo.

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_vision/__main__.py

[打开源码](../../src/rallymate_vision/__main__.py) · 5 行

直接引用的项目模块：`rallymate_vision.cli`。

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_vision/camera_motion.py

[打开源码](../../src/rallymate_vision/camera_motion.py) · 114 行

Background-supported image-plane stabilization, never 3D reconstruction.

- `CameraMotionGuard`：L14–L114；方法：`__init__` L15、`reset_reference` L22、`update` L30。
### src/rallymate_vision/cli.py

[打开源码](../../src/rallymate_vision/cli.py) · 111 行

直接引用的项目模块：`rallymate_scoring.registry_lifecycle`、`rallymate_vision.contracts`、`rallymate_vision.pipeline`。

- `build_parser`：L13–L71。
- `main`：L74–L111。
### src/rallymate_vision/contracts.py

[打开源码](../../src/rallymate_vision/contracts.py) · 357 行

直接引用的项目模块：`rallymate_vision.pose.presets`。

- `ContractError`：L16–L17。
- `ModelConfig`：L21–L34。
- `ProcessingConfig`：L38–L45。
- `CourtConfig`：L49–L56。
- `ScoringConfig`：L60–L63。
- `PipelineRequest`：L67–L76。
- `_resolve`：L79–L85。
- `_number`：L88–L97。
- `_validate_pose_preset_binding`：L100–L136。
- `load_request`：L139–L357。
### src/rallymate_vision/court.py

[打开源码](../../src/rallymate_vision/court.py) · 482 行

直接引用的项目模块：`rallymate_vision.utils`。

- `CourtDetector`：L11–L482；方法：`__init__` L19、`detect` L29、`_detect_lines` L95、`_court_surface_mask` L377、`_empty` L466。
### src/rallymate_vision/inference.py

[打开源码](../../src/rallymate_vision/inference.py) · 264 行

直接引用的项目模块：`rallymate_vision.pose`、`rallymate_vision.pose.metadata`、`rallymate_vision.utils`。

- `Yolo26Perception`：L29–L264；方法：`__init__` L41、`detect` L97、`_decode_detections` L127、`_small_ball_refinement` L172、`_merge_ball_scales` L180、`detection_metadata` L208、`_deduplicate` L218、`estimate_poses` L251、`pose_metadata` L263。
### src/rallymate_vision/pipeline.py

[打开源码](../../src/rallymate_vision/pipeline.py) · 1,232 行

直接引用的项目模块：`rallymate_scoring.calculation_readiness`、`rallymate_scoring.calibration`、`rallymate_scoring.calibration_promotion`、`rallymate_scoring.cycle_measurement`、`rallymate_scoring.granularity`、`rallymate_scoring.loop`、`rallymate_scoring.measurement_portfolio`、`rallymate_scoring.registry_lifecycle`、`rallymate_scoring.report`、`rallymate_scoring.runtime_profile_binding`、`rallymate_scoring.scoring_context`、`rallymate_scoring.scoring_loop_report`、`rallymate_scoring.stroke_candidates`、`rallymate_tracking`、`rallymate_vision.camera_motion`、`rallymate_vision.contracts`、`rallymate_vision.inference`、`rallymate_vision.pose.metadata`、`rallymate_vision.progress`、`rallymate_vision.quality`、`rallymate_vision.render`、`rallymate_vision.source_replay`、`rallymate_vision.timebase`、`rallymate_vision.tracking`、`rallymate_vision.utils`、`rallymate_vision.validation`。

- `_disabled_court_observation`：L75–L87。
- `_find_ffmpeg_executable`：L90–L104。
- `_transcode_annotated_video_for_browser`：L107–L178。
- `_load_runtime_authorization_json`：L181–L191。
- `load_trusted_runtime_binding_registry`：L194–L204。
- `load_runtime_view_evidence`：L207–L213。
- `resolve_scoring_feasibility_registry`：L216–L227。
- `_resolve_scoring_feasibility_registry_authority`：L230–L249。
- `_verify_scoring_registry_authority_unchanged`：L252–L262。
- `load_pipeline_calibrations`：L265–L394。
- `run_pipeline`：L397–L1232。
### src/rallymate_vision/pose/__init__.py

[打开源码](../../src/rallymate_vision/pose/__init__.py) · 25 行

直接引用的项目模块：`rallymate_vision.pose.adapters`、`rallymate_vision.pose.base`、`rallymate_vision.pose.presets`、`rallymate_vision.pose.registry`、`rallymate_vision.pose.rtmpose_backend`、`rallymate_vision.pose.yolo_backend`。

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_vision/pose/adapters.py

[打开源码](../../src/rallymate_vision/pose/adapters.py) · 134 行

直接引用的项目模块：`rallymate_vision.pose.base`、`rallymate_vision.pose.metadata`、`rallymate_vision.utils`。

- `PoseEstimator`：L13–L134；方法：`__init__` L16、`estimate` L31。
### src/rallymate_vision/pose/base.py

[打开源码](../../src/rallymate_vision/pose/base.py) · 61 行

- `PoseModelMetadata`：L10–L30；方法：`to_dict` L27。
- `PoseBackendOutput`：L34–L47；方法：`__post_init__` L41。
- `PoseBackend`：L51–L61；方法：`load` L52、`infer` L54、`metadata` L61。
### src/rallymate_vision/pose/metadata.py

[打开源码](../../src/rallymate_vision/pose/metadata.py) · 127 行

- `_coco_wholebody133_schema`：L13–L87。
- `load_keypoint_schemas`：L91–L107。
- `keypoint_schema`：L110–L114。
- `sha256_file`：L117–L122。
### src/rallymate_vision/pose/presets.py

[打开源码](../../src/rallymate_vision/pose/presets.py) · 111 行

- `PoseDeploymentPreset`：L11–L37；方法：`to_dict` L24。
- `deployment_preset_registry`：L40–L43。
- `default_pose_deployment_preset_id`：L46–L56。
- `_resolve_config_path`：L59–L73。
- `resolve_pose_deployment_preset`：L76–L111。
### src/rallymate_vision/pose/registry.py

[打开源码](../../src/rallymate_vision/pose/registry.py) · 45 行

直接引用的项目模块：`rallymate_vision.pose.base`、`rallymate_vision.pose.rtmpose_backend`、`rallymate_vision.pose.yolo_backend`。

- `build_pose_backend`：L10–L45。
### src/rallymate_vision/pose/rtmpose_backend.py

[打开源码](../../src/rallymate_vision/pose/rtmpose_backend.py) · 172 行

直接引用的项目模块：`rallymate_vision.pose.base`、`rallymate_vision.pose.metadata`。

- `RtmposePoseBackend`：L15–L172；方法：`__init__` L18、`_mmpose_device` L44、`_install_mmcv_lite_compatibility` L55、`load` L64、`infer` L120、`metadata` L152。
### src/rallymate_vision/pose/yolo_backend.py

[打开源码](../../src/rallymate_vision/pose/yolo_backend.py) · 113 行

直接引用的项目模块：`rallymate_vision.pose.base`、`rallymate_vision.pose.metadata`。

- `YoloPoseBackend`：L13–L113；方法：`__init__` L16、`load` L37、`infer` L54、`metadata` L97。
### src/rallymate_vision/pose_playback.py

[打开源码](../../src/rallymate_vision/pose_playback.py) · 270 行

Bounded, observed-only skeleton replay from existing frame artifacts.

直接引用的项目模块：`rallymate_vision.pose.metadata`。

- `PosePlaybackError`：L43–L44。
- `_finite`：L47–L53。
- `_integer`：L56–L57。
- `_strict_object`：L60–L66。
- `_reject_number`：L69–L70。
- `_rows`：L73–L96。
- `_primary_rows`：L99–L106。
- `_keypoints`：L109–L142。
- `_select_pose`：L145–L164。
- `build_pose_playback`：L167–L270。
### src/rallymate_vision/progress.py

[打开源码](../../src/rallymate_vision/progress.py) · 44 行

Work-based progress for the stages after frame inference.

- `postprocess_progress`：L19–L44。
### src/rallymate_vision/quality.py

[打开源码](../../src/rallymate_vision/quality.py) · 104 行

直接引用的项目模块：`rallymate_vision.utils`。

- `VideoMetadata`：L13–L18。
- `probe_video`：L21–L40。
- `analyze_frame`：L43–L63。
- `metadata_warnings`：L65–L74。
- `aggregate_quality`：L77–L104。
### src/rallymate_vision/render.py

[打开源码](../../src/rallymate_vision/render.py) · 116 行

- `annotate_frame`：L29–L116。
### src/rallymate_vision/source_replay.py

[打开源码](../../src/rallymate_vision/source_replay.py) · 51 行

Export selected source frames without flattening their presentation times.

- `export_source_replay`：L9–L51。
### src/rallymate_vision/timebase.py

[打开源码](../../src/rallymate_vision/timebase.py) · 48 行

Source presentation timestamps with explicit, auditable decoder fallback.

- `SourceVideoClock`：L7–L48；方法：`__init__` L8、`resolve` L18、`summary` L40。
### src/rallymate_vision/tracking.py

[打开源码](../../src/rallymate_vision/tracking.py) · 118 行

直接引用的项目模块：`rallymate_vision.utils`。

- `_Track`：L9–L14。
- `SimpleMultiClassTracker`：L17–L118；方法：`__init__` L24、`_miss_limit` L38、`_active` L41、`update` L48。
### src/rallymate_vision/trajectory.py

[打开源码](../../src/rallymate_vision/trajectory.py) · 1,077 行

Read-only motion observability derived from a Stage 1 frame artifact.

- `TrajectoryExtractionError`：L56–L57。
- `_finite`：L60–L64。
- `_positive_int`：L67–L70。
- `_clamp01`：L73–L74。
- `_round`：L77–L78。
- `_pair`：L81–L87。
- `_box`：L90–L99。
- `_sample_evenly`：L102–L110。
- `_mean_confidence`：L113–L119。
- `_confidence_summary`：L122–L135。
- `_longest_missing_run`：L138–L151。
- `_track_id`：L154–L157。
- `_normalised_center`：L160–L187。
- `_normalised_box`：L190–L205。
- `_point_from_detection`：L208–L242。
- `_read_observations`：L245–L373。
- `_select_track`：L376–L408。
- `_track_payload`：L411–L440。
- `_velocity`：L443–L468。
- `_constant_velocity_preview`：L471–L510。
- `_empty_track_payload`：L513–L529。
- `_point_distance`：L532–L533。
- `_segment_gap_ms`：L536–L537。
- `_link_is_plausible`：L540–L546。
- `_clean_track_points`：L549–L578。
- `_endpoint_velocity`：L581–L593。
- `_supported_bridge`：L596–L642。
- `_stitch_fragments`：L645–L731。
- `_interpolated_point`：L734–L763。
- `_build_reconstruction`：L766–L926。
- `build_trajectory_preview`：L929–L1064。
### src/rallymate_vision/utils.py

[打开源码](../../src/rallymate_vision/utils.py) · 98 行

- `clamp`：L8–L9。
- `box_iou`：L12–L21。
- `box_intersection_over_min_area`：L24–L36。
- `box_center`：L39–L40。
- `normalized_center_distance`：L43–L49。
- `normalize_box`：L52–L58。
- `expand_box`：L61–L75。
- `resolve_device`：L78–L87。
- `relative_or_absolute`：L90–L94。
- `safe_float`：L97–L98。
### src/rallymate_vision/validation.py

[打开源码](../../src/rallymate_vision/validation.py) · 1,026 行

直接引用的项目模块：`rallymate_events.schemas`、`rallymate_vision.contracts`、`rallymate_vision.pose.metadata`。

- `OutputValidationError`：L55–L56。
- `_validate_pipeline_derived_report`：L59–L107。
- `_require`：L110–L112。
- `_bounded`：L115–L119。
- `_load_jsonl`：L122–L139。
- `_sha256`：L142–L147。
- `_validate_feature_result`：L150–L191。
- `_compact_feature`：L194–L195。
- `_validate_indicator_quality_gate`：L198–L280。
- `_validate_scoring_artifacts`：L283–L747。
- `validate_frame_observation`：L750–L898。
- `validate_run_artifacts`：L901–L1026。

## src/rallymate_visualization

### src/rallymate_visualization/__init__.py

[打开源码](../../src/rallymate_visualization/__init__.py) · 19 行

Interactive, non-scoring visualizations for RallyMate artifacts.

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_visualization/assets/scoring-observer.css

[打开源码](../../src/rallymate_visualization/assets/scoring-observer.css) · 123 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_visualization/assets/scoring-observer.html

[打开源码](../../src/rallymate_visualization/assets/scoring-observer.html) · 106 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_visualization/assets/scoring-observer.js

[打开源码](../../src/rallymate_visualization/assets/scoring-observer.js) · 304 行

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### src/rallymate_visualization/scoring_observer.py

[打开源码](../../src/rallymate_visualization/scoring_observer.py) · 543 行

直接引用的项目模块：`rallymate_features.event_features`。

- `_sha256`：L32–L37。
- `_is_sha256`：L40–L46。
- `_canonical_sha256`：L49–L57。
- `_read_jsonl`：L60–L70。
- `_round_optional`：L73–L76。
- `_required_joints`：L79–L88。
- `_compact_feature`：L91–L101。
- `_select_pose`：L104–L114。
- `_diagnostics_by_frame`：L117–L134。
- `build_video_observer_payload`：L137–L396。
- `validate_video_observer_payload`：L399–L457。
- `build_observer_manifest`：L460–L501。
- `validate_observer_manifest`：L504–L543。

## tests

### tests/__init__.py

[打开源码](../../tests/__init__.py) · 5 行

RallyMate test package.

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
### tests/fixtures/legacy-web-v1/footwork-review.ts

[打开源码](../../tests/fixtures/legacy-web-v1/footwork-review.ts) · 28 行

- `footworkEpisodes`：L4。
- `footworkFeatureText`：L20。
- `footworkScoringStatus`：L26。
### tests/fixtures/legacy-web-v1/motion-analysis.ts

[打开源码](../../tests/fixtures/legacy-web-v1/motion-analysis.ts) · 84 行

- `motionAnalysisOf`：L8。
- `rotationAnalysisOf`：L63。
- `motionMetricText`：L82。
### tests/helpers/legacy_report_consumer.mjs

[打开源码](../../tests/helpers/legacy_report_consumer.mjs) · 23 行

- `load`：L7。
### tests/test_analysis_report.py

[打开源码](../../tests/test_analysis_report.py) · 152 行

直接引用的项目模块：`rallymate_service.analysis_report`。

- `AnalysisReportTests`：L9–L152；方法：`fixture` L10、`test_independent_measurements_survive_unknown_class_and_partial_vector` L24、`test_empty_evidence_does_not_generate_score_or_diagnosis` L39、`test_no_candidate_report_uses_existing_counts_without_claiming_continuity` L45、`test_no_candidate_diagnostics_reject_inconsistent_or_invalid_counts` L61、`test_existing_candidates_do_not_use_no_candidate_diagnostics` L72、`test_score_requires_explicit_semantics_and_availability` L77、`test_local_window_not_counted_as_full_episode_measurement` L85、`test_unavailable_or_out_of_episode_local_windows_do_not_create_review_evidence` L102、`test_invalid_numbers_or_status_cannot_revive_missing_measurement` L118、`test_truncation_is_explicit_and_unscoped_intervals_do_not_link` L127、`test_legacy_features_still_link_to_replay` L135、`test_return_projection_cannot_double_count_the_same_motion_measurements` L144。
### tests/test_annotated_video_codec.py

[打开源码](../../tests/test_annotated_video_codec.py) · 64 行

直接引用的项目模块：`rallymate_vision.pipeline`。

- `AnnotatedVideoCodecTests`：L10–L60；方法：`test_missing_ffmpeg_keeps_opencv_file_and_reports_fallback` L11、`test_successful_transcode_replaces_file_atomically_with_h264` L21、`test_transcode_timeout_preserves_original_and_cleans_temp` L43。
### tests/test_ball_refinement.py

[打开源码](../../tests/test_ball_refinement.py) · 86 行

直接引用的项目模块：`rallymate_vision.inference`。

- `Array`：L8–L12；方法：`__init__` L9、`detach` L10、`cpu` L11、`tolist` L12。
- `result`：L15–L17。
- `BallRefinementTests`：L20–L83；方法：`perception` L21、`test_hd_missing_ball_is_recovered_without_changing_player_or_racket` L32、`test_multiscale_same_ball_is_counted_once_and_separate_balls_remain` L43、`test_low_resolution_and_already_high_resolution_use_one_pass` L52、`test_refinement_can_be_disabled_and_handles_empty_base_output` L60、`test_refinement_excludes_large_signs_but_keeps_motion_blurred_small_ball` L69、`test_empty_refinement_preserves_adjacent_base_detections` L77、`test_nested_boxes_with_different_scales_are_not_forced_into_one_ball` L81。
### tests/test_batch_scoring.py

[打开源码](../../tests/test_batch_scoring.py) · 281 行

直接引用的项目模块：`rallymate_scoring.batch`、`rallymate_scoring.quality_policy`。

- `_record`：L10–L41。
- `_calibration`：L44–L68。
- `_runtime_bound_record`：L71–L99。
- `_authoritative_event`：L102–L116。
- `BatchScoringTests`：L119–L277；方法：`test_non_target_direction_indicator_rejects_scoring_context` L120、`test_pending_asset_stays_calibration_required` L130、`test_self_declared_passed_asset_cannot_score_without_trusted_loader` L140、`test_invalid_feature_is_unavailable_even_with_approved_asset` L158、`test_context_scoring_feature_cannot_be_dropped_during_rescoring` L167、`test_quality_block_is_preserved_during_batch_rescoring` L193、`test_stale_quality_policy_cannot_be_rescored_as_current` L208、`test_tampered_or_missing_quality_gate_is_rejected` L218、`test_runtime_bound_production_recomputes_gate_from_event` L232、`test_runtime_bound_production_requires_authoritative_sources` L248、`test_runtime_bound_production_exactly_matches_event_identity` L259。
### tests/test_blocker_taxonomy.py

[打开源码](../../tests/test_blocker_taxonomy.py) · 145 行

直接引用的项目模块：`rallymate_scoring.blocker_taxonomy`。

- `_jsonl`：L30–L35。
- `ScoringBlockerTaxonomyTests`：L38–L141；方法：`test_checked_in_machine_taxonomy_matches_the_python_source` L39、`test_each_known_flag_has_one_typed_reason_truth_requirement_and_group` L49、`test_jump_target_and_identity_reasons_cannot_be_conflated` L85、`test_phase_feedback_precedes_lead_foot_feedback_for_compatibility` L98、`test_all_real_three_video_score_blocks_are_exactly_typed` L115。
### tests/test_blocker_taxonomy_audit.py

[打开源码](../../tests/test_blocker_taxonomy_audit.py) · 67 行

直接引用的项目模块：`rallymate_scoring.blocker_taxonomy_audit`。

- `BlockerTaxonomyAuditTests`：L20–L63；方法：`setUpClass` L22、`test_real_three_video_replay_preserves_score_semantics` L41、`test_checked_in_audit_replays_exactly_except_timestamp` L52、`test_invalid_safety_claim_is_rejected` L59。
### tests/test_calculation_readiness.py

[打开源码](../../tests/test_calculation_readiness.py) · 187 行

直接引用的项目模块：`rallymate_scoring.calculation_readiness`。

- `_jsonl`：L23–L24。
- `_sources`：L27–L31。
- `CalculationReadinessTests`：L34–L183；方法：`test_missing_event_and_measured_indicator_are_not_conflated` L35、`test_validator_rejects_fake_completeness_or_gate_relaxation` L104、`_real_report` L121、`test_real_default_smoke_reports_all_13_measured_indicators` L133、`test_real_full_video_has_effectively_calculated_all_13` L155、`test_summary_schema_accepts_registry_driven_13_indicator_run` L166。
### tests/test_calibrated_scoring_cli.py

[打开源码](../../tests/test_calibrated_scoring_cli.py) · 741 行

直接引用的项目模块：`rallymate_scoring.quality_policy`、`rallymate_scoring.registry_lifecycle`、`rallymate_scoring.run_bundle_binding`、`rallymate_vision.pose.metadata`。

- `_lifecycle_runtime_fixture`：L22–L35。
- `CalibratedScoringCliTests`：L38–L737；方法：`_write_registry_lifecycle` L39、`_inputs` L98、`_production_command` L213、`test_cli_requires_trusted_ledger_for_production_asset` L324、`test_registry_path_pin_rejects_non_runtime_artifacts_without_outputs` L423、`test_operator_authorized_bundle_requires_current_lifecycle_provenance` L462、`test_production_rejects_deleted_event_quality_flag_without_outputs` L512、`test_production_rejects_missing_or_mismatched_artifact_binding` L536、`test_production_requires_operator_run_bundle_ledger` L574、`test_coordinated_bundle_rewrite_fails_external_trust_root` L600、`test_summary_raw_file_bytes_not_only_canonical_json_are_bound` L644、`test_ledger_authorized_summary_video_id_must_match_view` L667、`test_outputs_cannot_overwrite_bundle_or_trust_inputs` L692。
### tests/test_calibration.py

[打开源码](../../tests/test_calibration.py) · 344 行

直接引用的项目模块：`rallymate_scoring.calibration`、`rallymate_scoring.scoring`。

- `_feature`：L13–L23。
- `_test_only_evidence`：L34–L43。
- `_production_evidence`：L46–L56。
- `_threshold_calibration`：L59–L79。
- `CalibrationTests`：L82–L340；方法：`test_missing_calibration_never_generates_grade` L83、`test_invalid_feature_returns_unavailable_before_calibration` L95、`test_explicit_test_only_coach_calibration_can_score_threshold_backend` L107、`test_test_only_calibration_cannot_score_without_explicit_override` L121、`test_pending_independent_test_cannot_score` L134、`test_self_declared_production_calibration_cannot_score_without_ledger` L149、`test_independently_approved_asset_cannot_skip_F2_to_scored` L167、`test_thresholds_must_be_strictly_increasing` L182、`test_threshold_asset_is_bound_to_feature_version` L189、`test_boolean_threshold_is_not_accepted_as_number` L202、`test_passed_independent_test_requires_valid_report_hash` L208、`test_ordinal_backend_outputs_probability_backed_grade` L216、`test_ordinal_model_rejects_non_finite_coefficients` L248、`test_ordinal_asset_is_bound_to_feature_unit_and_version` L268、`test_multi_coach_agreement_supports_grades_and_rankings` L306。
### tests/test_calibration_all13_contract.py

[打开源码](../../tests/test_calibration_all13_contract.py) · 625 行

直接引用的项目模块：`rallymate_annotation.truth_pack`、`rallymate_features.event_features`、`rallymate_scoring.calibration_fitting`、`rallymate_scoring.calibration_independent_test`、`rallymate_scoring.feasibility`、`rallymate_scoring.indicator_requirements`、`rallymate_scoring.scoring_context`。

- `_synthetic_value`：L56–L59。
- `_fit_protocol`：L62–L116。
- `_independent_protocol`：L119–L150。
- `_grade_labels`：L153–L168。
- `_independent_samples`：L171–L313。
- `_prepared_dataset`：L316–L438。
- `CalibrationAll13ContractTests`：L441–L621；方法：`setUpClass` L443、`test_authoritative_requirement_sets_exactly_cover_all_13` L471、`test_fit_protocol_requires_and_binds_one_indicator` L499、`test_all13_test_only_fit_predict_and_sealed_evaluation_portfolio` L540。
### tests/test_calibration_dataset.py

[打开源码](../../tests/test_calibration_dataset.py) · 1,165 行

直接引用的项目模块：`rallymate_annotation.scoring_truth_intake`、`rallymate_scoring`、`rallymate_scoring.calibration_dataset`、`rallymate_scoring.calibration_fitting`、`rallymate_scoring.indicator_feature_qualification`、`rallymate_scoring.quality_policy`、`rallymate_scoring.run_bundle_binding`、`rallymate_scoring.scoring_truth_calibration_authorization`。

- `_write_json`：L47–L48。
- `_write_jsonl`：L51–L52。
- `CalibrationDatasetTests`：L55–L1161；方法：`test_indicator_feature_qualification_api_is_public` L56、`_fixture` L69、`_compile` L258、`test_exact_join_unanimous_labels_and_sealed_test` L274、`test_conflicting_coaches_are_not_silently_resolved` L332、`test_ranking_only_never_claims_ordinary_A_E_readiness` L353、`test_candidate_event_id_is_never_fuzzy_joined_to_manual_truth` L387、`test_connected_player_component_cannot_cross_splits` L403、`test_feature_status_and_quality_hard_fail_block_modeling_even_with_numeric_values` L421、`test_canonical_hash_is_key_order_independent_and_value_sensitive` L446、`test_output_is_immutable_and_existing_target_is_refused` L450、`test_input_contract_failure_leaves_no_partial_dataset` L465、`test_contracts_and_templates_match_versioned_python_inputs` L476、`test_real_blank_pack_emits_dynamic_13_indicator_annotation_required` L501、`test_valid_private_m89_intake_cannot_mint_real_calibration_input` L539、`test_caller_selected_manual_truth_is_diagnostic_without_live_authority` L770、`test_authorization_bound_snapshot_change_aborts_before_atomic_commit` L803、`test_private_test_issuer_exercises_real_scope_content_binding` L827。
### tests/test_calibration_fitting.py

[打开源码](../../tests/test_calibration_fitting.py) · 476 行

直接引用的项目模块：`rallymate_scoring.calibration_fitting`、`rallymate_scoring.scoring`、`rallymate_scoring.scoring_truth_calibration_authorization`。

- `_dataset`：L31–L128。
- `_refresh_integrity`：L131–L148。
- `_protocol`：L151–L204。
- `CalibrationFittingTests`：L207–L472；方法：`test_empty_not_ready_package_preserves_null_feature_contract_without_fitting` L208、`test_independent_test_seal_is_order_invariant_but_content_sensitive` L243、`test_fits_both_non_production_candidates_without_opening_test_seal` L252、`test_serialized_unverified_real_input_cannot_create_candidate` L275、`test_private_test_issuer_exercises_real_fit_binding_replay` L286、`test_candidate_scope_cannot_be_flipped_from_synthetic_to_real` L306、`test_candidate_semantics_must_match_both_hashed_input_provenances` L314、`test_candidate_requires_scope_specific_provenance_identifiers` L320、`test_candidate_schema_has_scope_specific_provenance_requirements` L344、`test_protocol_numbers_are_external_gates_not_internal_defaults` L363、`test_missing_grade_is_mathematical_hard_failure_even_if_protocol_min_is_zero` L369、`test_majority_or_implicit_consensus_is_never_accepted` L383、`test_independent_test_rows_are_rejected_from_prepared_input` L389、`test_group_and_component_leakage_is_rejected` L399、`test_candidate_validator_rejects_test_performance_or_promotion_claim` L405、`test_candidate_cannot_be_passed_to_production_scoring_contract` L415、`test_cli_gate_failure_creates_no_output_directory_or_asset` L440。
### tests/test_calibration_independent_test.py

[打开源码](../../tests/test_calibration_independent_test.py) · 1,141 行

直接引用的项目模块：`rallymate_scoring.calibration_fitting`、`rallymate_scoring.calibration_independent_test`、`rallymate_scoring.indicator_feature_qualification`、`rallymate_scoring.quality_policy`、`rallymate_scoring.scoring_truth_calibration_authorization`。

- `_samples`：L35–L191。
- `_candidate`：L194–L269。
- `_protocol`：L272–L296。
- `_requirements`：L299–L326。
- `_external_samples`：L329–L349。
- `_external_candidate`：L352–L363。
- `_external_protocol`：L366–L369。
- `CalibrationIndependentTestTests`：L372–L1137；方法：`test_real_sample_requires_v11_verified_qualification_snapshot` L373、`test_real_sample_missing_quality_gate_is_recomputed_as_ineligible` L413、`test_qualification_record_sha_and_ledger_digest_are_required` L447、`test_resolved_target_direction_removes_only_its_saved_scoring_block` L477、`test_sealed_test_passes_explicit_protocol_without_approving_scoring` L520、`test_full_report_reconciliation_rejects_internal_or_protocol_drift` L546、`test_new_coverage_contract_rejects_legacy_fields_and_rate_drift` L603、`test_complete_eligible_coverage_is_required_for_production_validation` L649、`test_candidate_error_for_eligible_sample_never_becomes_an_exclusion` L735、`test_tampered_sealed_sample_is_rejected_before_metrics` L752、`test_protocol_limits_are_external_and_failure_stays_unapproved` L764、`test_quality_blocked_numeric_feature_never_enters_test_metrics` L779、`test_protocol_label_semantics_must_match_candidate` L859、`test_unanimous_summary_cannot_replace_missing_raw_coach_labels` L873、`test_two_grade_records_from_one_coach_cannot_fake_unique_coaches` L885、`test_forged_contributing_ids_are_recomputed_from_raw_labels` L899、`test_external_adjudication_binds_every_raw_source_label` L915、`test_valid_seal_does_not_bypass_complete_sample_contract` L945、`test_missing_phase_cannot_be_resealed_as_evaluation_eligible` L958、`test_missing_semantic_cannot_be_resealed_as_evaluation_eligible` L978、`test_resealed_sample_cannot_shrink_authoritative_requirements` L998、`test_missing_candidate_provenance_id_is_controlled_rejection` L1025、`test_protocol_template_cannot_be_mistaken_for_registered_limits` L1039、`test_cli_refuses_to_overwrite_report` L1056、`test_cli_missing_candidate_provenance_is_clean_rejection` L1094。
### tests/test_calibration_measurement_sources.py

[打开源码](../../tests/test_calibration_measurement_sources.py) · 89 行

直接引用的项目模块：`rallymate_scoring.calibration_measurement_sources`。

- `CalibrationMeasurementSourcesTests`：L22–L85；方法：`setUpClass` L24、`test_published_sources_cover_all_truth_videos_with_one_current_pose_contract` L29、`test_source_specs_must_exactly_cover_truth_manifest` L53、`test_stale_primary_version_and_unsafe_claim_fail_closed` L64、`test_machine_schema_accepts_published_manifest` L74。
### tests/test_calibration_promotion.py

[打开源码](../../tests/test_calibration_promotion.py) · 2,185 行

直接引用的项目模块：`rallymate_scoring.calibration`、`rallymate_scoring.calibration_fitting`、`rallymate_scoring.calibration_independent_test`、`rallymate_scoring.calibration_promotion`、`rallymate_scoring.maturity_evidence`、`rallymate_scoring.scoring`、`rallymate_scoring.scoring_truth_calibration_authorization`。

- `_truth_authorization_fixture`：L59–L123。
- `_verified_truth_authorization`：L126–L127。
- `_candidate`：L130–L206。
- `_ordinal_candidate`：L209–L235。
- `_independent_test_protocol`：L238–L273。
- `_production_replay_fixture`：L276–L343。
- `_report`：L346–L474。
- `_registry`：L477–L511。
- `_write_registry_lifecycle`：L514–L571。
- `_decision`：L574–L660。
- `_maturity_evidence`：L663–L753。
- `CalibrationPromotionTests`：L756–L2181；方法：`test_human_decision_template_is_not_a_valid_approval_record` L757、`test_synthetic_candidate_creates_valid_test_only_asset` L767、`test_synthetic_candidate_rejects_production_only_replay_inputs` L795、`test_ordinal_candidate_parameters_are_copied_to_test_only_asset` L819、`test_real_candidate_at_F2_is_rejected_before_asset` L839、`test_real_candidate_at_F4_creates_production_asset` L856、`test_production_persists_exact_file_input_snapshot_bindings` L911、`test_production_requires_replay_sources_and_rejects_coordinated_exclusion` L997、`test_production_rejects_missing_protocol_or_unreconciled_report` L1103、`test_production_rejects_incomplete_or_zero_eligible_coverage` L1183、`test_serialized_or_drifted_truth_binding_cannot_promote_or_build_ledger` L1234、`test_production_requires_complete_maturity_evidence` L1314、`test_maturity_features_and_report_registry_binding_cannot_be_forged` L1331、`test_ledger_and_registry_authorization_still_requires_runtime_profile` L1381、`test_failed_or_preapproved_report_is_rejected` L1526、`test_tampered_candidate_report_or_registry_lineage_is_rejected` L1547、`test_decision_time_order_and_attestations_are_enforced` L1577、`test_cli_production_stays_closed_without_live_truth_authority` L1602、`test_cli_object_inputs_reject_ambiguous_json_before_write` L1721、`test_object_snapshot_hashes_exact_bytes_and_canonical_payload` L1802、`test_cli_replay_inputs_reject_duplicate_keys_and_nonfinite_constants_before_write` L1821、`test_cli_rechecks_every_production_input_before_any_output_write` L1905、`test_cli_rechecks_synthetic_inputs_before_test_only_output` L2031、`test_cli_refuses_overwrite_and_F2_rejection_leaves_no_files` L2113。
### tests/test_calibration_ranking.py

[打开源码](../../tests/test_calibration_ranking.py) · 593 行

直接引用的项目模块：`rallymate_scoring.calibration_fitting`、`rallymate_scoring.calibration_ranking`。

- `_label`：L41–L52。
- `_sample`：L55–L101。
- `_samples`：L104–L115。
- `_dataset`：L118–L126。
- `_protocol`：L129–L173。
- `_candidate`：L176–L180。
- `_test_protocol`：L183–L214。
- `RankingCalibrationTests`：L217–L589；方法：`test_full_path_is_relative_only_and_seals_test` L218、`test_grade_labels_are_rejected_not_coerced_to_rank` L283、`test_rank_group_must_not_cross_splits` L300、`test_leakage_group_must_not_cross_splits` L308、`test_same_player_cannot_cross_splits_even_if_group_ids_are_forged` L314、`test_dataset_tampering_is_detected` L320、`test_protocol_must_bind_exact_dataset` L326、`test_candidate_rejects_grade_parameters` L333、`test_ranking_candidate_is_rejected_by_absolute_grade_candidate_loader` L339、`test_test_labels_and_protocol_seal_cannot_change` L344、`test_json_schemas_accept_artifacts_and_forbid_candidate_cutpoints` L359、`test_cli_fit_emits_relative_candidate_only` L389、`test_all_three_clis_run_end_to_end_without_A_E_output` L420、`test_without_ranking_truth_fit_leaves_no_candidate` L508、`test_fit_cli_protocol_gate_failure_leaves_no_candidate` L562。
### tests/test_camera_compensated_coordinates.py

[打开源码](../../tests/test_camera_compensated_coordinates.py) · 123 行

直接引用的项目模块：`rallymate_evaluation.ground_truth`、`rallymate_features.coordinates`、`rallymate_features.event_features`、`rallymate_features.schemas`。

- `_camera_records`：L16–L34。
- `CameraCompensatedCoordinateTests`：L37–L119；方法：`tearDown` L38、`test_pan_rotation_and_scale_preserve_reference_geometry_and_motion` L41、`test_invalid_declared_transforms_fail_closed_without_using_raw_points` L59、`test_valid_reference_points_are_not_clamped_to_current_frame` L77、`test_manual_annotations_receive_exactly_the_same_reference_transform` L85、`test_epoch_change_splits_features_even_without_an_unavailable_frame` L103、`test_transform_scale_bounds_accept_valid_similarity_endpoints` L116。
### tests/test_camera_compensated_strokes.py

[打开源码](../../tests/test_camera_compensated_strokes.py) · 171 行

直接引用的项目模块：`rallymate_events.rules`、`rallymate_scoring.stroke_candidates`、`rallymate_vision.camera_motion`。

- `_similarity`：L19–L21。
- `_camera_frame`：L24–L38。
- `CameraCompensatedStrokeTests`：L41–L167；方法：`test_similarity_compensation_preserves_pose_and_raw_frame_racket_association` L42、`test_service_candidate_path_retains_swing_and_serve_under_pan_rotation_and_zoom` L59、`test_invalid_declared_transform_breaks_the_selection_epoch` L81、`test_reference_epoch_change_breaks_motion_even_without_missing_frames` L103、`test_ball_context_and_compensated_player_use_the_same_reference_coordinates` L110、`test_optical_flow_accumulation_maps_current_pixels_back_to_the_original_reference` L123、`test_fallback_reference_reset_never_reuses_old_camera_coordinates_or_epoch` L139、`test_epoch_only_optional_status_sequence_splits_without_crossing_reference_change` L159。
### tests/test_camera_guard_event_boundaries.py

[打开源码](../../tests/test_camera_guard_event_boundaries.py) · 156 行

直接引用的项目模块：`rallymate_events.rules`、`rallymate_features.event_features`、`rallymate_features.schemas`。

- `CameraGuardEventBoundaryTests`：L17–L152；方法：`tearDown` L18、`test_recursive_windows_preserve_global_evidence_indexes_and_unique_ids` L21、`test_feature_smoothing_cannot_read_across_camera_cut` L52、`test_manual_event_crossing_camera_cut_is_rejected_in_full` L76、`test_manual_event_builder_cannot_bypass_camera_gate` L89、`test_camera_break_prevents_other_segment_position_from_changing_event` L111、`test_blocked_preparation_cannot_supply_phases_outside_clipped_event` L128、`test_sparse_threshold_window_excludes_first_sample_beyond_radius` L146。
### tests/test_contracts.py

[打开源码](../../tests/test_contracts.py) · 277 行

直接引用的项目模块：`rallymate_vision.contracts`、`rallymate_vision.pose.presets`。

- `ContractTests`：L12–L273；方法：`test_relative_paths_resolve_from_request` L13、`test_duplicate_calibration_asset_paths_are_rejected` L57、`test_scoring_must_be_an_object` L77、`test_job_request_cannot_select_registry_lifecycle_authority` L95、`test_pose_backend_configuration_is_validated` L115、`test_unknown_pose_deployment_preset_is_rejected` L147、`test_pose_preset_rejects_every_mismatched_provenance_field` L168、`test_manual_court_requires_four_normalized_points` L215、`test_confidence_above_one_is_rejected` L236、`test_invalid_court_refresh_policy_is_rejected` L257。
### tests/test_court.py

[打开源码](../../tests/test_court.py) · 121 行

直接引用的项目模块：`rallymate_vision.court`。

- `CourtDetectorTests`：L11–L117；方法：`test_manual_polygon_maps_to_pixels` L12、`test_disabled_detector_is_explicit` L29、`test_manual_polygon_is_calibration_usable` L36、`test_manual_visible_region_is_not_metric_calibration` L69、`test_close_player_prevents_auto_geometry_promotion` L87、`test_tall_scene_support_is_not_used_as_court_boundary` L102。
### tests/test_cycle_measurement.py

[打开源码](../../tests/test_cycle_measurement.py) · 170 行

直接引用的项目模块：`rallymate_scoring.cycle_measurement`、`rallymate_vision.validation`。

- `_jsonl`：L28–L33。
- `_sources`：L36–L40。
- `CycleMeasurementTests`：L43–L166；方法：`setUpClass` L45、`_build` L50、`test_real_full_video_has_25_coherent_complete_cycles` L60、`test_short_video_has_one_complete_cycle_without_cross_cycle_mixing` L74、`test_linking_requires_exact_shared_phase_not_event_id_pattern` L82、`test_selection_is_invariant_to_feature_values_and_validator_rejects_claims` L97、`test_run_validator_rejects_stale_cycle_report_source_binding` L143、`test_schema_accepts_materialized_real_reports` L151。
### tests/test_default_pose_routing.py

[打开源码](../../tests/test_default_pose_routing.py) · 126 行

直接引用的项目模块：`rallymate_evaluation.default_pose_routing`。

- `_read`：L20–L21。
- `_sources`：L24–L35。
- `DefaultPoseRoutingAuditTests`：L38–L122；方法：`setUpClass` L40、`_build` L58、`test_real_default_worker_and_full_video_cover_all_13` L66、`test_rejects_yolo_default_or_explicit_smoke_override` L75、`test_rejects_weight_drift_or_missing_indicator_coverage` L95、`test_real_audit_file_validates` L117。
### tests/test_diagnostic_policy_review.py

[打开源码](../../tests/test_diagnostic_policy_review.py) · 319 行

直接引用的项目模块：`rallymate_evaluation.pose_diagnostic_truth`、`rallymate_scoring.diagnostic_policy_review`。

- `DiagnosticPolicyReviewTests`：L31–L315；方法：`_fixture` L32、`_protocol` L123、`test_real_blank_bundles_require_annotation_and_external_protocol` L183、`test_complete_preregistered_evidence_is_only_eligible_for_human_review` L210、`test_failed_metric_stays_fail_closed` L234、`test_protocol_must_predate_results_and_template_is_not_executable` L252、`test_evaluation_is_recomputed_from_bound_csv_before_policy_review` L275、`test_cli_emits_safe_real_empty_review_and_refuses_overwrite` L288。
### tests/test_dynamic_pose_wave_scripts.py

[打开源码](../../tests/test_dynamic_pose_wave_scripts.py) · 683 行

- `_load_script`：L13–L20。
- `DynamicPoseWaveScriptTests`：L23–L679；方法：`setUpClass` L25、`test_runner_scope_uses_registry_indicator_and_event_sets` L30、`test_same_window_comparison_accepts_any_shared_nonempty_set` L49、`test_same_window_comparison_aggregates_typed_score_block_reasons` L68、`test_report_summary_renders_actual_count_and_safe_statuses` L96、`test_report_scopes_are_derived_from_actual_model_rows` L136、`test_scripts_do_not_assert_a_literal_eight_indicator_scope` L163、`test_report_does_not_count_ledger_template_as_production_asset` L174、`test_event_disagreement_summary_rejects_accuracy_claim` L213、`test_fixed_boundary_summary_counts_actual_pair_states` L235、`test_pose_profile_routing_summary_rejects_accuracy_and_cherry_picking` L260、`test_scoring_blocker_summary_separates_measurement_from_scoring` L291、`test_truth_priority_worklist_stays_annotation_only` L321、`test_feature_gap_summary_forbids_automatic_profile_fallback` L347、`test_small_roi_summary_is_experimental_and_not_accuracy` L376、`test_small_roi_truth_summary_withholds_metrics_until_independent_truth` L429、`test_residual_computability_summary_proves_all_indicators_without_zero_fill` L481、`test_deployment_smoke_summary_is_registry_derived_and_safe` L547、`test_pose_diagnostic_truth_summary_rejects_fake_truth` L580、`test_multivideo_readiness_summary_separates_calculation_and_scoring` L611、`test_pose_diagnostic_policy_review_summary_is_fail_closed` L633。
### tests/test_event_bounded_pose_gaps.py

[打开源码](../../tests/test_event_bounded_pose_gaps.py) · 79 行

直接引用的项目模块：`rallymate_evaluation.event_bounded_pose_gaps`、`rallymate_features.schemas`。

- `_sequence`：L13–L25。
- `EventBoundedPoseGapTests`：L28–L79；方法：`test_only_short_internal_gap_is_interpolated_with_timestamp` L29、`test_leading_and_trailing_gaps_never_extrapolate` L46、`test_missing_topology_is_reported_not_created` L59、`test_invalid_contracts_fail_closed` L68。
### tests/test_event_disagreement.py

[打开源码](../../tests/test_event_disagreement.py) · 169 行

直接引用的项目模块：`rallymate_events.disagreement`。

- `_event`：L18–L25。
- `_write_jsonl`：L28–L32。
- `EventDisagreementTests`：L35–L165；方法：`test_ordered_dp_prioritises_global_match_count_over_one_high_iou_pair` L36、`test_matching_never_crosses_event_code_or_track` L70、`test_validator_rejects_an_accuracy_claim` L96、`test_machine_readable_schema_locks_safety_and_hash_fields` L108、`test_cli_hashes_raw_jsonl_and_emits_sensitivity_results` L125。
### tests/test_event_phase_candidates.py

[打开源码](../../tests/test_event_phase_candidates.py) · 373 行

直接引用的项目模块：`rallymate_events`、`rallymate_events.rules`、`rallymate_features.schemas`。

- `_smooth_step`：L33–L39。
- `_irregular_phase_sequence`：L42–L105。
- `EventPhaseCandidateTests`：L108–L369；方法：`test_near_equal_foot_peaks_keep_an_ambiguous_measurable_proxy` L109、`test_right_censored_activity_uses_observed_peak_only` L130、`test_two_sample_tail_uses_peak_without_filling_missing_values` L152、`test_right_boundary_speed_peak_requires_observed_post_event_decrease` L183、`test_irregular_timestamps_produce_explainable_FS01_FS02_proxies` L248、`test_missing_ankles_emit_null_phases_and_traceable_flags` L341。
### tests/test_events.py

[打开源码](../../tests/test_events.py) · 546 行

直接引用的项目模块：`rallymate_events`、`rallymate_events.rules`、`rallymate_features.schemas`、`rallymate_scoring.indicator_requirements`。

- `_motion_sequence`：L35–L65。
- `_static_sequence`：L68–L80。
- `_common_mode_jitter_sequence`：L83–L96。
- `_modest_coherent_motion_sequence`：L99–L109。
- `EventLayerTests`：L112–L542；方法：`test_pose_kinematic_coverage_is_scoped_to_each_emitted_event` L113、`test_v04x_event_provenance_contract_rejects_tampering` L135、`test_static_pose_does_not_manufacture_motion_events` L170、`test_missing_shoulders_use_direct_hip_motion_for_event_candidates` L174、`test_static_hip_only_pose_does_not_manufacture_motion_events` L188、`test_aligned_hip_fallback_does_not_create_a_reference_jump` L195、`test_low_amplitude_common_mode_jitter_is_not_a_motion_event` L214、`test_noise_guard_preserves_modest_coherent_motion_candidate` L220、`test_rule_baseline_outputs_fs01_fs02_fs09_with_uncertainty` L239、`test_detector_emits_every_registry_required_phase_key` L269、`test_event_metrics_compute_f1_iou_and_boundary_mae` L290、`test_no_ground_truth_is_not_mislabeled_as_perfect` L356、`test_global_matcher_prioritises_match_count_over_one_high_iou_pair` L380、`test_matching_is_stable_when_equal_iou_inputs_are_reordered` L410、`test_global_matcher_maximises_total_iou_after_cardinality` L439、`test_matching_never_crosses_video_event_code_or_track` L465、`test_provenance_source_id_resolves_prediction_video_identity` L496、`test_top_level_video_id_joins_truth_when_run_source_id_is_qualified` L514、`test_unscoped_prediction_is_rejected_from_truth_evaluation` L532。
### tests/test_extensible_scoring_contracts.py

[打开源码](../../tests/test_extensible_scoring_contracts.py) · 51 行

- `ExtensibleScoringContractTests`：L11–L47；方法：`test_indicator_feature_contract_is_registry_driven_not_six_id_enum` L12、`test_real_pose_wave_output_declares_all_registry_indicators` L23。
### tests/test_f2_error_budget_readiness.py

[打开源码](../../tests/test_f2_error_budget_readiness.py) · 132 行

直接引用的项目模块：`rallymate_evaluation.f2_error_budget_readiness`。

- `F2ErrorBudgetReadinessTests`：L20–L128；方法：`setUpClass` L22、`test_real_report_replays_current_registry_and_three_video_sources` L25、`test_every_indicator_separates_smoothing_from_missing_truth` L35、`test_empty_truth_cannot_be_relabelled_as_accuracy_or_promotion` L65、`test_machine_schema_accepts_real_report` L80、`test_cli_refuses_to_overwrite_current_report` L93。
### tests/test_feasibility.py

[打开源码](../../tests/test_feasibility.py) · 112 行

直接引用的项目模块：`rallymate_scoring.feasibility`。

- `FeasibilityRegistryTests`：L16–L108；方法：`test_six_target_indicators_have_f0_to_f4_contract_without_thresholds` L17、`test_F2_cannot_skip_directly_to_F4` L36、`test_F3_requires_truth_calibration_and_review_evidence` L50、`test_F4_requires_traceable_independent_test_approval` L84。
### tests/test_feasibility_v2.py

[打开源码](../../tests/test_feasibility_v2.py) · 139 行

直接引用的项目模块：`rallymate_features.event_features`、`rallymate_scoring.feasibility`、`rallymate_scoring.scoring_context`。

- `FeasibilityRegistryV2Tests`：L23–L135；方法：`_load_temporary` L24、`test_v1_contract_remains_exactly_the_original_six` L32、`test_pose_wave_v2_contains_current_thirteen_indicator_event_scope` L40、`test_expanded_metrics_match_implemented_feature_contract` L51、`test_v2_accepts_an_arbitrary_non_duplicate_indicator_set` L79、`test_v2_rejects_duplicate_indicator_ids` L91、`test_v2_rejects_stale_scope_count_and_feature_partition` L101、`test_v1_still_rejects_expanded_indicator_sets` L118、`test_v2_schema_declares_extensible_non_empty_indicator_array` L126。
### tests/test_feature_error_smoothing.py

[打开源码](../../tests/test_feature_error_smoothing.py) · 1,163 行

直接引用的项目模块：`rallymate_evaluation.feature_errors`、`rallymate_features.fs01_fs02_features`、`rallymate_features.schemas`。

- `_series`：L22–L38。
- `_vector_series`：L41–L55。
- `_result`：L58–L79。
- `SmoothingCounterfactualPureTests`：L82–L1055；方法：`test_fs01_m04_family_replays_shared_raw_slowdown_anchor` L83、`test_fs01_m04_replay_rejects_missing_anchor_position` L169、`test_fs02_m04_family_replays_one_shared_launch_kinematic_contract` L201、`test_fs02_m04_replay_rejects_missing_velocity_series` L268、`test_fs02_m05_family_replays_one_fixed_phase_kinematic_contract` L295、`test_fs02_m05_post_phase_replay_rejects_missing_phase_evidence` L379、`test_fs01_m03_family_replays_all_four_raw_evidence_contracts` L410、`test_fs01_m03_replay_rejects_shifted_prepared_grid` L511、`test_hip_acceleration_projection_replays_raw_direction_and_acceleration` L549、`test_stability_duration_recomputes_adaptive_envelope_on_raw_series` L588、`test_fs09_braking_phase_timing_replays_raw_timestamp_series` L616、`test_fs09_support_phase_timing_replays_raw_timestamp_series` L661、`test_fs09_phase_timing_rejects_misaligned_raw_grids` L703、`test_launch_direction_replays_raw_vector_with_production_function` L737、`test_launch_direction_rejects_malformed_raw_vector_without_zero_fill` L768、`test_exact_named_series_replays_event_aggregation` L799、`test_documented_absolute_series_alias_is_matchable` L828、`test_supported_multi_series_selection_is_replayed_from_raw` L857、`test_fs09_v2_side_replays_local_slowdown_fallback` L881、`test_fs09_v2_indeterminate_code_zero_is_not_missing` L923、`test_fs02_drive_side_and_selected_knee_replay_raw_flexion` L945、`test_fs02_drive_to_moving_foot_timing_replays_timestamps` L997、`test_unmatched_derived_series_is_null_with_reason` L1034。
- `SmoothingBudgetContractTests`：L1058–L1159；方法：`_sequence` L1060、`_events` L1069、`test_evaluated_budget_exposes_match_provenance` L1085、`test_unavailable_budget_is_null_and_counts_reason` L1120。
### tests/test_feature_evaluation.py

[打开源码](../../tests/test_feature_evaluation.py) · 412 行

直接引用的项目模块：`rallymate_evaluation.feature_errors`、`rallymate_evaluation.ground_truth`、`rallymate_features.coordinates`、`rallymate_features.schemas`。

- `_sequence`：L16–L50。
- `_direction_sequence`：L53–L93。
- `_target_semantic`：L96–L116。
- `FeatureEvaluationTests`：L119–L408；方法：`test_target_alignment_error_uses_manual_semantic_and_corrected_pose` L120、`test_target_alignment_truth_missing_is_incomplete_not_zero` L176、`test_target_alignment_truth_requires_independent_reviewer` L215、`test_without_truth_returns_null_metrics` L240、`test_feature_metrics_and_error_budget_use_matched_manual_event` L248、`test_invisible_joint_requires_null_coordinates` L300、`test_partial_manual_truth_never_reuses_unannotated_model_points` L320、`test_duplicate_manual_truth_requires_adjudication` L343、`test_sparse_truth_is_reported_as_insufficient_not_evaluated` L364。
### tests/test_feature_observation_gap_audit.py

[打开源码](../../tests/test_feature_observation_gap_audit.py) · 110 行

- `_load_script`：L13–L20。
- `FeatureObservationGapAuditTests`：L23–L106；方法：`setUpClass` L25、`test_real_audit_is_exact_non_scoring_and_no_profile_cherry_pick` L28、`test_compact_full_feature_tamper_is_rejected` L54、`test_measurement_hard_fail_gate_cannot_be_removed_from_complete_vector` L71。
### tests/test_features.py

[打开源码](../../tests/test_features.py) · 241 行

直接引用的项目模块：`rallymate_features.coordinates`、`rallymate_features.event_features`、`rallymate_features.kinematics`、`rallymate_features.schemas`。

- `_sequence`：L28–L56。
- `_fine_foot_sequence`：L59–L73。
- `FeatureLibraryTests`：L76–L237；方法：`test_stability_envelope_uses_irregular_timestamps_and_aligned_series` L77、`test_irregular_derivative_uses_timestamp_not_fixed_fps` L90、`test_ten_features_emit_version_unit_validity_and_evidence` L96、`test_missing_point_is_null_not_zero_and_can_pass_short_gap_interpolation` L134、`test_fine_foot_points_enable_ankle_angle_while_coco17_is_unavailable` L146、`test_halpe26_and_wholebody133_records_share_registered_fine_foot_feature` L157、`test_out_of_frame_point_is_missing_even_when_confidence_is_high` L210。
### tests/test_fixed_boundary_pose_ab.py

[打开源码](../../tests/test_fixed_boundary_pose_ab.py) · 362 行

直接引用的项目模块：`rallymate_evaluation`、`rallymate_evaluation.fixed_boundary_ab`。

- `_points`：L21–L41。
- `_records`：L44–L74。
- `_timeline`：L77–L88。
- `_registry`：L91–L117。
- `_write_jsonl`：L120–L123。
- `FixedBoundaryPoseABTests`：L126–L358；方法：`test_pose_ab_excludes_non_pose_scoring_context_from_measurement_vector` L127、`test_file_pipeline_preserves_boundaries_and_reports_disagreement_only` L147、`test_direction_uses_wrapped_difference_and_codes_only_use_agreement` L245、`test_invalid_or_semantically_mismatched_pair_never_enters_numeric_metric` L273、`test_strict_validator_rejects_accuracy_or_scoring_claims` L298。
### tests/test_footwork_review.py

[打开源码](../../tests/test_footwork_review.py) · 328 行

直接引用的项目模块：`rallymate_service.footwork_review`。

- `event`：L16–L25。
- `record`：L28–L47。
- `FootworkReviewTests`：L50–L283；方法：`test_bound_measurements_keep_interval_units_and_no_grade` L51、`test_all_identity_dimensions_and_indicator_family_must_match` L67、`test_no_event_file_or_valid_interval_never_invents_an_episode` L78、`test_video_identity_is_inferred_only_when_unambiguous` L87、`test_quality_gate_and_feature_status_suppress_values` L92、`test_bad_values_units_and_confidence_are_not_displayed` L112、`test_duplicate_records_collapse_but_conflicts_are_rejected` L125、`test_conflicting_duplicate_feature_does_not_win_by_order` L140、`test_valid_sibling_feature_survives_aggregate_unavailable_without_a_grade` L146、`test_hard_gate_blocks_even_individually_valid_features_with_specific_reason` L174、`test_conflict_only_suppresses_its_feature_and_never_picks_first` L183、`test_independent_measurements_still_reject_bad_numeric_payloads` L195、`test_unknown_view_does_not_erase_image_plane_features` L207、`test_independent_target_direction_feature_respects_scoring_quality` L219、`test_target_direction_cannot_bypass_context_gate_through_measurement_features` L240、`test_scoring_and_measurement_feature_conflicts_remain_unavailable` L250、`test_episode_limit_is_explicit_and_sorted` L259、`test_event_loader_fails_closed_without_private_paths` L270。
- `FootworkReviewApiTests`：L286–L324；方法：`test_demo_result_attaches_bound_review_and_bad_events_degrade_locally` L287、`test_demo_result_does_not_adopt_unrelated_event_video_identity` L313。
### tests/test_frontend_scoring_reference.py

[打开源码](../../tests/test_frontend_scoring_reference.py) · 238 行

- `walk_values`：L24–L31。
- `FrontendScoringReferenceTests`：L34–L234；方法：`setUpClass` L36、`test_all_298_source_cards_are_present_with_only_13_partial_measurement_links` L42、`test_all_displayed_original_fields_and_five_grades_are_verbatim` L63、`test_source_positions_and_document_hashes_trace_back_to_the_audit` L82、`test_three_source_errors_and_97_missing_boundaries_are_flagged_without_rewriting` L111、`test_scale_direction_and_cross_event_differences_use_readable_notes` L126、`test_24_visual_techniques_preserve_all_phases_and_clauses_with_optional_stages` L147、`test_public_catalog_has_no_private_paths_or_invented_scoring_configuration` L179、`test_generation_is_deterministic_and_does_not_mutate_the_source` L193、`test_check_detects_a_stale_artifact_without_overwriting_it` L202、`test_cli_refuses_to_overwrite_the_source_audit` L212、`test_unexpected_source_versions_counts_and_unsafe_references_fail_closed` L218。
### tests/test_fs01_fs02_features.py

[打开源码](../../tests/test_fs01_fs02_features.py) · 1,395 行

直接引用的项目模块：`rallymate_features.event_features`、`rallymate_features.fs01_fs02_features`、`rallymate_features.kinematics`、`rallymate_features.schemas`。

- `_motion_sequence`：L46–L145。
- `_mirrored_sequence`：L148–L167。
- `_with_timestamps`：L170–L180。
- `_static_offset_sequence`：L183–L233。
- `FS01FS02PureFunctionTests`：L236–L913；方法：`test_fs01_m04_pure_functions_share_slowdown_anchor_and_boundary_contract` L237、`test_fs01_m04_pure_functions_reject_grid_and_scale_mismatch` L291、`test_fs01_m03_pure_functions_share_irregular_timestamp_contract` L318、`test_fs01_m03_pure_functions_reject_misaligned_or_invalid_inputs` L394、`test_hip_acceleration_projection_uses_shared_irregular_time_direction` L438、`test_hip_acceleration_projection_rejects_misaligned_series` L460、`test_launch_direction_uses_irregular_timestamp_ms` L478、`test_fine_foot_is_preferred_and_coco17_fallback_is_explicit` L497、`test_aligned_sparse_fallback_does_not_create_false_motion` L519、`test_no_overlap_excludes_unaligned_fallback_from_derivatives` L556、`test_complete_static_event_reports_observed_zero_rise_duration` L587、`test_incomplete_static_event_keeps_rise_duration_null` L610、`test_observed_zero_requires_timestamp_gap_within_smoothing_contract` L633、`test_single_boundary_frame_uses_only_nearest_valid_in_event_sample` L675、`test_boundary_censored_window_does_not_bridge_timestamp_gap` L712、`test_fs02_m04_launch_kinematics_share_one_irregular_time_contract` L744、`test_fs02_m04_launch_kinematics_fail_closed_on_grid_or_scale` L786、`test_fs02_m05_first_step_family_uses_fixed_phase_and_irregular_time` L823、`test_fs02_m05_first_step_family_fails_closed_on_grid_or_phase` L870。
- `FS01FS02FeatureIntegrationTests`：L916–L1391；方法：`setUp` L917、`test_nonpositive_bilateral_extension_is_measurable_negative_evidence` L920、`test_fs02_m04_features_publish_shared_replayable_launch_evidence` L950、`test_fs02_m05_features_publish_shared_phase_replay_evidence` L987、`test_all_next_wave_features_are_versioned_valid_and_json_safe` L1033、`test_fs01_m03_features_publish_replayable_raw_and_prepared_evidence` L1054、`test_fs01_m04_features_publish_replayable_slowdown_evidence` L1101、`test_synthetic_motion_exposes_expected_proxy_structure` L1133、`test_post_step_features_use_versioned_event_phase_not_late_global_peak` L1190、`test_declared_unalignable_post_step_phase_fails_closed` L1234、`test_mirror_preserves_scalars_and_transforms_direction_and_side` L1255、`test_ankle_fallback_keeps_measurement_but_lowers_confidence` L1285、`test_sparse_fine_foot_falls_back_per_frame_and_reports_ratio` L1315、`test_excessive_missing_fine_foot_is_unavailable_not_numeric_zero` L1362、`test_definitions_never_claim_contact_force_or_grade_observation` L1381。
### tests/test_fs01_fs02_truth_evaluation.py

[打开源码](../../tests/test_fs01_fs02_truth_evaluation.py) · 461 行

直接引用的项目模块：`rallymate_evaluation.feature_errors`、`rallymate_evaluation.ground_truth`、`rallymate_features`、`rallymate_features.fs01_fs02_features`、`rallymate_features.schemas`。

- `_write_jsonl`：L72–L76。
- `_coordinates`：L79–L135。
- `_fixture_records`：L138–L192。
- `_event`：L195–L250。
- `FS01FS02TruthEvaluationTests`：L253–L457；方法：`test_cli_evaluates_all_new_features_from_manual_coco17_truth` L254、`test_quality_gate_failure_cannot_count_a_diagnostic_zero_as_valid` L413。
### tests/test_fs02_production_scoring_context.py

[打开源码](../../tests/test_fs02_production_scoring_context.py) · 622 行

直接引用的项目模块：`rallymate_scoring.batch`、`rallymate_scoring.calibration_fitting`、`rallymate_scoring.calibration_independent_test`、`rallymate_scoring.calibration_promotion`、`rallymate_scoring.quality_policy`、`rallymate_scoring.runtime_profile_binding`、`rallymate_scoring.scoring`、`rallymate_scoring.scoring_context`。

- `_f4_fs02_registry`：L54–L67。
- `_fs02_maturity_evidence`：L70–L165。
- `_fs02_production_replay_fixture`：L168–L254。
- `_fs02_runtime_fixture`：L257–L406。
- `_feature`：L409–L425。
- `FS02ProductionScoringContextTests`：L428–L618；方法：`test_runtime_bound_fs02_context_scores_and_each_evidence_item_is_bound` L429。
### tests/test_fs09_features.py

[打开源码](../../tests/test_fs09_features.py) · 405 行

直接引用的项目模块：`rallymate_features.event_features`、`rallymate_features.fs09_features`、`rallymate_features.schemas`。

- `_braking_sequence`：L43–L121。
- `FS09PureFunctionTests`：L124–L271；方法：`test_event_edge_change_is_robust_median_delta` L125、`test_braking_side_is_explicit_numeric_proxy` L137、`test_braking_side_uses_local_slowdown_when_net_drop_is_nonpositive` L143、`test_double_support_proxy_uses_timestamp_duration` L180、`test_phase_timing_pure_functions_use_irregular_timestamps` L194、`test_phase_timing_pure_functions_fail_closed_on_misaligned_inputs` L226、`test_hip_velocity_respects_irregular_timestamp_spacing` L264。
- `FS09EventFeatureIntegrationTests`：L274–L401；方法：`setUp` L275、`test_all_fs09_features_are_versioned_and_machine_readable` L279、`test_braking_absorption_and_stability_proxies_have_expected_signs` L295、`test_missing_keypoint_is_null_in_evidence_not_numeric_zero` L352、`test_no_feature_definition_claims_contact_force_or_grade` L365、`test_repeated_event_summaries_reuse_and_clear_full_sequence_cache` L375。
### tests/test_historical_registry_scripts.py

[打开源码](../../tests/test_historical_registry_scripts.py) · 431 行

- `_load_script`：L23–L30。
- `_copy_historical_registry`：L33–L34。
- `HistoricalRegistryScriptTests`：L37–L427；方法：`setUpClass` L39、`test_main_requires_explicit_historical_replay_before_workspace_access` L42、`test_explicit_flag_is_accepted_by_every_parser` L62、`test_current_registry_replacement_fails_in_main_before_outputs` L68、`test_every_script_pins_the_same_exact_historical_registry_bytes` L83、`test_minimum_loop_outputs_are_labeled_historical` L124、`test_report_builder_labels_html_and_embedded_summaries` L177、`test_pose_ab_json_producers_label_historical_scope` L221、`test_minimum_loop_reuse_rejects_current_registry_records` L286、`test_report_builder_rejects_current_registry_records_and_scores` L335、`test_checked_in_historical_bundles_pass_content_validation` L396。
### tests/test_indicator_requirements.py

[打开源码](../../tests/test_indicator_requirements.py) · 125 行

直接引用的项目模块：`rallymate_annotation.truth_pack`、`rallymate_scoring.feasibility`、`rallymate_scoring.indicator_requirements`。

- `IndicatorRequirementsTests`：L25–L121；方法：`test_real_thirteen_indicator_requirement_matrix_is_closed` L26、`test_checked_in_snapshot_matches_current_registry_and_truth_contract` L59、`test_default_builder_exactly_reproduces_checked_in_snapshot` L83、`test_snapshot_rejects_shrunk_or_duplicate_requirements` L113。
### tests/test_isotropic_coordinates.py

[打开源码](../../tests/test_isotropic_coordinates.py) · 127 行

直接引用的项目模块：`rallymate_evaluation.ground_truth`、`rallymate_features.coordinates`、`rallymate_features.event_features`、`rallymate_features.schemas`。

- `_records`：L22–L37。
- `_sequence`：L40–L42。
- `IsotropicCoordinateTests`：L45–L123；方法：`test_crop_padding_and_proportional_resize_preserve_geometry_and_body_units` L46、`test_pixels_are_authoritative_and_dimensions_are_traceable` L67、`test_missing_or_invalid_dimensions_never_guess_aspect_ratio` L77、`test_invalid_pixel_observation_is_not_rescued_by_normalized_values` L87、`test_camera_status_is_preserved_without_removing_angle_observations` L97、`test_manual_truth_uses_the_same_isotropic_space` L106。
### tests/test_kinematic_continuity.py

[打开源码](../../tests/test_kinematic_continuity.py) · 143 行

直接引用的项目模块：`rallymate_evaluation.kinematic_continuity`、`rallymate_features.kinematics`、`rallymate_features.schemas`。

- `ContinuousDerivativeCandidateTests`：L25–L74；方法：`test_missing_ankle_position_cannot_create_speed_at_occlusion_boundaries` L26、`test_long_unsampled_interval_cannot_be_used_as_a_velocity_stencil` L37、`test_adjacent_irregular_observations_keep_exact_legacy_stencil` L45、`test_breaks_are_column_specific_and_isolated_observations_stay_missing` L52、`test_existing_160ms_limit_is_inclusive` L59、`test_empty_single_and_invalid_time_inputs` L64。
- `FixedEventContinuityDiagnosticTests`：L77–L139；方法：`sequence` L79、`event` L86、`test_fixed_boundary_comparison_preserves_event_and_explains_source_support` L90、`test_no_borrowing_across_event_boundaries` L106、`test_no_observed_event_samples_do_not_become_zero_speed` L111、`test_file_entrypoint_binds_sources_and_refuses_overwrite` L117。
### tests/test_m67_measurement_recovery_summary.py

[打开源码](../../tests/test_m67_measurement_recovery_summary.py) · 98 行

- `_load_script`：L11–L18。
- `_margin`：L21–L36。
- `_combined`：L39–L47。
- `M67MeasurementRecoverySummaryTests`：L50–L94；方法：`setUpClass` L52、`test_summary_recomputes_balanced_projection_and_keeps_it_experimental` L55。
### tests/test_m68_pose_router_summary.py

[打开源码](../../tests/test_m68_pose_router_summary.py) · 75 行

- `M68PoseRouterSummaryTests`：L18–L71；方法：`test_validator_rejects_regression_or_unsafe_selection` L19。
### tests/test_m69_high_resolution_summary.py

[打开源码](../../tests/test_m69_high_resolution_summary.py) · 54 行

- `M69HighResolutionSummaryTests`：L19–L50；方法：`setUpClass` L21、`test_real_three_video_projection_is_exact_and_regression_free` L30、`test_summary_rejects_unsafe_claim_or_hidden_regression` L42。
### tests/test_m70_pose_profile_ablation_summary.py

[打开源码](../../tests/test_m70_pose_profile_ablation_summary.py) · 86 行

- `M70PoseProfileAblationSummaryTests`：L23–L82；方法：`setUpClass` L25、`test_real_three_video_ablation_and_anchored_projection_are_exact` L38、`test_anchored_validator_and_summary_fail_closed` L63。
### tests/test_m71_larger_pose_model_extension.py

[打开源码](../../tests/test_m71_larger_pose_model_extension.py) · 78 行

- `_report`：L12–L59。
- `M71LargerPoseModelExtensionTests`：L62–L74；方法：`test_safe_report_validates` L63、`test_hidden_loss_or_accuracy_claim_fails_closed` L66。
### tests/test_m71_larger_pose_model_summary.py

[打开源码](../../tests/test_m71_larger_pose_model_summary.py) · 58 行

- `M71LargerPoseModelSummaryTests`：L20–L54；方法：`setUpClass` L22、`test_real_three_video_projection_is_exact_and_regression_free` L31、`test_summary_fails_closed_on_claim_or_regression` L46。
### tests/test_m72_additive_keypoint_fusion.py

[打开源码](../../tests/test_m72_additive_keypoint_fusion.py) · 81 行

- `_report`：L12–L58。
- `M72AdditiveKeypointFusionTests`：L61–L77；方法：`test_safe_report_validates` L62、`test_overwrite_regression_or_accuracy_claim_fails_closed` L65。
### tests/test_m72_additive_keypoint_fusion_summary.py

[打开源码](../../tests/test_m72_additive_keypoint_fusion_summary.py) · 66 行

- `M72AdditiveKeypointFusionSummaryTests`：L21–L62；方法：`setUpClass` L23、`test_real_projection_and_remaining_residual_are_exact` L33、`test_summary_rejects_overwrite_claim_or_hidden_loss` L50。
### tests/test_m73_wholebody_mapped_fusion.py

[打开源码](../../tests/test_m73_wholebody_mapped_fusion.py) · 91 行

- `_report`：L12–L66。
- `M73WholeBodyMappedFusionTests`：L69–L87；方法：`test_safe_report_validates` L70、`test_hidden_regression_mapping_or_accuracy_claim_fails_closed` L73。
### tests/test_m73_wholebody_mapped_fusion_summary.py

[打开源码](../../tests/test_m73_wholebody_mapped_fusion_summary.py) · 42 行

- `M73WholeBodyMappedFusionSummaryTests`：L17–L38；方法：`test_real_projection_preserves_m72_and_records_zero_indicator_gain` L18、`test_summary_rejects_hidden_gain_or_accuracy_claim` L30。
### tests/test_m74_event_bounded_gap_audit.py

[打开源码](../../tests/test_m74_event_bounded_gap_audit.py) · 50 行

直接引用的项目模块：`rallymate_evaluation.event_bounded_pose_gaps`。

- `M74EventBoundedGapAuditTests`：L24–L46；方法：`test_real_report_is_source_bound_and_keeps_production_unchanged` L25、`test_report_rejects_accuracy_or_hidden_production_recovery` L35。
### tests/test_m75_event_gap_keypoint_truth.py

[打开源码](../../tests/test_m75_event_gap_keypoint_truth.py) · 237 行

直接引用的项目模块：`rallymate_evaluation.event_gap_keypoint_truth`、`rallymate_evaluation.small_roi_keypoint_truth`。

- `_jsonl`：L36–L37。
- `_write_csv`：L40–L44。
- `_write_jsonl`：L47–L51。
- `_build_temp_pack`：L54–L61。
- `_complete_with_synthetic_prediction_coordinates`：L64–L104。
- `EventGapKeypointTruthTests`：L107–L233；方法：`test_real_blank_pack_is_blind_and_fail_closed` L108、`test_synthetic_completed_contract_computes_zero_error_without_promotion` L150、`test_sealed_prediction_tampering_is_rejected` L173、`test_rehashed_prediction_tampering_still_fails_m74_source_replay` L185、`test_reviewer_must_be_independent` L209、`test_output_directory_is_immutable` L222。
### tests/test_m76_event_gap_feature_truth.py

[打开源码](../../tests/test_m76_event_gap_feature_truth.py) · 212 行

直接引用的项目模块：`rallymate_evaluation.event_gap_feature_truth`、`rallymate_evaluation.event_gap_keypoint_truth`、`rallymate_evaluation.small_roi_keypoint_truth`。

- `_jsonl`：L45–L50。
- `_write_csv`：L53–L57。
- `_complete_pack`：L60–L103。
- `EventGapFeatureTruthTests`：L106–L208；方法：`test_real_empty_report_is_source_replayable_and_fail_closed` L107、`test_synthetic_truth_replays_47_features_with_zero_difference` L128、`test_invisible_truth_is_missing_not_zero` L166、`test_circular_direction_error_wraps_at_360` L196、`test_rehashed_report_metric_tampering_fails_source_replay` L204。
### tests/test_m77_fs09_phase_truth.py

[打开源码](../../tests/test_m77_fs09_phase_truth.py) · 1,593 行

直接引用的项目模块：`rallymate_evaluation.fs09_phase_handoff`、`rallymate_evaluation.fs09_phase_truth`、`rallymate_evaluation.small_roi_keypoint_truth`、`rallymate_features`。

- `_jsonl`：L54–L55。
- `_write_csv`：L58–L62。
- `_write_jsonl`：L65–L66。
- `_write_json`：L69–L73。
- `_read_csv`：L76–L78。
- `_tree_snapshot`：L81–L86。
- `_build`：L89–L99。
- `ingest_fs09_phase_truth_exports`：L102–L121。
- `_complete`：L124–L184。
- `_export_files`：L187–L214。
- `_run_evaluator_cli`：L217–L237。
- `_test_only_authorized_handoff_snapshot`：L240–L254。
- `FS09PhaseTruthTests`：L257–L1589；方法：`setUp` L258、`test_analysis_plan_is_schema_valid_repository_freeze_not_external_registration` L267、`test_browser_review_clips_are_h264_and_map_to_source_windows` L349、`test_rehashed_review_clip_probe_claim_is_redecoded_and_rejected` L363、`test_workbench_and_local_media_are_plan_bound_not_manifest_self_declared` L385、`test_intake_source_manifest_alias_cannot_change_artifact_root` L428、`test_evaluation_rechecks_sources_after_feature_computation` L460、`test_evaluation_clears_feature_cache_when_feature_computation_raises` L491、`test_real_pack_is_blind_empty_and_source_replayable` L524、`test_synthetic_equal_candidate_truth_has_zero_phase_and_boundary_error` L620、`test_technical_handoff_cannot_enter_production_intake` L668、`test_test_only_session_is_rejected_after_authorization_patch_is_removed` L692、`test_export_intake_creates_new_traceable_session_without_editing_blank_pack` L720、`test_export_intake_rejects_handoff_plan_or_timestamp_drift_before_output` L800、`test_cross_artifact_chronology_is_fail_closed` L876、`test_export_intake_rejects_identity_or_source_mismatch_before_output` L920、`test_export_intake_rejects_wrong_header_before_output` L961、`test_export_intake_raw_tamper_breaks_evaluation_lineage` L980、`test_export_intake_rejects_session_output_inside_source_pack_without_writes` L997、`test_direct_nonempty_pack_cannot_become_ready_or_evaluate` L1018、`test_raw_export_semantic_replacement_with_updated_lineage_sha_is_rejected` L1034、`test_manual_event_tamper_with_updated_artifact_sha_is_rejected` L1085、`test_manual_event_replay_rejects_type_loose_duplicate_and_noncanonical_jsonl` L1113、`test_validation_replay_rejects_bool_count_and_lowercase_sha` L1177、`test_manifest_runtime_rejects_unknown_and_missing_schema_fields` L1209、`test_committed_intake_recompile_is_rejected_without_byte_changes` L1226、`test_intake_manifest_rejects_unknown_fields_counts_and_identity_metadata` L1255、`test_semicolon_and_casefold_equivalent_identities_fail_closed` L1291、`test_evaluator_cli_reports_domain_errors_without_traceback_or_output` L1332、`test_evaluator_cli_rejects_output_inside_immutable_session` L1360、`test_synthetic_absent_events_are_not_forced_into_feature_replay` L1386、`test_phase_order_and_reviewer_independence_fail_closed` L1432、`test_rehashed_candidate_tamper_still_fails_m74_replay` L1465、`test_analysis_plan_binding_and_freeze_chronology_fail_closed` L1482、`test_analysis_plan_tamper_and_pre_freeze_labels_fail_closed` L1519、`test_output_directory_is_immutable` L1576。
### tests/test_m79_pose_diagnostic_clip_truth.py

[打开源码](../../tests/test_m79_pose_diagnostic_clip_truth.py) · 336 行

直接引用的项目模块：`rallymate_evaluation.pose_diagnostic_clip_truth`。

- `_write_csv`：L30–L34。
- `_copy_contract_shell`：L37–L44。
- `_coverage_id`：L47–L48。
- `_complete_synthetic_contract`：L51–L131。
- `PoseDiagnosticClipTruthM79Tests`：L134–L332；方法：`setUpClass` L136、`test_real_pack_is_empty_blind_and_source_replayable` L147、`test_every_clip_is_independent_h264_with_exact_frame_count` L187、`test_rehashed_pose_evidence_tamper_fails_source_frame_replay` L212、`test_rehashed_sealed_candidate_tamper_fails_queue_replay` L239、`test_synthetic_two_annotator_third_party_flow_only_reports_precision` L261、`test_reviewer_cannot_be_one_of_the_two_annotators` L284、`test_range_server_returns_partial_content` L300。
### tests/test_m86_fs09_phase_workbench_ui.py

[打开源码](../../tests/test_m86_fs09_phase_workbench_ui.py) · 508 行

- `FS09PhaseWorkbenchUIContractTests`：L16–L506；方法：`setUpClass` L18、`_run_node_behavior` L23、`test_javascript_has_valid_node_syntax` L209、`test_import_is_strict_and_reviewer_independence_is_per_task` L215、`test_bootstrap_handoff_gate_blocks_invalid_sources` L226、`test_decision_time_is_stable_across_exports_and_updates_only_after_reedit` L259、`test_adjudication_time_is_stable_and_reedit_updates_it` L302、`test_dirty_task_identity_and_mode_transitions_use_the_same_timestamp_rule` L339、`test_local_storage_is_isolated_by_handoff_and_plan` L374、`test_annotation_import_requires_exact_handoff_and_plan` L398、`test_reloaded_changed_annotation_revisions_require_every_task_to_be_readjudicated` L416、`test_media_gate_requires_full_seekable_range_and_disables_truth_actions` L441、`test_event_phase_and_confidence_require_active_confirmation` L453、`test_ids_reject_semicolon_and_newlines` L463、`test_authority_pack_requires_public_handoff_allowlist_server` L470、`test_export_requires_successful_save` L482、`test_dirty_form_is_saved_or_transition_is_blocked` L485、`test_reviewer_comparison_has_all_manual_fields_and_diff_marker` L491、`test_frame_and_millisecond_controls_use_bootstrap_fps` L497、`test_sealed_candidate_and_pose_data_are_not_loaded` L502。
### tests/test_m88_fs09_blind_handoff.py

[打开源码](../../tests/test_m88_fs09_blind_handoff.py) · 853 行

直接引用的项目模块：`rallymate_evaluation.fs09_phase_handoff`。

- `_write_manifest`：L48–L52。
- `_content_root`：L55–L62。
- `_tree`：L65–L70。
- `_free_port`：L73–L76。
- `FS09PhaseBlindHandoffTests`：L79–L849；方法：`setUpClass` L81、`tearDownClass` L89、`_copy` L92、`_coordinated_rebind` L99、`_run_bundled_validate_only` L176、`test_schema_runtime_and_source_replay_accept_moved_bundle` L190、`test_manifest_is_technical_only_until_external_protocol_is_verified` L220、`test_noncanonical_utc_offset_timestamp_is_rejected` L292、`test_generated_at_is_part_of_bundle_identity` L323、`test_public_tree_excludes_candidate_records_and_exact_private_timestamps` L356、`test_public_html_has_handoff_gate_and_replays_private_html` L439、`test_every_artifact_single_byte_tamper_is_rejected` L451、`test_coordinated_rehash_cannot_replace_plan_bound_assets_or_review` L462、`test_bundled_server_rejects_coordinated_protocol_gate_rehash` L483、`test_bundled_server_rejects_coordinated_metric_plan_rehash` L518、`test_bundled_server_rejects_coordinated_javascript_rehash` L531、`test_bundled_server_rejects_coordinated_outer_html_rehash` L543、`test_bundled_server_rejects_coordinated_public_readme_rehash` L564、`test_manifest_bundle_id_content_root_and_paths_are_strict` L575、`test_extra_file_and_extra_directory_are_rejected` L598、`test_builder_is_atomic_rejects_overlap_existing_and_leaves_no_residue` L609、`test_build_cli_and_bundled_server_validate_after_move` L649、`test_standalone_http_server_has_root_range_and_private_path_denials` L719。
### tests/test_m90_operator_annotation_plan.py

[打开源码](../../tests/test_m90_operator_annotation_plan.py) · 68 行

直接引用的项目模块：`rallymate_annotation.scoring_truth_authorization`、`rallymate_annotation.scoring_truth_event_handoff`。

- `M90OperatorAnnotationPlanTest`：L22–L64；方法：`test_frozen_zero_label_plan_matches_schema_runtime_and_exact_m89_handoff` L23。
### tests/test_m93_scoring_truth_event_workbench_ui.py

[打开源码](../../tests/test_m93_scoring_truth_event_workbench_ui.py) · 541 行

- `M93ScoringTruthEventWorkbenchUITests`：L20–L537；方法：`setUpClass` L22、`_run_node` L28、`test_all_assets_exist_and_javascript_has_valid_syntax` L163、`test_template_is_one_role_fixed_bootstrap_template` L175、`test_dom_controller_has_media_gate_storage_and_exact_import_contracts` L189、`test_bootstrap_exact_shape_status_and_storage_isolation` L211、`test_annotation_revisions_are_stable_and_only_edited_video_changes` L237、`test_c_import_is_raw_pinned_and_adjudication_stales_per_video_revision` L339、`test_canonical_file_parser_rejects_noncanonical_or_invalid_utf8` L427、`test_zero_source_events_still_require_three_c_video_confirmations` L443。
### tests/test_m96_pose_pilot.py

[打开源码](../../tests/test_m96_pose_pilot.py) · 311 行

直接引用的项目模块：`rallymate_evaluation.m96_pose_pilot`。

- `_csv_bytes`：L36–L41。
- `_annotation_rows`：L44–L77。
- `_free_port`：L80–L83。
- `_request`：L86–L98。
- `M96PosePilotTests`：L101–L307；方法：`setUpClass` L103、`test_blank_pilot_is_deterministic_complete_and_evaluation_only` L106、`test_role_packages_are_separate_and_ui_supports_draft_csv_restore` L139、`test_governance_template_has_no_inferred_values` L154、`test_atomic_A_B_intake_preserves_raw_bytes_and_builds_only_C_disagreements` L166、`test_invalid_or_reused_A_B_identity_leaves_no_partial_bundle` L224、`test_loopback_server_exposes_only_selected_role_and_three_videos` L241。
### tests/test_m97_x_pose_operational.py

[打开源码](../../tests/test_m97_x_pose_operational.py) · 229 行

直接引用的项目模块：`rallymate_evaluation.pose_x_operational`。

- `_impact`：L25–L40。
- `_report`：L43–L124。
- `M97XOperationalTests`：L127–L225；方法：`test_protocol_binds_only_three_development_videos_and_never_hashes_holdout` L128、`test_frame_and_roi_comparability_fails_closed_on_context_drift` L145、`test_recovery_set_comparison_reports_additions_and_losses` L181、`test_per_video_status_exposes_m71_loss_and_claims_fail_closed` L196、`test_latency_distribution_is_exact_and_rejects_nonfinite_values` L219。
### tests/test_m97_x_pose_operational_summary.py

[打开源码](../../tests/test_m97_x_pose_operational_summary.py) · 112 行

直接引用的项目模块：`rallymate_evaluation.pose_x_operational`。

- `M97XOperationalSummaryTests`：L25–L108；方法：`setUpClass` L27、`test_real_258_frame_projection_and_reference_differences_are_exact` L38、`test_saved_summary_is_valid_and_reconstructs_the_same_metrics` L77、`test_summary_fails_closed_on_hidden_m71_loss_or_accuracy_claim` L99。
### tests/test_manual_event_features.py

[打开源码](../../tests/test_manual_event_features.py) · 420 行

直接引用的项目模块：`rallymate_scoring.calibration_dataset`、`rallymate_scoring.manual_event_features`。

- `_write_jsonl`：L33–L34。
- `_event`：L37–L56。
- `ManualEventFeaturesTests`：L59–L416；方法：`_inputs` L60、`test_manual_boundaries_emit_all_dynamic_registry_indicators_without_grades` L177、`test_manual_feature_output_exactly_joins_calibration_compiler` L224、`test_rejects_multi_video_manual_input_and_candidate_only_flags` L351、`test_missing_manual_phase_cannot_be_reported_as_measured` L381。
### tests/test_maturity_evidence.py

[打开源码](../../tests/test_maturity_evidence.py) · 452 行

直接引用的项目模块：`rallymate_scoring.calibration_fitting`、`rallymate_scoring.maturity_evidence`。

- `_sha`：L31–L32。
- `_reference`：L35–L43。
- `_registry`：L46–L52。
- `_error_stats`：L55–L63。
- `_metric_summary`：L66–L67。
- `_payload`：L70–L154。
- `_rehash`：L157–L166。
- `_bundle`：L169–L217。
- `_production_shaped_bundle`：L220–L252。
- `_actual_f4_registry`：L255–L281。
- `MaturityEvidenceTests`：L284–L448；方法：`test_complete_synthetic_chain_validates_and_is_machine_schema_valid` L285、`test_chain_is_fail_closed_when_any_transition_is_missing` L310、`test_payload_and_transition_hash_tampering_are_rejected` L316、`test_registry_history_and_previous_hash_must_be_sequential` L331、`test_f2_requires_error_budget_and_external_grade_gap_decisions` L347、`test_embedded_numeric_acceptance_threshold_is_rejected` L362、`test_production_scope_requires_human_and_external_source_kinds` L371、`test_production_shaped_chain_with_external_bindings_validates` L395、`test_bundle_binds_to_exact_current_f4_registry` L405、`test_binding_contains_final_transition_and_bundle_hash` L422、`test_review_timestamp_and_preregistration_order_are_enforced` L432、`test_non_evidence_template_cannot_validate` L441。
### tests/test_measurement_plans.py

[打开源码](../../tests/test_measurement_plans.py) · 180 行

直接引用的项目模块：`rallymate_events.rules`、`rallymate_features.event_features`、`rallymate_scoring.feasibility`、`rallymate_scoring.indicator_requirements`、`rallymate_scoring.measurement_plans`。

- `MeasurementPlanTests`：L28–L176；方法：`setUpClass` L30、`test_all_298_cards_compile_to_unique_traceable_plans` L39、`test_current_f2_is_derived_from_registry_and_current_scope_is_complete` L49、`test_changing_feasibility_maturity_changes_plans_without_code_list` L77、`test_existing_f3_or_f4_metric_remains_feature_measurable` L98、`test_no_plan_can_emit_a_grade_or_threshold_without_calibration` L112、`test_runtime_contract_is_bound_to_actual_registry_and_implementations` L121、`test_source_visibility_gate_is_not_a_feature_or_grade_threshold` L134、`test_f2_never_erases_a_partial_source_clause_gap` L142、`test_known_source_clauses_map_to_required_observation_families` L147、`test_five_new_current_event_pose_only_indicators_are_F2` L154、`test_coarse_partial_primitives_are_not_promoted_by_status_sync` L165。
### tests/test_measurement_portfolio.py

[打开源码](../../tests/test_measurement_portfolio.py) · 228 行

直接引用的项目模块：`rallymate_scoring.measurement_portfolio`、`rallymate_vision.validation`。

- `_jsonl`：L26–L31。
- `_sources`：L34–L38。
- `MeasurementPortfolioTests`：L41–L224；方法：`_real_portfolio` L42、`test_representative_is_selected_by_observation_quality_not_value` L60、`test_real_short_and_full_portfolios_preserve_honest_scope` L143、`test_validator_rejects_grade_value_selection_or_invalid_representative` L172、`test_schema_accepts_real_materialized_portfolios` L192、`test_run_validator_rejects_stale_declared_derived_source_hashes` L215。
### tests/test_mmpose_dataset.py

[打开源码](../../tests/test_mmpose_dataset.py) · 863 行

直接引用的项目模块：`rallymate_training.mmpose_dataset`、`rallymate_training.pose_finetune_readiness`、`rallymate_vision.pose.metadata`。

- `_sha256`：L51–L52。
- `_canonical_sha256`：L55–L63。
- `_write_json`：L66–L70。
- `_write_jsonl`：L73–L81。
- `_write_csv`：L84–L88。
- `_binding`：L91–L92。
- `_build_pack`：L95–L238。
- `_write_governance`：L241–L254。
- `_fake_extract`：L257–L274。
- `_identical_fake_extract`：L277–L295。
- `_same_pixels_distinct_jpeg_extract`：L298–L318。
- `MMPoseDatasetExportTests`：L321–L859；方法：`_ready_inputs` L322、`test_exports_deterministic_coco_with_explicit_joint_states_and_hashes` L338、`test_refuses_holdout_and_empty_validation_split` L412、`test_refuses_immutable_bbox_outside_frame_even_when_truth_is_ready` L430、`test_refuses_duplicate_cross_pack_supervision_and_existing_output` L455、`test_refuses_source_hash_drift_before_extracting` L481、`test_refuses_source_alias_across_train_val` L499、`test_refuses_known_m95_holdout_even_when_governance_calls_it_val` L525、`test_refuses_global_only_halpe26_coverage_when_train_is_partial` L556、`test_refuses_any_export_frame_without_a_visible_coordinate` L580、`test_refuses_half_open_boundary_and_visible_point_outside_bbox` L609、`test_refuses_cross_split_jpeg_content_sha_leakage` L652、`test_refuses_cross_split_decoded_pixel_leakage` L671、`test_refuses_noncanonical_governance_split_spelling` L690、`test_refuses_source_alias_within_one_split` L710、`test_refuses_one_video_frame_split_across_truth_packs` L750、`test_refuses_source_drift_during_extract_and_publishes_nothing` L782、`test_frame_extractor_reads_exact_indexes_and_verifies_dimensions` L806。
### tests/test_mmpose_finetune.py

[打开源码](../../tests/test_mmpose_finetune.py) · 608 行

直接引用的项目模块：`rallymate_training.mmpose_dataset`、`rallymate_training.mmpose_finetune`、`rallymate_training.pose_finetune_readiness`。

- `_distinct_fake_extract`：L31–L46。
- `_FakeHook`：L49–L50。
- `_FakeRunner`：L53–L78；方法：`__init__` L54、`register_hook` L66、`train` L71。
- `_ready_dataset`：L81–L118。
- `_write_manifest`：L121–L133。
- `_refresh_artifact_bindings`：L136–L145。
- `MMPoseFineTuneTests`：L148–L604；方法：`test_dry_run_validates_real_runtime_and_writes_immutable_plan` L149、`test_mixed_visibility_export_is_accepted_by_real_adapter_dry_run` L183、`test_not_ready_and_zero_truth_status_fail_before_runner_or_output` L232、`test_train_val_subject_leakage_is_rejected_even_when_manifest_claims_ready` L256、`test_artifact_hash_drift_and_base_config_hash_drift_are_rejected` L306、`test_governance_is_rechecked_against_every_coco_image` L348、`test_visible_keypoint_outside_annotation_crop_is_rejected` L377、`test_decoded_pixel_leakage_is_rejected_even_when_jpeg_bytes_differ` L405、`test_unknown_manifest_field_and_holdout_guard_drift_are_rejected` L454、`test_execute_invokes_runner_only_after_all_gates` L477、`test_runner_failures_record_whether_training_really_started` L518。
### tests/test_motion_api.py

[打开源码](../../tests/test_motion_api.py) · 52 行

直接引用的项目模块：`rallymate_scoring.stroke_analysis`、`rallymate_scoring.stroke_candidates`。

- `MotionApiTests`：L16–L48；方法：`test_old_result_backfills_measured_motion_without_overwriting_history` L17。
### tests/test_multivideo_coverage.py

[打开源码](../../tests/test_multivideo_coverage.py) · 139 行

直接引用的项目模块：`rallymate_scoring.multivideo_coverage`。

- `MultivideoIndicatorCalculationCoverageTests`：L29–L135；方法：`setUpClass` L31、`test_real_three_video_portfolio_computes_all_thirteen_in_every_video` L36、`test_each_indicator_has_traceable_real_feature_vector_per_video` L57、`test_missing_video_report_cannot_build_portfolio` L93、`test_replayed_indicator_count_cannot_be_forged` L104、`test_accuracy_or_grade_claim_fails_closed_without_replay` L112、`test_machine_schema_accepts_real_coverage` L122。
### tests/test_multivideo_measurement_recovery.py

[打开源码](../../tests/test_multivideo_measurement_recovery.py) · 112 行

直接引用的项目模块：`rallymate_evaluation.multivideo_measurement_recovery`。

- `_build`：L27–L35。
- `MultivideoMeasurementRecoveryTests`：L38–L108；方法：`test_real_sources_replay_to_exact_vector_and_operational_counts` L39、`test_missing_experiment_for_an_eligible_video_fails_closed` L72、`test_self_reported_experiment_gain_is_replayed_not_trusted` L82、`test_projection_cannot_change_hard_fail_count_or_safety` L99。
### tests/test_multivideo_readiness.py

[打开源码](../../tests/test_multivideo_readiness.py) · 175 行

直接引用的项目模块：`rallymate_scoring.multivideo_readiness`。

- `MultivideoScoringReadinessTests`：L28–L171；方法：`setUpClass` L30、`test_real_decomposition_is_exhaustive_and_preserves_score_status` L33、`test_all_three_videos_and_thirteen_indicators_are_decomposed` L54、`test_typed_reasons_and_single_block_recovery_are_source_replayed` L62、`test_typed_reason_or_recovery_claim_tampering_fails_closed` L92、`test_strong_validator_rejects_rebalanced_self_reported_counts` L112、`test_accuracy_gate_or_zero_fill_claim_fails_closed` L126、`test_build_is_immutable_and_latest_is_strongly_loadable` L139、`test_machine_schema_accepts_real_audit` L159。
### tests/test_numeric_performance_equivalence.py

[打开源码](../../tests/test_numeric_performance_equivalence.py) · 118 行

直接引用的项目模块：`rallymate_events`、`rallymate_events.rules`、`rallymate_features.kinematics`、`rallymate_features.schemas`、`rallymate_features.smoothing`。

- `_reference_smooth`：L17–L31。
- `_reference_derivative`：L34–L45。
- `NumericPerformanceEquivalenceTests`：L48–L114；方法：`test_batched_smoothing_matches_irregular_finite_weight_definition` L49、`test_duplicate_and_reverse_time_preserve_existing_primitives` L62、`test_derivative_keeps_column_specific_gaps_and_endpoint_stencil` L71、`test_empty_smoothing_and_derivative_keep_shape` L81、`test_phase_signals_are_computed_once_for_multiple_bouts` L87、`test_read_only_export_retains_content_and_isolates_mutable_layers` L105。
### tests/test_pipeline_progress.py

[打开源码](../../tests/test_pipeline_progress.py) · 27 行

直接引用的项目模块：`rallymate_vision.progress`。

- `PipelineProgressTests`：L6–L27；方法：`test_all_postprocessing_stages_remain_visible_and_below_complete` L7、`test_partial_measurement_progress_reports_actual_work` L19、`test_empty_work_never_implies_complete` L24。
### tests/test_pipeline_scoring_registry.py

[打开源码](../../tests/test_pipeline_scoring_registry.py) · 257 行

直接引用的项目模块：`rallymate_scoring.calibration_promotion`、`rallymate_scoring.scoring`、`rallymate_vision.contracts`、`rallymate_vision.pipeline`。

- `PipelineScoringRegistryTests`：L35–L253；方法：`_request` L36、`test_pipeline_defaults_to_current_pose_wave_v2_registry` L45、`test_request_can_only_pin_the_lifecycle_authorized_registry` L57、`test_historical_registry_is_rejected_before_run_directory_creation` L81、`test_pipeline_loads_only_ledger_authorized_production_calibration` L90、`test_handwritten_passed_asset_is_rejected_even_with_F4_lineage` L188、`test_invalid_calibration_is_rejected_before_run_directory_creation` L243。
### tests/test_pose_backend.py

[打开源码](../../tests/test_pose_backend.py) · 178 行

直接引用的项目模块：`rallymate_vision.pose.adapters`、`rallymate_vision.pose.base`、`rallymate_vision.pose.metadata`、`rallymate_vision.pose.registry`。

- `StubBackend`：L15–L43；方法：`__init__` L16、`load` L21、`infer` L24、`metadata` L29。
- `PoseBackendTests`：L46–L178；方法：`test_roi_adapter_preserves_legacy_coco17_contract` L47、`test_backend_must_return_one_slot_per_roi` L76、`test_small_roi_guard_is_configurable_but_default_is_unchanged` L87、`test_halpe26_registry_preserves_official_first_17_order` L114、`test_coco_wholebody133_registry_preserves_official_groups` L136、`test_rtmpose_factory_forwards_registered_native_topology` L160。
### tests/test_pose_baseline.py

[打开源码](../../tests/test_pose_baseline.py) · 137 行

直接引用的项目模块：`rallymate_pose_evaluation.baseline`。

- `_keypoints`：L35–L59。
- `PoseBaselineTests`：L62–L133；方法：`test_nonuniform_timestamps_and_missing_are_diagnostic_not_zero_filled` L63。
### tests/test_pose_crop_context.py

[打开源码](../../tests/test_pose_crop_context.py) · 83 行

直接引用的项目模块：`rallymate_evaluation.pose_crop_context`。

- `_rows`：L22–L27。
- `PoseCropContextReplayTests`：L30–L79；方法：`test_real_m68_sources_replay_exact_composition_counts` L31、`test_unknown_changed_pose_source_fails_closed` L59。
### tests/test_pose_deployment_smoke_v2.py

[打开源码](../../tests/test_pose_deployment_smoke_v2.py) · 137 行

直接引用的项目模块：`rallymate_scoring.feasibility`。

- `_load_script`：L14–L21。
- `PoseDeploymentSmokeV2Tests`：L24–L133；方法：`setUpClass` L26、`_artifacts` L36、`test_contract_uses_all_registry_indicators_without_literal_six` L65、`test_contract_rejects_missing_registry_indicator` L79、`test_contract_rejects_uncalibrated_grade_or_threshold` L91、`test_launcher_exposes_three_profiles_and_explicit_v2_registry` L115、`test_smoke_report_language_is_dynamic_and_disclaims_accuracy` L125。
### tests/test_pose_diagnostic_review.py

[打开源码](../../tests/test_pose_diagnostic_review.py) · 431 行

直接引用的项目模块：`rallymate_evaluation.pose_diagnostic_review`。

- `_keypoint`：L25–L31。
- `_frames`：L34–L58。
- `_timeline`：L61–L71。
- `_event`：L74–L92。
- `_score`：L95–L117。
- `_source`：L120–L136。
- `PoseDiagnosticReviewTests`：L139–L427；方法：`_queue` L140、`test_exact_frame_deduplication_and_indicator_impact` L172、`test_generated_queue_cannot_contain_truth_or_scoring_claims` L208、`test_html_is_video_seekable_and_does_not_embed_absolute_workspace_paths` L220、`test_decision_csv_requires_every_task_and_annotator` L235、`test_current_real_bundle_builds_safe_deduplicated_queue` L262、`test_cli_writes_json_and_video_html_without_overwriting` L304、`test_decision_validation_cli_emits_non_adjudicated_report` L346、`test_schema_is_strict_about_safety_and_task_decisions` L404。
### tests/test_pose_diagnostic_truth.py

[打开源码](../../tests/test_pose_diagnostic_truth.py) · 527 行

直接引用的项目模块：`rallymate_evaluation.pose_diagnostic_review`、`rallymate_evaluation.pose_diagnostic_truth`。

- `_sha`：L31–L32。
- `_canonical_hash`：L35–L44。
- `_csv_text`：L47–L52。
- `PoseDiagnosticTruthTests`：L55–L523；方法：`_source_queue` L56、`_blank_pack` L197、`_coverage` L211、`_positives` L233、`test_blank_pack_is_video_first_and_cannot_claim_truth` L286、`test_blank_pack_overwrite_refuses_changed_annotation_file` L310、`test_blank_candidate_review_does_not_create_recall_metrics` L331、`test_full_timeline_truth_computes_exact_precision_recall_and_f1` L355、`test_validator_recomputes_metrics_instead_of_trusting_reported_rates` L378、`test_partial_coverage_is_explicitly_not_full_timeline_recall` L395、`test_truth_outside_accepted_coverage_and_weak_review_are_rejected` L414、`test_tampered_queue_binding_fails_closed` L433、`test_evaluation_binds_manifest_and_exact_input_file_content` L445、`test_cli_and_machine_schemas` L472。
### tests/test_pose_finetune_readiness.py

[打开源码](../../tests/test_pose_finetune_readiness.py) · 563 行

直接引用的项目模块：`rallymate_training.pose_finetune_readiness`。

- `_sha256`：L46–L47。
- `_canonical_sha256`：L50–L58。
- `_write_json`：L61–L65。
- `_write_jsonl`：L68–L75。
- `_write_csv`：L78–L84。
- `_binding`：L87–L88。
- `_build_pack`：L91–L228。
- `_write_governance`：L231–L232。
- `_governance_row`：L235–L249。
- `PoseFineTuneReadinessTests`：L252–L559；方法：`test_complete_halpe26_truth_and_governance_are_export_ready` L253、`test_ready_truth_without_governance_requires_governance` L283、`test_zero_truth_fails_closed_as_annotation_required_and_is_read_only` L293、`test_reviewer_must_be_independent_from_both_annotators` L324、`test_compiled_artifact_hash_mismatch_fails_closed` L345、`test_compiled_validation_status_must_explicitly_be_ready` L366、`test_subject_split_leakage_requires_governance_repair` L389、`test_source_path_and_hash_alias_cannot_cross_train_val` L420、`test_sealed_holdout_id_is_rejected_before_source_is_opened` L467、`test_sealed_holdout_path_and_declared_sha_aliases_are_rejected` L491、`test_shadow_registry_hash_drift_stops_the_audit` L522、`test_train_split_itself_must_cover_every_visible_halpe26_joint` L533。
### tests/test_pose_observability_residual.py

[打开源码](../../tests/test_pose_observability_residual.py) · 155 行

直接引用的项目模块：`rallymate_evaluation.pose_observability_residual`。

- `PoseObservabilityResidualTests`：L28–L151；方法：`setUpClass` L30、`test_real_m68_residual_is_exact_and_source_replayable` L36、`test_tampered_counts_or_safety_fail_closed` L58、`test_second_stage_transition_preserves_source_measurement_gate` L64、`test_high_resolution_report_cannot_hide_regression_or_enable_production` L109。
### tests/test_pose_observability_router.py

[打开源码](../../tests/test_pose_observability_router.py) · 214 行

直接引用的项目模块：`rallymate_evaluation.pose_observability_router`。

- `_point`：L19–L25。
- `_row`：L28–L45。
- `_shift_selected_pose`：L48–L52。
- `PoseObservabilityRouterTests`：L55–L210；方法：`test_real_registry_resolves_exact_pose_joint_scope` L56、`test_only_strict_valid_joint_superset_is_selected` L76、`test_candidate_cannot_change_non_pose_data_or_other_people` L110、`test_multicandidate_router_selects_unique_largest_valid_superset` L122、`test_unknown_registry_feature_fails_closed` L156、`test_report_cannot_hide_regression_or_enable_production` L165。
### tests/test_pose_playback.py

[打开源码](../../tests/test_pose_playback.py) · 241 行

直接引用的项目模块：`rallymate_vision.pose_playback`。

- `point`：L16–L17。
- `pose`：L20–L22。
- `frame`：L25–L29。
- `primary`：L32–L34。
- `write_rows`：L37–L38。
- `PosePlaybackTests`：L41–L175；方法：`build` L42、`test_pixel_coordinates_use_each_source_dimension_and_never_boxes` L51、`test_low_confidence_invalid_and_offscreen_points_are_missing_not_clamped` L63、`test_missing_joint_and_conflicting_duplicate_never_create_a_joint` L71、`test_primary_selects_one_exact_identity_and_ambiguous_frames_clear` L76、`test_primary_binding_and_duplicate_pose_fail_closed_without_another_player` L87、`test_missing_primary_allows_only_one_visible_person_and_splits_track_changes` L95、`test_sampling_keeps_original_times_and_does_not_extend_across_dropped_frames` L101、`test_long_time_gap_and_empty_pose_stop_previous_display` L112、`test_window_and_epoch_are_stable_across_paging` L119、`test_window_keeps_only_a_still_valid_preceding_observation` L127、`test_preceding_context_cannot_revive_a_missing_pose_or_cross_person_change` L136、`test_unverified_time_and_missing_pose_do_not_borrow_a_prior_joint` L144、`test_unknown_source_time_dimensions_or_crop_coordinates_do_not_overlay` L150、`test_nonmonotonic_frames_and_conflicting_primary_rows_are_rejected` L156、`test_read_limits_and_incomplete_tail_are_explicit` L163。
- `PosePlaybackApiTests`：L178–L237；方法：`test_existing_job_endpoint_is_authenticated_read_only_and_bounded` L179、`test_running_cache_invalidates_for_frames_and_primary_selection` L203、`test_missing_corrupt_and_inactive_jobs_fail_locally` L224。
### tests/test_pose_policy_composition.py

[打开源码](../../tests/test_pose_policy_composition.py) · 272 行

直接引用的项目模块：`rallymate_evaluation.pose_policy_composition`。

- `_frame`：L13–L23。
- `PosePolicyCompositionTests`：L26–L268；方法：`test_disjoint_same_track_pose_changes_are_composed` L27、`test_overlap_and_non_pose_mutation_are_rejected` L56、`test_missing_keypoint_addition_preserves_every_valid_baseline_point` L70、`test_missing_keypoint_addition_rejects_topology_or_non_pose_drift` L106、`test_mapped_topology_addition_preserves_halpe_identity_and_valid_points` L146、`test_mapped_topology_addition_rejects_format_map_or_non_pose_drift` L217。
### tests/test_pose_presets.py

[打开源码](../../tests/test_pose_presets.py) · 345 行

直接引用的项目模块：`rallymate_service.config`、`rallymate_vision.contracts`、`rallymate_vision.pose.presets`。

- `PoseDeploymentPresetTests`：L17–L341；方法：`test_online_and_analysis_presets_resolve_expected_models` L18、`test_service_preset_overrides_individual_pose_environment_fields` L55、`test_shadow_preset_is_explicit_and_does_not_change_the_default` L72、`test_shadow_preset_parses_in_request_contract` L97、`test_m71_candidate_registry_remains_an_immutable_historical_source` L134、`test_shadow_preset_is_exposed_by_explicit_startup_arguments` L159、`test_split_process_scripts_share_the_same_pose_preset_contract` L179、`test_yolo_baseline_remains_an_explicit_rollback` L225、`test_generic_requests_use_halpe26_and_current_scoring_registry` L239、`test_preset_config_falls_back_to_installed_mmpose_package` L263、`test_service_defaults_to_halpe26_and_keeps_wholebody_explicit` L306。
### tests/test_pose_profile_ablation.py

[打开源码](../../tests/test_pose_profile_ablation.py) · 96 行

- `_impact`：L12–L19。
- `_router`：L22–L34。
- `_report`：L37–L77。
- `PoseProfileAblationTests`：L80–L92；方法：`test_safe_regression_free_ablation_validates` L81、`test_hidden_regression_or_accuracy_claim_fails_closed` L84。
### tests/test_pose_profile_routing_audit.py

[打开源码](../../tests/test_pose_profile_routing_audit.py) · 165 行

- `_load_script`：L12–L19。
- `_summary`：L22–L40。
- `_fixed`：L43–L77。
- `_event`：L80–L101。
- `PoseProfileRoutingAuditTests`：L104–L161；方法：`setUpClass` L106、`test_fixed_boundary_coverage_retains_primary_without_accuracy_claim` L109、`test_validator_rejects_automatic_promotion` L137。
### tests/test_pose_same_frame_diagnostic.py

[打开源码](../../tests/test_pose_same_frame_diagnostic.py) · 357 行

直接引用的项目模块：`rallymate_evaluation.pose_same_frame_diagnostic`。

- `_sha`：L21–L22。
- `_write_json`：L25–L27。
- `_pose`：L30–L50。
- `_observations`：L53–L69。
- `PoseSameFrameDiagnosticTests`：L72–L353；方法：`_workspace` L73、`test_registry_binds_three_models_three_development_videos_and_does_not_open_holdout` L192、`test_registry_rejects_development_alias_of_holdout_by_hash` L199、`test_registry_rejects_default_registry_drift` L211、`test_same_frame_sampling_is_deterministic_consecutive_and_largest_track` L219、`test_coverage_denominator_includes_missing_pose` L266、`test_temporal_metric_is_named_as_observed_motion_not_jitter` L280、`test_repeatability_and_cross_model_agreement_are_descriptive` L291、`test_report_validator_rejects_accuracy_claim_and_sample_hash_drift` L310。
### tests/test_pose_shadow_smoke.py

[打开源码](../../tests/test_pose_shadow_smoke.py) · 163 行

直接引用的项目模块：`rallymate_evaluation.pose_shadow_smoke`。

- `_sha`：L16–L17。
- `_write_json`：L20–L22。
- `PoseShadowRegistryTests`：L25–L159；方法：`_workspace` L26、`test_registry_binds_default_candidate_and_development_inputs` L106、`test_checkpoint_tamper_is_rejected` L114、`test_holdout_cannot_appear_in_development_videos` L121、`test_sample_selection_is_stable_and_requires_declared_frame_count` L131。
### tests/test_primary_player.py

[打开源码](../../tests/test_primary_player.py) · 383 行

直接引用的项目模块：`rallymate_features.coordinates`、`rallymate_tracking.primary_player`、`rallymate_vision.pose.metadata`。

- `_pose`：L24–L45。
- `_record`：L48–L71。
- `PrimaryPlayerTests`：L74–L379；方法：`test_selection_threshold_preserves_020_boundary_without_lowering_measurement_gate` L75、`test_low_confidence_primary_stays_stable_during_brief_background_detection` L91、`test_nonfinite_or_out_of_frame_points_are_not_primary_pose_evidence` L105、`test_timeline_version_binding_rejects_legacy_or_mixed_artifacts` L124、`test_persistent_pose_track_beats_short_lived_larger_box` L156、`test_equivalent_players_are_order_invariant_and_report_ambiguity` L177、`test_single_candidate_does_not_claim_identity_accuracy` L222、`test_legacy_timeline_remains_diagnosable_without_false_uniqueness` L241、`test_primary_player_contract_accepts_new_and_legacy_timeline_records` L265、`test_fragmented_source_tracks_are_stitched_under_stable_primary_id` L290、`test_missing_pose_is_reported_not_zero_filled` L309、`test_diagnostics_recompute_validity_from_current_pose_artifact` L321、`test_out_of_frame_joint_is_excluded_from_current_pose_validity` L329、`test_jump_and_swap_diagnostics_preserve_joint_level_evidence` L342。
### tests/test_quality_policy.py

[打开源码](../../tests/test_quality_policy.py) · 733 行

直接引用的项目模块：`rallymate_features`、`rallymate_scoring.loop`、`rallymate_scoring.quality_policy`、`rallymate_scoring.scoring`。

- `_valid_feature`：L23–L33。
- `_point`：L36–L43。
- `_write_pose_fixture`：L46–L99。
- `_write_current_fixture_registry`：L102–L110。
- `IndicatorEventQualityPolicyTests`：L113–L729；方法：`test_phase_peak_fallback_preserves_measurement_but_blocks_only_dependent_indicator` L114、`test_malformed_phase_fallback_flag_is_rejected` L202、`test_truth_and_provisional_flags_do_not_block_F2_measurement` L208、`test_event_boundary_coverage_preserves_measurement_but_blocks_grade` L221、`test_primary_track_low_coverage_and_confirmed_id_switch_are_hard_failures` L252、`test_primary_pose_low_coverage_preserves_valid_F2_measurement_but_blocks_grade` L263、`test_restabilization_is_indicator_specific` L297、`test_required_phase_missing_is_an_indicator_measurement_hard_fail` L312、`test_malformed_required_phase_flag_is_rejected` L341、`test_candidate_diagnostics_are_advisory_and_traceable` L347、`test_unconfirmed_source_track_switch_keeps_measurement_but_blocks_grade` L367、`test_ambiguous_primary_identity_keeps_measurement_but_blocks_grade` L385、`test_fs02_target_direction_context_blocks_only_its_formal_grade` L395、`test_pose_diagnostic_blocks_affected_grades_but_not_F2_measurement` L425、`test_joint_scoped_jump_only_blocks_features_that_use_the_joint` L457、`test_joint_scoped_swap_only_blocks_features_that_use_the_pair` L497、`test_unclassified_flag_is_preserved_as_non_blocking_advisory` L540、`test_scoring_consumes_quality_without_inventing_grade_thresholds` L548、`test_invalid_feature_preserves_all_concurrent_score_only_blocks` L580、`test_hard_failure_preserves_concurrent_identity_and_swap_blocks` L610、`test_loop_marks_hard_failed_measurements_unavailable` L634、`test_loop_keeps_complete_features_under_sparse_primary_pose_as_F2_only` L678。
### tests/test_range_http_server.py

[打开源码](../../tests/test_range_http_server.py) · 340 行

- `_free_port`：L22–L25。
- `_request`：L28–L40。
- `RangeHTTPServerPrivacyTests`：L43–L336；方法：`_start_server` L44、`test_loopback_host_allowlist_ranges_and_private_path_denials` L67、`test_non_loopback_bind_is_rejected` L160、`test_request_path_is_percent_decoded_exactly_once` L185、`test_symlink_or_reparse_component_is_rejected` L226、`test_pose_scoring_launcher_root_cannot_serve_other_reports` L244、`test_pose_scoring_launcher_refuses_prebound_port` L280、`test_current_docs_launcher_is_scoped_and_refuses_prebound_port` L305。
### tests/test_registry_lifecycle.py

[打开源码](../../tests/test_registry_lifecycle.py) · 462 行

直接引用的项目模块：`rallymate_scoring.feasibility`、`rallymate_scoring.registry_lifecycle`。

- `RegistryLifecycleTests`：L48–L458；方法：`_copy_authority` L49、`_manifest` L62、`_write_manifest` L65、`_mutate_artifact_and_rebind_hash` L71、`_inject_raw_artifact_member_and_rebind_hash` L91、`test_checked_in_schema_and_manifest_are_strict_and_valid` L112、`test_checked_in_authority_matches_all_known_raw_hashes_and_versions` L136、`test_runtime_role_returns_same_validated_payload_as_low_level_loader` L177、`test_unknown_and_non_runtime_names_cannot_be_resolved_as_roles` L192、`test_runtime_roles_reject_wrong_lifecycle_and_kind` L200、`test_manifest_rejects_duplicate_paths_in_fixed_slots` L227、`test_manifest_rejects_normalized_path_aliases` L238、`test_relative_paths_reject_traversal_absolute_and_windows_drive_forms` L251、`test_raw_file_sha_binding_fails_before_changed_json_is_used` L286、`test_each_kind_requires_its_exact_embedded_version_field` L300、`test_each_derived_kind_requires_its_exact_source_registry_version` L340、`test_manifest_source_chain_cannot_be_redirected` L381、`test_duplicate_json_keys_are_rejected` L395、`test_noncanonical_json_is_rejected_in_manifest_and_artifact` L405。
### tests/test_registry_lifecycle_integration.py

[打开源码](../../tests/test_registry_lifecycle_integration.py) · 58 行

直接引用的项目模块：`rallymate_service.config`。

- `RegistryLifecycleIntegrationTests`：L12–L54；方法：`test_deployment_image_contains_current_lifecycle_authority` L13、`test_service_default_is_manifest_role_not_a_second_hardcoded_registry` L33、`test_production_source_does_not_reference_the_historical_registry` L49。
### tests/test_regression_suites.py

[打开源码](../../tests/test_regression_suites.py) · 89 行

- `NamedTest`：L9–L14；方法：`__init__` L10、`id` L13。
- `manifest`：L17–L21。
- `RegressionSuiteAccountingTests`：L24–L85；方法：`test_default_executes_all_and_inventory_is_explicit` L25、`test_partition_is_exhaustive_and_preserves_new_runtime_tests` L30、`test_method_selector_does_not_remove_synthetic_siblings` L38、`test_stale_duplicate_and_overlapping_selectors_fail_closed` L44、`test_fixture_error_never_turns_unstarted_methods_into_passes` L51、`test_subtest_failures_keep_record_and_method_denominators_separate` L72。
### tests/test_render.py

[打开源码](../../tests/test_render.py) · 60 行

直接引用的项目模块：`rallymate_vision.render`。

- `AnnotateFrameTests`：L11–L56；方法：`setUp` L12、`render` L25、`test_default_replay_is_clean_and_preserves_inference_evidence` L28、`test_detection_overlay_is_opt_in_and_filters_low_confidence` L37、`test_pose_overlay_can_be_enabled_without_detection_points_or_status` L46、`test_status_banner_is_opt_in` L53。
### tests/test_report_compatibility.py

[打开源码](../../tests/test_report_compatibility.py) · 162 行

直接引用的项目模块：`rallymate_scoring.stroke_candidates`、`rallymate_service.footwork_review`、`rallymate_service.report_compatibility`。

- `consume_with_main_client`：L22–L29。
- `ReportCompatibilityTests`：L32–L158；方法：`test_partial_footwork_stays_unavailable_in_legacy_indicator_contract` L33、`test_unsupported_classifier_is_never_mislabelled_as_rule_inference` L53、`test_original_client_consumes_all_api_views_and_artifacts_stay_native` L69。
### tests/test_residual_computability.py

[打开源码](../../tests/test_residual_computability.py) · 199 行

直接引用的项目模块：`rallymate_evaluation.residual_computability`、`rallymate_features.event_features`。

- `_feature`：L22–L38。
- `_event`：L41–L61。
- `_frame`：L64–L77。
- `ResidualComputabilityTests`：L80–L195；方法：`_build` L81、`test_classifies_structural_residuals_without_weakening_gates` L135、`test_validator_rejects_zero_fill_or_accuracy_claim` L155、`test_real_report_proves_all_13_have_measured_examples` L166、`test_video_ranges_merge_overlapping_residual_events` L179。
### tests/test_resumable_uploads.py

[打开源码](../../tests/test_resumable_uploads.py) · 209 行

直接引用的项目模块：`rallymate_service.api`、`rallymate_service.config`、`rallymate_service.database`、`rallymate_service.uploads`。

- `ResumableUploadTests`：L24–L205；方法：`test_out_of_order_resume_corruption_and_exactly_once_complete` L25、`_legacy_manifest` L62、`test_empty_legacy_session_requests_new_id_without_changing_old_protocol` L71、`test_partial_legacy_upload_reassembles_at_original_size_and_completes_once` L93、`test_completed_legacy_session_is_never_migrated_even_with_no_chunk_records` L120、`test_disk_corruption_is_removed_from_resume_manifest` L134、`test_expiry_and_identity_conflicts` L145、`test_http_upload_persists_and_queues_once_without_inference_on_parts` L165。
### tests/test_roi_margin_pose_recovery_experiment.py

[打开源码](../../tests/test_roi_margin_pose_recovery_experiment.py) · 211 行

- `_load_script`：L11–L20。
- `_frame`：L23–L69。
- `RoiMarginPoseRecoveryExperimentTests`：L72–L207；方法：`setUpClass` L74、`test_selector_targets_only_existing_pose_and_changed_crop` L77、`test_selector_can_preselect_only_clipped_baseline_rois` L105、`test_selector_rejects_nonexpanding_margin_and_duplicate_timeline` L128、`test_report_validator_keeps_results_experimental_even_with_gain` L154。
### tests/test_rotation_windows.py

[打开源码](../../tests/test_rotation_windows.py) · 102 行

直接引用的项目模块：`rallymate_scoring.rotation_analysis`。

- `phase`：L10–L11。
- `IndependentRotationWindowTests`：L14–L98；方法：`test_local_windows_do_not_promote_fragmented_episode_summary` L15、`test_phase_measurement_uses_only_corresponding_observed_window` L33、`test_phase_bounds_do_not_hide_clipped_or_missing_samples` L44、`test_short_and_unobserved_phases_keep_null_values` L57、`test_camera_epoch_and_normalization_changes_split_local_windows` L67、`test_local_window_keeps_minimum_duration_and_sample_count` L81、`test_reasons_distinguish_missing_axis_from_foreshortening` L87。
### tests/test_runtime_profile_binding.py

[打开源码](../../tests/test_runtime_profile_binding.py) · 479 行

直接引用的项目模块：`rallymate_scoring.calibration_fitting`、`rallymate_scoring.calibration_promotion`、`rallymate_scoring.runtime_profile_binding`、`rallymate_scoring.scoring`。

- `_runtime_fixture`：L47–L190。
- `_bind`：L193–L200。
- `RuntimeProfileBindingTests`：L203–L475；方法：`test_exact_binding_is_runtime_only_and_traceable` L204、`test_pose_backend_model_sha_and_native_topology_are_exact` L304、`test_event_primary_quality_and_feature_contract_are_registry_exact` L320、`test_profile_builder_rejects_runtime_pipeline_drift` L335、`test_view_group_protocol_and_all_constraints_are_mandatory` L374、`test_asset_registry_ledger_and_active_entry_identity_are_exact` L404、`test_duplicate_active_exact_binding_is_rejected` L434、`test_runtime_profile_and_view_contract_schemas_are_machine_readable` L445、`test_unknown_fields_and_tampered_profile_hash_fail_closed` L463。
### tests/test_runtime_registry_binding.py

[打开源码](../../tests/test_runtime_registry_binding.py) · 206 行

直接引用的项目模块：`rallymate_scoring.calibration_fitting`、`rallymate_scoring.calibration_promotion`、`rallymate_scoring.scoring`、`rallymate_vision.contracts`、`rallymate_vision.pipeline`。

- `_promoted_fixture`：L32–L58。
- `_features`：L61–L73。
- `RuntimeRegistryBindingTests`：L76–L202；方法：`test_ledger_authorization_exposes_promoted_registry_identity` L77、`test_equal_version_but_changed_content_is_rejected` L96、`test_different_registry_version_is_rejected` L106、`test_current_non_f4_indicator_is_rejected` L116、`test_ledger_authorized_but_registry_unbound_asset_cannot_score` L124、`test_trusted_asset_requires_exact_promoted_feature_contract` L141、`test_pipeline_rejects_unregistered_calibration_registry_before_creating_run_dir` L171。
### tests/test_score_explanation.py

[打开源码](../../tests/test_score_explanation.py) · 174 行

直接引用的项目模块：`rallymate_service.score_explanation`、`rallymate_service.training_evaluation`。

- `ScoreExplanationTests`：L12–L170；方法：`record` L13、`indicator` L16、`assert_closed` L20、`test_single_observation_excludes_repeatability_without_lost_points` L32、`test_missing_confidence_and_no_evidence_stay_excluded` L44、`test_ties_to_even_is_retained_and_accounted_for` L62、`test_aggregate_excludes_missing_indicators_and_preserves_both_rounding_steps` L71、`test_aggregate_component_weights_reflect_each_indicators_missing_components` L83、`test_feature_gaps_distinguish_missing_values_from_measurement_gate_exclusion` L96、`test_blocker_counts_overlap_and_replay_examples_are_bounded` L112、`test_duplicate_conflicting_and_wrong_event_records_do_not_inflate_explanations` L127、`test_nonfinite_boolean_and_malicious_measurements_never_form_explanation_points` L138、`test_invalid_weights_values_or_mismatched_score_fail_closed_without_recalculation` L151、`test_unknown_blocker_payload_is_not_reflected_as_explanatory_text` L161。
### tests/test_scoring_batch_audit.py

[打开源码](../../tests/test_scoring_batch_audit.py) · 239 行

直接引用的项目模块：`rallymate_service.training_evaluation`。

- `record`：L18–L24。
- `ScoringBatchFormulaAuditTests`：L27–L105；方法：`check` L28、`test_independent_formula_matches_single_missing_and_repeated_inputs` L36、`test_python_half_even_and_two_level_mean` L43、`test_conflicting_duplicate_and_wrong_event_cannot_add_evidence` L54、`test_repeatability_uses_circular_angles_and_category_frequency` L67、`test_changed_weight_score_or_explanation_is_reported` L82、`test_invalid_confidence_is_excluded_and_failure_is_json_safe` L98。
- `ScoringBatchSnapshotAuditTests`：L108–L235；方法：`setUp` L109、`write_artifact` L143、`audit` L149、`test_complete_fixture_passes_and_source_files_stay_unchanged` L152、`test_tampered_source_hash_and_frame_gap_fail` L159、`test_unapproved_grade_fails_even_with_self_reported_authority` L169、`test_pending_and_failed_entries_never_become_passes` L182、`test_completed_missing_artifact_fails_and_cli_preserves_existing_output` L194、`test_source_contract_requires_actual_matching_word_bytes` L205、`test_foreign_event_and_indicator_identity_are_not_accepted` L230。
### tests/test_scoring_blocker_audit.py

[打开源码](../../tests/test_scoring_blocker_audit.py) · 146 行

- `_load_script`：L12–L19。
- `_row`：L22–L36。
- `ScoringBlockerAuditTests`：L39–L142；方法：`setUpClass` L41、`test_decomposition_and_priority_stop_at_calibration_required` L44、`test_typed_reason_misattribution_is_rejected` L89、`test_extra_identity_reason_without_identity_flag_is_rejected` L106。
### tests/test_scoring_context.py

[打开源码](../../tests/test_scoring_context.py) · 565 行

直接引用的项目模块：`rallymate_scoring.batch`、`rallymate_scoring.calibration_dataset`、`rallymate_scoring.calibration_fitting`、`rallymate_scoring.indicator_feature_qualification`、`rallymate_scoring.loop`、`rallymate_scoring.quality_policy`、`rallymate_scoring.scoring_context`、`rallymate_tracking`。

- `_event`：L37–L43。
- `_accepted_worklist`：L46–L66。
- `_point`：L69–L76。
- `ScoringContextTests`：L79–L561；方法：`test_score_evidence_identity_overrides_untrusted_context_fields` L80、`test_pending_worklist_is_safe_and_machine_valid` L102、`test_accepted_reference_computes_wrapped_alignment_not_grade` L120、`test_reference_loader_hashes_the_exact_parsed_byte_snapshot` L212、`test_reference_rejects_self_review_and_stale_event_binding` L246、`test_loop_consumes_accepted_reference_without_inventing_grade` L267、`test_manual_target_semantic_enters_complete_calibration_vector` L470、`test_schema_accepts_pending_and_rejects_grade_fields` L540。
### tests/test_scoring_loop.py

[打开源码](../../tests/test_scoring_loop.py) · 680 行

直接引用的项目模块：`rallymate_scoring.feasibility`、`rallymate_scoring.loop`、`rallymate_scoring.scoring_loop_report`。

- `_point`：L17–L24。
- `ScoringLoopTests`：L27–L676；方法：`test_previous_feature_contract_cannot_silently_bind_isotropic_geometry` L28、`test_static_pose_produces_valid_no_event_unavailable_bundle` L43、`test_no_calibration_never_emits_grade_or_threshold` L122、`test_report_renders_event_level_grade_without_aggregate_grade` L241、`test_report_renders_all_contract_facts_and_all_event_instances` L368、`test_report_escapes_facts_and_bounds_long_feature_series` L581。
### tests/test_scoring_readiness.py

[打开源码](../../tests/test_scoring_readiness.py) · 324 行

直接引用的项目模块：`rallymate_scoring.granularity`、`rallymate_scoring.report`。

- `ScoringReadinessTests`：L34–L320；方法：`_frames` L35、`test_static_granularity_matches_298_card_contract` L77、`test_video_evidence_is_audited_without_fabricating_scores` L95、`test_six_minimum_loop_indicators_are_measured_not_reported_as_zero` L134、`test_pose_wave_v2_runtime_membership_drives_13_indicator_report` L189、`test_f4_scored_indicator_is_rendered_as_event_grade_without_total_score` L266。
### tests/test_scoring_run_bundle_binding.py

[打开源码](../../tests/test_scoring_run_bundle_binding.py) · 248 行

直接引用的项目模块：`rallymate_scoring.run_bundle_binding`、`rallymate_vision.pose.metadata`。

- `_summary`：L33–L54。
- `_ledger`：L57–L75。
- `ScoringRunBundleBindingTests`：L78–L244；方法：`test_exact_bundle_is_content_addressed_and_traceable` L79、`test_coordinated_summary_and_artifact_rewrite_is_not_authorized` L106、`test_wrong_video_revocation_and_duplicate_active_root_fail_closed` L121、`test_invalid_review_order_unknown_fields_and_template_are_rejected` L174、`test_contract_schema_accepts_the_runtime_validated_ledger` L198、`test_all_real_m78_summaries_can_be_bound_without_promoting_scores` L209。
### tests/test_scoring_truth_action_readiness.py

[打开源码](../../tests/test_scoring_truth_action_readiness.py) · 301 行

直接引用的项目模块：`rallymate_annotation.scoring_action_readiness`、`rallymate_evaluation.pose_diagnostic_truth`。

- `_inputs`：L35–L49。
- `_csv_text`：L52–L57。
- `ScoringTruthActionReadinessTests`：L60–L297；方法：`setUpClass` L62、`test_real_empty_truth_remains_fully_fail_closed` L65、`test_full_timeline_pose_truth_satisfies_only_pose_work_items` L90、`test_exact_manual_event_and_phase_evidence_satisfies_phase_item` L154、`test_self_declared_pose_evaluation_is_recomputed` L255、`test_self_declared_feature_evaluation_is_recomputed` L266、`test_validator_rejects_forged_satisfied_item` L277、`test_machine_schema_accepts_real_report` L288。
### tests/test_scoring_truth_action_worklist.py

[打开源码](../../tests/test_scoring_truth_action_worklist.py) · 153 行

直接引用的项目模块：`rallymate_annotation.scoring_action_worklist`。

- `_build`：L27–L39。
- `ScoringTruthActionWorklistTests`：L42–L149；方法：`setUpClass` L44、`test_current_worklist_covers_every_unavailable_instance` L47、`test_measurement_context_and_pose_causes_are_not_conflated` L61、`test_source_hash_drift_is_rejected` L105、`test_validator_rejects_dropped_instance_action` L114、`test_html_and_csv_are_video_first_and_non_scoring` L123、`test_machine_schema_accepts_real_worklist` L140。
### tests/test_scoring_truth_authorization.py

[打开源码](../../tests/test_scoring_truth_authorization.py) · 424 行

直接引用的项目模块：`rallymate_annotation.scoring_truth_authorization`。

- `_canonical_bytes`：L35–L42。
- `_sha256`：L45–L46。
- `_write_json`：L49–L54。
- `_manifest_technical`：L57–L69。
- `_plan`：L72–L93。
- `_release_record`：L96–L126。
- `_make_evidence`：L129–L145。
- `_verify`：L148–L153。
- `ScoringTruthAuthorizationTest`：L156–L420；方法：`setUp` L157、`tearDown` L167、`test_valid_local_operator_release_verifies_exact_m89_and_schemas` L171、`test_v1_plan_is_rejected_as_incompatible` L203、`test_release_binds_exact_plan_bytes` L212、`test_json_boolean_cannot_be_substituted_with_numeric_one` L221、`test_verification_uses_captured_plan_and_release_bytes_without_reopening` L228、`test_release_must_bind_all_exact_lineage_hashes` L252、`test_operator_review_and_annotation_only_limits_are_exact` L283、`test_claimed_release_chronology_is_enforced_but_not_trusted_time` L329、`test_duplicate_json_key_and_noncanonical_timestamp_fail_closed` L340、`test_template_is_deliberately_invalid_and_cannot_execute` L354、`test_manifest_and_artifact_mutation_cannot_bypass_exact_m89` L367、`test_binding_tamper_and_scope_escalation_are_rejected` L386、`test_crypto_dependency_and_obsolete_contracts_are_removed` L398。
### tests/test_scoring_truth_authorized_intake.py

[打开源码](../../tests/test_scoring_truth_authorized_intake.py) · 309 行

直接引用的项目模块：`rallymate_annotation.scoring_truth_event_phase_intake`、`rallymate_annotation.scoring_truth_intake`、`rallymate_scoring.scoring_truth_authorized_intake`、`rallymate_scoring.scoring_truth_calibration_authorization`。

- `_sha`：L41–L42。
- `_write`：L45–L46。
- `_csv`：L49–L54。
- `AuthorizedIntakeTests`：L57–L305；方法：`setUpClass` L59、`tearDownClass` L156、`setUp` L159、`verify` L162、`test_actual_m93_and_csv_replay_issues_only_process_local_binding` L170、`test_wrong_pin_scope_revision_revocation_and_unknown_fields` L179、`test_ledger_revocation_invalidates_live_object` L190、`test_compiled_and_original_csv_tampering_invalidates_live_object` L198、`test_untrusted_nested_ledger_and_duplicate_identity_are_rejected` L209、`test_duplicate_json_and_nonfinite_are_rejected` L218、`test_coherently_recompiled_invalid_truth_still_cannot_be_authorized` L224、`test_transient_projection_and_plan_bytes_cannot_differ_from_final_snapshot` L272、`test_approval_predating_evidence_and_collector_approval_are_rejected` L287、`test_closed_schema_and_runtime_validator_agree` L295。
### tests/test_scoring_truth_calibration_handoff.py

[打开源码](../../tests/test_scoring_truth_calibration_handoff.py) · 123 行

直接引用的项目模块：`rallymate_scoring.calibration_fitting`、`rallymate_scoring.calibration_handoff`。

- `ScoringTruthCalibrationHandoffTests`：L27–L119；方法：`setUpClass` L29、`test_empty_truth_builds_all_thirteen_nonready_contracts_without_scoring` L40、`test_latest_pointer_and_all_sources_replay` L61、`test_prepared_contracts_are_valid_but_fit_gate_rejects_every_indicator` L68、`test_replayed_fit_readiness_cannot_be_forged` L81、`test_existing_target_is_immutable` L89、`test_machine_schema_accepts_generated_manifest` L97、`test_current_published_handoff_is_fail_closed` L112。
### tests/test_scoring_truth_calibration_portfolio.py

[打开源码](../../tests/test_scoring_truth_calibration_portfolio.py) · 91 行

直接引用的项目模块：`rallymate_scoring.calibration_fitting`、`rallymate_scoring.calibration_portfolio`。

- `ScoringTruthCalibrationPortfolioTests`：L22–L87；方法：`setUpClass` L24、`test_empty_truth_portfolio_is_complete_but_fail_closed` L29、`test_all_thirteen_prepared_contracts_validate_but_fit_reject` L48、`test_status_cannot_be_forged_after_source_replay` L60、`test_unsafe_claim_is_rejected_without_source_replay` L68、`test_machine_schema_accepts_published_manifest` L74。
### tests/test_scoring_truth_event_execution.py

[打开源码](../../tests/test_scoring_truth_event_execution.py) · 1,027 行

直接引用的项目模块：`rallymate_annotation.scoring_truth_event_execution`。

- `_sha256`：L60–L61。
- `_canonical_file`：L64–L74。
- `_milliseconds_after`：L77–L81。
- `_recompute_annotation_submission_revisions`：L84–L134。
- `_run_node`：L137–L161。
- `ScoringTruthEventExecutionTests`：L164–L1023；方法：`setUpClass` L166、`tearDownClass` L310、`_copytree` L313、`_rewrite_declared_artifact` L318、`test_cross_language_end_to_end_and_role_topology` L355、`test_cross_language_same_start_ids_use_codepoint_order` L370、`test_schema_instances_and_exact_versions` L464、`test_canonical_bytes_role_and_revision_tamper_fail_closed` L479、`test_strict_json_duplicate_nonfinite_surrogate_and_timestamp_fail_closed` L500、`test_cross_language_integers_are_javascript_safe` L521、`test_phase_missing_is_not_zero_and_duration_bounds_are_enforced` L549、`test_annotation_and_adjudication_claimed_chronology_fail_closed` L570、`test_c_decision_cannot_postdate_same_video_review` L589、`test_zero_event_video_review_cannot_postdate_submission` L653、`test_c_source_coverage_and_exact_revision_binding_fail_closed` L669、`test_bundle_tamper_extra_file_and_portable_validation_fail_closed` L687、`test_portable_server_dynamic_port_and_read_only_methods` L707、`test_adjudication_bundle_cannot_predate_source_exports` L755、`test_adjudication_scope_frame_rate_must_equal_m89` L794、`test_execution_bundle_cannot_predate_future_claimed_release` L833、`test_coordinated_asset_rehash_cannot_replace_approved_ui` L891、`test_review_template_and_bootstrap_are_exact_in_portable_validator` L924、`test_declared_extra_artifacts_fail_exact_a_and_c_topology` L971、`test_c_source_and_reviewer_identity_tamper_fail_closed` L1012、`test_expected_manifest_names_are_not_generated_as_release_instances` L1020。
### tests/test_scoring_truth_event_handoff.py

[打开源码](../../tests/test_scoring_truth_event_handoff.py) · 501 行

直接引用的项目模块：`rallymate_annotation.scoring_truth_event_handoff`。

- `_tree`：L40–L45。
- `_canonical_bytes`：L48–L55。
- `_content_root`：L58–L59。
- `_write_manifest`：L62–L68。
- `_replace_file`：L71–L73。
- `_read_response`：L76–L94。
- `ScoringTruthEventHandoffTests`：L97–L497；方法：`setUpClass` L99、`tearDownClass` L111、`_copy` L114、`_coordinated_rehash` L119、`test_schema_runtime_source_replay_and_moved_standalone_validation` L130、`test_server_loader_executes_the_exact_verified_snapshot_bytes` L163、`test_exact_three_full_video_bytes_and_no_fourth_video` L186、`test_recursive_public_byte_scan_excludes_source_only_records_and_commitments` L202、`test_manifest_is_fail_closed_and_has_no_authorized_transition` L238、`test_workbench_is_playback_seek_and_frame_only` L256、`test_every_artifact_single_byte_tamper_is_rejected` L287、`test_coordinated_rehash_cannot_replace_bound_content` L297、`test_extra_fourth_video_is_rejected_even_after_manifest_rehash` L318、`test_build_requires_new_target_and_failure_leaves_no_final_or_staging` L354、`test_cli_source_replay_and_nonloopback_bind_fail_closed` L380、`test_snapshot_server_get_head_ranges_host_and_traversal` L425、`test_server_uses_validated_snapshot_after_disk_change` L480。
### tests/test_scoring_truth_event_phase_intake.py

[打开源码](../../tests/test_scoring_truth_event_phase_intake.py) · 650 行

直接引用的项目模块：`rallymate_annotation.scoring_truth_event_execution`、`rallymate_annotation.scoring_truth_event_phase_intake`。

- `_sha`：L41–L42。
- `_raw`：L45–L55。
- `_phases`：L58–L72。
- `_binding`：L75–L106。
- `_source_event`：L109–L123。
- `_review`：L126–L132。
- `SyntheticInputs`：L135–L433；方法：`__init__` L136、`video_ids` L203、`_bundle_snapshot` L206、`_annotation_submission` L240、`_adjudication_submission` L273、`_submission_snapshot` L358、`execution_api` L376、`kwargs` L397、`patched` L412。
- `ScoringTruthEventPhaseIntakeTests`：L436–L646；方法：`_ingest` L437、`test_positive_private_intake_preserves_raw_and_projects_final_event` L444、`test_manifest_validates_against_draft_2020_12_schema` L499、`test_generated_at_covers_all_source_manifests_and_exports` L512、`test_roles_are_distinct_case_insensitively_and_failure_is_atomic` L541、`test_missing_c_source_coverage_is_rejected_atomically` L556、`test_missing_video_adjudication_is_rejected` L574、`test_raw_compiled_and_topology_changes_fail_closed` L588、`test_cli_validation_error_is_exit_two_without_traceback` L624。
### tests/test_scoring_truth_evidence_plan.py

[打开源码](../../tests/test_scoring_truth_evidence_plan.py) · 133 行

直接引用的项目模块：`rallymate_annotation.scoring_evidence_plan`。

- `ScoringTruthEvidencePlanTests`：L21–L129；方法：`setUpClass` L23、`test_current_plan_covers_every_work_item_and_instance` L29、`test_full_timeline_pose_truth_is_deduplicated_by_type` L42、`test_every_input_work_item_has_at_least_one_unit` L62、`test_validator_rejects_forged_completion` L71、`test_source_replay_rejects_synchronised_forgery` L77、`test_writer_outputs_video_first_non_scoring_artifacts` L108、`test_machine_schema_accepts_real_plan` L119。
### tests/test_scoring_truth_intake.py

[打开源码](../../tests/test_scoring_truth_intake.py) · 370 行

直接引用的项目模块：`rallymate_annotation.scoring_truth_intake`。

- `_csv_bytes`：L30–L37。
- `_make_source`：L40–L73。
- `_exports`：L76–L77。
- `_canonical_root`：L80–L97。
- `ScoringTruthIntakeTests`：L100–L366；方法：`test_blank_intake_is_atomic_replayable_and_machine_schema_valid` L101、`test_invalid_annotations_leave_no_final_or_staging_tree` L145、`test_csv_encoding_header_column_and_repeated_header_are_fail_closed` L164、`test_export_membership_overlap_and_existing_output_are_rejected` L189、`test_original_source_and_export_tampering_are_detected` L217、`test_session_extra_missing_and_byte_tampering_are_detected` L238、`test_compiled_artifact_co_rehash_is_rejected_by_replay` L259、`test_authorization_invariant_is_replayed_not_inferred_from_source_status` L288、`test_generated_at_requires_a_real_canonical_utc_instant` L326、`test_cli_reports_validation_errors_as_exit_two_without_traceback` L339。
### tests/test_scoring_truth_priority_worklist.py

[打开源码](../../tests/test_scoring_truth_priority_worklist.py) · 59 行

- `_load_script`：L13–L18。
- `ScoringTruthPriorityWorklistTests`：L21–L56；方法：`setUpClass` L23、`test_real_worklist_is_hash_bound_video_first_and_non_scoring` L26、`test_hash_mismatch_is_rejected` L42。
### tests/test_scoring_truth_refresh.py

[打开源码](../../tests/test_scoring_truth_refresh.py) · 176 行

直接引用的项目模块：`rallymate_annotation.scoring_truth_refresh`。

- `ScoringTruthRefreshTests`：L55–L172；方法：`_build` L56、`test_current_latest_snapshot_is_hash_bound_and_fail_closed` L71、`test_full_refresh_uses_immutable_snapshots_and_publishes_latest_last` L81、`test_invalid_annotations_fail_preflight_without_latest_pointer` L101、`test_unbound_compiled_file_cannot_enter_refresh_snapshot` L129、`test_latest_pointer_hash_tampering_is_rejected` L146、`test_source_fingerprint_tampering_is_rejected` L155、`test_machine_schema_accepts_current_manifest` L161。
### tests/test_scoring_visual_observer.py

[打开源码](../../tests/test_scoring_visual_observer.py) · 204 行

直接引用的项目模块：`rallymate_visualization.scoring_observer`。

- `_jsonl_count`：L31–L33。
- `_sha256`：L36–L41。
- `_canonical_sha256`：L44–L52。
- `ScoringVisualObserverTests`：L55–L200；方法：`setUpClass` L57、`test_real_three_video_observer_replays_current_sources` L70、`test_manifest_and_video_payloads_match_v1_1_schemas` L108、`test_builder_requires_explicit_runs_root` L120、`test_every_indicator_is_visible_but_no_grade_or_threshold_is_created` L139、`test_pose_or_status_tampering_breaks_canonical_content_binding` L161、`test_rehashed_manifest_cannot_switch_source_summary` L173、`test_browser_assets_expose_video_pose_events_features_and_safety` L182。
### tests/test_service.py

[打开源码](../../tests/test_service.py) · 495 行

直接引用的项目模块：`rallymate_service.api`、`rallymate_service.config`、`rallymate_service.database`、`rallymate_service.worker`。

- `ServiceTests`：L19–L491；方法：`_settings` L20、`_video` L37、`test_upload_queue_worker_and_status_round_trip` L48、`test_succeeded_job_without_indicator_features_returns_empty_demo_result` L318、`test_bearer_auth_is_enforced` L370、`test_production_rejects_development_license` L384、`test_trusted_ledger_cannot_live_in_job_writable_directories` L398、`test_registry_authority_inputs_cannot_live_in_job_writable_directories` L416、`test_lifecycle_resolved_artifact_cannot_live_in_job_writable_directory` L440、`test_historical_registry_override_is_not_ready_without_calibration` L472。
### tests/test_service_boundaries.py

[打开源码](../../tests/test_service_boundaries.py) · 286 行

直接引用的项目模块：`rallymate_scoring.technique_registry`、`rallymate_service.api`、`rallymate_service.config`、`rallymate_service.database`。

- `ServiceBoundaryTests`：L21–L282；方法：`_settings` L22、`test_web_url_and_cors_validation_rejects_ambiguous_values` L36、`test_production_cors_wildcard_is_rejected` L53、`test_public_base_url_cors_wildcard_is_rejected_even_outside_production` L64、`test_production_readiness_requires_bearer_key` L74、`test_production_readiness_rejects_placeholder_and_short_keys_without_app_build_failure` L89、`test_readiness_does_not_expose_worker_paths` L118、`test_path_redaction_handles_spaces_and_embedded_paths` L138、`test_path_redaction_handles_bracketed_paths_and_nested_error_values` L144、`test_public_production_api_fails_closed_without_key` L163、`test_create_app_validates_injected_web_settings` L179、`test_cors_preflight_and_request_id_are_bounded` L186、`test_openapi_description_declares_technique_and_evidence_surface` L224、`test_registry_cache_refreshes_on_content_change` L237、`test_technique_registry_requires_source_document_hashes` L256、`test_calibration_assets_cannot_live_in_job_writable_roots` L266。
### tests/test_small_roi_keypoint_truth.py

[打开源码](../../tests/test_small_roi_keypoint_truth.py) · 246 行

直接引用的项目模块：`rallymate_evaluation.small_roi_keypoint_truth`。

- `_write_json`：L24–L25。
- `_write_jsonl`：L28–L32。
- `_write_csv`：L35–L39。
- `_frame`：L42–L80。
- `SmallRoiKeypointTruthTests`：L83–L242；方法：`setUp` L84、`tearDown` L140、`_complete_annotations` L143、`test_blank_pack_is_blind_and_does_not_report_accuracy` L186、`test_complete_independent_truth_produces_zero_error_metrics` L204、`test_reviewer_must_be_independent` L220、`test_immutable_task_tampering_is_rejected` L231、`test_evaluator_rejects_csv_changed_after_compilation` L238。
### tests/test_small_roi_pose_recovery_experiment.py

[打开源码](../../tests/test_small_roi_pose_recovery_experiment.py) · 160 行

- `_load_script`：L11–L18。
- `_frame`：L21–L31。
- `SmallRoiPoseRecoveryExperimentTests`：L34–L156；方法：`setUpClass` L36、`test_selector_only_targets_baseline_size_guard_skips` L39、`test_validator_rejects_production_or_regression_claims` L63、`test_operational_recovery_preserves_original_measurement_gate` L95。
### tests/test_small_roi_recovery_video.py

[打开源码](../../tests/test_small_roi_recovery_video.py) · 46 行

- `_load_script`：L11–L18。
- `SmallRoiRecoveryVideoTests`：L21–L43；方法：`setUpClass` L23、`test_event_ranges_merge_with_padding_and_preserve_codes` L26、`test_explicit_ffmpeg_path_must_exist` L41。
### tests/test_smoothing.py

[打开源码](../../tests/test_smoothing.py) · 51 行

直接引用的项目模块：`rallymate_features.smoothing`。

- `TimestampWeightedSmoothingTests`：L10–L47；方法：`test_vectorized_columns_preserve_weighted_finite_semantics` L11、`test_one_dimensional_series_shape_is_preserved` L42。
### tests/test_smoothing_counterfactual_coverage.py

[打开源码](../../tests/test_smoothing_counterfactual_coverage.py) · 1,029 行

直接引用的项目模块：`rallymate_evaluation.smoothing_coverage`。

- `_series`：L20–L24。
- `_row`：L27–L57。
- `SmoothingCounterfactualCoverageTests`：L60–L1025；方法：`test_m57_evidence_migration_preserves_production_feature_outputs` L61、`test_m58_acceleration_evidence_preserves_production_feature_outputs` L113、`test_m59_fs09_timing_evidence_preserves_production_feature_outputs` L164、`test_m60_fs01_m03_evidence_preserves_production_feature_outputs` L219、`test_m61_fs01_m04_evidence_preserves_production_feature_outputs` L283、`test_m62_fs02_m04_evidence_preserves_production_feature_outputs` L350、`test_m63_fs02_m05_evidence_preserves_production_feature_outputs` L425、`test_launch_direction_impact_uses_wrapped_circular_difference` L503、`test_real_m59_report_replays_all_bound_sources` L551、`test_real_m60_report_completes_fs01_m03_family` L653、`test_real_m61_report_covers_fs01_m04_family_without_relaxing_failures` L688、`test_real_m62_report_covers_fs02_m04_without_inventing_raw_side` L738、`test_real_m63_report_covers_all_pose_features_without_phase_drift` L801、`test_builds_exact_safe_decomposition` L862、`test_registry_lineage_mismatch_is_rejected` L937、`test_validator_rejects_accuracy_claim` L969、`test_validator_rejects_forged_feature_status` L993。
### tests/test_source_aligned_features.py

[打开源码](../../tests/test_source_aligned_features.py) · 385 行

直接引用的项目模块：`rallymate_events.associations`、`rallymate_features.schemas`、`rallymate_features.source_alignment`、`rallymate_scoring.source_aligned_features`。

- `event`：L19–L23。
- `fixture`：L26–L49。
- `indicator`：L52–L53。
- `feature`：L56–L57。
- `SourceAlignedFeatureTests`：L60–L368；方法：`setUp` L61、`test_reversed_preload_repaired_from_observed_turn_without_mutating_event` L65、`test_descent_already_in_progress_at_first_event_frame_is_censored` L85、`test_true_hip_width_ratio_and_zero_are_not_missing` L97、`test_source_joint_70_percent_is_conservative_simultaneous_mask` L110、`test_no_turn_boundary_peak_or_single_spike_is_not_repaired` L129、`test_phase_or_window_gap_cannot_bridge_missing_observations` L141、`test_successor_same_person_video_and_continuity` L151、`test_intervening_identity_switch_rejects_link_even_when_endpoints_match` L164、`test_confirmed_identity_switch_blocks_every_source_measurement` L173、`test_nonidentity_hard_failure_keeps_its_evidence_without_claiming_identity_switch` L191、`test_malformed_quality_flags_make_measurements_unavailable_without_aborting` L201、`test_pose_candidate_flags_do_not_become_confirmed_measurement_failures` L212、`test_confirmed_switch_count_must_be_unknown_or_nonnegative_integer` L220、`test_source_track_ids_require_nonnegative_integers_even_when_timeline_matches` L234、`test_supplied_identity_timeline_is_checked_for_each_local_measurement_window` L246、`test_missing_empty_or_conflicting_timeline_rows_never_fall_back_to_event_summary` L262、`test_identical_duplicate_timeline_rows_remain_compatible` L278、`test_identity_discontinuity_cannot_select_a_preload_turn` L285、`test_absent_timeline_preserves_legacy_event_diagnostic_measurements` L295、`test_conflicting_timeline_rows_and_successor_tracks_do_not_claim_verified_identity` L302、`test_zero_time_is_observed_when_both_candidates_share_the_exact_frame` L318、`test_degenerate_hip_width_invalid_confidence_and_unverified_frames_are_unavailable` L328、`test_fs10_only_associated_when_explicitly_supplied_and_no_successor_is_missing` L348、`test_preparation_is_reused_and_full_sequence_geometry_not_repeated` L360。
- `SuccessorIndexTests`：L371–L381；方法：`test_conflicts_ties_wrong_video_and_wrong_person_not_arbitrarily_selected` L372。
### tests/test_source_assessment.py

[打开源码](../../tests/test_source_assessment.py) · 159 行

直接引用的项目模块：`rallymate_scoring.source_assessment`、`rallymate_service.source_assessment`。

- `fixture`：L15–L32。
- `SourceAssessmentTests`：L35–L155；方法：`test_exact_seventy_percent_passes_visibility_without_a_grade` L36、`test_mismatched_video_source_and_invented_grade_fail_closed` L45、`test_wrong_person_out_of_bounds_and_duplicate_events_do_not_become_evidence` L53、`test_absent_or_nonfinite_measurement_is_not_imputed` L61、`test_identity_failures_keep_null_and_explain_the_reason_in_chinese` L69、`test_simultaneous_observed_successor_keeps_zero_without_imputing_missing` L87、`test_measurement_cannot_borrow_another_event_window_source_or_unit` L105、`test_truncation_preserves_full_counts_and_explicit_missing_examples` L116、`test_file_bindings_and_cache_invalidation` L125、`test_packaged_rules_are_reproducible_and_do_not_fix_source_conflicts` L146。
### tests/test_source_assessment_backfill.py

[打开源码](../../tests/test_source_assessment_backfill.py) · 73 行

- `BackfillTests`：L18–L69；方法：`fixture` L19、`test_preservation_guard_rejects_existing_baseline_mutations` L33、`test_preservation_guard_runs_even_when_measurement_writer_fails` L41、`test_statistics_distinguish_measurement_missing_from_zero_and_unique_phase_repairs` L52。
### tests/test_source_replay.py

[打开源码](../../tests/test_source_replay.py) · 113 行

直接引用的项目模块：`rallymate_vision.pipeline`、`rallymate_vision.source_replay`。

- `_timestamps`：L18–L26。
- `SourceReplayFailureTests`：L29–L71；方法：`setUp` L30、`_export` L39、`test_no_encoder_preserves_existing_output` L43、`test_empty_success_does_not_publish_stale_or_empty_output` L47、`test_timeout_and_encoder_failure_keep_old_output_and_clean_partial_file` L58、`test_replay_never_overwrites_its_source` L68。
- `SourceReplayVariableTimeTests`：L74–L109；方法：`setUpClass` L76、`test_real_vfr_clip_and_stride_preserve_all_selected_pts_including_last_frame` L94。
### tests/test_stroke_analysis.py

[打开源码](../../tests/test_stroke_analysis.py) · 440 行

直接引用的项目模块：`rallymate_scoring.rotation_analysis`、`rallymate_scoring.stroke_analysis`、`rallymate_scoring.stroke_candidates`。

- `make_frame`：L13–L23。
- `swing`：L26–L39。
- `serve`：L42–L48。
- `mirrored`：L51–L61。
- `motion`：L64–L65。
- `rotation_samples`：L68–L85。
- `RotationAnalysisTests`：L88–L212；方法：`test_known_independent_axes_use_real_timestamps_and_keep_score_unavailable` L89、`test_static_axes_are_observed_zero_motion_and_not_missing_measurements` L110、`test_independent_endpoint_flip_and_crossing_angle_wrap_do_not_make_spikes` L117、`test_one_foreshortened_axis_does_not_invalidate_other_axis_or_fabricate_separation` L127、`test_short_missing_axis_in_middle_is_never_stitched_into_a_full_rotation` L140、`test_missing_torso_sample_and_frame_index_hole_split_despite_short_timestamp_gap` L152、`test_identity_change_timestamp_reversal_and_scale_jump_cannot_be_bridged` L166、`test_equal_shoulder_and_hip_pose_spike_does_not_cancel_into_valid_separation` L181、`test_mirror_preserves_magnitudes_and_reverses_signed_series` L191、`test_empty_short_and_nonfinite_evidence_preserve_null_not_zero` L201。
- `StrokeMotionAnalysisTests`：L215–L436；方法：`test_forehand_is_measured_with_ordered_phases_and_no_contact_or_score` L216、`test_image_mirroring_and_left_handed_player_do_not_invert_forehand_label` L238、`test_single_and_two_hand_backhand_use_hand_and_wrist_relationships` L244、`test_serve_has_measured_phases_but_no_confirmation_of_ball_contact` L249、`test_analysis_peak_is_reestimated_independently_of_a_late_candidate_peak` L259、`test_clipped_motion_does_not_invent_completed_follow_through` L274、`test_trimmed_empty_or_short_classification_window_is_unclassified` L286、`test_recentered_preparation_and_recovery_do_not_double_count_one_motion` L293、`test_foreshortened_body_axis_remains_unclassified` L307、`test_shoulder_endpoint_order_flip_is_not_a_180_degree_body_rotation` L317、`test_projected_arm_collapse_withholds_elbow_angle` L327、`test_uniform_video_scaling_preserves_normalized_motion_metrics` L337、`test_primary_identity_change_cannot_be_bridged_even_for_one_frame` L350、`test_low_confidence_shoulder_keeps_independent_hip_without_stitching_shoulder` L355、`test_gap_and_pose_spike_do_not_create_measured_episodes` L371、`test_no_return_inferred_from_swing_without_opponent_and_ball_context` L380、`_return_frames` L387、`test_return_requires_opponent_serve_and_same_ball_track_approaching_receiver` L414、`test_ball_track_switch_or_outgoing_ball_cannot_impute_return` L422。
### tests/test_stroke_candidates.py

[打开源码](../../tests/test_stroke_candidates.py) · 214 行

直接引用的项目模块：`rallymate_scoring.stroke_candidates`、`rallymate_scoring.technique_assessment`。

- `frame`：L13–L25。
- `swing`：L28–L30。
- `serve`：L33–L42。
- `StrokeCandidateTests`：L45–L210；方法：`test_cross_body_swing_has_one_reviewable_interval_not_hit_count` L46、`test_ordered_overhead_motion_has_serve_candidate` L58、`test_arm_raise_without_toss_or_follow_through_is_not_serve` L65、`test_no_racket_no_swing_and_static_or_translated_body_not_motion` L70、`test_one_pose_spike_does_not_create_a_swing` L75、`test_identity_change_or_missing_pose_cannot_join_a_swing` L80、`test_background_players_and_their_rackets_are_not_primary_evidence` L90、`test_ambiguous_overlapping_people_do_not_share_one_racket` L99、`test_repeated_motion_after_recovery_produces_separate_intervals` L107、`test_low_confidence_or_out_of_frame_wrists_are_not_used` L118、`test_candidate_summary_cannot_impute_confirmed_registry_techniques` L125、`test_baseline_family_explains_unclassified_motion_without_imputing_subtypes` L139、`test_no_motion_distinguishes_unavailable_classifier_from_no_stroke` L153、`test_explicit_classified_event_is_not_overridden_by_candidates` L161、`test_left_handed_mirror_is_not_silently_dropped` L169、`test_opposite_arm_lift_after_overhead_swing_is_not_serve` L182、`test_ambiguous_primary_identity_is_excluded` L188、`test_candidate_labels_in_legacy_containers_cannot_become_observed` L196、`test_missing_artifacts_mean_unavailable_and_not_zero_hits` L203。
### tests/test_technical_review.py

[打开源码](../../tests/test_technical_review.py) · 272 行

直接引用的项目模块：`rallymate_service.api`、`rallymate_service.config`、`rallymate_service.database`、`rallymate_service.technical_review`。

- `TechnicalReviewTests`：L22–L268；方法：`setUp` L23、`submission` L45、`save` L59、`rejects` L62、`test_catalog_and_initial_get_are_source_bound_without_fabricated_reviews` L67、`test_save_restart_revision_history_and_exact_retry` L76、`test_two_reviewers_remain_independent_without_majority_resolution` L102、`test_concurrent_writes_have_one_winner_and_no_lost_history` L109、`test_mutation_id_is_idempotent_only_for_identical_content` L120、`test_revisions_cannot_change_person_source_window_or_author` L126、`test_all_source_blockers_disallow_grade_but_keep_unassessable_reason` L133、`test_no_unevaluable_or_partial_view_becomes_an_e_grade` L144、`test_interval_player_and_event_bindings_are_enforced` L153、`test_source_and_reference_identity_are_not_trusted_from_client` L169、`test_strict_input_rejects_unknown_fields_coercion_duplicates_and_nonfinite` L179、`test_compatible_artifact_change_after_get_requires_explicit_reload` L191、`test_visual_rules_record_observation_without_invented_grade_or_deduction` L202、`test_missing_malformed_or_duplicate_artifacts_fail_closed` L217、`test_unfinished_jobs_and_incomplete_video_metadata_fail_closed` L225、`test_history_cannot_be_updated_or_deleted` L230、`test_http_endpoints_auth_validation_and_export_use_same_persistent_contract` L237。
### tests/test_technique_assessment.py

[打开源码](../../tests/test_technique_assessment.py) · 231 行

直接引用的项目模块：`rallymate_scoring.technique_assessment`、`rallymate_scoring.technique_registry`、`rallymate_service.api`、`rallymate_service.config`、`rallymate_service.database`。

- `TechniqueAssessmentTests`：L18–L227；方法：`test_catalog_contains_all_document_families_without_numeric_grade_policy` L19、`test_invalid_source_hash_is_rejected` L37、`test_assessment_matches_versioned_contract` L48、`test_assessment_maps_legacy_events_and_keeps_contact_proxy_explicit` L68、`test_contact_status_uses_presence_only_and_primary_player_coverage` L95、`test_missing_events_are_not_imputed` L127、`test_event_scoped_phase_cannot_mark_other_techniques_measured` L145、`test_unscoped_or_contradictory_phases_are_not_measured` L156、`test_named_technique_and_event_alias_phases_remain_measurable` L169、`test_api_discovery_and_assessment_routes` L181。
### tests/test_temporal_duration.py

[打开源码](../../tests/test_temporal_duration.py) · 64 行

直接引用的项目模块：`rallymate_features.event_features`、`rallymate_features.fs01_fs02_features`、`rallymate_features.fs09_features`、`rallymate_features.temporal`。

- `TemporalDurationTests`：L13–L60；方法：`test_longest_means_elapsed_time_instead_of_sample_count_in_all_proxies` L14、`test_long_observation_gap_is_not_added_to_stability_or_support_duration` L28、`test_missing_event_indexes_and_missing_measurements_break_runs` L42、`test_gap_threshold_is_inclusive_and_cannot_inflate_endpoint_cadence` L52。
### tests/test_temporal_recognition.py

[打开源码](../../tests/test_temporal_recognition.py) · 250 行

直接引用的项目模块：`rallymate_scoring.stroke_candidates`、`rallymate_scoring.temporal_recognition`。

- `samples_from`：L15–L21。
- `TemporalWindowTests`：L24–L85；方法：`test_halpe_order_matches_the_existing_pose_adapter_contract` L25、`test_model_layout_has_real_confidence_and_explicit_missing_mask` L30、`test_sampling_uses_real_milliseconds_and_never_fills_a_missing_observation` L46、`test_missing_confidence_does_not_turn_into_certainty` L61、`test_identity_camera_time_and_normalization_boundaries_are_rejected` L70、`test_nonfinite_joint_is_masked_and_does_not_leak_nan` L80。
- `TemporalPipelineTests`：L88–L246；方法：`test_rules_are_explicitly_untrained_and_phase_contract_is_serializable` L89、`test_custom_backend_is_used_in_the_real_candidate_to_episode_path` L103、`test_missing_elbow_keeps_wrist_motion_and_body_axes_but_no_elbow_measurement` L128、`test_persistent_missing_shoulder_or_hip_keeps_the_other_axis_and_wrist` L139、`test_partial_torso_normalization_never_invents_a_swing_from_a_static_pose` L158、`test_missing_elbow_inside_a_motion_is_not_stitched_into_an_elbow_range` L165、`test_unavailable_or_broken_adapter_preserves_measurement_with_explicit_rule_fallback` L173、`test_non_mapping_adapter_output_uses_existing_invalid_output_fallback` L195、`test_unrelated_earlier_hand_votes_cannot_hide_or_relabel_a_later_swing` L212、`test_backend_cannot_invent_contact_timestamps_or_missing_stage_bounds` L230。
### tests/test_tracking.py

[打开源码](../../tests/test_tracking.py) · 168 行

直接引用的项目模块：`rallymate_vision.inference`、`rallymate_vision.tracking`、`rallymate_vision.utils`。

- `TrackingTests`：L10–L164；方法：`_ball` L12、`test_ball_identity_expires_by_source_time_at_different_fps` L15、`test_ball_tracking_gate_is_invariant_to_uniform_resolution_scale` L27、`test_simultaneous_balls_keep_separate_ids_and_timestamp_rewind_clears_tracks` L38、`test_timestamp_input_is_validated_without_changing_player_gap_policy` L45、`test_iou` L55、`test_track_id_is_stable_for_small_motion` L59、`test_classes_do_not_share_track_ids` L85、`test_near_identical_player_boxes_are_deduplicated` L105、`test_nested_player_duplicate_is_deduplicated` L122、`test_player_track_survives_longer_detection_gap` L138。
### tests/test_training.py

[打开源码](../../tests/test_training.py) · 172 行

直接引用的项目模块：`rallymate_training.config`、`rallymate_training.dataset`、`rallymate_training.registry`。

- `TrainingConfigTests`：L19–L46；方法：`test_detect_config_loads_and_resolves_paths` L20、`test_invalid_quality_gate_is_rejected` L26。
- `DatasetPreparationTests`：L49–L115；方法：`_make_manifest` L50、`test_prepare_writes_split_lists_without_session_or_subject_leakage` L89、`test_non_normalized_label_is_rejected` L103。
- `RegistryTests`：L118–L168；方法：`_config` L119、`test_promotion_requires_gates_and_version_is_immutable` L133。
### tests/test_training_evaluation.py

[打开源码](../../tests/test_training_evaluation.py) · 344 行

直接引用的项目模块：`rallymate_service.training_evaluation`。

- `TrainingEvaluationTests`：L9–L340；方法：`_record` L11、`test_builds_thirteen_evidence_reference_scores_without_technical_grade` L57、`test_single_sample_has_no_invented_repeatability_or_technical_praise` L95、`test_repeated_extreme_measurements_do_not_generate_coaching_approval` L110、`test_unmeasured_record_cannot_add_scoring_evidence` L124、`test_missing_features_cannot_claim_complete_scoring_evidence` L133、`test_direction_summary_respects_wraparound_and_ambiguous_opposites` L140、`test_independent_target_direction_context_is_included_when_present` L172、`test_duplicate_event_is_one_observation_not_a_repeat` L205、`test_conflicting_duplicate_event_is_rejected_in_either_order` L214、`test_event_family_mismatch_cannot_contribute_to_indicator` L224、`test_legacy_records_without_identity_never_establish_independent_repeats` L231、`test_unmeasured_and_out_of_scope_records_never_become_numeric_zero` L245、`test_different_absolute_measurements_are_described_without_technical_rank` L275、`test_partial_evidence_scores_never_become_technical_scores_or_missing_zero` L292、`test_missing_confidence_is_not_zero_and_is_excluded_from_effective_weights` L314、`test_internal_reason_codes_are_translated_not_exposed` L326。
### tests/test_trajectory.py

[打开源码](../../tests/test_trajectory.py) · 433 行

直接引用的项目模块：`rallymate_vision.trajectory`。

- `_frame`：L17–L56。
- `TrajectoryPreviewTests`：L59–L429；方法：`_write` L60、`test_ball_preview_has_observed_points_and_explicit_heuristic_prediction` L68、`test_missing_ball_is_not_promoted_to_prediction` L111、`test_untracked_observations_are_exposed_without_fake_track_id` L126、`test_malformed_frames_fail_closed` L137、`test_parameter_bounds_are_checked` L144、`test_artifact_limits_fail_closed_before_unbounded_materialization` L154、`test_prediction_reaches_long_requested_horizon_with_bounded_points` L167、`test_preview_matches_versioned_contract` L177、`test_reconstruction_keeps_multiple_segments_and_interpolates_short_gap` L197、`test_partial_reader_ignores_only_unterminated_last_row` L219、`test_all_tracks_are_kept_and_coverage_counts_frames_once` L229、`test_point_budget_preserves_long_track_endpoints_and_short_tracks` L246、`test_two_sided_motion_bridges_medium_gap_without_changing_observations` L259、`test_cross_id_bridge_uses_stricter_gap_limit` L282、`test_cross_id_bridge_requires_stricter_direction_agreement` L300、`test_untracked_detections_never_create_associated_motion` L312、`test_transitive_stitching_cannot_join_simultaneously_observed_ids` L331、`test_unsupported_gaps_remain_disconnected` L343、`test_competing_ball_fragments_are_not_arbitrarily_joined` L358、`test_simultaneously_observed_ids_cannot_be_stitched_later` L369、`test_sampling_preserves_interpolation_interval_provenance` L378、`test_interval_budget_marks_incomplete_provenance_explicitly` L394、`test_display_quality_is_source_based_without_claiming_active_ball_or_accuracy` L406、`test_untracked_display_quality_does_not_promote_positions_to_identity` L424。
### tests/test_trajectory_api.py

[打开源码](../../tests/test_trajectory_api.py) · 232 行

直接引用的项目模块：`rallymate_service.api`、`rallymate_service.config`、`rallymate_service.database`。

- `_frame`：L16–L40。
- `TrajectoryApiTests`：L43–L228；方法：`_app` L44、`_succeeded_job` L61、`test_trajectory_route_is_read_only_and_compatible_with_public_job` L94、`test_trajectory_route_requires_frames_artifact` L139、`test_trajectory_route_keeps_bearer_auth` L165、`test_running_preview_reads_complete_prefix_and_refreshes_after_append` L180、`test_running_preview_rejects_malformed_complete_row` L212、`test_failed_job_does_not_expose_partial_preview` L222。
### tests/test_truth_evaluation_cli.py

[打开源码](../../tests/test_truth_evaluation_cli.py) · 217 行

直接引用的项目模块：`rallymate_evaluation.scoring_truth`。

- `_write_jsonl`：L27–L31。
- `_event`：L34–L62。
- `TruthEvaluationCliTests`：L65–L213；方法：`test_input_snapshot_hashes_the_exact_bytes_supplied_to_the_parser` L66、`test_cli_computes_event_feature_metrics_and_error_budget` L86。
### tests/test_truth_pack.py

[打开源码](../../tests/test_truth_pack.py) · 670 行

直接引用的项目模块：`rallymate_annotation`、`rallymate_annotation.truth_pack`。

- `_csv`：L111–L115。
- `_write_pack`：L118–L150。
- `_events`：L153–L180。
- `_reviews`：L183–L193。
- `_keypoints_for_first_event_per_code`：L196–L222。
- `_unobservable_semantics`：L225–L261。
- `_coach_labels`：L264–L293。
- `TruthPackTests`：L296–L666；方法：`test_build_refuses_existing_output_before_writing` L297、`test_current_pack_version_requires_exact_canonical_scope` L316、`test_invalid_compile_preserves_existing_compiled_tree_byte_for_byte` L371、`test_truth_pack_scope_covers_all_current_pose_only_indicators` L402、`test_workbench_contract_matches_compiler_csv_headers` L422、`test_offline_workbench_embeds_only_manual_templates_and_all_js_selectors_exist` L428、`test_blank_pack_reports_missing_truth_without_generating_thresholds` L517、`test_only_fs01_cannot_satisfy_event_readiness` L530、`test_events_with_empty_required_phases_cannot_be_ready` L544、`test_two_coaches_on_one_indicator_cannot_satisfy_thirteen_indicator_matrix` L560、`test_unobservable_semantic_truth_requires_reason_and_never_gets_a_value` L581、`test_unobservable_semantic_without_reason_is_invalid` L615、`test_complete_explicit_matrix_can_reach_review_ready_without_thresholds` L632。
### tests/test_user_demo.py

[打开源码](../../tests/test_user_demo.py) · 547 行

直接引用的项目模块：`rallymate_service.user_demo`。

- `UserDemoResultTests`：L14–L543；方法：`test_history_contract_warns_without_overwriting_measurement_values` L15、`test_camera_and_source_time_warnings_describe_excluded_intervals` L28、`_summary` L40、`_record` L61、`test_builds_separate_formation_and_analysis_scores_without_formal_grade` L76、`test_amplitudes_exclude_unavailable_and_gate_denied_records` L182、`test_amplitudes_deduplicate_the_same_feature_within_each_event` L219、`test_formal_grade_in_summary_is_never_exposed_by_demo_v1` L265、`test_full_recognizable_information_reaches_100_without_becoming_a_grade` L284、`test_measured_status_without_valid_features_never_creates_reference_scores` L360、`test_invalid_records_cannot_inflate_compatibility_scores_in_a_mixed_report` L370、`test_unknown_indicator_ids_cannot_inflate_demo_scores_or_amplitudes` L398、`test_frontend_supports_batches_restore_and_user_facing_assessments` L441、`test_rejects_summary_that_is_not_completed` L476、`test_gs_action_counts_and_input_readiness_do_not_claim_hits` L480、`test_motion_candidates_are_passed_through_separately_from_hits` L492、`test_loads_valid_jsonl_and_skips_blank_lines` L506、`test_rejects_empty_jsonl` L518、`test_rejects_duplicate_json_key` L526、`test_rejects_nonfinite_json_numbers` L534。
### tests/test_validation.py

[打开源码](../../tests/test_validation.py) · 738 行

直接引用的项目模块：`rallymate_scoring.feasibility`、`rallymate_scoring.loop`、`rallymate_scoring.scoring_context`、`rallymate_vision.pose.metadata`、`rallymate_vision.validation`。

- `valid_record`：L24–L66。
- `_read_jsonl`：L69–L74。
- `_write_jsonl`：L77–L81。
- `_sha256`：L84–L85。
- `_quality_gate`：L88–L116。
- `_make_first_indicator_features_valid`：L119–L220。
- `OutputValidationTests`：L223–L734；方法：`test_validation_module_imports_in_fresh_interpreter` L224、`test_valid_pose_player_link` L238、`test_valid_halpe26_keeps_unregistered_extra_joints_null` L241、`test_valid_coco_wholebody133_preserves_first_17_downstream_mappings` L262、`test_frame_observation_contract_capacity_includes_wholebody133` L287、`test_orphan_pose_is_rejected` L309、`test_out_of_range_court_point_is_rejected` L315、`test_real_rtmpose_scoring_bundle_is_cross_artifact_valid` L321、`test_current_context_evidence_video_identity_is_cross_artifact_bound` L352、`test_target_direction_block_cannot_be_removed_with_pending_context` L473、`test_scoring_bundle_accepts_all_valid_features_with_hard_quality_fail` L518、`test_scoring_bundle_accepts_explicit_measurement_block_without_hard_fail` L528、`test_scoring_bundle_without_hard_fail_uses_required_feature_validity` L542、`test_scoring_bundle_rejects_non_unavailable_status_on_hard_quality_fail` L562、`test_v04_bundle_cannot_hide_concurrent_score_block_reasons` L579、`test_scoring_bundle_rejects_score_indicator_status_mismatch` L617、`test_scoring_bundle_rejects_feature_value_cross_layer_mismatch` L637、`test_current_bundle_hashes_reject_synchronised_artifact_tamper` L652、`test_no_event_unavailable_scoring_bundle_is_valid` L699。
### tests/test_video_spec_invariance.py

[打开源码](../../tests/test_video_spec_invariance.py) · 99 行

直接引用的项目模块：`rallymate_events.rules`、`rallymate_scoring.stroke_candidates`、`rallymate_vision.camera_motion`、`rallymate_vision.quality`、`rallymate_vision.timebase`。

- `VideoSpecInvarianceTests`：L17–L95；方法：`test_actual_variable_frame_intervals_are_preserved` L18、`test_broken_decoder_time_is_explicit_and_monotonic` L25、`test_portrait_and_landscape_quality_spec_warning_matches` L34、`test_background_guard_distinguishes_pan_and_player_motion` L38、`test_featureless_background_is_unavailable_not_stable` L55、`test_camera_motion_blocks_translation_bouts` L64、`test_stroke_sample_rejects_unverified_motion_time` L73、`test_local_threshold_is_independent_of_remote_padding` L77、`test_local_threshold_is_stable_across_regular_frame_rates` L88。
### tests/test_vision_cli_registry_lifecycle.py

[打开源码](../../tests/test_vision_cli_registry_lifecycle.py) · 74 行

直接引用的项目模块：`rallymate_scoring.registry_lifecycle`、`rallymate_vision`。

- `VisionCliRegistryLifecycleTests`：L14–L70；方法：`_request` L15、`_summary` L26、`test_default_operator_manifest_is_forwarded_to_pipeline` L34、`test_explicit_operator_manifest_overrides_the_default` L48。
### tests/test_worker_cpu_threads.py

[打开源码](../../tests/test_worker_cpu_threads.py) · 257 行

直接引用的项目模块：`rallymate_service`、`rallymate_service.config`。

- `_FakeTorch`：L14–L35；方法：`__init__` L15、`get_num_threads` L21、`set_num_threads` L24、`get_num_interop_threads` L28、`set_num_interop_threads` L31。
- `_FakeCv2`：L38–L48；方法：`__init__` L39、`getNumThreads` L43、`setNumThreads` L46。
- `WorkerCpuThreadTests`：L51–L253；方法：`test_service_settings_default_and_environment_override` L52、`test_invalid_environment_value_is_diagnostic` L72、`test_configures_and_verifies_all_runtime_thread_pools` L99、`test_same_limit_is_idempotent_for_torch_interop` L117、`test_pool_configuration_failure_names_pool_and_requested_value` L128、`test_persistent_runner_limits_threads_before_device_and_model` L139、`test_thread_configuration_failure_prevents_device_and_model_loading` L182、`test_worker_scripts_set_blas_and_opencv_environment_before_python` L207。

## training

### training/configs/rtmpose_x_halpe26_384x288_rallymate.py

[打开源码](../../training/configs/rtmpose_x_halpe26_384x288_rallymate.py) · 175 行

Fail-closed RallyMate fine-tune overlay for RTMPose-X / Halpe26.

此文件属于配置、模板、样式、脚本或未匹配到顶层声明的模块，请直接阅读文件。
