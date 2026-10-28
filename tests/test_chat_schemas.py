from datetime import datetime

import pytest
from pydantic import ValidationError

from app.core.ai.prompts import Citation
from app.core.chat import MessageRole, MessageStatus, QueryType
from app.schemas.chat import ChatRequest, MessagePage, MessageRead, SearchRequest, SessionDetail
from app.schemas.paper import PaperBrief


@pytest.mark.parametrize("value", ["", "   ", "\n\t"])
def test_search_request_rejects_blank(value: str) -> None:
    with pytest.raises(ValidationError):
        SearchRequest(query=value)


@pytest.mark.parametrize("value", ["", "   ", "\n\t"])
def test_chat_request_rejects_blank(value: str) -> None:
    with pytest.raises(ValidationError):
        ChatRequest(message=value)


def test_requests_trim_and_forbid_extra_fields() -> None:
    assert SearchRequest(query="  transformer  ").query == "transformer"
    assert ChatRequest(message="  explain  ").message == "explain"

    with pytest.raises(ValidationError):
        SearchRequest(query="x", page=1)
    with pytest.raises(ValidationError):
        ChatRequest(message="x", session_id=1)


def test_requests_reject_over_2000_chars() -> None:
    with pytest.raises(ValidationError):
        SearchRequest(query="x" * 2001)
    with pytest.raises(ValidationError):
        ChatRequest(message="x" * 2001)

    assert len(SearchRequest(query=f"  {'x' * 2000}  ").query) == 2000
    assert len(ChatRequest(message=f"  {'x' * 2000}  ").message) == 2000


def test_session_detail_and_message_page_serialize_utc_z() -> None:
    created_at = datetime(2026, 10, 28, 12, 30, 45)
    detail = SessionDetail(
        id=1,
        query="transformer",
        query_type=QueryType.text,
        paper_count=1,
        evidence_count=2,
        related_repo_count=0,
        has_evidence=True,
        system_message="system",
        conversation_id=3,
        papers=[
            PaperBrief(
                id=9,
                title="Attention Is All You Need",
                authors=["Vaswani"],
                year=2017,
                venue="NeurIPS",
                is_archived=False,
            )
        ],
        created_at=created_at,
    )
    page = MessagePage(
        items=[
            MessageRead(
                id=7,
                role=MessageRole.assistant,
                content="Answer [1]",
                citations=[],
                status=MessageStatus.complete,
                created_at=created_at,
            )
        ],
        has_more=False,
        next_before_id=None,
    )

    detail_json = detail.model_dump(mode="json")
    page_json = page.model_dump(mode="json")

    assert detail_json["created_at"] == "2026-10-28T12:30:45Z"
    assert page_json["items"][0]["created_at"] == "2026-10-28T12:30:45Z"


def test_message_read_deserializes_citations_json() -> None:
    message = MessageRead.model_validate(
        {
            "id": 1,
            "role": "assistant",
            "content": "Answer [1]",
            "citations": [
                {
                    "index": 1,
                    "paper_id": 2,
                    "chunk_id": 3,
                    "page": 4,
                    "quote": "quote",
                }
            ],
            "status": "complete",
            "created_at": "2026-10-28T12:30:45Z",
        }
    )

    assert message.citations == [Citation(index=1, paper_id=2, chunk_id=3, page=4, quote="quote")]
