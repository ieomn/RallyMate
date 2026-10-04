import ScoreLab from "./ScoreLab";
import registry from "./data/metric-cards.json";

export default function Home() {
  return (
    <ScoreLab
      registryVersion={registry.registryVersion}
      sources={registry.sources}
    />
  );
}
