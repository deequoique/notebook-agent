"""Tenant-scoped read-only knowledge services used by Agent tools."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import math

from sqlalchemy import and_, false, or_, select
from sqlalchemy.orm import Session

from app.agent.limits import SEARCH_RESULT_LIMIT
from app.agent.types import Citation
from app.browser_capture import timestamp_url
from app.channels.types import TenantContext
from app.diagnostics import RequestDiagnostics
from app.ingest.embed import EmbeddingError, EmbeddingProvider
from app.models import ContentItem, Segment
from app.retrieval.search import Hit, bm25_search, vector_search


SEARCH_CANDIDATE_POOL_LIMIT = 50
MAX_SOURCE_ITEMS = 5


class KnowledgeNotFound(LookupError):
    """Raised uniformly for missing and cross-tenant resources."""


class EmbeddingUnavailable(RuntimeError):
    """Query embedding is missing or could not be safely produced."""


class RetrievalUnavailable(RuntimeError):
    """Database retrieval did not finish and must not be presented as no evidence."""


@dataclass(frozen=True)
class ItemDetails:
    item_id: int
    title: str
    author: str | None
    description: str | None
    url: str
    platform: str
    duration_sec: int | None


def _title(item: ContentItem) -> str:
    return item.title or item.platform_id


def _url(item: ContentItem, segment: Segment | None = None) -> str:
    if (
        item.platform in {"youtube", "ntu_kaltura"}
        and segment is not None
        and segment.start_sec is not None
    ):
        return timestamp_url(item.platform, item.url, float(segment.start_sec))
    if segment is not None and segment.anchor:
        return f"{item.url}#{segment.anchor}"
    return item.url


def _citation(item: ContentItem, segment: Segment, *, score: float | None = None) -> Citation:
    citation = Citation(
        item_id=item.id,
        segment_id=segment.id,
        title=_title(item),
        excerpt=segment.text,
        url=_url(item, segment),
        start_sec=float(segment.start_sec) if segment.start_sec is not None else None,
    )
    citation._retrieval_score = score
    return citation


def _diversify_hits(hits: Iterable[Hit], *, limit: int) -> list[Hit]:
    """Keep ranked segment evidence while reserving representation per video.

    Retrieval backends are allowed to return many strong adjacent transcript
    chunks from one video.  The Agent needs candidate *videos* before it can
    choose a Top-5 answer, so the public window first reserves one strongest
    segment from each of at most five item groups and only then fills spare
    positions with further segments from those selected groups.
    """

    best_by_segment: dict[int, Hit] = {}
    for hit in hits:
        current = best_by_segment.get(hit.segment_id)
        if current is None or hit.score > current.score:
            best_by_segment[hit.segment_id] = hit

    ranked = sorted(best_by_segment.values(), key=lambda value: value.score, reverse=True)
    by_item: dict[int, list[Hit]] = {}
    for hit in ranked:
        by_item.setdefault(hit.item_id, []).append(hit)

    selected_item_ids = list(by_item)[:MAX_SOURCE_ITEMS]
    representatives = [by_item[item_id][0] for item_id in selected_item_ids]
    selected_segments = {hit.segment_id for hit in representatives}
    remaining = [
        hit
        for hit in ranked
        if hit.item_id in selected_item_ids and hit.segment_id not in selected_segments
    ]
    return (representatives + remaining)[:limit]


class KnowledgeServices:
    """Read-only services whose tenant is fixed at construction time."""

    def __init__(
        self,
        tenant: TenantContext,
        session_factory: Callable[[], Session],
        *,
        embedder: EmbeddingProvider | None = None,
        max_results: int = SEARCH_RESULT_LIMIT,
        reference_scope: Iterable[tuple[str, str]] | None = None,
    ) -> None:
        self._tenant = tenant
        self._session_factory = session_factory
        self._embedder = embedder
        self._max_results = max(1, min(max_results, SEARCH_RESULT_LIMIT))
        self._diagnostics: RequestDiagnostics | None = None
        # ``None`` is the only unrestricted sentinel.  An explicitly supplied
        # scope that contains no valid references remains an empty, fail-closed
        # scope instead of silently widening to every tenant item.
        self._reference_scope: tuple[tuple[str, str], ...] | None = None
        self.set_reference_scope(reference_scope)

    def set_diagnostics(self, diagnostics: RequestDiagnostics) -> None:
        """Attach the request-scoped, redacted diagnostic sink."""

        self._diagnostics = diagnostics

    def set_reference_scope(
        self, references: Iterable[tuple[str, str]] | None
    ) -> None:
        """Attach the exact platform/reference set from the current message."""

        if references is None:
            self._reference_scope = None
            return
        values: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for value in references:
            try:
                platform, platform_id = value
            except (TypeError, ValueError):
                continue
            key = (str(platform), str(platform_id))
            if not key[0] or not key[1] or key in seen:
                continue
            seen.add(key)
            values.append(key)
        self._reference_scope = tuple(values)

    def _reference_predicates(self) -> list:
        if self._reference_scope is None:
            return []
        if not self._reference_scope:
            return [false()]
        return [
            or_(
                *(
                    and_(
                        ContentItem.platform == platform,
                        ContentItem.platform_id == platform_id,
                    )
                    for platform, platform_id in self._reference_scope
                )
            )
        ]

    def _search_scope_kwargs(self) -> dict[str, object]:
        """Translate the normalized scope into backend search predicates."""

        if self._reference_scope is None:
            return {}
        if not self._reference_scope:
            return {"platform": "__no_matching_platform__", "platform_ids": ()}
        platforms = {platform for platform, _ in self._reference_scope}
        # YouTube is currently the only supported submission platform.  Keep
        # mixed-platform future inputs fail-closed instead of broadening to
        # every tenant video.
        if len(platforms) != 1:
            return {"platform": "__no_matching_platform__", "platform_ids": ()}
        platform = next(iter(platforms))
        return {
            "platform": platform,
            "platform_ids": tuple(
                platform_id
                for current_platform, platform_id in self._reference_scope
                if current_platform == platform
            ),
        }

    def search_segments(
        self,
        query: str,
        *,
        limit: int = SEARCH_RESULT_LIMIT,
        item_id: int | None = None,
    ) -> list[Citation]:
        """Hybrid lexical/vector search, merged without exposing tenant arguments."""

        query = query.strip()
        if not query:
            return []
        if item_id is not None:
            try:
                item_id = int(item_id)
            except (TypeError, ValueError):
                return []
            if item_id <= 0:
                return []
        limit = max(1, min(limit, self._max_results))
        candidate_limit = min(
            SEARCH_CANDIDATE_POOL_LIMIT,
            max(20, limit * 5),
        )
        vector = self._embed_query(query)
        self._event("retrieval_started")
        try:
            with self._session_factory() as db:
                if item_id is None:
                    hits = bm25_search(
                        db,
                        query,
                        user_id=self._tenant.app_user_id,
                        k=candidate_limit,
                        **self._search_scope_kwargs(),
                    )
                    hits.extend(
                        vector_search(
                            db,
                            vector,
                            user_id=self._tenant.app_user_id,
                            k=candidate_limit,
                            **self._search_scope_kwargs(),
                        )
                    )
                else:
                    scope_kwargs = self._search_scope_kwargs()
                    scope_kwargs["item_id"] = item_id
                    hits = bm25_search(
                        db,
                        query,
                        user_id=self._tenant.app_user_id,
                        k=candidate_limit,
                        **scope_kwargs,
                    )
                    hits.extend(
                        vector_search(
                            db,
                            vector,
                            user_id=self._tenant.app_user_id,
                            k=candidate_limit,
                            **scope_kwargs,
                        )
                    )

                selected_hits = _diversify_hits(hits, limit=limit)
                segment_ids = [hit.segment_id for hit in selected_hits]
                if not segment_ids:
                    self._event("retrieval_completed", error_code="no_evidence")
                    return []
                rows = db.execute(
                    select(Segment, ContentItem)
                    .join(ContentItem, Segment.item_id == ContentItem.id)
                    .where(
                        ContentItem.user_id == self._tenant.app_user_id,
                        ContentItem.deleted_at.is_(None),
                        ContentItem.archived_at.is_(None),
                        ContentItem.state == "ready",
                        Segment.id.in_(segment_ids),
                        *([Segment.item_id == item_id] if item_id is not None else []),
                        *self._reference_predicates(),
                    )
                ).all()
                by_id = {
                    segment.id: _citation(
                        item,
                        segment,
                        score=max(
                            (
                                hit.score
                                for hit in hits
                                if hit.segment_id == segment.id
                            ),
                            default=None,
                        ),
                    )
                    for segment, item in rows
                }
                citations = [by_id[value] for value in segment_ids if value in by_id]
                self._event("retrieval_completed")
                return citations
        except Exception as exc:
            self._event(
                "retrieval_failed", error_code="retrieval_unavailable", exception=exc
            )
            raise RetrievalUnavailable("retrieval unavailable") from exc

    def _embed_query(self, query: str) -> list[float]:
        self._event("embedding_started")
        try:
            if self._embedder is None:
                raise EmbeddingUnavailable("embedding provider is not configured")
            vectors = self._embedder.embed([query])
            if len(vectors) != 1:
                raise ValueError("query embedding count mismatch")
            vector = vectors[0]
            if len(vector) != self._embedder.dimensions:
                raise ValueError("query embedding dimension mismatch")
            if any(not math.isfinite(value) for value in vector):
                raise ValueError("query embedding contains invalid values")
            self._event("embedding_completed")
            return vector
        except Exception as exc:
            self._event(
                "embedding_failed", error_code="embedding_unavailable", exception=exc
            )
            if isinstance(exc, EmbeddingUnavailable):
                raise
            raise EmbeddingUnavailable("query embedding unavailable") from exc

    def _event(
        self,
        stage: str,
        *,
        error_code: str | None = None,
        exception: BaseException | None = None,
    ) -> None:
        if self._diagnostics is not None:
            self._diagnostics.event(
                stage, error_code=error_code, exception=exception
            )

    def get_neighbors(self, segment_id: int, *, radius: int = 1) -> list[Citation]:
        """Return adjacent segments only when the anchor belongs to this tenant."""

        radius = max(1, min(radius, 3))
        with self._session_factory() as db:
            anchor = db.execute(
                select(Segment, ContentItem)
                .join(ContentItem, Segment.item_id == ContentItem.id)
                .where(
                    Segment.id == segment_id,
                    ContentItem.user_id == self._tenant.app_user_id,
                    ContentItem.deleted_at.is_(None),
                    ContentItem.archived_at.is_(None),
                    ContentItem.state == "ready",
                    *self._reference_predicates(),
                )
            ).one_or_none()
            if anchor is None:
                raise KnowledgeNotFound("segment not found")
            segment, item = anchor
            neighbors = db.scalars(
                select(Segment)
                .join(ContentItem, Segment.item_id == ContentItem.id)
                .where(
                    Segment.item_id == item.id,
                    ContentItem.user_id == self._tenant.app_user_id,
                    ContentItem.deleted_at.is_(None),
                    ContentItem.archived_at.is_(None),
                    Segment.seq.between(segment.seq - radius, segment.seq + radius),
                    ContentItem.state == "ready",
                    *self._reference_predicates(),
                )
                .order_by(Segment.seq)
            ).all()
            return [_citation(item, value) for value in neighbors]

    def get_item(self, item_id: int) -> ItemDetails:
        """Return item metadata if and only if it belongs to the fixed tenant."""

        with self._session_factory() as db:
            item = db.scalar(
                select(ContentItem).where(
                    ContentItem.id == item_id,
                    ContentItem.user_id == self._tenant.app_user_id,
                    ContentItem.deleted_at.is_(None),
                    ContentItem.archived_at.is_(None),
                    *self._reference_predicates(),
                )
            )
            if (
                self._reference_scope is not None
                and item is not None
                and item.state != "ready"
            ):
                item = None
            if item is None:
                raise KnowledgeNotFound("item not found")
            return ItemDetails(
                item_id=item.id,
                title=_title(item),
                author=item.author,
                description=item.description,
                url=item.url,
                platform=item.platform,
                duration_sec=item.duration_sec,
            )

    def open_at(self, segment_id: int) -> Citation:
        """Resolve a tenant-owned segment to its timestamp or article anchor."""

        with self._session_factory() as db:
            row = db.execute(
                select(Segment, ContentItem)
                .join(ContentItem, Segment.item_id == ContentItem.id)
                .where(
                    Segment.id == segment_id,
                    ContentItem.user_id == self._tenant.app_user_id,
                    ContentItem.deleted_at.is_(None),
                    ContentItem.archived_at.is_(None),
                    ContentItem.state == "ready",
                    *self._reference_predicates(),
                )
            ).one_or_none()
            if row is None:
                raise KnowledgeNotFound("segment not found")
            return _citation(row[1], row[0])
