"""Build ``gold_pubmed_evidence`` for the top-priority signals (requirements §11).

Reads the current gold_signal_scores, takes the highest-priority drug-event pairs,
retrieves + scores PubMed articles, and writes per-article evidence rows plus a
literature-support summary that feeds back into the composite priority score.

Run: python -m pharmasignal.pubmed.build_evidence [--top N]
"""
from __future__ import annotations

import argparse

import pandas as pd

from ..config import load_drug_domain
from ..serving.lakehouse import read_gold, write_gold
from . import eutils, relevance


def _synonyms_for(drug: str) -> list[str]:
    for d in load_drug_domain():
        if d.canonical_name == drug:
            return [drug, *d.brands]
    return [drug]


def _candidate_pairs(top_n: int, *, min_reports: int = 25) -> list[tuple[str, str]]:
    """Drug-event pairs to retrieve literature for, as an ordered, de-duplicated union of:

      1. **Genuine disproportionality signals** — flagged pairs from ``signal_scores``
         that are well-reported (>= ``min_reports``), ranked by report count. These are
         the real, documentable associations (e.g. semaglutide->nausea); the emerging
         mart's priority ranking alone misses them because it is dominated by
         mass-reporting artifacts that have no literature.
      2. **Trend-emerging signals** — top pairs from ``emerging_signals`` by composite
         priority, so newly accelerating signals are covered too.
    """
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _extend(df: pd.DataFrame) -> None:
        for _, r in df.iterrows():
            key = (r["drug_name_normalized"], r["adverse_event"])
            if key not in seen:
                seen.add(key)
                pairs.append(key)

    try:
        ss = read_gold("signal_scores")
        real = ss[(ss["disproportionality_flag"]) & (ss["a_drug_event"] >= min_reports)]
        _extend(real.sort_values("a_drug_event", ascending=False).head(top_n))
    except FileNotFoundError:
        pass
    try:
        em = read_gold("emerging_signals").sort_values("priority_score", ascending=False)
        _extend(em.head(top_n))
    except FileNotFoundError:
        if not pairs:  # neither mart present — last-resort ROR ranking
            ss = read_gold("signal_scores").sort_values("ror", ascending=False)
            _extend(ss.head(top_n))
    return pairs


def build(top_n: int = 25, *, use_cache: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    evidence_rows: list[dict] = []
    summary_rows: list[dict] = []
    for drug, event in _candidate_pairs(top_n):
        articles = eutils.search(drug, event, _synonyms_for(drug), use_cache=use_cache)
        scored = [relevance.score_article(a, drug, event) for a in articles]
        for s in scored:
            evidence_rows.append(
                {
                    "drug_name_normalized": drug,
                    "adverse_event": event,
                    "pmid": s.article.pmid,
                    "title": s.article.title,
                    "journal": s.article.journal,
                    "publication_year": s.article.publication_year,
                    "relevance_score": s.relevance_score,
                    "evidence_snippet": s.evidence_snippet,
                    "mentions_drug": s.mentions_drug,
                    "mentions_event": s.mentions_event,
                    "adverse_context": s.adverse_context,
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{s.article.pmid}/",
                }
            )
        lit_score = relevance.literature_support_score(scored)
        summary_rows.append(
            {
                "drug_name_normalized": drug,
                "adverse_event": event,
                "literature_support_count": len(scored),
                "literature_support_score": lit_score,
                "support_level": relevance.support_level(lit_score),
            }
        )

    evidence_df = pd.DataFrame(evidence_rows)
    summary_df = pd.DataFrame(summary_rows)
    write_gold(evidence_df, "pubmed_evidence")
    write_gold(summary_df, "pubmed_support_summary")
    return evidence_df, summary_df


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=25,
                        help="pairs to take from EACH source (flagged signals + emerging)")
    args = parser.parse_args()
    ev, summ = build(args.top)
    print(f"Wrote {len(ev)} evidence rows for {len(summ)} signals.")


if __name__ == "__main__":
    main()
