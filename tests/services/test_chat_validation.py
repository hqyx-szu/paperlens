from __future__ import annotations

from app.core.ai.prompts import REFUSAL_TEXT
from app.services.chat import ChatEvidence, validate_answer_citations


def _evidence(index: int, *, quote: str = "证据原文", content: str | None = None) -> ChatEvidence:
    return ChatEvidence(
        index=index,
        paper_id=index,
        chunk_id=index * 10,
        page=index,
        quote=quote,
        content=content if content is not None else quote,
    )


def test_valid_single_citation() -> None:
    answer, citations = validate_answer_citations("结论 [1]", [_evidence(1, quote="证据原文")])

    assert answer == "结论 [1]"
    assert len(citations) == 1
    assert citations[0].model_dump() == {
        "index": 1,
        "paper_id": 1,
        "chunk_id": 10,
        "page": 1,
        "quote": "证据原文",
    }


def test_out_of_range_index_dropped() -> None:
    answer, citations = validate_answer_citations("结论 [9]", [_evidence(1)])

    assert answer == "结论 "
    assert citations == []


def test_quote_not_substring_dropped() -> None:
    evidence = _evidence(1, quote="不存在的 quote", content="真实内容")

    answer, citations = validate_answer_citations("结论 [1]", [evidence])

    assert answer == "结论 "
    assert citations == []


def test_renumber_out_of_order() -> None:
    evidence = [_evidence(1, quote="alpha"), _evidence(2, quote="beta")]

    answer, citations = validate_answer_citations("B [2] A [1]", evidence)

    assert answer == "B [1] A [2]"
    assert [citation.index for citation in citations] == [1, 2]
    assert [citation.quote for citation in citations] == ["beta", "alpha"]


def test_duplicate_marker_reuses_index() -> None:
    answer, citations = validate_answer_citations(
        "A [1] B [1]",
        [_evidence(1, quote="alpha")],
    )

    assert answer == "A [1] B [1]"
    assert len(citations) == 1


def test_refusal_has_no_citations() -> None:
    answer, citations = validate_answer_citations(REFUSAL_TEXT, [_evidence(1)])

    assert answer == REFUSAL_TEXT
    assert citations == []


def test_markdown_or_chinese_text_preserved() -> None:
    answer, citations = validate_answer_citations(
        "**结论**：中文内容 `[1]`，保留格式。",
        [_evidence(1, quote="中文")],
    )

    assert answer == "**结论**：中文内容 `[1]`，保留格式。"
    assert len(citations) == 1


def test_quote_truncated_to_500() -> None:
    content = "q" * 600
    answer, citations = validate_answer_citations("结论 [1]", [_evidence(1, quote=content)])

    assert answer == "结论 [1]"
    assert len(citations) == 1
    assert citations[0].quote == "q" * 500
    assert citations[0].quote in content


def test_more_than_12_citations_truncated() -> None:
    evidence = [_evidence(index, quote=f"quote-{index}") for index in range(1, 14)]
    answer, citations = validate_answer_citations(
        " ".join(f"[{index}]" for index in range(1, 14)),
        evidence,
    )

    assert "[13]" not in answer
    assert len(citations) == 12
    assert [citation.index for citation in citations] == list(range(1, 13))
