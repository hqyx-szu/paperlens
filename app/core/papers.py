from enum import StrEnum


class PaperStatus(StrEnum):
    queued = "queued"
    parsing = "parsing"
    indexing = "indexing"
    ready = "ready"
    failed = "failed"


PROCESSING_STATUSES = (
    PaperStatus.queued.value,
    PaperStatus.parsing.value,
    PaperStatus.indexing.value,
)

INTERRUPTED_MESSAGE = "处理中断，请重试"
