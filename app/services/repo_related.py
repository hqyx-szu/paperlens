from __future__ import annotations

import math
import re
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.repos import RepoVisibility
from app.models import Paper, Repo, RepoPaper, SearchSession, SessionPaper, User
from app.schemas.common import UserBrief
from app.schemas.repo import RelatedRepoItem, RelatedRepoPage
from app.services.repos import repo_counts

KEYWORD_LIMIT = 20
REASON_MAX = 100
STAR_WEIGHT = 0.2
# 权重来自契约 §10.12 与交接 §10.4：仓库名 3、简介 2、仓库论文标题 1，归一化后相加。
NAME_WEIGHT = 3.0
DESCRIPTION_WEIGHT = 2.0
PAPER_TITLE_WEIGHT = 1.0
WEIGHT_TOTAL = NAME_WEIGHT + DESCRIPTION_WEIGHT + PAPER_TITLE_WEIGHT

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+")

_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "are",
        "was",
        "were",
        "what",
        "how",
        "why",
        "which",
        "paper",
        "papers",
        "study",
        "method",
        "基于",
        "关于",
        "什么",
        "怎么",
        "如何",
        "哪些",
        "这个",
        "那个",
        "论文",
        "研究",
        "方法",
        "问题",
        "可以",
        "我们",
        "他们",
        "以及",
        "或者",
        "一个",
        "进行",
        "通过",
        "本文",
        "这些",
        "那些",
    }
)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _token_variants(token: str) -> list[str]:
    if token.isascii():
        return [token] if len(token) >= 2 else []
    variants: list[str] = []
    if len(token) <= 4:
        variants.append(token)
    for size in (2, 3, 4):
        if len(token) > size:
            variants.extend(token[index : index + size] for index in range(len(token) - size + 1))
    return variants


def extract_keywords(text: str) -> list[str]:
    """中文按标点/空白切分后取 2–4 字 n-gram，英文小写分词，去停用词，最多 20 个。"""
    keywords: list[str] = []
    seen: set[str] = set()
    for token in _TOKEN_RE.findall(text):
        for candidate in _token_variants(token):
            key = candidate.lower()
            if key in seen or key in _STOPWORDS:
                continue
            seen.add(key)
            keywords.append(candidate)
            if len(keywords) >= KEYWORD_LIMIT:
                return keywords
    return keywords


def _match(keywords: list[str], text: str | None) -> tuple[float, list[str]]:
    if not text or not keywords:
        return 0.0, []
    lowered = text.lower()
    hits = [keyword for keyword in keywords if keyword.lower() in lowered]
    return len(hits) / len(keywords), hits


def score_repo(
    *,
    keywords: list[str],
    name: str,
    description: str,
    paper_titles: list[str],
    star_count: int,
) -> tuple[float, float, list[str], int]:
    """返回 (base, score, 命中词, 相关论文数)；base>0 才允许入选。"""
    name_ratio, name_hits = _match(keywords, name)
    description_ratio, description_hits = _match(keywords, description)

    best_ratio = 0.0
    matched_titles = 0
    paper_hits: list[str] = []
    for title in paper_titles:
        ratio, hits = _match(keywords, title)
        if ratio > 0:
            matched_titles += 1
        if ratio > best_ratio:
            best_ratio = ratio
            paper_hits = hits

    base = (
        NAME_WEIGHT * name_ratio
        + DESCRIPTION_WEIGHT * description_ratio
        + PAPER_TITLE_WEIGHT * best_ratio
    ) / WEIGHT_TOTAL
    score = base + STAR_WEIGHT * math.log1p(star_count)
    matched = list(dict.fromkeys([*name_hits, *description_hits, *paper_hits]))
    return base, score, matched, matched_titles


def build_reason(*, matched: list[str], related_papers: int) -> str:
    if matched:
        words = "、".join(matched[:3])
        reason = f"与本次查询相关：命中关键词「{words}」"
    else:
        reason = "与本次查询的论文主题相关"
    if related_papers:
        reason += f"，仓库内 {related_papers} 篇论文主题接近"
    return reason[:REASON_MAX]


@dataclass(frozen=True, slots=True)
class RankedRepo:
    repo: Repo
    score: float
    star_count: int
    is_starred: bool
    paper_count: int
    reason: str


async def session_keywords(db: AsyncSession, session: SearchSession) -> list[str]:
    rows = (
        await db.execute(
            select(Paper.title, Paper.abstract)
            .join(SessionPaper, SessionPaper.paper_id == Paper.id)
            .where(SessionPaper.session_id == session.id)
        )
    ).all()
    parts = [session.query]
    for title, abstract in rows:
        parts.append(title)
        parts.append(abstract or "")
    return extract_keywords("\n".join(parts))


async def _candidate_repo_ids(
    db: AsyncSession,
    *,
    keywords: list[str],
    user_id: int,
) -> set[int]:
    scope = (Repo.visibility == RepoVisibility.public.value, Repo.owner_id != user_id)
    repo_filters = [
        condition
        for keyword in keywords
        for condition in (
            Repo.name.like(f"%{_escape_like(keyword)}%", escape="\\"),
            Repo.description.like(f"%{_escape_like(keyword)}%", escape="\\"),
        )
    ]
    title_filters = [
        Paper.title.like(f"%{_escape_like(keyword)}%", escape="\\") for keyword in keywords
    ]

    repo_ids = set((await db.scalars(select(Repo.id).where(*scope, or_(*repo_filters)))).all())
    title_ids = set(
        (
            await db.scalars(
                select(RepoPaper.repo_id)
                .join(Paper, Paper.id == RepoPaper.paper_id)
                .join(Repo, Repo.id == RepoPaper.repo_id)
                .where(*scope, or_(*title_filters))
                .distinct()
            )
        ).all()
    )
    return repo_ids | title_ids


async def rank_related_repos(
    db: AsyncSession,
    *,
    session: SearchSession,
    user_id: int,
) -> list[RankedRepo]:
    """相关仓库的唯一打分入口；SessionDetail.related_repo_count 也复用它。"""
    if session.evidence_count == 0:
        return []

    keywords = await session_keywords(db, session)
    if not keywords:
        return []

    candidate_ids = await _candidate_repo_ids(db, keywords=keywords, user_id=user_id)
    if not candidate_ids:
        return []

    repos = list((await db.scalars(select(Repo).where(Repo.id.in_(candidate_ids)))).all())
    if not repos:
        return []

    title_rows = (
        await db.execute(
            select(RepoPaper.repo_id, Paper.title)
            .join(Paper, Paper.id == RepoPaper.paper_id)
            .where(RepoPaper.repo_id.in_([repo.id for repo in repos]))
        )
    ).all()
    titles: dict[int, list[str]] = {}
    for repo_id, title in title_rows:
        titles.setdefault(repo_id, []).append(title)

    counts = await repo_counts(
        db,
        repo_ids=[repo.id for repo in repos],
        user_id=user_id,
    )

    ranked: list[RankedRepo] = []
    for repo in repos:
        stats = counts.get(repo.id)
        star_count = stats.star_count if stats else 0
        base, score, matched, related_papers = score_repo(
            keywords=keywords,
            name=repo.name,
            description=repo.description,
            paper_titles=titles.get(repo.id, []),
            star_count=star_count,
        )
        if base <= 0:
            continue
        ranked.append(
            RankedRepo(
                repo=repo,
                score=score,
                star_count=star_count,
                is_starred=bool(stats and stats.is_starred),
                paper_count=stats.paper_count if stats else 0,
                reason=build_reason(matched=matched, related_papers=related_papers),
            )
        )

    ranked.sort(key=lambda item: (-item.score, -item.star_count, item.repo.id))
    return ranked


async def related_repos(
    db: AsyncSession,
    *,
    session: SearchSession,
    user_id: int,
    page: int,
    page_size: int,
) -> RelatedRepoPage:
    ranked = await rank_related_repos(db, session=session, user_id=user_id)
    start = (page - 1) * page_size
    window = ranked[start : start + page_size]

    owner_ids = {item.repo.owner_id for item in window}
    owners = (
        dict(
            (
                await db.execute(select(User.id, User.display_name).where(User.id.in_(owner_ids)))
            ).all()
        )
        if owner_ids
        else {}
    )

    items = [
        RelatedRepoItem(
            id=item.repo.id,
            name=item.repo.name,
            description=item.repo.description,
            owner=UserBrief(
                id=item.repo.owner_id,
                display_name=owners.get(item.repo.owner_id),
            ),
            paper_count=item.paper_count,
            star_count=item.star_count,
            is_starred=item.is_starred,
            reason=item.reason,
        )
        for item in window
    ]
    return RelatedRepoPage(items=items, total=len(ranked), page=page, page_size=page_size)


async def count_related_repos(
    db: AsyncSession,
    *,
    session: SearchSession,
    user_id: int,
) -> int:
    ranked = await rank_related_repos(db, session=session, user_id=user_id)
    return len(ranked)
