"""Build ``gold_interaction_signals`` — co-reported drug-pair signals (future-enh #6).

FAERS reports list multiple drugs per case. For each pair of drugs in the domain that
are co-reported often enough, we compute the disproportionality of each adverse event
**among reports containing BOTH drugs**, and compare it to each drug's single-agent ROR.
When the combination's ROR materially exceeds both single-drug RORs, that's a candidate
drug–drug interaction signal (an interaction *reporting* signal — not proof, §16.3).

Efficient in API mode: one co-report count + one ranked-events call per drug pair; event
marginals and single-drug RORs are reused from the main pipeline.
Run: python -m pharmasignal.pipeline.build_interactions
"""
from __future__ import annotations

import os
import time
from itertools import combinations

import pandas as pd

from .. import config
from ..ingestion import openfda
from ..modeling import signal_scores as ss
from ..serving.lakehouse import read_gold, write_gold


def _log(m: str) -> None:
    print(f"[interactions] {m}", flush=True)


def build(*, api_key: str = openfda.DEFAULT_API_KEY, use_cache: bool = True,
          min_co_reports: int = 50, top_events_per_pair: int = 50, min_event_reports: int = 5,
          interaction_multiple: float = 2.0, polite_delay: float | None = None) -> pd.DataFrame:
    if polite_delay is None:
        polite_delay = float(os.getenv("PHARMASIGNAL_POLITE_DELAY", "0.05"))
    faers = config.load_faers_config()
    win = faers["window"]
    window_clause = openfda.date_range_clause(win["start_date"], win["end_date"])
    drugs = config.load_drug_domain()
    all_total = openfda.count(window_clause, api_key=api_key, use_cache=use_cache)

    # Single-drug ROR lookup for the comparison baseline.
    scores = read_gold("signal_scores")
    single_ror = {(r.drug_name_normalized, r.adverse_event): r.ror for r in scores.itertuples()}

    # Event marginals (cached; reused from build_gold's bronze cache where possible).
    event_tot: dict[str, int] = {}

    def event_total(term: str) -> int | None:
        if term not in event_tot:
            try:
                event_tot[term] = openfda.count(
                    openfda.and_query([openfda.event_clause(term), window_clause]),
                    api_key=api_key, use_cache=use_cache)
            except openfda.OpenFDABadQuery:
                event_tot[term] = None
        return event_tot[term]

    def drug_or(d) -> str:
        aliases = sorted({d.canonical_name.upper(), *d.aliases, *(b.upper() for b in d.brands)})
        return " OR ".join(openfda.drug_clause(a) for a in aliases)

    pairs = list(combinations(drugs, 2))
    _log(f"scanning {len(pairs)} drug pairs for co-reported interaction signals")
    rows: list[dict] = []
    for da, db in pairs:
        a_or, b_or = drug_or(da), drug_or(db)
        both = openfda.and_query([a_or, b_or, window_clause])
        n_ab = openfda.count(both, api_key=api_key, use_cache=use_cache)
        time.sleep(polite_delay)
        if n_ab < min_co_reports:
            continue
        events = openfda.count_field(both, openfda.EVENT_FIELD, top_events_per_pair,
                                     api_key=api_key, use_cache=use_cache)
        time.sleep(polite_delay)
        for ev in events:
            term, a_ab = ev["term"], int(ev["count"])
            if a_ab < min_event_reports:
                continue
            et = event_total(term)
            if not et:
                continue
            cont = ss.Contingency.from_totals(a_ab, n_ab, et, all_total)
            disp = ss.disproportionality(cont)
            ror_a = single_ror.get((da.canonical_name, term), float("nan"))
            ror_b = single_ror.get((db.canonical_name, term), float("nan"))
            # Baseline = the stronger KNOWN single-agent ROR. If the event isn't
            # disproportionate for either drug alone we cannot establish that the
            # combination exceeds the singles, so we do NOT flag (avoids inflating
            # artifacts where the single-drug ROR is simply unknown).
            known = [r for r in (ror_a, ror_b) if r == r]
            base = max(known) if known else float("nan")
            ratio = (disp.ror / base) if (base == base and base > 0) else float("nan")
            rows.append({
                "drug_a": da.canonical_name, "drug_b": db.canonical_name, "adverse_event": term,
                "co_reports": n_ab, "pair_event_reports": a_ab,
                "ror_combination": disp.ror, "ror_ci_lower": disp.ror_ci_lower, "ror_ci_upper": disp.ror_ci_upper,
                "prr_combination": disp.prr, "chi_square": disp.chi_square,
                "ror_drug_a": ror_a, "ror_drug_b": ror_b, "single_max_ror": base,
                "comparable": bool(known),
                "interaction_ratio": ratio,
                "interaction_flag": bool(
                    known and a_ab >= min_event_reports and disp.ror_ci_lower > 1.0
                    and ratio == ratio and ratio >= interaction_multiple),
            })
    return pd.DataFrame(rows)


def main() -> None:
    df = build()
    path = write_gold(df, "interaction_signals")
    flagged = int(df["interaction_flag"].sum()) if not df.empty else 0
    print(f"Wrote {len(df)} interaction rows ({flagged} flagged) -> {path}")
    if flagged:
        top = df[df["interaction_flag"]].sort_values("interaction_ratio", ascending=False).head(8)
        print(top[["drug_a", "drug_b", "adverse_event", "pair_event_reports",
                   "ror_combination", "single_max_ror", "interaction_ratio"]].to_string(index=False))


if __name__ == "__main__":
    main()
