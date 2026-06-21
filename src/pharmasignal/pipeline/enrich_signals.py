"""Enrich ``gold_emerging_signals`` with literature + population context (requirements §9.6).

`build_gold` computes the emerging-signals priority from disproportionality (D),
trend anomaly (T), and seriousness (S) only — the literature (L) and population-context
(P) components are placeholders because PubMed/NHANES are produced by later steps.

This mart step closes the loop: it joins the real ``pubmed_support_summary`` and
``nhanes_population_context`` back onto each emerging signal and **recomputes the full
composite priority** with all five transparent components. Run it after build_gold +
nhanes + pubmed:

    python -m pharmasignal.pipeline.enrich_signals
"""
from __future__ import annotations

import pandas as pd

from .. import config
from ..modeling import signal_scores as ss
from ..serving.lakehouse import read_gold, write_gold


def _population_context_score(med: str, drug_class: str | None,
                              med_n: dict[str, int], classes: set[str],
                              small_n: int) -> tuple[float, bool]:
    """Transparent population-context score in [0, 1] (requirements §10).

    Tiered by how directly NHANES supports the drug, always honoring small-sample
    instability:
      1.0  medication-level NHANES use with a reliable unweighted sample
      0.6  medication-level use but small unweighted sample (unstable)
      0.4  only drug-class-level context available
      0.0  no NHANES context
    """
    if med in med_n:
        return (1.0, True) if med_n[med] >= small_n else (0.6, True)
    if drug_class and drug_class in classes:
        return 0.4, True
    return 0.0, False


def enrich() -> pd.DataFrame:
    thresholds = config.load_thresholds()
    weights = config.load_priority_weights()

    emerging = read_gold("emerging_signals").copy()
    scores = read_gold("signal_scores")[
        ["drug_name_normalized", "adverse_event", "bayesian_shrunken_score"]]

    # Literature support (real PubMed). Optional — may be absent if pubmed step skipped.
    try:
        lit = read_gold("pubmed_support_summary")[
            ["drug_name_normalized", "adverse_event",
             "literature_support_count", "literature_support_score"]]
    except FileNotFoundError:
        lit = pd.DataFrame(columns=["drug_name_normalized", "adverse_event",
                                    "literature_support_count", "literature_support_score"])

    # NHANES population context (real, aggregate; never person-linked).
    try:
        nhanes = read_gold("nhanes_population_context")
        med_n = dict(zip(nhanes["medication_name_normalized"],
                         nhanes["unweighted_sample_count"]))
        nhanes_classes = set(nhanes["drug_class"].dropna())
    except FileNotFoundError:
        med_n, nhanes_classes = {}, set()

    # Bring in the shrinkage score for the disproportionality component.
    df = emerging.merge(scores, on=["drug_name_normalized", "adverse_event"], how="left")
    df = df.merge(lit, on=["drug_name_normalized", "adverse_event"], how="left",
                  suffixes=("", "_lit"))

    df["literature_support_count"] = df["literature_support_count_lit"].fillna(
        df.get("literature_support_count", 0)).fillna(0).astype(int)
    df["literature_support_score"] = df["literature_support_score"].fillna(0.0)

    rows = []
    for _, r in df.iterrows():
        d_score = ss.normalize_disproportionality(r.get("bayesian_shrunken_score", 0.0) or 0.0)
        t_score = ss.normalize_trend(r.get("anomaly_score", float("nan")))
        s_score = float(r.get("seriousness_rate", 0.0) or 0.0)
        l_score = float(r.get("literature_support_score", 0.0) or 0.0)
        p_score, p_avail = _population_context_score(
            r["drug_name_normalized"], r.get("drug_class"),
            med_n, nhanes_classes, thresholds.small_n_nhanes_warning)

        priority = ss.priority_score(
            disproportionality_score=d_score,
            trend_anomaly_score=t_score,
            seriousness_score=s_score,
            literature_support_score=l_score,
            population_context_score=p_score,
            weights=weights,
        )
        out = r.to_dict()
        out.update({
            # transparent component breakdown (never hide the composite's inputs)
            "disproportionality_component": round(d_score, 4),
            "trend_component": round(t_score, 4),
            "seriousness_component": round(s_score, 4),
            "literature_component": round(l_score, 4),
            "population_context_component": round(p_score, 4),
            "literature_support_count": int(r["literature_support_count"]),
            "nhanes_context_available": bool(p_avail),
            "priority_score": round(priority, 4),
            "priority_level": ss.priority_level(
                priority, thresholds.high_priority_score, thresholds.moderate_priority_score),
        })
        rows.append(out)

    enriched = pd.DataFrame(rows)
    # drop the helper merge columns
    enriched = enriched.drop(columns=[c for c in enriched.columns if c.endswith("_lit")],
                             errors="ignore")
    return enriched


def main() -> None:
    enriched = enrich()
    path = write_gold(enriched, "emerging_signals")
    moved = int((enriched["priority_level"] == "High").sum())
    print(f"Enriched {len(enriched)} emerging signals -> {path}")
    print(f"  priority levels: {enriched['priority_level'].value_counts().to_dict()}")
    cols = ["drug_name_normalized", "adverse_event", "literature_support_count",
            "nhanes_context_available", "priority_score", "priority_level"]
    print(enriched.sort_values("priority_score", ascending=False)[cols].head(10).to_string(index=False))


if __name__ == "__main__":
    main()
