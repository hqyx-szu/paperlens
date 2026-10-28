from sqlalchemy import ColumnElement

from app.core.papers import PaperStatus
from app.models import Paper


def visible_paper_conditions() -> tuple[ColumnElement[bool], ...]:
    """新搜索/新加仓库入口统一使用：ready 且未归档。"""
    return (
        Paper.status == PaperStatus.ready.value,
        Paper.is_archived.is_(False),
    )
