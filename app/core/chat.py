from enum import StrEnum


class QueryType(StrEnum):
    text = "text"
    pdf = "pdf"


class MessageRole(StrEnum):
    system = "system"
    user = "user"
    assistant = "assistant"


class MessageStatus(StrEnum):
    complete = "complete"
    stopped = "stopped"
    error = "error"


class ChatScope(StrEnum):
    session = "session"
    repo = "repo"
    paper = "paper"


SESSION_SYSTEM_MESSAGE = "我会只根据本次查询的论文证据回答，并在没有足够证据时拒答。"
EMPTY_SESSION_SYSTEM_MESSAGE = "没有找到足够证据回答这次查询。你可以修改查询，或去公开仓库看看。"
