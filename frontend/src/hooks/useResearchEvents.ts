import { useEffect, useRef, useState } from "react";
import { eventStreamUrl, getResearch } from "../api";
import type { ResearchTask, WorkflowEvent } from "../types";

const EVENT_TYPES = [
  "WORKFLOW_STARTED",
  "WORKFLOW_RESUMED",
  "WORKFLOW_COMPLETED",
  "WORKFLOW_FAILED",
  "WORKFLOW_BUDGET_EXCEEDED",
  "NODE_STARTED",
  "NODE_COMPLETED",
  "NODE_FAILED",
  "MODEL_CALL_STARTED",
  "MODEL_CALL_COMPLETED",
  "MODEL_CALL_FAILED",
  "MODEL_CALL_RETRIED",
  "TOOL_CALL_STARTED",
  "TOOL_CALL_COMPLETED",
  "TOOL_CALL_FAILED",
  "TOOL_CALL_RETRIED",
  "SOURCE_FETCHED",
  "EVIDENCE_CREATED",
  "EVIDENCE_REJECTED",
  "RESEARCH_PROGRESS_UPDATED",
  "RESEARCH_STOPPED",
  "SUPPLEMENT_STARTED",
  "SUPPLEMENT_COMPLETED",
  "REVIEW_STARTED",
  "REVIEW_PASSED",
  "REVIEW_FAILED",
  "REVISION_STARTED",
  "REVISION_COMPLETED",
  "REPORT_URLS_REDACTED",
  "CITATION_REPAIR_STARTED",
  "CITATION_REPAIR_COMPLETED",
  "REPORT_VALIDATION_FAILED",
  "CONFLICTS_IDENTIFIED",
  "DELIVERY_SELF_CHECK_COMPLETED",
];

const TERMINAL_EVENTS = new Set([
  "WORKFLOW_COMPLETED",
  "WORKFLOW_FAILED",
  "WORKFLOW_BUDGET_EXCEEDED",
]);

export function useResearchEvents(taskId: string) {
  const [events, setEvents] = useState<WorkflowEvent[]>([]);
  const [task, setTask] = useState<ResearchTask | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cursor = useRef(0);
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    let active = true;
    const source = new EventSource(eventStreamUrl(taskId, cursor.current));
    const handleEvent = (message: MessageEvent<string>) => {
      const event = JSON.parse(message.data) as WorkflowEvent;
      if (!active || event.sequence <= cursor.current) return;
      cursor.current = event.sequence;
      setEvents((current) => [...current, event]);
      if (TERMINAL_EVENTS.has(event.event_type)) {
        void refresh();
      }
    };

    EVENT_TYPES.forEach((type) => source.addEventListener(type, handleEvent));
    source.onopen = () => {
      setConnected(true);
      setError(null);
    };
    async function refresh() {
      try {
        const snapshot = await getResearch(taskId);
        if (active) setTask(snapshot);
        return snapshot;
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : "任务状态读取失败");
        return null;
      }
    }
    const poll = window.setInterval(() => { void refresh(); }, 3000);
    source.onerror = () => {
      if (!active) return;
      setConnected(false);
      // Historical terminal frames can precede later resumed work. Close only
      // after stream EOF and a current terminal snapshot without an active lease.
      void refresh().then((snapshot) => {
        if (active && snapshot && !snapshot.is_running &&
          ["COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "BUDGET_EXCEEDED"].includes(snapshot.status)) {
          source.close();
          window.clearInterval(poll);
        }
      });
    };
    void refresh();

    return () => {
      active = false;
      window.clearInterval(poll);
      EVENT_TYPES.forEach((type) => source.removeEventListener(type, handleEvent));
      source.close();
    };
  }, [taskId, generation]);

  return {
    events,
    task,
    connected,
    error,
    reconnect: () => setGeneration((value) => value + 1),
  };
}
