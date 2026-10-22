# Five-card continuous-window evidence

`temporal-card-evidence-v1.0.0` implements joint visual conditions for FS01-M02,
FS01-M05, and FS09-M03/M04/M05. The source remains the 298-card Word catalogue.
The numeric cutoffs in `CARD_CONDITIONS` are **provisional engineering rules**,
not thresholds stated in Word or experimentally calibrated coaching standards.
The engine never emits an original-card grade. A consumer may apply the separately
versioned `condition_completion_v1` policy only to a `measured_visual_proxy` row;
`needs_review` rows must retain null grades, regardless of their numeric values.

## Input and evidence gates

`compute_temporal_card_evidence(sequence, event, events=..., primary_timeline=...)`
accepts an immutable event snapshot and the camera-compensated `PoseSequence`.
Every card includes the candidate window, actual source frame numbers, timestamps,
required joints, valid-frame numerator and full sampled-window denominator.
FS09-M04 requires both shoulders, hips and ankles. The other four cards also require
both knees. Every counted frame requires all of that card's joints simultaneously.

The source gate is at least 70% required-joint observations. A derivative condition
also requires an uninterrupted trajectory, at least three samples and 80 ms, exact
observed endpoints, one camera-reference epoch, and no source-frame gaps. No
interpolation connects occlusion, subject switches, or missing source frames.
Durations, speed denominators and successor intervals use verified integer
decoder PTS/timebase; rounded playback timestamps select/display frames only.
An interval displayed as 80 ms but actually 79.8 ms does not pass the duration gate.
The reference transform must be an applied, valid similarity transform. The
normalizing scale is the one median shoulder-midpoint-to-hip-midpoint distance
inside the candidate, with at least 12 source pixels at every sample.

For source-bound SAM geometry, the same fraction is explicitly **geometry support**;
`visibility_verified=false` and the Word 70% visibility result remains unknown.
No numeric confidence or visible-joint claim is invented. Actual frame selection
must retain the same unambiguous player/source track through the evaluated window
and across a successor-event gap. Existing hard-failure and scoring-block quality
flags remain active. Event candidates and their phases are not ground truth.

The local `temporal-card-local-quality-v1` gate always retains low pose/kinematic
coverage and required-joint jump/swap candidates for review, independently of
legacy production-grade policy changes. A jump or swap diagnostic can be ignored
only when its complete named-joint evidence is disjoint from this card's required
joints; missing diagnostic provenance remains blocking. Raw input flags, local
review flags and irrelevant-joint flags are exported with every evaluated row.

## Calculations and candidate boundaries

- FS01-M02 uses the existing independently supported pre-takeoff descent turn.
  Its five conditions are reference hip descent / fixed torso scale ≥ 0.03;
  left and right knee flexion change each ≥ 4°; simultaneous hip descent and
  bilateral knee flexion in at least 70% of the window's interval duration;
  and maximum adjacent body-speed change / max(mean body speed, 0.05) ≤ 1.5.
  Hip descent never subtracts ankle motion. Flexion is 180° minus the 2D internal
  hip-knee-ankle angle. All five conditions use the same interval.
- FS01-M05 begins at the landing candidate and ends at the earlier of event end
  and the next same-player FS02 onset. Conditions are hip projection inside the
  horizontal ankle interval in at least 80% of frames; body-centre interval-speed
  standard deviation ≤ 0.3 torso/s; and landing-to-FS02 interval ≤ 500 ms.
  A missing FS02 is missing evidence, never zero delay or grade E.
- FS09-M03 begins at the observed peak-speed candidate. It ends at the first
  reported restabilization boundary if at least 80 ms later and within 600 ms;
  otherwise it ends at the last sampled frame within that 600 ms bound or event.
  Conditions are first-minus-last adjacent hip speed ≥ 0.2 torso/s and ≥ 25%
  of initial speed, bilateral flexion changes each ≥ 4°, hip descent ≥ 0.03
  torso, trunk-tilt standard deviation ≤ 10°, and simultaneous slowing, descent
  and bilateral flexion during at least 50% of eligible interval duration.
  Whole-event first/last quartiles are not used.
- FS09-M04 starts at the deceleration-peak candidate. Conditions are at least
  80% projected support; a continuous supported low-motion run ≥ 120 ms; and
  first-minus-last maximum shoulder/hip axis angular speed ≥ 5°/s. The low-motion
  run requires hip speed ≤ 0.6 torso/s, both ankle speeds ≤ 0.5 torso/s, and the hip
  inside the ankle interval at both interval endpoints. Mere immobile feet do not
  satisfy the support proxy when the hip is outside their interval.
- FS09-M05 starts at the stable-control candidate, stopping at the earlier of event
  end and the next same-player FS10/FS02 onset. Conditions are body-speed standard
  deviation ≤ 0.3 torso/s, the supported low-motion run ≥ 120 ms, and successor
  interval ≤ 500 ms. Missing successors or discontinuous identity require review.

Body centre is the midpoint of shoulder midpoint and hip midpoint. Speeds use
adjacent observed displacements divided by adjacent elapsed time and the fixed
torso scale. Shoulder and hip axis angles are unwrapped before differentiation.
None of these quantities measures physical centre of mass, energy, force, true
plantar contact, or 3D balance. These limitations accompany every output row.

## Bounded fallback and real negative example

When the pre-takeoff locator cannot form a complete interval, a fallback searches
for fully observed local hip turns before the existing landing candidate (or
event end). It requires at least two descending and two ascending changes,
observed surrounding samples, the same quality gates, and at least 80 ms of
descent. It selects the earliest complete candidate, not the highest-scoring
candidate. Original events are never rewritten and takeoff is not inferred.

These candidates always remain `needs_review` with
`fallback_local_dip_action_type_unverified`, retaining measurements but no passed
condition flags. Actual viewing of source video `71D3F59B7A8B966EF7645CAC412CB03679376F70A2F080D270391A32697D7FA9`
at 1.100, 1.133, 1.467, 1.533, 1.633, 1.733, 1.833 and 2.000 seconds showed a
player holding a racket and adjusting the feet/heel. A local dip at 1.467–1.633 s
does not establish split-step preloading. This is an agent visual review, not an
expert biomechanical annotation. The raw-coordinate regression fixture preserves
source frames 33–124 and the original early takeoff candidate (1.133 s); it proves
that an available dip cannot silently become an automatic preload grade.

## Verification on 2026-10-22

The new engine was actually replayed against all 2,911 frame observations from
run `0a3df3a3-2629-4dd5-80a1-54e4b11f7341` (97.000 s last-frame timestamp;
97.033 s media duration). Eighty existing FS01/FS09 candidates produced 200 card
instances: 109 with complete visual measurements and 91 requiring review. Per
card, measurable/review counts were FS01-M02 3/37, FS01-M05 24/16, FS09-M03 27/13,
FS09-M04 28/12 and FS09-M05 27/13. Original automatic grades remain zero.
FS09 event `fs09-007-856a3e655a74` retains the input flag
`pose_kinematic_coverage_low` as a local review reason. A prior replay that relied
only on the relaxed legacy policy reported 110/90; that count is superseded.

Five additional local-dip candidates were retained for review at 1.467–1.633,
24.333–24.500, 33.200–33.367, 37.133–37.433 and 77.967–78.100 seconds. They are
not five confirmed detections or grading improvements. The unreviewed candidates
need independent action labels. Frequent limitations were missing/censored phase
boundaries, 27 instances below the required geometry fraction, and seven instances
with torso scale below 12 pixels. These counts measure evidence availability,
not detector precision/recall or human-validated score accuracy.

`tests/test_temporal_card_evidence.py` covers fixed-reference translation, temporal
joint conditions, 70% denominator retention, missing successors, identity gaps,
camera/frame discontinuities, quality hard failures, local joint requirements,
and the actual source-coordinate negative example. Run with `PYTHONPATH=src` and
`python -m unittest discover -s tests -p test_temporal_card_evidence.py -v`.

## Service adapter

`rallymate_service.card_execution.build_card_execution_report(run_directory)`
reads `frames.jsonl`, `events.jsonl` and `primary-player.jsonl` once, constructs one
pose sequence/context, and recomputes every supported event. Saved score files,
pass flags and grades never authorize output. The report always links 50 FS and
248 GS cards, but explicitly reports only five wired temporal calculators.
Observed cards without a connected calculator remain `not_executed`; this is
separate from cards whose event type was not observed and from manual review.

The adapter verifies engine, metric, unit, required-joint and threshold agreement
with the catalogue, then compares fresh measurements. `needs_review` inputs
become `review_required` with null proxy grade and score. `measured_visual_proxy`
instances use the declared condition-completion mapping, separately labelled
`proxy_scored`. Successor delay is explicitly bounded between 0 and 500 ms.
Both all-298 coverage and observed-event-type coverage are reported. Missing
cards remain in the denominator and outside score means; original/proxy/manual
channels stay separate and `overall_score` remains null.

A cache retains at most two completed reports, keyed by input file sizes and
nanosecond modification times, catalogue content hash and engine versions.
Raw frames are not cached. Concurrent requests share one computation; returned
copies cannot alter cached reports. Changed inputs invalidate the cache, and an
input mutation during calculation is rejected. JSON parsing rejects duplicate
keys, non-finite values and excessive file/record sizes.
Every original frame record must carry the same `job_id`, matching the run
directory and every event's `video_id`. Mixed videos or conflicting run IDs are
rejected before scoring. The sequence-only helper requires a trusted
`source_run_id` argument or that ID in its coordinate metadata. Identical event
duplicates are idempotent; conflicting snapshots remain review with null scores.

The adapter was actually run on the complete 97-second run: 200 instances yielded
109 `proxy_scored` and 91 `review_required`, covering five of 298 source cards
with engineering proxy output and zero original grades. The mean proxy condition score
is not a validated tennis-skill score. Service tests independently cover real
coordinate replay, score forgery resistance, conflicting event snapshots,
run/video identity, rule drift, cache invalidation/isolation and input validation.

The replay evidence sidecar binds the report's raw SHA-256, the four original
input artifacts, and every imported RallyMate module plus runtime JSON resource.
Source digests record both raw bytes and UTF-8 text with CRLF/CR normalized to LF.
The catalogue digest is separately declared `canonical_json_sorted_utf8_v1`;
it must not be compared with the catalogue file's raw byte digest. Summary JSON
is hashed for provenance only and is never a scoring input.
