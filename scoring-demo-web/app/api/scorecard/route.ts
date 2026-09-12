import registry from "../../data/metric-cards.json";
import {
  buildScoreReport,
  scenarioFromStage1Summary,
  type Domain,
  type MetricCard,
  type Scenario,
} from "../../scoring/engine";
import { SCENARIOS } from "../../scoring/scenarios";

export async function GET() {
  return Response.json({
    service: "RallyMate Score Lab",
    apiVersion: "1.3.0",
    compatibilityVersion: "1.2.0",
    surface: "local_compatibility_scorecard_adapter",
    registryKind: "legacy_gs_fs_cards",
    registryVersion: registry.registryVersion,
    techniqueRegistry: {
      source: "FastAPI /v1/techniques",
      endpoint: "/v1/techniques",
      semantics: "qualitative_evidence_readiness_only",
    },
    indicatorCount: registry.cards.length,
    scenarios: SCENARIOS.map(({ id, label, mode, description }) => ({ id, label, mode, description })),
    usage: "POST { scenarioId, domain? } or { stage1Summary, domain? }",
  });
}

export async function POST(request: Request) {
  let body: { scenarioId?: string; domain?: Domain; stage1Summary?: Record<string, unknown> };
  try {
    const parsed: unknown = await request.json();
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      return Response.json({ error: "request_body_must_be_an_object" }, { status: 400 });
    }
    body = parsed as typeof body;
  } catch {
    return Response.json({ error: "invalid_json" }, { status: 400 });
  }

  let scenario: Scenario | undefined;
  if (body.stage1Summary !== undefined) {
    if (body.stage1Summary === null || typeof body.stage1Summary !== "object" || Array.isArray(body.stage1Summary)) {
      return Response.json({ error: "stage1Summary_must_be_an_object" }, { status: 400 });
    }
    scenario = scenarioFromStage1Summary(body.stage1Summary);
  } else {
    scenario = SCENARIOS.find((item) => item.id === body.scenarioId);
  }
  if (!scenario) {
    return Response.json({ error: "unknown_scenario", validScenarioIds: SCENARIOS.map((item) => item.id) }, { status: 400 });
  }
  if (body.domain && body.domain !== "GS" && body.domain !== "FS") {
    return Response.json({ error: "domain_must_be_GS_or_FS" }, { status: 400 });
  }

  const report = buildScoreReport(registry.cards as MetricCard[], scenario);
  const results = report.results
    .filter((result) => !body.domain || result.card.domain === body.domain)
    .map((result) => ({
      indicatorId: result.card.id,
      indicatorName: result.card.name,
      domain: result.card.domain,
      eventCode: result.card.eventCode,
      stageCode: result.card.stageCode,
      status: result.status,
      score: result.score,
      grade: result.grade,
      evidence: result.evidence,
      confidence: result.confidence,
      dimensions: result.dimensions,
      verdict: result.verdict,
      feedback: result.feedback,
    }));

  return Response.json({
    apiVersion: "1.3.0",
    compatibilityVersion: "1.2.0",
    surface: "local_compatibility_scorecard_adapter",
    reportVersion: report.reportVersion,
    registryKind: "legacy_gs_fs_cards",
    registryVersion: registry.registryVersion,
    generatedAt: report.generatedAt,
    scenario: report.scenario,
    overallScore: report.overallScore,
    overallGrade: report.overallGrade,
    overallEvidence: report.overallEvidence,
    acceptanceStatus: report.acceptanceStatus,
    domains: report.domains,
    formula: report.formula,
    resultCount: results.length,
    results,
  });
}
