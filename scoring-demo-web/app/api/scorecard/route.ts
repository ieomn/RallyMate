import catalog from "../../data/technique-catalog.json";

const CATEGORY_NAMES: Record<string, string> = { baseline: "底线击球", serve: "发球", return: "接发", net_attack: "网前进攻", footwork: "步伐" };

export async function GET() {
  return Response.json({ service: "RallyMate 练习伴侣", apiVersion: "2.0.0", surface: "practice_catalog", registryVersion: catalog.registry_version, techniqueCount: catalog.techniques.length, categories: Object.entries(CATEGORY_NAMES).map(([id, name]) => ({ id, name, techniques: catalog.techniques.filter((technique) => technique.family === id).map((technique) => ({ id: technique.id, name: technique.name_zh, phases: technique.phases, focusPoints: technique.core_visual_features, observationLimits: technique.proxy_limits })) })), semantics: { purpose: "帮助普通网球爱好者理解动作并选择下一次练习", adviceKind: "general_practice_guidance", numericalGrades: false, competitiveRanking: false, missingEvidencePolicy: "信息不足时说明不确定性，不推测动作表现", observationPolicy: "头部朝向不能证明真实视线；缺少可靠球与球拍轨迹时不判断真实触球" }, sourceDocuments: catalog.source_documents.map((source) => source.file_name), adviceEndpoint: "/api/advice" }, { headers: { "Cache-Control": "public, max-age=3600" } });
}

export async function POST() {
  return Response.json({ error: "scorecard_retired", message: "原评分接口已停用，请使用练习建议接口。", adviceEndpoint: "/api/advice" }, { status: 410, headers: { "Cache-Control": "no-store", Link: '</api/advice>; rel="successor-version"' } });
}
