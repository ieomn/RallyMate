# RallyMate Offline Research & Milestone Packages

This directory holds offline evaluation, annotation workbenches, pose-pilot experiments, and milestone audit packages separated from production runtime code in `src/`:

- `rallymate_evaluation`: offline ground-truth error evaluation, pose diagnostic truth packs, and M67–M97 experiment harnesses.
- `rallymate_annotation`: offline HTML/CSV truth-pack workbenches and annotation compilers.
- `rallymate_pose_evaluation`: offline pose baseline comparison helpers.
- `rallymate_visualization`: offline HTML timeline report templates.

Production Docker images (`deploy/Dockerfile`, `deploy/Dockerfile.api`) copy only `src/` and never bundle `research/`.
For local development or offline research scripts/tests, include `research` on `PYTHONPATH` (e.g., `PYTHONPATH="src:research:tests"` on Linux or `$env:PYTHONPATH="src;research;tests"` on Windows; `tests/conftest.py` and `pyproject.toml` add it automatically).
