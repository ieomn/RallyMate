import ScoreLab from "./ScoreLab";
import registry from "./data/metric-cards.json";
import type { MetricCard } from "./scoring/engine";

export default function Home() {
  return (
    <ScoreLab
      cards={registry.cards as MetricCard[]}
      registryVersion={registry.registryVersion}
      sources={registry.sources}
    />
  );
}
