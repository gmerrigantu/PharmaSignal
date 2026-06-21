"""Transparent, non-semantic relevance + literature-support scoring (requirements §11.2).

These scores describe *retrieval support*, not clinical evidence strength. The MVP
uses keyword overlap + title weighting + adverse-context detection so the score is
fully explainable and easy to validate. The full version can swap in embeddings.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import load_pubmed_config
from .eutils import Article


@dataclass(frozen=True)
class ScoredArticle:
    article: Article
    relevance_score: float       # 0..1
    mentions_drug: bool
    mentions_event: bool
    adverse_context: bool
    evidence_snippet: str


def _contains(text: str, term: str) -> bool:
    return term.lower() in (text or "").lower()


def score_article(article: Article, drug: str, event: str) -> ScoredArticle:
    cfg = load_pubmed_config()
    adverse_terms = cfg.get("adverse_context_terms", [])
    title, abstract = article.title or "", article.abstract or ""
    blob = f"{title} {abstract}"

    mentions_drug = _contains(blob, drug)
    mentions_event = _contains(blob, event)
    in_title = _contains(title, drug) and _contains(title, event)
    adverse_context = any(_contains(blob, t) for t in adverse_terms)

    # Transparent weighted sum, clipped to [0, 1].
    score = 0.0
    score += 0.35 if mentions_drug else 0.0
    score += 0.35 if mentions_event else 0.0
    score += 0.20 if in_title else 0.0
    score += 0.10 if adverse_context else 0.0

    snippet = (abstract[:280] + "…") if len(abstract) > 280 else abstract
    if not snippet:
        snippet = title

    return ScoredArticle(
        article=article,
        relevance_score=round(min(1.0, score), 3),
        mentions_drug=mentions_drug,
        mentions_event=mentions_event,
        adverse_context=adverse_context,
        evidence_snippet=snippet,
    )


def literature_support_score(scored: list[ScoredArticle], *, recent_year_cutoff: int = 2022) -> float:
    """Composite literature-support score (requirements §11.2), in [0, 1]."""
    if not scored:
        return 0.0
    total = len(scored)
    recent = sum(1 for s in scored if (s.article.publication_year or 0) >= recent_year_cutoff)
    max_rel = max(s.relevance_score for s in scored)
    adverse = sum(1 for s in scored if s.adverse_context)

    norm_total = min(1.0, total / 20)
    norm_recent = min(1.0, recent / 10)
    adverse_ctx = min(1.0, adverse / max(1, total))

    return round(
        0.35 * norm_total + 0.25 * norm_recent + 0.25 * max_rel + 0.15 * adverse_ctx, 3
    )


def support_level(score: float) -> str:
    """Retrieval-support label — NOT clinical evidence strength."""
    if score <= 0.0:
        return "None"
    if score < 0.25:
        return "Weak"
    if score < 0.55:
        return "Moderate"
    return "Strong"
