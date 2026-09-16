import { useEffect, useState } from "react";
import { getArtifacts, getProgress, resumeResearch } from "../api";
import { ResearchArtifactsPanel } from "../components/ResearchArtifactsPanel";
import { useResearchEvents } from "../hooks/useResearchEvents";
import type { ResearchArtifacts, ResearchProgress } from "../types";

interface Props {
  taskId: string;
  navigate: (path: string) => void;
}

const LABELS: Record<string, string> = {
  WORKFLOW_STARTED: "工作流启动",
  WORKFLOW_RESUMED: "从检查点恢复",
  WORKFLOW_COMPLETED: "工作流完成",
  WORKFLOW_FAILED: "工作流失败",
  WORKFLOW_BUDGET_EXCEEDED: "预算耗尽",
  NODE_STARTED: "节点开始",
  NODE_COMPLETED: "节点完成",
  NODE_FAILED: "节点失败",
  MODEL_CALL_STARTED: "模型调用开始",
  MODEL_CALL_COMPLETED: "模型调用完成",
  MODEL_CALL_FAILED: "模型调用失败",
  MODEL_CALL_RETRIED: "模型调用重试",
  TOOL_CALL_STARTED: "工具调用开始",
  TOOL_CALL_COMPLETED: "工具调用完成",
  TOOL_CALL_FAILED: "工具调用失败",
  TOOL_CALL_RETRIED: "工具调用重试",
  SOURCE_FETCHED: "来源已保存",
  EVIDENCE_CREATED: "证据已通过",
  EVIDENCE_REJECTED: "证据已拒绝",
  RESEARCH_PROGRESS_UPDATED: "研究覆盖已更新",
  RESEARCH_STOPPED: "研究停止",
  SUPPLEMENT_STARTED: "开始补证",
  SUPPLEMENT_COMPLETED: "补证结束",
  REVIEW_STARTED: "审核开始",
  REVIEW_PASSED: "审核通过",
  REVIEW_FAILED: "审核未通过",
  REVISION_STARTED: "修订开始",
  REVISION_COMPLETED: "修订完成",
  REPORT_URLS_REDACTED: "已移除正文链接",
  CITATION_REPAIR_STARTED: "开始修复引文",
  CITATION_REPAIR_COMPLETED: "引文修复完成",
  REPORT_VALIDATION_FAILED: "报告校验未通过",
  CONFLICTS_IDENTIFIED: "发现冲突证据",
  DELIVERY_SELF_CHECK_COMPLETED: "交付自检完成",
};

export function ExecutionPage({ taskId, navigate }: Props) {
  const { events, task, connected, error, reconnect } = useResearchEvents(taskId);
  const [artifacts, setArtifacts] = useState<ResearchArtifacts | null>(null);
  const [progress, setProgress] = useState<ResearchProgress | null>(null);
  const [resumeError, setResumeError] = useState<string | null>(null);
  const [resuming, setResuming] = useState(false);
  const lastEvent = events.at(-1);
  const terminal = task && ["COMPLETED", "COMPLETED_WITH_WARNINGS"].includes(task.status);
  const resumable = task && !task.is_running && !terminal;

  useEffect(() => {
    let active = true;
    void getArtifacts(taskId).then((value) => { if (active) setArtifacts(value); }).catch(() => undefined);
    void getProgress(taskId).then((value) => { if (active) setProgress(value); }).catch(() => undefined);
    return () => { active = false; };
  }, [taskId, task?.status, events.length]);

  async function resume() {
    setResuming(true);
    setResumeError(null);
    try {
      await resumeResearch(taskId);
      reconnect();
    } catch (reason) {
      setResumeError(reason instanceof Error ? reason.message : "恢复请求失败");
    } finally {
      setResuming(false);
    }
  }

  const cost = task?.execution.cost_amount;
  const costLabel =
    cost == null
      ? "未知"
      : `${task?.execution.cost_currency ?? "币种未记录"} ${cost.toFixed(4)}`;

  return (
    <section className="content-page">
      <div className="page-heading split-heading">
        <div>
          <p className="eyebrow">EXECUTION RECORD</p>
          <h1>调研执行过程</h1>
          <p className="mono">{taskId}</p>
        </div>
        <div className={`connection ${connected ? "online" : ""}`}>
          <span /> {connected ? "SSE 已连接" : "等待或已结束"}
        </div>
      </div>

      <div className="stats-grid">
        <article className="metric-card"><span>当前阶段</span><strong>{lastEvent?.stage ?? task?.status ?? "PENDING"}</strong></article>
        <article className="metric-card"><span>模型调用</span><strong>{task?.execution.model_calls ?? "—"}</strong></article>
        <article className="metric-card"><span>工具调用</span><strong>{task?.execution.tool_calls ?? "—"}</strong></article>
        <article className="metric-card"><span>预算计费 Token</span><strong>{task ? task.execution.prompt_tokens + task.execution.completion_tokens : "—"}</strong></article>
        <article className="metric-card"><span>记录成本</span><strong>{task ? costLabel : "—"}</strong></article>
        <article className="metric-card"><span>延迟</span><strong>{task ? `${Math.round(task.execution.latency_ms)} ms` : "—"}</strong></article>
      </div>

      {error && <p className="error-text panel">{error}</p>}
      {resumeError && <p className="error-text panel">{resumeError}</p>}
      {task?.error && <p className="error-text panel">{task.error}</p>}

      <div className="execution-grid">
        <div className="panel timeline-panel">
          <div className="panel-title"><h2>事件时间线</h2><span>{events.length} events</span></div>
          <ol className="timeline">
            {events.map((event) => (
              <li key={event.event_id} className={event.event_type.includes("FAILED") || event.event_type.includes("REJECTED") ? "danger" : ""}>
                <span className="timeline-index">{event.sequence}</span>
                <div>
                  <strong>{LABELS[event.event_type] ?? event.event_type}</strong>
                  <p>{event.stage} · {new Date(event.timestamp).toLocaleTimeString()}</p>
                  {Object.keys(event.payload).length > 0 && <code>{JSON.stringify(event.payload)}</code>}
                </div>
              </li>
            ))}
            {events.length === 0 && <li className="empty">等待第一个持久化事件…</li>}
          </ol>
        </div>
        <aside className="panel stage-panel">
          <h2>执行语义</h2>
          <p>绿色事件表示已成功持久化；红色事件表示失败、证据拒绝或审核未通过。</p>
          <p>断线重连后，服务端会按单调 sequence 回放缺失事件。</p>
          {progress && (
            <div className="progress-summary">
              <h3>研究覆盖</h3>
              <ul>
                {progress.questions.map((question) => {
                  const coverage = progress.coverage.find(
                    (item) => item.question_id === question.question_id,
                  );
                  return (
                    <li key={question.question_id}>
                      <strong>{coverage?.status ?? "unanswered"}</strong>
                      <span>{question.text}</span>
                    </li>
                  );
                })}
              </ul>
              <p>
                研究额度：{progress.budget_remaining.research_remaining_model_attempts} 次 / {Math.max(0, progress.budget_remaining.global_remaining_tokens - progress.budget_remaining.downstream_reserved_tokens)} Token
              </p>
              <p>
                下游预留：{progress.budget_remaining.downstream_reserved_model_attempts} 次 / {progress.budget_remaining.downstream_reserved_tokens} Token
              </p>
              {progress.stop_reason && <p>停止原因：{progress.stop_reason}</p>}
            </div>
          )}
          {artifacts && (
            <p>
              已保存 {artifacts.sources.length} 个来源、{artifacts.evidence.length} 条证据。
            </p>
          )}
          {resumable && (
            <button className="primary" disabled={resuming} onClick={resume}>
              {resuming ? "正在恢复…" : "从已保存步骤恢复"}
            </button>
          )}
          {terminal && (
            <button className="primary" onClick={() => navigate(`/report/${encodeURIComponent(taskId)}`)}>
              查看报告
            </button>
          )}
        </aside>
      </div>
      {artifacts && <ResearchArtifactsPanel artifacts={artifacts} />}
    </section>
  );
}
