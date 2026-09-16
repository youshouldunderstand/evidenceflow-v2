"""Minimal local-document search without a vector database."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app.schemas.common import NonEmptyStr


class LocalSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: NonEmptyStr
    excerpt: NonEmptyStr


class SimpleLocalSearch:
    """Case-insensitive substring search for UTF-8 Markdown and text files."""

    def __init__(self, root: Path, max_results: int = 10) -> None:
        self._root = root.resolve()
        self._max_results = max_results

    def search(self, query: str) -> list[LocalSearchResult]:
        needle = query.strip().casefold()
        if not needle or not self._root.is_dir():
            return []

        results: list[LocalSearchResult] = []
        for path in sorted(self._root.rglob("*")):
            if path.suffix.lower() not in {".md", ".txt"} or not path.is_file():
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(self._root):
                continue
            try:
                text = resolved.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            position = text.casefold().find(needle)
            if position < 0:
                continue
            start = max(0, position - 120)
            end = min(len(text), position + len(query) + 240)
            results.append(
                LocalSearchResult(path=str(resolved), excerpt=text[start:end].strip())
            )
            if len(results) >= self._max_results:
                break
        return results

