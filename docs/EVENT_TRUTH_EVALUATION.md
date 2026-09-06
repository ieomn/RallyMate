# Event truth evaluation semantics

`rallymate_events.evaluate_events` evaluates candidate FS01/FS02/FS09 event
intervals against a non-empty manual event-truth set. Candidate-to-truth
association is not itself an accuracy claim and its minimum Segment IoU is not an
A-E scoring threshold or an acceptance criterion.

## One-to-one association

Events are first partitioned by all of the following keys:

1. resolved video identity;
2. `event_code`;
3. `person_track_id`.

Video identity is resolved from top-level `video_id`, then from
`provenance.source_id`. A non-empty truth evaluation rejects every unscoped event;
it never guesses that two missing identities refer to the same video. Conflicting
top-level and provenance identities are also rejected. This prevents intervals at
similar timestamps from different videos, event types, or player tracks from
being paired.

Within each partition, matching is a deterministic global bipartite optimum. The
objectives are lexicographic:

1. maximise the number of one-to-one matches whose Segment IoU satisfies the
   caller-supplied association protocol;
2. among those maximum-cardinality solutions, maximise total Segment IoU;
3. when both values tie, use stable event ordering and residual-edge order.

The implementation uses an exact-rational minimum-cost maximum-flow calculation,
so it does not have the descending-IoU greedy failure mode where one locally high
IoU pair suppresses two globally valid pairs.

## Metric meaning

Event precision, recall, F1, mean Segment IoU, and start/end Boundary MAE/P95 are
computed only after matching against manual truth. Key-phase Boundary MAE/P95 and
valid rate use only non-null truth phases; a missing predicted phase lowers phase
validity and is not converted to zero error.

If the truth list is empty, `status` is `ground_truth_required`. Accuracy fields,
false-positive/false-negative counts, and per-code F1 values remain `null`.
Candidate counts are retained only as unverified inventory. An empty truth input
therefore cannot appear as a perfect evaluation or as a measured zero-accuracy
result.

Cross-model candidate-event comparison is a separate agreement diagnostic. It
must not be relabelled as truth evaluation, accuracy, or evidence of scoring
readiness.
