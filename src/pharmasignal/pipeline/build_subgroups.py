"""Build ``gold_subgroup_signals`` — ROR/PRR by age band and sex (future-enhancement #7).

Disproportionality is recomputed *within* demographic strata so the dashboard can show
e.g. "this signal is concentrated in women 65+". Strata-level marginals (N, drug total,
event total) are cached so the call budget stays bounded; subgroup signals are computed
only for the strongest base signals.

Reuses the same 2×2 / ROR machinery as the main pipeline — only the population (the
stratum) changes. Run: python -m pharmasignal.pipeline.build_subgroups
"""
from __future__ import annotations

import os
import time

import pandas as pd

from .. import config
from ..ingestion import openfda
from ..modeling import signal_scores as ss
from ..serving.lakehouse import read_gold, write_gold

# Strata: (type, label, clause-builder args). Sex codes 1=male, 2=female; age bands years.
SEX_STRATA = [("sex", "male", openfda.sex_clause(1)), ("sex", "female", openfda.sex_clause(2))]
AGE_STRATA = [
    ("age", "0-17", openfda.age_clause(0, 17)),
    ("age", "18-64", openfda.age_clause(18, 64)),
    ("age", "65+", openfda.age_clause(65, 120)),
]
STRATA = SEX_STRATA + AGE_STRATA


def _log(m: str) -> None:
    print(f"[subgroups] {m}", flush=True)


def build(*, api_key: str = openfda.DEFAULT_API_KEY, use_cache: bool = True,
          top_pairs: int = 30, min_base_reports: int = 100, min_stratum_a: int = 3,
          polite_delay: float | None = None) -> pd.DataFrame:
    if polite_delay is None:
        polite_delay = float(os.getenv("PHARMASIGNAL_POLITE_DELAY", "0.05"))
    faers = config.load_faers_config()
    win = faers["window"]
    window_clause = openfda.date_range_clause(win["start_date"], win["end_date"])
    drugs = {d.canonical_name: d for d in config.load_drug_domain()}

    scores = read_gold("signal_scores")
    base = (scores[scores["disproportionality_flag"] & (scores["a_drug_event"] >= min_base_reports)]
            .sort_values("a_drug_event", ascending=False).head(top_pairs))
    _log(f"computing subgroup ROR for {len(base)} base signals × {len(STRATA)} strata")

    # Per-stratum caches (independent of the pair).
    n_stratum: dict[str, int] = {}
    drug_tot: dict[tuple[str, str], int] = {}
    event_tot: dict[tuple[str, str], int] = {}

    def _drug_or(drug_name: str) -> str:
        d = drugs[drug_name]
        aliases = sorted({d.canonical_name.upper(), *d.aliases, *(b.upper() for b in d.brands)})
        return " OR ".join(openfda.drug_clause(a) for a in aliases)

    rows: list[dict] = []
    for _, sig in base.iterrows():
        drug, event = sig["drug_name_normalized"], sig["adverse_event"]
        drug_or = _drug_or(drug)
        for stype, label, sclause in STRATA:
            try:
                if label not in n_stratum:
                    n_stratum[label] = openfda.count(
                        openfda.and_query([window_clause, sclause]), api_key=api_key, use_cache=use_cache)
                if (drug, label) not in drug_tot:
                    drug_tot[(drug, label)] = openfda.count(
                        openfda.and_query([drug_or, window_clause, sclause]), api_key=api_key, use_cache=use_cache)
                if (event, label) not in event_tot:
                    event_tot[(event, label)] = openfda.count(
                        openfda.and_query([openfda.event_clause(event), window_clause, sclause]),
                        api_key=api_key, use_cache=use_cache)
                a = openfda.count(
                    openfda.and_query([drug_or, openfda.event_clause(event), window_clause, sclause]),
                    api_key=api_key, use_cache=use_cache)
            except openfda.OpenFDABadQuery:
                continue
            time.sleep(polite_delay)
            if a < min_stratum_a:
                continue
            cont = ss.Contingency.from_totals(a, drug_tot[(drug, label)], event_tot[(event, label)], n_stratum[label])
            disp = ss.disproportionality(cont)
            rows.append({
                "drug_name_normalized": drug, "drug_class": sig["drug_class"], "adverse_event": event,
                "subgroup_type": stype, "subgroup": label,
                "stratum_reports": a, "stratum_population": n_stratum[label],
                "ror": disp.ror, "ror_ci_lower": disp.ror_ci_lower, "ror_ci_upper": disp.ror_ci_upper,
                "prr": disp.prr, "chi_square": disp.chi_square,
                "overall_ror": sig["ror"],
            })
    return pd.DataFrame(rows)


def main() -> None:
    df = build()
    path = write_gold(df, "subgroup_signals")
    print(f"Wrote {len(df)} subgroup rows -> {path}")
    if not df.empty:
        # Show where a subgroup ROR most exceeds the overall ROR (concentration).
        df = df.assign(ratio=df["ror"] / df["overall_ror"].clip(lower=0.01))
        top = df.sort_values("ratio", ascending=False).head(8)
        print(top[["drug_name_normalized", "adverse_event", "subgroup_type", "subgroup",
                   "stratum_reports", "ror", "overall_ror"]].to_string(index=False))


if __name__ == "__main__":
    main()
