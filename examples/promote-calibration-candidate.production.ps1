# Example only: every angle-bracket value must be replaced with a controlled,
# immutable input or output path. This file is not a promotion authorization.
python scripts/promote_calibration_candidate.py `
  --candidate <candidate.json> `
  --independent-test-report <independent-test-report.json> `
  --decision <signed-human-promotion-decision.json> `
  --feasibility-registry <controlled-F4-registry.json> `
  --maturity-evidence <controlled-F0-to-F4-maturity-evidence.json> `
  --promoted-at <ISO-8601> `
  --asset-output <production-calibration.json> `
  --promotion-report-output <promotion-report.json> `
  --trusted-ledger-output <trusted-calibration-promotion-ledger.json> `
  --ledger-id <operator-ledger-id> `
  --ledger-version <immutable-ledger-version> `
  --ledger-authority-id <release-authority-id> `
  --ledger-registered-at <ISO-8601>
