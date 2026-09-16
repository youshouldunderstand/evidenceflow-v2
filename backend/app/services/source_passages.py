"""Deterministic passage segmentation and local search over source snapshots."""

from __future__ import annotations

import hashlib
import re

from app.schemas.evidence import SourceRecord
from app.schemas.passage import PassageMatch, SourcePassage


PASSAGE_EXTRACTION_VERSION = "passages-v1"
DEFAULT_PASSAGE_CHARS = 1800
MIN_PASSAGE_CHARS = 500
_BOUNDARY = re.compile(r"[.!?。！？;；](?:\s+|$)")


def source_passages(
    source: SourceRecord,
    *,
    query: str | None = None,
    offset: int = 0,
    limit: int = 20,
    context: int = 1,
) -> list[SourcePassage]:
    """Return sequential passages or locally ranked matches with context."""

    passages = _segment(source)
    if not query or not query.strip():
        return passages[offset : offset + limit]

    query_value = query.casefold().strip()
    query_tokens = {
        token for token in re.findall(r"[\w\-]+", query_value) if len(token) > 1
    }
    ranked: list[tuple[int, int, list[PassageMatch]]] = []
    for index, passage in enumerate(passages):
        text = passage.text.casefold()
        matches = _substring_matches(text, query_value)
        token_hits = sum(1 for token in query_tokens if token in text)
        score = len(matches) * 1000 + token_hits
        if score:
            ranked.append((score, index, matches))
    ranked.sort(key=lambda item: (-item[0], item[1]))

    selected: set[int] = set()
    matches_by_index: dict[int, list[PassageMatch]] = {}
    for _, index, matches in ranked[offset:]:
        for candidate in range(max(0, index - context), min(len(passages), index + context + 1)):
            if len(selected) >= limit and candidate not in selected:
                break
            selected.add(candidate)
        matches_by_index[index] = matches
        if len(selected) >= limit:
            break
    return [
        passages[index].model_copy(
            update={"matches": matches_by_index.get(index, [])}
        )
        for index in sorted(selected)
    ]


def _segment(source: SourceRecord) -> list[SourcePassage]:
    content = source.normalized_content
    passages: list[SourcePassage] = []
    cursor = 0
    while cursor < len(content):
        while cursor < len(content) and content[cursor].isspace():
            cursor += 1
        if cursor >= len(content):
            break
        hard_end = min(len(content), cursor + DEFAULT_PASSAGE_CHARS)
        end = hard_end
        if hard_end < len(content):
            candidates = [
                match.end()
                for match in _BOUNDARY.finditer(
                    content, cursor + MIN_PASSAGE_CHARS, hard_end
                )
            ]
            if candidates:
                end = candidates[-1]
        trimmed_end = end
        while trimmed_end > cursor and content[trimmed_end - 1].isspace():
            trimmed_end -= 1
        index = len(passages)
        identity = "\x1f".join(
            (source.source_id, source.content_hash, str(index), str(cursor), str(trimmed_end))
        )
        passages.append(
            SourcePassage(
                passage_id="PASSAGE_" + hashlib.sha256(identity.encode()).hexdigest()[:12],
                source_id=source.source_id,
                paragraph_index=index,
                text=content[cursor:trimmed_end],
                start_offset=cursor,
                end_offset=trimmed_end,
                locator=f"chars:{cursor}-{trimmed_end}",
            )
        )
        cursor = max(end, cursor + 1)
    return passages


def _substring_matches(text: str, query: str) -> list[PassageMatch]:
    matches: list[PassageMatch] = []
    start = 0
    while query:
        index = text.find(query, start)
        if index < 0:
            break
        matches.append(PassageMatch(start_offset=index, end_offset=index + len(query)))
        start = index + len(query)
    return matches
