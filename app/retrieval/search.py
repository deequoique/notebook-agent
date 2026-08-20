"""Independent vector and lexical retrieval paths for P0 comparison."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import desc, false, func, or_, select

from app.browser_capture import timestamp_url
from app.models import ContentItem, Segment


@dataclass(frozen=True)
class Hit:
    item_id: int
    title: str | None
    platform_id: str
    segment_id: int
    text: str
    start_sec: float
    score: float
    platform: str = "youtube"
    source_url: str | None = None

    @property
    def url(self) -> str:
        source_url = self.source_url or f"https://youtu.be/{self.platform_id}"
        return timestamp_url(self.platform, source_url, self.start_sec)


def _hits(rows) -> list[Hit]:
    return [
        Hit(
            item.id,
            item.title,
            item.platform_id,
            segment.id,
            segment.text,
            float(segment.start_sec),
            float(score),
            platform=item.platform,
            source_url=item.url,
        )
        for segment, item, score in rows
    ]


def vector_search(
    db,
    query_vector: list[float],
    *,
    user_id: int,
    k: int = 20,
    platform: str | None = None,
    platform_ids: Iterable[str] | None = None,
    platform_id: str | None = None,
    item_id: int | None = None,
) -> list[Hit]:
    distance = Segment.embedding.cosine_distance(query_vector)
    predicates = [
        ContentItem.user_id == user_id,
        ContentItem.deleted_at.is_(None),
        ContentItem.archived_at.is_(None),
        ContentItem.state == "ready",
        Segment.embedding.isnot(None),
    ]
    if platform is not None:
        predicates.append(ContentItem.platform == platform)
    if platform_ids is not None or platform_id is not None:
        values = tuple(platform_ids) if platform_ids is not None else (platform_id,)
        predicates.append(ContentItem.platform_id.in_(values) if values else false())
    if item_id is not None:
        predicates.append(Segment.item_id == item_id)
    stmt = (
        select(Segment, ContentItem, (1 - distance).label("score"))
        .join(ContentItem)
        .where(*predicates)
        .order_by(distance)
        .limit(k)
    )
    return _hits(db.execute(stmt).all())


def bm25_search(
    db,
    query: str,
    *,
    user_id: int,
    k: int = 20,
    platform: str | None = None,
    platform_ids: Iterable[str] | None = None,
    platform_id: str | None = None,
    item_id: int | None = None,
) -> list[Hit]:
    is_zh = bool(re.search(r"[\u3400-\u9fff]", query))
    predicates = [
        ContentItem.user_id == user_id,
        ContentItem.deleted_at.is_(None),
        ContentItem.archived_at.is_(None),
        ContentItem.state == "ready",
    ]
    if platform is not None:
        predicates.append(ContentItem.platform == platform)
    if platform_ids is not None or platform_id is not None:
        values = tuple(platform_ids) if platform_ids is not None else (platform_id,)
        predicates.append(ContentItem.platform_id.in_(values) if values else false())
    if item_id is not None:
        predicates.append(Segment.item_id == item_id)
    base = select(Segment, ContentItem).join(ContentItem).where(*predicates)
    if is_zh:
        score = func.similarity(Segment.text, query)
        stmt = base.add_columns(score.label("score")).where(ContentItem.lang.like("zh%"), or_(Segment.text.op("%")(query), Segment.text.ilike(f"%{query}%"))).order_by(desc(score)).limit(k)
    else:
        tsquery = func.websearch_to_tsquery("english", query)
        score = func.ts_rank_cd(Segment.fts, tsquery)
        stmt = base.add_columns(score.label("score")).where(Segment.fts.op("@@")(tsquery)).order_by(desc(score)).limit(k)
    return _hits(db.execute(stmt).all())
