"""Build ``gold_drug_label_flags`` — labeled-vs-novel status per drug-event pair.

For every drug-event pair in ``gold_signal_scores`` we test whether the event already
appears in the drug's official openFDA labeling. One label is fetched per drug (cached
to bronze); matching is then local, so this is cheap (~10 API calls for the domain).

Output columns:
  drug_name_normalized, adverse_event,
  labeled_event   (bool)  — event appears in a label safety section
  label_section   (str)   — most-severe matching section, else None
  label_found     (bool)  — a label was retrieved for the drug at all
  label_status    (str)   — "labeled" | "novel" | "unknown"
  novel_flag      (bool)  — label_found AND NOT labeled_event (the interesting case)

Run: python -m pharmasignal.pipeline.build_label_flags
"""
from __future__ import annotations

import pandas as pd

from .. import config
from ..ingestion import drug_label, openfda
from ..serving.lakehouse import read_gold, write_gold


def _rxcui_map() -> dict[str, str]:
    """drug_name_normalized -> RxNorm ingredient RxCUI, from the drug_dimension mart.

    After the Option B re-key, ``signal_scores.drug_name_normalized`` IS the ingredient
    name, so we look the RxCUI up by ``analysis_key`` (which equals the ingredient) to
    drive an exact ``openfda.rxcui`` label join. Empty when the mart isn't built yet.
    """
    try:
        dim = read_gold("drug_dimension")
    except FileNotFoundError:
        return {}
    if dim.empty or "ingredient_rxcui" not in dim.columns:
        return {}
    keyed = dim.dropna(subset=["ingredient_rxcui"])
    return dict(zip(keyed["analysis_key"], keyed["ingredient_rxcui"].astype(str)))


def build(*, api_key: str = openfda.DEFAULT_API_KEY, use_cache: bool = True) -> pd.DataFrame:
    scores = read_gold("signal_scores")
    pairs = scores[["drug_name_normalized", "adverse_event"]].drop_duplicates()

    # Curated alias/brand lists still help the few GLP-1 drugs; the whole-database drugs
    # are now keyed on their RxNorm ingredient name (the openFDA generic_name), and we
    # join by RxCUI when the drug_dimension mart provides one.
    aliases = {
        d.canonical_name.upper(): tuple({*d.aliases, *(b.upper() for b in d.brands)})
        for d in config.load_drug_domain()
    }
    rxcui_map = _rxcui_map()

    label_cache: dict[str, dict] = {}

    def label_for(drug: str) -> dict:
        if drug not in label_cache:
            label_cache[drug] = drug_label.fetch_label(
                drug, aliases.get(drug.upper(), ()), api_key=api_key,
                use_cache=use_cache, rxcui=rxcui_map.get(drug))
        return label_cache[drug]

    rows = []
    for _, r in pairs.iterrows():
        drug, event = r["drug_name_normalized"], r["adverse_event"]
        labeled, section, found = drug_label.event_in_label(label_for(drug), event)
        status = "labeled" if labeled else ("novel" if found else "unknown")
        rows.append({
            "drug_name_normalized": drug,
            "adverse_event": event,
            "labeled_event": labeled,
            "label_section": section,
            "label_found": found,
            "label_status": status,
            "novel_flag": bool(found and not labeled),
        })
    return pd.DataFrame(rows)


def apply_to_signal_scores(flags: pd.DataFrame) -> str | None:
    """Fold ``label_status`` / ``novel_flag`` into ``signal_scores`` and rewrite it.

    The whole-database label mart is ~1.5M rows — far too large to ship in the dashboard
    payload. Instead the flag lives as a column on the matrix so the API can push a
    "novel only" filter into the Parquet scan (like ``disproportionality_flag``), and the
    scatter sample carries it for the Overview label breakdown. Idempotent: any prior
    label columns are dropped before the merge.
    """
    scores = read_gold("signal_scores")
    scores = scores.drop(columns=[c for c in ("label_status", "novel_flag", "labeled_event")
                                  if c in scores.columns])
    cols = ["drug_name_normalized", "adverse_event", "labeled_event", "label_status", "novel_flag"]
    merged = scores.merge(flags[cols], on=["drug_name_normalized", "adverse_event"], how="left")
    merged["label_status"] = merged["label_status"].fillna("unknown")
    merged["novel_flag"] = merged["novel_flag"].fillna(False).astype(bool)
    merged["labeled_event"] = merged["labeled_event"].fillna(False).astype(bool)
    path = write_gold(merged, "signal_scores")
    # The stats + scatter marts were built before labels existed; regenerate them so
    # novel_total is precomputed and the scatter sample carries novel_flag.
    from . import scoring
    write_gold(scoring.summary_stats(merged), "signal_scores_stats")
    write_gold(scoring.scatter_sample(merged), "signal_scores_sample")
    return path


def main() -> None:
    df = build()
    path = write_gold(df, "drug_label_flags")
    counts = df["label_status"].value_counts().to_dict()
    print(f"Wrote {len(df)} drug-label flags -> {path}")
    print(f"  status breakdown: {counts}")
    sig_path = apply_to_signal_scores(df)
    print(f"  folded label_status/novel_flag into signal_scores -> {sig_path}")
    # A novel + disproportionate pair is the headline case; show a few.
    scores = read_gold("signal_scores")[
        ["drug_name_normalized", "adverse_event", "ror", "a_drug_event", "disproportionality_flag"]]
    merged = df.merge(scores, on=["drug_name_normalized", "adverse_event"])
    novel = merged[(merged["novel_flag"]) & (merged["disproportionality_flag"])]
    novel = novel.sort_values("ror", ascending=False).head(8)
    if not novel.empty:
        print("\n  Top NOVEL + disproportionate pairs (not found in label):")
        print(novel[["drug_name_normalized", "adverse_event", "a_drug_event", "ror"]].to_string(index=False))


if __name__ == "__main__":
    main()
