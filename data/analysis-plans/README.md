# Analysis-plan authority and history

`fs09-phase-truth-m77-descriptive-v1.json` is the current M88 zero-label re-freeze for
the two-task FS09 descriptive pilot. The stable filename is retained for existing
builder/validator entry points; the authoritative identity is the in-file
`plan_version`, `plan_id`, `frozen_at`, and raw SHA-256, not the filename suffix.

Superseded bytes are retained under `history/` and are never accepted by the current
builder:

- `history/fs09-phase-truth-m77-descriptive-v1.1.0.superseded.json` — exact M87
  v1.1.0 bytes, raw SHA-256
  `C621CCE3D06DFCBFF02A5660E04E68B03B1841356DB2DD82D4065A0A203BCEEC`.
- `history/fs09-phase-truth-m77-descriptive-v1.2.0.superseded.json` — exact first
  M88 plan bytes, superseded before any labels after the fixed-padding handoff was
  found to disclose candidate boundaries; raw SHA-256
  `D0AEE64A57109B855095CA7C3FB33C0EE9CA852F0D01463D50C54F374000577E`.

M88 changed the workbench, handoff contract, media windows, provenance fields, and
technical-only authorization gate while the canonical pack still contained zero
annotations and zero adjudications. It therefore creates a new plan identity instead
of pretending the earlier freeze remained unchanged. Historical files are evidence
only and are not executable current aliases.

`scoring-truth-event-m89-operator-plan-v2.json` is the frozen M90 zero-label plan
for independent event/phase review across the exact three videos in the M89 technical
handoff. It binds the handoff manifest bytes, bundle ID, content root, source
projection, full-video task scope, and A/B/C review roles. It is not itself a release
record and cannot enable a first annotation write: a separate locally reviewed operator
record must bind these exact frozen bytes. This plan supports event/phase annotation
only; grades, thresholds, calibration, promotion, and production remain disabled.
Its frozen raw SHA-256 is
`BE1A8BDB73DB48982F3737C53EBD5C116A2B413CDA05B5EBD057163B112BDC3F`.
