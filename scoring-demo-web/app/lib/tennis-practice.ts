import type { TennisImprovement } from "./tennis-assessment";
import type { TrainingFocusSelection } from "./training-guidance";
import { productText } from "./presentation-text";

/** The saved practice target keeps its source time and the same visible wording. */
export function tennisImprovementFocus(item: TennisImprovement): TrainingFocusSelection {
  return { title: productText(item.title), question: `${productText(item.action)} 复测：${productText(item.retest)}`,
    ...(item.start_ms === null ? {} : { timestampMs: item.start_ms }) };
}
