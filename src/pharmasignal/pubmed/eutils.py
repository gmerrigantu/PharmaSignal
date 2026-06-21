"""PubMed evidence retrieval via NCBI E-utilities (requirements §7.4, §11).

MVP: on-demand keyword search per drug-event pair with title/abstract relevance
scoring. Results are cached by query hash to avoid repeated API calls (ING-PUBMED-003).

Evidence labels describe *retrieval support*, NOT clinical evidence strength.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from xml.etree import ElementTree as ET

import requests

from ..config import load_pubmed_config
from ..ingestion.drug_label import americanize  # British->American spelling normalizer
from ..serving import storage

# Words too generic to constrain an event phrase. Dropping them lets word-order variants
# match (MedDRA "OPTIC ISCHAEMIC NEUROPATHY" vs literature "ischemic optic neuropathy")
# without over-broadening to, e.g., every paper containing "disorder".
_EVENT_STOPWORDS = {"the", "and", "with", "for", "of", "in", "nos",
                    "syndrome", "disorder", "disease", "condition"}


@dataclass(frozen=True)
class Article:
    pmid: str
    title: str
    abstract: str
    journal: str
    publication_year: int | None
    mesh_terms: tuple[str, ...]


def _cache_uri(query: str) -> str:
    """Bronze cache location (local path or s3://) for a PubMed query, via the storage
    backend — so an s3:// data root is handled correctly (not wrapped in a local Path)."""
    day = date.today().isoformat()
    h = hashlib.sha256(query.encode()).hexdigest()[:16]
    return storage.bronze_uri("pubmed", f"date={day}", f"query_{h}.json")


def _common_params() -> dict:
    cfg = load_pubmed_config()["eutils"]
    params = {"tool": cfg.get("tool_name", "pharmasignal"), "db": "pubmed"}
    email = os.getenv(cfg.get("email_env", "NCBI_EMAIL"), "")
    if email:
        params["email"] = email
    key = os.getenv("NCBI_API_KEY", "")
    if key:
        params["api_key"] = key
    return params


def _eutils_get(url: str, params: dict, timeout: int, delay: float, retries: int = 5):
    """GET an E-utilities endpoint with throttling + exponential backoff on 429/5xx."""
    last: Exception | None = None
    for attempt in range(retries):
        time.sleep(delay)  # pre-request throttle to stay under NCBI's rate limit
        resp = requests.get(url, params=params, timeout=timeout)
        if resp.status_code == 429 or resp.status_code >= 500:
            wait = 2.0 ** attempt  # 1, 2, 4, 8, 16s
            time.sleep(wait)
            last = requests.HTTPError(f"{resp.status_code} from NCBI; retried")
            continue
        resp.raise_for_status()
        return resp
    raise last or requests.HTTPError("NCBI request failed")


def _event_clause(event: str) -> str:
    """A tolerant Title/Abstract clause for a MedDRA event term.

    MedDRA preferred terms are exact British-spelled phrases that rarely appear verbatim
    in the literature, so an exact ``"phrase"[Title/Abstract]`` match misses most real
    papers (e.g. only 1 hit for the semaglutide/NAION signal). We match the exact phrase
    OR all of its significant words ANDed (order-independent), across both the original
    and Americanized spelling (ISCHAEMIC->ISCHEMIC, OEDEMA->EDEMA). Still fully
    deterministic and explainable — no semantic expansion.
    """
    parts: list[str] = []
    seen: set[str] = set()

    def _add(clause: str) -> None:
        if clause not in seen:
            seen.add(clause)
            parts.append(clause)

    variants: list[str] = []
    for v in (event, americanize(event.lower())):
        v = v.strip()
        if v and v not in variants:
            variants.append(v)
    for v in variants:
        _add(f'"{v}"[Title/Abstract]')
        words = [w for w in v.split() if len(w) > 3 and w.lower() not in _EVENT_STOPWORDS]
        if len(words) > 1:
            _add("(" + " AND ".join(f"{w}[Title/Abstract]" for w in words) + ")")
    return "(" + " OR ".join(parts) + ")"


def build_query(drug: str, event: str, synonyms: list[str] | None = None) -> str:
    """``(drug OR brand synonyms) AND <tolerant event clause>``, all in Title/Abstract."""
    syn = synonyms or [drug]
    drug_clause = " OR ".join(f'"{s}"[Title/Abstract]' for s in syn) \
        or f'"{drug}"[Title/Abstract]'
    return f"({drug_clause}) AND {_event_clause(event)}"


def search(drug: str, event: str, synonyms: list[str] | None = None, *, use_cache: bool = True) -> list[Article]:
    """ING-PUBMED-001/002: esearch -> efetch, returning parsed article metadata."""
    cfg = load_pubmed_config()["eutils"]
    query = build_query(drug, event, synonyms)
    cache_uri = _cache_uri(query)
    if use_cache and storage.exists(cache_uri):
        payload = storage.read_json(cache_uri)
        return [Article(**{**a, "mesh_terms": tuple(a["mesh_terms"])}) for a in payload["articles"]]

    base = cfg["base_url"]
    retmax = cfg.get("max_articles_per_pair", 25)
    timeout = cfg.get("retrieval_timeout_seconds", 30)
    common = _common_params()
    # NCBI allows 3 req/s without a key, 10 with one. Throttle accordingly.
    delay = 0.12 if "api_key" in common else 0.4

    # 1) esearch for PMIDs
    es = _eutils_get(f"{base}/esearch.fcgi",
                     {**common, "term": query, "retmax": retmax, "retmode": "json"},
                     timeout, delay)
    pmids = es.json().get("esearchresult", {}).get("idlist", [])
    articles: list[Article] = []
    if pmids:
        ef = _eutils_get(f"{base}/efetch.fcgi",
                         {**common, "id": ",".join(pmids), "retmode": "xml"},
                         timeout, delay)
        articles = _parse_efetch(ef.text)

    storage.write_json(
        {
            "_query": query,
            "_retrieval_date": datetime.now(timezone.utc).isoformat(),
            "articles": [asdict(a) for a in articles],
        },
        cache_uri,
    )
    return articles


def _parse_efetch(xml_text: str) -> list[Article]:
    root = ET.fromstring(xml_text)
    out: list[Article] = []
    for art in root.findall(".//PubmedArticle"):
        pmid = art.findtext(".//PMID") or ""
        title = "".join(art.find(".//ArticleTitle").itertext()) if art.find(".//ArticleTitle") is not None else ""
        abstract = " ".join(
            "".join(node.itertext()) for node in art.findall(".//Abstract/AbstractText")
        )
        journal = art.findtext(".//Journal/Title") or ""
        year_text = art.findtext(".//JournalIssue/PubDate/Year") or art.findtext(".//PubDate/Year")
        year = int(year_text) if year_text and year_text.isdigit() else None
        mesh = tuple(m.text for m in art.findall(".//MeshHeading/DescriptorName") if m.text)
        out.append(Article(pmid=pmid, title=title, abstract=abstract, journal=journal,
                           publication_year=year, mesh_terms=mesh))
    return out
