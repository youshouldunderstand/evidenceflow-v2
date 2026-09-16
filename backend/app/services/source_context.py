"""Bounded provenance for model context; metadata is not factual evidence."""

from app.schemas.evidence import EvidenceCard, SourceRecord


def source_metadata(source: SourceRecord) -> dict[str, object]:
    return source.model_dump(mode="json", include={
        "source_id", "title", "url", "source_type", "retrieved_at",
        "content_hash", "content_type", "extraction_version", "source_type_reason",
        "published_at", "source_updated_at",
    })


def evidence_source_metadata(
    evidence: list[EvidenceCard], sources: list[SourceRecord],
) -> list[dict[str, object]]:
    referenced = {item.source_id for item in evidence}
    return [source_metadata(source) for source in sources if source.source_id in referenced]
