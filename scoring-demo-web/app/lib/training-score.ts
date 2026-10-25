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
    const eventCodes = [...new Set(cards.cards.filter(card => card.instances.some(instance => instance.status === "scored")).map(card => card.event_code))];
    const onlyFootwork = eventCodes.length > 0 && eventCodes.every(code => code.startsWith("FS"));
    return { score: cards.overall_score, source: "validated_card_execution" as const,
      measuredIndicatorCount: cards.summary.formal_scored_cards, observedDimensionCount: null,
      scoreLabel: onlyFootwork ? "步伐动作得分" : "已测动作得分", onlyFootwork,
      scoredInstanceCount: cards.summary.formal_scored_instances };
  }
  const process = prepared === undefined ? processEvaluationOf(result?.process_evaluation) : prepared;
  const reference = process?.relative_reference;
  return { score: reference?.overall_score ?? null,
    source: reference?.overall_score !== null && reference?.overall_score !== undefined
      ? "observed_process_reference" as const : "unavailable" as const,
    measuredIndicatorCount: null, observedDimensionCount: reference?.observed_dimension_count ?? 0,
    scoreLabel: "已测动作得分", onlyFootwork: false, scoredInstanceCount: null };
}
