import type { ResearchArtifacts } from "../types";
import { QuoteContext } from "./QuoteContext";

export function ResearchArtifactsPanel({ artifacts }: { artifacts: ResearchArtifacts }) {
  return <section className="panel timeline-panel">
    <h2>已保存的来源与证据</h2>
    <p>这些是中间研究产物；证据已通过原文检查，不代表最终结论已获语义支持。</p>
    {artifacts.evidence.map((evidence) => {
      const source = artifacts.sources.find((item) => item.source_id === evidence.source_id);
      return <details key={evidence.evidence_id} className="evidence-card">
        <summary>{evidence.evidence_id} · {evidence.evidence_summary}</summary>
        <blockquote>{evidence.quote}</blockquote>
        <p>关联问题：{(evidence.question_ids ?? [evidence.question_id]).join("、")}</p>
        <QuoteContext evidence={evidence} source={source} />
        {source && <a href={source.url} target="_blank" rel="noreferrer">{source.title} ↗</a>}
      </details>;
    })}
    {artifacts.evidence.length === 0 && <p>尚无有效证据。</p>}
    <details><summary>来源快照（{artifacts.sources.length}）</summary>
      {artifacts.sources.map((source) => <details key={source.source_id} className="evidence-card">
        <summary>{source.title}</summary>
        <p>{source.retrieved_at} · {source.extraction_version}</p>
        <a href={source.url} target="_blank" rel="noreferrer">打开来源 ↗</a>
        <p className="snapshot-text">{source.normalized_content}</p>
      </details>)}
    </details>
  </section>;
}
