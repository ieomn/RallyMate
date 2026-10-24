import contract from "./formal-card-scoring-contract";

export const formalScoringPolicyVersion = contract.version;
export const formalScoreSemantics = contract.semantics;
export const isFormalScoringPolicy = (value: unknown) => value === contract.version;
export const isAdvisoryQualityFlag = (value: unknown) => typeof value === "string"
  && (contract.advisory_quality_flags as readonly string[]).includes(value);

/** A declared derived ledger must cover every original frame, not a subset. */
export function validDerivedSelectionLedger(values: unknown[], required = false, expected?: unknown): boolean {
  const rows = values.map(value => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {});
  if (required && (!Number.isSafeInteger(expected) || Number(expected) < 0)) return false;
  if (!required && !rows.some(row => "derived_selection_epoch" in row)) return true;
  return rows.length > 0 && rows.every(row => Number.isSafeInteger(row.derived_selection_epoch) && Number(row.derived_selection_epoch) >= 0
    && row.derived_selection_epoch === rows[0].derived_selection_epoch && (!required || row.derived_selection_epoch === expected));
}
