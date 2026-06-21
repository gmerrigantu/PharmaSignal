"""NHANES population-context ingestion (requirements §7.3, §10).

Downloads public NHANES XPT files, computes survey-weighted medication-use prevalence
and demographic/clinical profiles by drug / drug class, and writes
``gold_nhanes_population_context``.

Cycle: **2017-2020 pre-pandemic ("P_" files)**. The public 2021-2023 cycle does not
release per-drug prescription names (see config/nhanes_variables.yml), so medication-
level context uses the pre-pandemic cycle, which includes generic drug names and early
GLP-1 use.

DESIGN PRINCIPLE (non-negotiable): NHANES is population CONTEXT ONLY. Participants are
NEVER linked to FAERS reports (ING-NHANES-006). Only aggregate context joins to
drug/class outputs. Every estimate carries the unweighted sample count and a small-n
instability flag (§10.3).

Run: python -m pharmasignal.nhanes.ingest
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

import pandas as pd

from ..config import canonical_to_class, load_nhanes_config, load_thresholds
from ..paths import BRONZE_DIR
from ..serving.lakehouse import write_gold


def _download_xpt(url: str, dest) -> pd.DataFrame:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        import requests

        resp = requests.get(url, timeout=180)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    return pd.read_sas(io.BytesIO(dest.read_bytes()), format="xport")


def weighted_prevalence(base: pd.DataFrame, weight_col: str, mask: pd.Series) -> float:
    """Survey-weighted proportion of the population in ``mask`` (0..1).

    Uses the documented MEC weight (§10.3). Point estimate only; full complex-survey
    variance (strata/PSU CIs) is documented but not computed, to avoid overstating
    precision in the MVP.
    """
    total_w = base[weight_col].sum()
    if total_w <= 0:
        return float("nan")
    return float(base.loc[mask, weight_col].sum() / total_w)


def build_population_context() -> pd.DataFrame:
    cfg = load_nhanes_config()
    thresholds = load_thresholds()
    cycle = cfg["cycle"]
    base_url = cfg["base_url"]
    design = cfg["design"]
    files = cfg["files"]
    med_map = {k.upper(): v for k, v in cfg["medication_name_map"].items()}
    bronze = BRONZE_DIR / "nhanes" / f"cycle={cycle}"

    def load(key: str) -> pd.DataFrame:
        fc = files[key]
        url = f"{base_url}/{fc['filename']}"
        dest = bronze / f"component={fc['component']}" / fc["filename"]
        return _download_xpt(url, dest)

    demo = load("demographics")
    rx = load("prescriptions")
    bmx = load("body_measures")
    ghb = load("glycohemoglobin")
    diq = load("diabetes")

    weight_col = design["mec_weight_var"]
    bmi_col = files["body_measures"]["bmi_var"]
    hba1c_col = files["glycohemoglobin"]["hba1c_var"]
    diq_col = files["diabetes"]["diagnosed_var"]
    drug_col = files["prescriptions"]["drug_name_var"]

    # Normalize NHANES drug strings -> canonical names. Combination products are
    # recorded as "DRUG_A; DRUG_B"; split and map each component.
    rx = rx[["SEQN", drug_col]].copy()
    rx[drug_col] = rx[drug_col].astype(str).str.upper().str.strip()
    rx = rx.assign(component=rx[drug_col].str.split(";")).explode("component")
    rx["component"] = rx["component"].str.strip()
    rx["medication_name_normalized"] = rx["component"].map(med_map)
    rx = rx.dropna(subset=["medication_name_normalized"])
    user_meds = rx[["SEQN", "medication_name_normalized"]].drop_duplicates()

    # Participant-level covariate frame (MEC-weighted population).
    base = demo[["SEQN", weight_col, design["strata_var"], design["psu_var"],
                 design["sex_var"], design["age_var"]]].copy()
    base = base[base[weight_col].notna() & (base[weight_col] > 0)]
    base = base.merge(bmx[["SEQN", bmi_col]], on="SEQN", how="left")
    base = base.merge(ghb[["SEQN", hba1c_col]], on="SEQN", how="left")
    base = base.merge(diq[["SEQN", diq_col]], on="SEQN", how="left")

    class_map = canonical_to_class()
    rows = []
    for med, grp in user_meds.groupby("medication_name_normalized"):
        mask = base["SEQN"].isin(grp["SEQN"])
        users = base[mask]
        n = len(users)
        if n == 0:
            continue
        rows.append({
            "survey_cycle": cycle,
            "drug_class": class_map.get(med),
            "medication_name_normalized": med,
            "weighted_prevalence": weighted_prevalence(base, weight_col, mask),
            "estimated_users": float(users[weight_col].sum()),
            "unweighted_sample_count": int(n),
            "median_age": float(users[design["age_var"]].median()),
            "female_percent": float((users[design["sex_var"]] == 2).mean() * 100),
            "bmi_ge_30_percent": float((users[bmi_col] >= 30).mean() * 100),
            "diabetes_percent": float((users[diq_col] == 1).mean() * 100),
            "hba1c_median": float(users[hba1c_col].median()),
            "weight_variable_used": weight_col,
            "small_n_flag": bool(n < thresholds.small_n_nhanes_warning),
            "very_small_n_flag": bool(n < thresholds.very_small_n_nhanes_warning),
            "ingest_timestamp": datetime.now(timezone.utc),
        })
    return pd.DataFrame(rows).sort_values("weighted_prevalence", ascending=False)


def main() -> None:
    df = build_population_context()
    path = write_gold(df, "nhanes_population_context")
    print(f"Wrote {len(df)} NHANES medication-context rows -> {path}")
    if not df.empty:
        cols = ["medication_name_normalized", "unweighted_sample_count",
                "weighted_prevalence", "diabetes_percent", "bmi_ge_30_percent"]
        print(df[cols].to_string(index=False))


if __name__ == "__main__":
    main()
