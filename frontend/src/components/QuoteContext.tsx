import type { EvidenceCard, SourceRecord } from "../types";

export function QuoteContext({ evidence, source }: { evidence: EvidenceCard; source?: SourceRecord }) {
  if (!source) return <p>来源快照不可用，无法核对原文。</p>;
  // Backend offsets count Unicode code points, not JavaScript UTF-16 units.
  const chars = Array.from(source.normalized_content);
  const start = evidence.start_offset;
  const end = evidence.end_offset;
  const hasOffsets = start != null && end != null;
  const exact = hasOffsets && start >= 0 && end > start && end <= chars.length
    && chars.slice(start, end).join("") === evidence.quote;
  const found = source.normalized_content.includes(evidence.quote);
  return <>
    <p>{exact ? "原文匹配通过（保存的位置）" : hasOffsets ? "原文位置不匹配，请复核" : found ? "原文包含引文；历史记录缺少位置" : "原文未匹配，请复核"}</p>
    {exact && <details open className="source-context">
      <summary>原文上下文与适用条件</summary>
      <p>{start > 240 ? "…" : ""}{chars.slice(Math.max(0, start - 240), start).join("")}
        <mark>{chars.slice(start, end).join("")}</mark>
        {chars.slice(end, end + 240).join("")}{end + 240 < chars.length ? "…" : ""}</p>
    </details>}
    <dl className="evidence-meta">
      <dt>章节／定位</dt><dd>{evidence.locator || "未记录"}</dd>
      <dt>提取版本</dt><dd>{source.extraction_version || "未记录"}</dd>
      <dt>抓取时间</dt><dd>{source.retrieved_at}</dd>
      <dt>发布时间</dt><dd>{source.published_at || "未知"}</dd>
      <dt>更新时间</dt><dd>{source.source_updated_at || "未知"}</dd>
    </dl>
  </>;
}
