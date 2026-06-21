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


def build(*, api_key: str = openfda.DEFAULT_API_KEY, use_cache: bool = True) -> pd.DataFrame:
    scores = read_gold("signal_scores")
    pairs = scores[["drug_name_normalized", "adverse_event"]].drop_duplicates()

    # alias/brand list per canonical drug, for the label search.
    aliases = {
        d.canonical_name: tuple({*d.aliases, *(b.upper() for b in d.brands)})
        for d in config.load_drug_domain()
    }

    label_cache: dict[str, dict] = {}

    def label_for(drug: str) -> dict:
        if drug not in label_cache:
            label_cache[drug] = drug_label.fetch_label(
                drug, aliases.get(drug, ()), api_key=api_key, use_cache=use_cache)
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


def main() -> None:
    df = build()
    path = write_gold(df, "drug_label_flags")
    counts = df["label_status"].value_counts().to_dict()
    print(f"Wrote {len(df)} drug-label flags -> {path}")
    print(f"  status breakdown: {counts}")
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
