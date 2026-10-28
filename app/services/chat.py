from __future__ import annotations

from dataclasses import dataclass

from app.core.ai.prompts import CITATION_RE, REFUSAL_TEXT, Citation

MAX_CITATIONS = 12


@dataclass(frozen=True, slots=True)
class ChatEvidence:
    index: int
    paper_id: int
    chunk_id: int
    page: int | None
    quote: str
    content: str


def validate_answer_citations(
    answer: str,
    evidence: list[ChatEvidence],
) -> tuple[str, list[Citation]]:
    if REFUSAL_TEXT in answer.strip():
        return answer, []

    by_index = {item.index: item for item in evidence}
    mapping: dict[int, int] = {}
    citations: list[Citation] = []

    def replace(match: object) -> str:
        number = int(match.group(1))  # type: ignore[attr-defined]
        if number in mapping:
            return f"[{mapping[number]}]"
        item = by_index.get(number)
        if item is None:
            return ""
        quote = item.quote[:500]
        if quote not in item.content:
            return ""
        if len(citations) >= MAX_CITATIONS:
            return ""

        new_index = len(citations) + 1
        mapping[number] = new_index
        citations.append(
            Citation(
                index=new_index,
                paper_id=item.paper_id,
                chunk_id=item.chunk_id,
                page=item.page,
                quote=quote,
            )
        )
        return f"[{new_index}]"

    return CITATION_RE.sub(replace, answer), citations
