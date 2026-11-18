import type { TennisAssessment, TennisImprovement } from "./tennis-assessment";
import type { TrainingFocusSelection } from "./training-guidance";
import { productText } from "./presentation-text";

/** Preserve the report's priority order and only lead with a measured dimension. */
export function primaryTennisPractice(report: TennisAssessment | null) {
  return report?.improvements.find(item =>
    report.dimensions.some(dimension => dimension.id === item.dimension_id && dimension.score !== null)
  );
}

/** The saved practice target keeps its source time and the same visible wording. */
export function tennisImprovementFocus(item: TennisImprovement): TrainingFocusSelection {
  return { title: productText(item.title), question: `${productText(item.drill)} ${productText(item.action)} 复测：${productText(item.retest)}`,
    ...(item.start_ms === null ? {} : { timestampMs: item.start_ms }) };
}
