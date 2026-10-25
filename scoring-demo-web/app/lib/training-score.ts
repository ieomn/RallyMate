import type { DemoResultResponse, ProcessEvaluation } from "./api-types";
import { cardExecutionOf } from "./card-execution";
import { isFormalScoringPolicy } from "./formal-card-scoring-policy";
import { processEvaluationOf } from "./process-evaluation";

/** Select one validated result; stored totals and evidence-quality scores are not inputs. */
export function trainingScoreOf(result: DemoResultResponse | null, prepared?: ProcessEvaluation | null) {
  const cards = cardExecutionOf(result?.card_execution);
  if (cards && isFormalScoringPolicy(cards.scoring_policy_version)
      && (!result?.job_id || cards.source_run_id === result.job_id)
      && cards.summary.formal_scored_instances > 0 && cards.overall_score !== null) {
    return { score: cards.overall_score, source: "validated_card_execution" as const,
      measuredIndicatorCount: cards.summary.formal_scored_cards, observedDimensionCount: null };
  }
  const process = prepared === undefined ? processEvaluationOf(result?.process_evaluation) : prepared;
  const reference = process?.relative_reference;
  return { score: reference?.overall_score ?? null,
    source: reference?.overall_score !== null && reference?.overall_score !== undefined
      ? "observed_process_reference" as const : "unavailable" as const,
    measuredIndicatorCount: null, observedDimensionCount: reference?.observed_dimension_count ?? 0 };
}
