"""确定性证据核对原语：删除线/废弃检测、引文定位、版本适用性。

为什么单独成模块
----------------
这些检查被"判断引用是否真的落在来源里"的代码共用：交付链路
（`validators/delivery_self_check.py` → 工作流与报告 API）需要它，
任何独立的报告核对工具也需要它。

把原语下沉到 `validators/` 而不是和交付流程写在一起，是为了让判定逻辑只有一份：
交付链路若在别处复制一份，两侧判定会各自漂移（本项目已经发生过"测量工具自己有 bug"的事故）。
本模块不依赖持久化层，只依赖 schema。

本模块**不调用任何模型**：所有结论都是程序可判定的。
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Identifier, NonEmptyStr
from app.schemas.evidence import EvidenceCard, SourceRecord

# 原始 HTML 中表示"已删除/已废弃"的标签。正文提取会丢弃这些内容，
# 但原始快照仍保留，因此可以据此发现"引文来自已废弃段落"。
DEPRECATED_TAGS = ("s", "del", "strike")

VERSION_PATTERN = re.compile(r"\b\d+\.\d{1,2}(?:\.\d+){0,2}\b")
_WHITESPACE = re.compile(r"\s+")

DEFAULT_CONTEXT_RADIUS = 600


def normalize_span(text: str) -> str:
    """折叠空白，用于跨行引文比对。"""

    return _WHITESPACE.sub(" ", text).strip()


class DeprecationFinding(BaseModel):
    """确定性结论：某条证据的引文是否落在已废弃区域。"""

    model_config = ConfigDict(extra="forbid")

    evidence_id: Identifier
    source_id: Identifier
    verdict: bool
    reason: NonEmptyStr
    deprecated_span: str | None = None


class SnapshotContext(BaseModel):
    """一条证据在**原始快照**中的有界上下文。

    这是独立于模型的信息通道：抽取后的正文会丢掉删除线、版本注记等语义，
    只有原始 HTML 能自证"这句话现在还算不算数"。
    """

    model_config = ConfigDict(extra="forbid")

    evidence_id: Identifier
    source_id: Identifier
    # 来源缺失时允许为空，便于把「无法核对」与「核对通过」区分开。
    url: str | None = None
    quote: NonEmptyStr
    # 引文是否存在于来源中。必须同时看原始 HTML 与抽取后的正文：
    # 引文是从抽取正文里取的，而原始 HTML 含实体与内联标签，只搜 raw 会大量误报。
    quote_found_in_source: bool = False
    quote_found_in_raw_html: bool = False
    context_origin: str = "none"
    raw_context: str = ""
    deprecated: bool
    deprecated_reason: NonEmptyStr | None = None
    version_tokens_in_url: list[str] = Field(default_factory=list)
    version_tokens_in_context: list[str] = Field(default_factory=list)
    extraction_version: str | None = None
    retrieved_at: str | None = None
    published_at: str | None = None


def _deprecated_texts(raw_html: str) -> list[str]:
    """抽取原始 HTML 中所有删除线/废弃标签内的文本。"""

    soup = BeautifulSoup(raw_html, "html.parser")
    texts: list[str] = []
    for tag in DEPRECATED_TAGS:
        for element in soup.find_all(tag):
            text = normalize_span(element.get_text(" ", strip=True))
            if text:
                texts.append(text)
    return texts


def find_deprecated_quotes(
    evidence: list[EvidenceCard], sources: list[SourceRecord]
) -> list[DeprecationFinding]:
    """判断每条证据的引文是否来自原始快照中的已废弃段落。

    这是纯确定性检查，不调用任何模型。它能抓住真实人工验收发现的
    「引文匹配原文、但原文已被官方划掉」这一类错误。
    """

    source_by_id = {item.source_id: item for item in sources}
    findings: list[DeprecationFinding] = []
    for card in evidence:
        source = source_by_id.get(card.source_id)
        if source is None:
            findings.append(
                DeprecationFinding(
                    evidence_id=card.evidence_id,
                    source_id=card.source_id,
                    verdict=False,
                    reason="来源缺失，无法判定是否已废弃",
                )
            )
            continue
        quote = normalize_span(card.quote)
        deprecated_spans = _deprecated_texts(source.raw_content)
        matched = next(
            (span for span in deprecated_spans if quote and quote in span), None
        )
        findings.append(
            DeprecationFinding(
                evidence_id=card.evidence_id,
                source_id=card.source_id,
                verdict=matched is not None,
                reason=(
                    "引文位于原始快照的删除线/废弃标签内"
                    if matched is not None
                    else "引文不在任何删除线/废弃标签内"
                ),
                deprecated_span=matched,
            )
        )
    return findings


def version_tokens(text: str) -> set[str]:
    return set(VERSION_PATTERN.findall(text or ""))


def check_version_applicability(
    version_scope: str, source_urls: list[str]
) -> tuple[bool | None, str]:
    """确定性检查：已保存来源 URL 是否命中题目要求的版本号。"""

    target = version_tokens(version_scope)
    if not target:
        return None, "题目未指定版本号，无法自动判定"
    hits = [url for url in source_urls if target & version_tokens(url)]
    if hits:
        return True, f"命中版本号 {sorted(target)} 的来源 {len(hits)} 条"
    return False, f"来源 URL 均未包含目标版本号 {sorted(target)}，需人工核查适用性"


def verify_span(span: str | None, haystacks: list[str]) -> bool | None:
    """核验给出的原文引证是否真实存在。

    返回 None 表示未给出引证（无法核验），False 表示引证无法定位
    （按「编造依据」计入错误统计）。
    """

    if not span:
        return None
    needle = normalize_span(span)
    if not needle:
        return None
    for haystack in haystacks:
        if needle in normalize_span(haystack):
            return True
    return False


def _normalized_with_map(text: str) -> tuple[str, list[int]]:
    """折叠空白，并保留「归一化位置 → 原始位置」的映射。"""

    out: list[str] = []
    mapping: list[int] = []
    previous_space = False
    for index, char in enumerate(text):
        if char.isspace():
            if previous_space:
                continue
            out.append(" ")
            mapping.append(index)
            previous_space = True
        else:
            out.append(char)
            mapping.append(index)
            previous_space = False
    return "".join(out), mapping


def locate_span(raw: str, needle: str) -> tuple[int, int] | None:
    """在原始文本中定位一段（可能跨行、含多余空白的）引文。"""

    normalized_needle = normalize_span(needle)
    if not normalized_needle:
        return None
    normalized_raw, mapping = _normalized_with_map(raw)
    index = normalized_raw.find(normalized_needle)
    if index < 0:
        return None
    start = mapping[index]
    end = mapping[min(index + len(normalized_needle) - 1, len(mapping) - 1)]
    return start, end + 1


def build_snapshot_contexts(
    evidence: list[EvidenceCard],
    sources: list[SourceRecord],
    *,
    radius: int = DEFAULT_CONTEXT_RADIUS,
) -> list[SnapshotContext]:
    """为每条证据构造原始快照上下文 + 确定性结论。

    零模型调用。输出的 `deprecated` 与版本命中情况是**事实**，
    下游只能引用，不能推翻。
    """

    source_by_id = {item.source_id: item for item in sources}
    findings = {
        item.evidence_id: item for item in find_deprecated_quotes(evidence, sources)
    }
    contexts: list[SnapshotContext] = []
    for card in evidence:
        source = source_by_id.get(card.source_id)
        if source is None:
            contexts.append(
                SnapshotContext(
                    evidence_id=card.evidence_id,
                    source_id=card.source_id,
                    url=None,
                    quote=card.quote,
                    quote_found_in_source=False,
                    quote_found_in_raw_html=False,
                    context_origin="none",
                    raw_context="",
                    deprecated=False,
                    deprecated_reason="来源缺失，无法核对原始快照",
                )
            )
            continue
        in_raw = locate_span(source.raw_content, card.quote)
        in_normalized = locate_span(source.normalized_content, card.quote)
        if in_raw is not None:
            start, end = in_raw
            raw_context = source.raw_content[
                max(0, start - radius) : min(len(source.raw_content), end + radius)
            ]
            origin = "raw_html"
        elif in_normalized is not None:
            start, end = in_normalized
            raw_context = source.normalized_content[
                max(0, start - radius) : min(len(source.normalized_content), end + radius)
            ]
            origin = "normalized_text"
        else:
            raw_context = ""
            origin = "none"
        finding = findings.get(card.evidence_id)
        contexts.append(
            SnapshotContext(
                evidence_id=card.evidence_id,
                source_id=source.source_id,
                url=source.url,
                quote=card.quote,
                quote_found_in_source=in_raw is not None or in_normalized is not None,
                quote_found_in_raw_html=in_raw is not None,
                context_origin=origin,
                raw_context=raw_context,
                deprecated=bool(finding and finding.verdict),
                deprecated_reason=finding.reason if finding else None,
                version_tokens_in_url=sorted(version_tokens(source.url)),
                version_tokens_in_context=sorted(version_tokens(raw_context)),
                extraction_version=source.extraction_version,
                retrieved_at=source.retrieved_at.isoformat(),
                published_at=(
                    source.published_at.isoformat() if source.published_at else None
                ),
            )
        )
    return contexts
