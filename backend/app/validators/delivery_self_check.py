"""交付前确定性自检：报告落盘前跑一遍，零模型调用。

为什么单独成模块
----------------
确定性检查此前只在离线报告核对工具里存在，产品交付链路从来没有人跑它。
结果是：一次真实运行可以自动完成、自动评分 94，
而它引用的原文其实来自官方用 `<s>` 划掉的**已废弃**段落。

本模块把那条防线接进**交付链路**（`workflow/graph.py` 的 `_save_report` 之前）
以及**报告 API**（`api/reports.py`），使产品链路上也有确定性检查。

设计约束
--------
- **只读、纯函数**：不写数据库、不改任务状态、不删除任何证据或报告。
- **零模型调用**：判定完全由程序做出，`deterministic_only` 字段自证这一点。
- **复用而非复制**：删除线与快照定位逻辑直接调用 `app.validators.evidence_facts`，
  不在此处重写一份，避免判定漂移。
- **不改冻结门槛**：本模块只**标注**问题，不新增/下调任何发布门槛。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Identifier
from app.schemas.evidence import EvidenceCard, SourceRecord
from app.schemas.report import ResearchReport
from app.validators.evidence_facts import (
    build_snapshot_contexts,
    version_tokens,
)


class DeliveryFindingCode(StrEnum):
    """确定性可判定的问题类别。"""

    # 引文落在原始 HTML 的 <s>/<del>/<strike> 内 —— 官方已划掉的废弃说法。
    DEPRECATED_EVIDENCE = "DEPRECATED_EVIDENCE"
    # 引文在原始快照与归一化正文中都定位不到。
    QUOTE_NOT_FOUND = "QUOTE_NOT_FOUND"
    # 报告出现版本号，但没有任何来源 URL 命中该版本。
    VERSION_NOT_MATCHED = "VERSION_NOT_MATCHED"


class DeliverySeverity(StrEnum):
    # 该依据不可用：报告若据此下结论，结论没有可核验的支撑。
    BLOCKING = "blocking"
    WARNING = "warning"
    INFO = "info"


class DeliveryFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: DeliveryFindingCode
    severity: DeliverySeverity
    message: str
    evidence_id: Identifier | None = None
    source_id: Identifier | None = None
    source_url: str | None = None
    # 出问题的原文片段，供人工直接核对（这正是"可回溯核验"的落点）。
    evidence_span: str | None = None


class DeliverySelfCheck(BaseModel):
    """一次交付自检的确定性结论。"""

    model_config = ConfigDict(extra="forbid")

    status: str
    findings: list[DeliveryFinding] = Field(default_factory=list)
    checked_evidence: int = 0
    checked_sources: int = 0
    # 自证：本次检查没有调用任何模型，结论全部由程序判定。
    deterministic_only: bool = True
    # 给用户看的一句话摘要（由 notice 属性生成）；无问题时为 null。
    notice: str | None = None

    @property
    def blocking_findings(self) -> list[DeliveryFinding]:
        return [
            item for item in self.findings
            if item.severity is DeliverySeverity.BLOCKING
        ]

    @property
    def summary(self) -> str | None:
        """给用户看的一句话摘要；没有问题时不产生噪音。"""

        if not self.findings:
            return None
        blocking = len(self.blocking_findings)
        deprecated = [
            item for item in self.findings
            if item.code is DeliveryFindingCode.DEPRECATED_EVIDENCE
        ]
        parts: list[str] = []
        if deprecated:
            ids = "、".join(
                sorted({item.evidence_id or "?" for item in deprecated})
            )
            parts.append(
                f"交付自检：{len(deprecated)} 条依据落在来源的删除线／已废弃段落内"
                f"（{ids}），相关结论可能已不适用"
            )
        if blocking:
            parts.append(f"其中 {blocking} 项为阻断级问题")
        if not parts:
            parts.append(f"交付自检发现 {len(self.findings)} 项提示")
        return "；".join(parts) + "。"


def run_delivery_self_check(
    report: ResearchReport,
    evidence: list[EvidenceCard],
    sources: list[SourceRecord],
) -> DeliverySelfCheck:
    """对一份即将交付的报告执行确定性自检。

    只检查**被报告实际引用**的证据：未被引用的证据不影响本次结论。
    """

    source_by_id = {item.source_id: item for item in sources}
    cited_ids: list[str] = []
    for claim in report.claims:
        for evidence_id in claim.evidence_ids:
            if evidence_id not in cited_ids:
                cited_ids.append(evidence_id)

    cited_evidence = [
        item for item in evidence if item.evidence_id in set(cited_ids)
    ]
    # 引文定位与删除线判定复用评测侧的既有实现，避免两侧漂移。
    contexts = build_snapshot_contexts(cited_evidence, sources)

    findings: list[DeliveryFinding] = []
    for context in contexts:
        source = source_by_id.get(context.source_id)
        if context.deprecated:
            findings.append(
                DeliveryFinding(
                    code=DeliveryFindingCode.DEPRECATED_EVIDENCE,
                    severity=DeliverySeverity.BLOCKING,
                    message=(
                        f"证据 {context.evidence_id} 的引文落在来源"
                        f" {context.source_id} 的删除线／已废弃段落内"
                        f"（{context.deprecated_reason}）"
                    ),
                    evidence_id=context.evidence_id,
                    source_id=context.source_id,
                    source_url=context.url,
                    evidence_span=context.raw_context or context.quote,
                )
            )
            continue
        if not context.quote_found_in_source:
            findings.append(
                DeliveryFinding(
                    code=DeliveryFindingCode.QUOTE_NOT_FOUND,
                    severity=DeliverySeverity.BLOCKING,
                    message=(
                        f"证据 {context.evidence_id} 的引文在来源"
                        f" {context.source_id} 的原始快照与归一化正文中都定位不到"
                    ),
                    evidence_id=context.evidence_id,
                    source_id=context.source_id,
                    source_url=context.url,
                    evidence_span=context.quote,
                )
            )

    findings.extend(_version_findings(report, sources))

    status = (
        "fail"
        if any(item.severity is DeliverySeverity.BLOCKING for item in findings)
        else ("warn" if findings else "pass")
    )
    result = DeliverySelfCheck(
        status=status,
        findings=findings,
        checked_evidence=len(cited_evidence),
        checked_sources=len(sources),
    )
    return result.model_copy(update={"notice": result.summary})


def _version_findings(
    report: ResearchReport, sources: list[SourceRecord]
) -> list[DeliveryFinding]:
    """报告里提到的版本号，是否有来源 URL 命中。

    这是**提示级**：来源 URL 不含版本号并不等于结论错误（可能是无版本路径的官方文档），
    所以它只提示人工核查适用性，不阻断交付。
    """

    report_versions = version_tokens(report.model_dump_json())
    if not report_versions:
        return []
    matched = {
        token
        for source in sources
        for token in version_tokens(source.url)
    }
    unmatched = sorted(report_versions - matched)
    if not unmatched:
        return []
    return [
        DeliveryFinding(
            code=DeliveryFindingCode.VERSION_NOT_MATCHED,
            severity=DeliverySeverity.INFO,
            message=(
                f"报告提到版本 {unmatched}，但没有任何来源 URL 包含这些版本号；"
                "结论的版本适用性需人工核查"
            ),
        )
    ]


__all__ = [
    "DeliveryFinding",
    "DeliveryFindingCode",
    "DeliverySelfCheck",
    "DeliverySeverity",
    "run_delivery_self_check",
]
