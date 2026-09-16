import { useEffect, useMemo, useState } from "react";
import { getProgress, getReport } from "../api";
import type { DeliverySelfCheck, ReportResponse, ResearchProgress } from "../types";

import { QuoteContext } from "../components/QuoteContext";

const FINDING_LABEL: Record<string, string> = {
  DEPRECATED_EVIDENCE: "依据落在已废弃段落",
  QUOTE_NOT_FOUND: "引文在快照中定位不到",
  VERSION_NOT_MATCHED: "版本适用性待核查",
};

/** 交付前确定性自检结论。零模型调用，由程序判定，前端只负责显性呈现。 */
export function DeliverySelfCheckPanel({ check }: { check: DeliverySelfCheck }) {
  if (!check.findings.length) return null;
  const blocking = check.findings.filter((item) => item.severity === "blocking").length;
  return (
    <section className={`delivery-self-check ${blocking > 0 ? "warning" : "notice"}`}>
      <h2>交付自检（确定性，未调用模型）</h2>
      <p>
        报告落盘前由程序核对 {check.checked_evidence} 条引用证据与 {check.checked_sources} 个来源，
        发现 {check.findings.length} 项问题
        {blocking > 0 ? `，其中 ${blocking} 项为阻断级` : ""}。
        {check.notice ? ` ${check.notice}` : ""}
      </p>
      <ul>
        {check.findings.map((finding, index) => (
          <li key={`${finding.code}-${finding.evidence_id ?? index}`}>
            <strong>[{FINDING_LABEL[finding.code] ?? finding.code}]</strong> {finding.message}
            {finding.source_url && (
              <> <a href={finding.source_url} target="_blank" rel="noreferrer">查看来源 ↗</a></>
            )}
            {finding.evidence_span && (
              <blockquote className="finding-span">…{finding.evidence_span.slice(0, 240)}…</blockquote>
            )}
          </li>
        ))}
      </ul>
      <p className="notice">
        自检只标注问题，不删除报告内容、不改变交付状态；最终判断仍需人工核对原文。
      </p>
    </section>
  );
}

interface Props { taskId: string; }

export function ReportPage({ taskId }: Props) {
  const [data, setData] = useState<ReportResponse | null>(null);
  const [progress, setProgress] = useState<ResearchProgress | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setData(null);
    setError(null);
    setProgress(null);
    void getProgress(taskId).then((value) => { if (active) setProgress(value); }).catch(() => {});
    void getReport(taskId)
      .then((report) => {
        if (!active) return;
        setData(report);
      })
      .catch((reason: Error) => { if (active) setError(reason.message); });
    return () => { active = false; };
  }, [taskId]);

  if (error) return <section className="content-page"><p className="error-text panel">{error}</p></section>;
  if (!data) return <section className="content-page"><p className="panel">正在加载报告…</p></section>;
  return <ReportView key={taskId} data={data} progress={progress} />;
}

export function ReportView({ data, progress = null }: { data: ReportResponse; progress?: ResearchProgress | null }) {
  const [selectedClaim, setSelectedClaim] = useState<string | null>(data.report.claims[0]?.claim_id ?? null);
  const chain = useMemo(() => data.citations.filter((item) => item.claim_id === selectedClaim), [data, selectedClaim]);
  return (
    <section className="content-page report-page">
      <div className="page-heading">
        <p className="eyebrow">{data.status}</p>
        <h1>{data.report.title}</h1>
        <p>{data.report.executive_summary}</p>
      </div>
      <p className="provider-notice">{data.provider_notice}</p>
      {data.report.unverified_notice && <p className="warning">{data.report.unverified_notice}</p>}
      {data.delivery_self_check && <DeliverySelfCheckPanel check={data.delivery_self_check} />}

      <div className="stats-grid report-metrics">
        <article className="metric-card"><span>流程质量分</span><strong>{data.metrics.overall_score.toFixed(1)}</strong></article>
        <article className="metric-card"><span>引用覆盖</span><strong>{Math.round(data.metrics.citation_coverage * 100)}%</strong></article>
        <article className="metric-card"><span>引用精确</span><strong>{Math.round(data.metrics.citation_precision * 100)}%</strong></article>
        <article className="metric-card"><span>模型判断结论支持</span><strong>{Math.round(data.metrics.claim_support_rate * 100)}%</strong></article>
      </div>

      <p className="notice">流程质量分汇总引用、覆盖和格式等检查，不代表事实准确率。原文匹配仅确认引文存在；语义审核由模型判断，仍可能出错。</p>
      <p className="notice">记录费用：{data.execution.cost_amount == null
        ? "未知（" + (data.execution.cost_reason || "未记录费用") + "）"
        : data.execution.cost_amount.toFixed(4) + " " + (data.execution.cost_currency || "币种未记录") + (data.execution.cost_is_estimated ? "（估算）" : "")}</p>
      {((progress?.open_gaps.length ?? 0) > 0 || data.review.missing_dimensions.length > 0 || (data.review.gap_requests?.length ?? 0) > 0 || progress?.coverage.some((item) => item.status === "conflicting")) && <section className="warning">
        <h2>未覆盖问题与证据冲突</h2>
        <ul>{[...new Set([...(progress?.open_gaps ?? []), ...data.review.missing_dimensions])].map((gap) => <li key={gap}>{gap}</li>)}
          {progress?.coverage.filter((item) => item.status === "conflicting").map((item) => <li key={item.question_id}>冲突：{progress.questions.find((q) => q.question_id === item.question_id)?.text || item.question_id}；证据 {item.evidence_ids.join("、")}</li>)}
          {data.review.gap_requests?.map((gap) => <li key={gap.gap_id}>{gap.missing_information}（{gap.reason}）</li>)}</ul>
      </section>}
      <div className="report-grid">
        <div className="panel claims-panel">
          <div className="panel-title"><h2>结论</h2><span>点击查看证据链</span></div>
          {data.report.claims.map((claim) => (
            <button
              key={claim.claim_id}
              className={`claim-card ${selectedClaim === claim.claim_id ? "selected" : ""}`}
              onClick={() => setSelectedClaim(claim.claim_id)}
            >
              <span>{claim.claim_id} · {claim.dimension}</span>
              <strong>{claim.text}</strong>
              <small>不确定性：{claim.uncertainty} · 证据 {claim.evidence_ids.length} 条</small>
            </button>
          ))}
          <h2>建议</h2><p>{data.report.recommendation}</p>
          <h2>风险与限制</h2>
          <ul>{data.report.risks_and_limitations.map((item) => <li key={item}>{item}</li>)}</ul>
        </div>

        <aside className="panel evidence-panel">
          <div className="panel-title"><h2>Claim → Evidence → Source</h2><span>{selectedClaim}</span></div>
          {chain.map((citation) => {
            const evidence = data.evidence.find((item) => item.evidence_id === citation.evidence_id);
            const source = data.sources.find((item) => item.source_id === citation.source_id);
            const semantic = data.review.evidence_support_results?.find((item) => item.claim_id === citation.claim_id && item.evidence_id === citation.evidence_id);
            return (
              <article className="evidence-card" key={`${citation.claim_id}-${citation.evidence_id}`}>
                <p className="chain-label">{citation.claim_id} → {citation.evidence_id} → {citation.source_id}</p>
                <blockquote>“{citation.quote}”</blockquote>
                {evidence && <p>{evidence.evidence_summary}</p>}
                {evidence && <QuoteContext evidence={evidence} source={source} />}
                <p>模型语义审核：{semantic ? semantic.status + " — " + semantic.reason : "未记录"}</p>
                <a href={citation.source_url} target="_blank" rel="noreferrer">{citation.source_title} ↗</a>
              </article>
            );
          })}
          {chain.length === 0 && <p>该结论没有引用证据。</p>}
        </aside>
      </div>
    </section>
  );
}
