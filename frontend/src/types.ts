export type TaskStatus =
  | "PENDING"
  | "PLANNING"
  | "RESEARCHING"
  | "VALIDATING_EVIDENCE"
  | "WRITING"
  | "VALIDATING_CITATIONS"
  | "REVIEWING"
  | "REVISING"
  | "COMPLETED"
  | "COMPLETED_WITH_WARNINGS"
  | "FAILED"
  | "BUDGET_EXCEEDED";

export interface ExecutionStats {
  model_calls: number;
  tool_calls: number;
  search_calls: number;
  pages_read: number;
  prompt_tokens: number;
  completion_tokens: number;
  actual_prompt_tokens: number;
  actual_completion_tokens: number;
  actual_model_attempts: number;
  estimated_model_attempts: number;
  unknown_model_attempts: number;
  usage_measurement: "actual" | "estimated" | "unknown" | "mixed";
  estimated_cost: number;
  cost_is_estimated: boolean;
  cost_amount: number | null;
  cost_currency: string | null;
  cost_reason: string | null;
  latency_ms: number;
}

export interface ResearchTask {
  is_running?: boolean;
  task_id: string;
  query: string;
  status: TaskStatus;
  workflow_version: string;
  error: string | null;
  execution: ExecutionStats;
  created_at: string;
  updated_at: string;
}

export interface WorkflowEvent {
  event_id: string;
  sequence: number;
  task_id: string;
  event_type: string;
  stage: string;
  timestamp: string;
  payload: Record<string, unknown>;
}

export interface ReportClaim {
  claim_id: string;
  text: string;
  dimension: string;
  claim_type: string;
  requires_citation: boolean;
  evidence_ids: string[];
  uncertainty: string;
}

export interface EvidenceCard {
  evidence_id: string;
  question_id: string;
  question_ids: string[];
  evidence_summary: string;
  quote: string;
  source_id: string;
  locator: string | null;
  start_offset: number | null;
  end_offset: number | null;
  support_type: string;
  source_quality: string;
}

export interface SourceRecord {
  source_id: string;
  title: string;
  url: string;
  source_type: string;
  retrieved_at: string;
  normalized_content: string;
  // 后端 SourceRecord.normalized_url 可空（历史快照可能未保存规范化 URL）。
  normalized_url: string | null;
  url_fragment: string | null;
  content_type: string | null;
  extraction_version: string;
  published_at: string | null;
  source_updated_at: string | null;
  source_type_reason: string | null;
}

export interface QuestionCoverage {
  question_id: string;
  status: "unanswered" | "partial" | "supported" | "conflicting";
  evidence_ids: string[];
}

export interface ResearchProgress {
  task_id: string;
  step_index: number;
  questions: { question_id: string; text: string; requirement_ids: string[] }[];
  coverage: QuestionCoverage[];
  open_gaps: string[];
  source_refs: string[];
  evidence_refs: string[];
  stop_reason: string | null;
  budget_remaining: {
    total_model_attempts: number;
    research_model_attempts: number;
    global_remaining_model_attempts: number;
    research_remaining_model_attempts: number;
    downstream_reserved_model_attempts: number;
    global_remaining_tokens: number;
    downstream_reserved_tokens: number;
  };
}

export interface ResearchArtifacts {
  task_id: string;
  status: TaskStatus;
  searches: { search_id: string; query: string; result_urls: string[] }[];
  sources: SourceRecord[];
  evidence: EvidenceCard[];
  progress: ResearchProgress | null;
}

export interface CitationView {
  claim_id: string;
  evidence_id: string;
  source_id: string;
  source_title: string;
  quote: string;
  source_url: string;
}

export interface QualityMetrics {
  citation_coverage: number;
  citation_precision: number;
  claim_support_rate: number;
  requirement_coverage: number;
  source_quality_score: number;
  format_completeness: number;
  overall_score: number;
  passed: boolean;
}

export interface ReportResponse {
  task_id: string;
  status: TaskStatus;
  report: {
    title: string;
    executive_summary: string;
    claims: ReportClaim[];
    comparison_table: Record<string, unknown>[];
    recommendation: string;
    risks_and_limitations: string[];
    unverified_notice: string | null;
  };
  evidence: EvidenceCard[];
  sources: SourceRecord[];
  citations: CitationView[];
  // 与后端 ReviewResult 字段一一对应。claim_support_results、
  // evidence_support_results、requirement_checks 在 API 响应中始终存在；
  // report_section_results、gap_requests 由 Pydantic 默认补空数组。
  review: {
    unsupported_claims: string[];
    overclaimed_claims: string[];
    citation_issues: string[];
    missing_dimensions: string[];
    revision_instructions: string[];
    claim_support_results: {
      claim_id: string;
      status: string;
      requires_citation_override: boolean | null;
      reason: string;
      assessment_method: string;
    }[];
    evidence_support_results: {
      claim_id: string;
      evidence_id: string;
      status: string;
      reason: string;
      assessment_method: string;
    }[];
    requirement_checks: {
      requirement_id: string;
      covered: boolean;
      reason: string;
    }[];
    report_section_results: {
      section: string;
      status: string;
      related_claim_ids: string[];
      reason: string;
      assessment_method: string;
    }[];
    gap_requests: {
      gap_id: string;
      question_ids: string[];
      claim_ids: string[];
      missing_information: string;
      suggested_source_types: string[];
      reason: string;
      requires_research: boolean;
    }[];
  };
  metrics: QualityMetrics;
  execution: ExecutionStats;
  delivery_self_check?: DeliverySelfCheck | null;
  provider_notice: string;
}

// 交付前确定性自检（零模型调用）。见 backend/app/validators/delivery_self_check.py。
export interface DeliveryFinding {
  code: "DEPRECATED_EVIDENCE" | "QUOTE_NOT_FOUND" | "VERSION_NOT_MATCHED";
  severity: "blocking" | "warning" | "info";
  message: string;
  evidence_id: string | null;
  source_id: string | null;
  source_url: string | null;
  evidence_span: string | null;
}

export interface DeliverySelfCheck {
  status: "pass" | "warn" | "fail";
  findings: DeliveryFinding[];
  checked_evidence: number;
  checked_sources: number;
  deterministic_only: boolean;
  /** 后端计算的一句话摘要；后端未提供时前端按 findings 自行生成。 */
  notice?: string | null;
}
