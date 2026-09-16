# EvidenceFlow API 文档

默认地址：`http://127.0.0.1:8000`。FastAPI 自动文档位于 `/docs`，本页解释业务语义与 SSE 续读规则。

## 健康检查

### `GET /health`

响应：

```json
{
  "status": "ok",
  "app": "EvidenceFlow",
  "workflow_version": "v2.0"
}
```

## 创建调研

### `POST /api/research`

请求：

```json
{
  "query": "比较 LangGraph 与 OpenAI Agents SDK 的持久化和人工介入能力",
  "depth": "standard"
}
```

V1 只接受 `depth=standard`。接口返回 HTTP 202，工作流在后台执行：

```json
{
  "task_id": "TASK_abc123",
  "status": "PENDING"
}
```

## 查询任务

### `GET /api/research/{task_id}`

返回查询、状态、错误、Workflow Version、时间和执行统计。可能的终态：

- `COMPLETED`
- `COMPLETED_WITH_WARNINGS`
- `FAILED`
- `BUDGET_EXCEEDED`

`execution` 包含模型、工具、搜索和页面尝试数、Token、费用与延迟。V2 同时返回以下用量质量字段：

- `actual_prompt_tokens`、`actual_completion_tokens`：Provider 明确返回的实际 Token；
- `actual_model_attempts`、`estimated_model_attempts`、`unknown_model_attempts`：按 usage 来源统计的模型尝试数；
- `usage_measurement`：`actual`、`estimated`、`unknown` 或 `mixed`；
- `cost_amount`、`cost_currency`、`cost_reason`：费用无法可靠取得时 `cost_amount` 为 `null`，并说明原因。

原有 `prompt_tokens`、`completion_tokens`、`estimated_cost` 和 `cost_is_estimated` 字段继续保留，供 V1 客户端兼容使用。失败和预算耗尽终态也从调用账本汇总已经发生的尝试，未知 usage 不记作实际零。

## 查询调用轨迹

### `GET /api/research/{task_id}/trace?after=0&limit=100`

按 `sequence` 升序返回模型、搜索和页面的每次真实调用尝试。`after` 是排他游标，只返回 `sequence > after` 的记录；`limit` 范围为 1–500。

```json
{
  "task_id": "TASK_abc123",
  "attempts": [
    {
      "attempt_id": "ATTEMPT_xxx",
      "sequence": 1,
      "logical_call_id": "CALL_xxx",
      "attempt_number": 1,
      "call_kind": "model",
      "phase": "research",
      "operation_name": "research.decide_next_action",
      "arguments_hash": "sha256...",
      "status": "succeeded",
      "usage": {
        "measurement": "actual",
        "prompt_tokens": 120,
        "completion_tokens": 42,
        "total_tokens": 162,
        "cost_amount": null,
        "cost_currency": null,
        "cost_reason": "model pricing is not configured"
      }
    }
  ]
}
```

同一次逻辑调用的重试共享 `logical_call_id`，每次网络尝试拥有不同的 `attempt_id` 和 `attempt_number`。接口只公开参数哈希及安全元数据，不返回原始 Prompt、推理正文、请求头或密钥。

## 查询研究进度

### `GET /api/research/{task_id}/progress?step_limit=20`

返回持久化的 `ResearchProgress`、最近工具步骤和动作统计：

- `coverage`：每个问题的 `unanswered`、`partial`、`supported` 或 `conflicting` 状态及有效 Evidence ID；
- `open_gaps`、`conflicts`、`stop_reason`：未解决内容和研究停止原因；
- `budget_remaining`：全局、研究阶段与下游预留余额；
- `recent_steps`：与 Provider `tool_call_id` 对应的程序化 Observation，包含有效证据摘要、完整覆盖状态、错误反馈和预算；
- `action_stats`：外部工具动作、本地工具动作、无效动作和缓存命中分别统计。

`step_limit` 范围为 0–100。计划尚未生成时返回 404；进度一经创建，会在每个工具动作后更新。

## 查询来源段落

### `GET /api/sources/{source_id}/passages?query=关键词&offset=0&limit=20&context=1`

从已经保存的来源快照中进行确定性本地查找，不发起新的网络请求。响应包含快照 `content_hash`、正文提取版本、段落算法版本，以及每个段落的稳定 ID、字符区间、定位符和匹配高亮。

省略 `query` 时按正文顺序分页。提供 `query` 时按精确短语和关键词命中排序，并返回有限相邻上下文。所有 `start_offset`／`end_offset` 均指向同一份 `normalized_content` 快照。

## 工作流事件

### `GET /api/research/{task_id}/events`

响应 Content-Type 为 `text/event-stream`。每帧格式：

```text
id: 18
event: EVIDENCE_CREATED
data: {"event_id":"EVENT_xxx","sequence":18,"task_id":"TASK_xxx",...}
```

主要事件组：

- 工作流：`WORKFLOW_STARTED/RESUMED/COMPLETED/FAILED/BUDGET_EXCEEDED`
- 节点：`NODE_STARTED/COMPLETED/FAILED`
- 模型：`MODEL_CALL_STARTED/COMPLETED/FAILED/RETRIED`
- 工具：`TOOL_CALL_STARTED/COMPLETED/FAILED/RETRIED`
- 证据：`SOURCE_FETCHED`、`EVIDENCE_CREATED/REJECTED`
- 研究停止：`RESEARCH_STOPPED`，payload 中包含阶段停止原因
- 审核与修订：`REVIEW_STARTED/PASSED/FAILED`、`REVISION_STARTED/COMPLETED`
- 报告交付保障：
  - `REPORT_URLS_REDACTED`：报告正文中的链接已被确定性剔除，payload 含 `redacted_count`。
    最终引用 URL 只由持久化 `SourceRecord` 渲染，因此剔除不损失内容，但必须记录，不是静默处理。
  - `CITATION_REPAIR_STARTED` / `CITATION_REPAIR_COMPLETED`：确定性引文校验发现**可修复**问题后
    触发的一次定向修复（最多一次，payload 含 `issue_codes` / `repair_count`）。
  - `REPORT_VALIDATION_FAILED`：引文校验**致命**失败（证据链与快照不一致，或修复后仍不通过）。
    payload 含 `issues`、`fatal_codes`、`repairable_codes`、`citation_repair_count` 与
    `rejected_report`（被拒草稿全文），用于失败后定位原因。
  - `CONFLICTS_IDENTIFIED`：Reviewer 报告了相互冲突的证据，已落盘到 `ResearchProgress.conflicts`。
    payload 含 `conflict_count`、`new_conflicts` 与涉及的 `question_ids`。

报告侧问题（正文含 URL、事实性结论缺引用、引用不存在的 `evidence_id`、重复 `claim_id`）被标记为
可修复，系统会重写一次而不是让整个任务失败；证据链损坏（`unknown_source_id`、
`quote_missing_from_snapshot`、`quote_offset_mismatch`）仍然是致命失败。

## 审核输入与冲突处理

Reviewer 收到的是**被引用的证据**，外加**数量可控时**的未引用证据（阈值
`ReviewerAgent(max_uncited_evidence=…)`，默认 10；超过阈值则退回只给被引用证据，避免上下文膨胀）。
`REVIEW_SCOPE_JSON` 用 `cited_evidence_ids` / `uncited_evidence_ids` /
`uncited_evidence_included` 显式区分两者——未引用证据的用途是让 Reviewer 发现**选择性引用**，
而不是被当成"漏引用"错误。

`ResearchProgress.conflicts` 由 Reviewer 的 `ReviewResult.conflicts` 写入（此前该字段从未被赋值：
`SupportType` 只有 `direct`／`partial`，数据模型无法表达"相反"）。**存在未消解冲突时任务不判为
`COMPLETED`**，而是 `COMPLETED_WITH_WARNINGS`，并在 `unverified_notice` 中列出涉及的 `question_id`。

无新事件时服务端发送 `: heartbeat` 注释。心跳不写数据库，也不推进 sequence。

断线续读支持两种方式：

```http
Last-Event-ID: 18
```

或：

```text
/api/research/{task_id}/events?after=18
```

服务端只返回 `sequence > 18` 的事件。sequence 是数据库单调整数；终态事件发送后连接关闭。

## 获取报告

### `GET /api/reports/{task_id}`

任务尚无报告时返回 HTTP 404。成功响应包含：

- `report`：标题、摘要、Claim、比较表、建议、风险和未验证提示。
- `evidence`：EvidenceCard 与逐字 Quote。
- `sources`：来源 URL、独立资源 URL／锚点、类型及分类依据、抓取时间、可空的发布／更新时间、正文提取版本、快照正文与 content hash。
- `citations`：程序生成的 Claim → Evidence → Source → URL 关系。
- `review`：Reviewer 的 Claim/Pair/Requirement 审核。
- `metrics`：确定性质量指标与 passed。
- `delivery_self_check`：交付前**确定性自检**结论（`status` / `findings` / `checked_evidence` /
  `checked_sources` / `deterministic_only`）。按请求重算，零模型调用，因此永远与当前
  持久化数据一致，不需要额外表或迁移。`findings[].severity` 为 `blocking` 时表示该依据
  不可用（例如引文落在来源的删除线／已废弃段落内）；自检**只标注**，不改变 `status`、
  不删除内容。历史 V1 报告没有自检记录时该字段为 `null`。
- `execution`：运行统计。
- `provider_notice`：本次 Provider 的事实声明。

最终 URL 不来自 Manager 输出，只从持久化 SourceRecord 渲染。

## 常见错误

| 状态码 | 含义 |
|---|---|
| 400 | SSE 游标不是非负整数 |
| 404 | Task 或 Report 不存在 |
| 422 | 请求不符合 Pydantic Schema |
| 503 | GLM 模式缺少 API Key，Research Service 不可用 |

公共失败事件只公开错误分类和安全文案，不回传 Provider 原始响应、请求头或密钥。

## V2 中间产物与恢复

### `GET /api/research/{task_id}/artifacts`

报告生成前和失败后均可读取已提交的 `searches`、`sources`、`evidence` 和可空 `progress`，响应同时含 task_id/status。任务不存在返回 404；尚无产物时返回空数组。

### `POST /api/research/{task_id}/resume`

无请求体。获得持久化运行租约后返回 202，响应为 `{task_id, accepted: true, status}`；status 是接收请求时的状态，后台执行进度通过状态接口和 SSE 查询。运行中或已经完成返回 409；不存在返回 404。

恢复不会增加预算或清除旧账本。预算耗尽的任务即使请求被接受，仍可能立即再次停止。已提交工具结果复用；外部响应返回到提交结果之间的崩溃窗口不能保证不重试，重试记录为新的 attempt。已结束的图快照复用，不重复写报告。当前租约有效期一小时，每 60 秒续租；续租失败或所有权被替换时取消旧执行器，避免旧终态覆盖新任务。进程长时间挂起、外部请求已经发出但尚未提交的窗口仍不具有恰好一次保证；尚不宣称多进程生产部署保证。

## V2 审核、用量和定位补充

- `review.report_section_results` 对 executive_summary、recommendation 和 comparison_table 单独给出语义审核结果；`gap_requests` 给出缺失信息、关联问题／结论、原因和是否需要再研究。
- `claim_support_results` 和 `evidence_support_results` 是模型判断；原文与 offset 校验是确定性检查。自动流程质量分不代表事实准确率。
- offsets 是 normalized_content 的 **Unicode code point** 半开区间 [start, end)，不是 UTF-8 字节，也不是 JavaScript UTF-16 下标。快照更新后不得沿用旧 offset；历史记录可为空。
- execution.prompt_tokens/completion_tokens 是实际结算或保守预留的预算计费数量；actual_prompt_tokens/actual_completion_tokens 仅累加已知实际值。usage_measurement 及三类 attempt 数区分 actual/estimated/unknown。
- cost_amount/cost_currency 可空，cost_reason 解释缺失。无模型账本、部分费用未知或币种不一致均不显示为免费。旧 estimated_cost 只为兼容保留，禁止作为实际账单依据。
- trace 增加 reserved_prompt_tokens、reserved_completion_tokens 和 budget_phase；这些是预算预留，不是 Provider 实际 usage。
- `RESEARCH_PROGRESS_UPDATED` 在观察持久化后发送；补证事件使用 SUPPLEMENT_STARTED、SUPPLEMENT_COMPLETED；具体缺口保存在 progress 和 review.gap_requests。事件序号在同一数据库全局递增，同一任务不保证连续，按 sequence 去重和续读。

- 任务快照新增 `is_running`：当前是否存在有效运行租约。不暴露 owner token。非完成任务在没有有效租约时可尝试恢复，服务端仍原子检查并发冲突。
- 任务快照在存在调用账本时实时汇总调用与 Token，V1 无账本记录保留 legacy 统计；latency_ms 仍为已保存的执行耗时，不是运行中的实时计时器。
- SSE 不会因历史终态事件中断后续恢复回放；仅在当前任务终态、无有效租约且最新终态事件已消费后结束。
