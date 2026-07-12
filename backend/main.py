import asyncio
import difflib
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, TypedDict

import db
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langfuse import get_client, observe
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel


def chunk_list(items, chunk_size=25):
    for index in range(0, len(items), chunk_size):
        yield items[index : index + chunk_size]


def reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    """Convert OpenAlex abstract_inverted_index to plain text."""
    if not inverted_index:
        return ""
    max_pos = max(pos for positions in inverted_index.values() for pos in positions)
    words: list[str] = [""] * (max_pos + 1)
    for word, positions in inverted_index.items():
        for pos in positions:
            words[pos] = word
    return " ".join(words)


load_dotenv()
langfuse = get_client()

app = FastAPI(
    title="AIRA Scholar-KG Backend",
    description="Backend API for academic Knowledge Graph data.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://86.50.20.161",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize the local SQLite cache (creates backend/data/aira_scholar.db if missing).
# Runs once when uvicorn imports this module.  A failure leaves the app in live-only mode.
db.init_db()


@app.get("/")
def health_check():
    return {
        "status": "ok",
        "message": "AIRA Scholar-KG backend is running",
    }


@app.get("/graph/sample")
def get_sample_graph():
    return {
        "nodes": [
            {
                "id": "p1",
                "label": "Agentic AI for Research Discovery",
                "type": "Paper",
                "year": 2025,
                "citations": 42,
                "details": "Sample paper node for the KG prototype.",
            },
            {
                "id": "a1",
                "label": "University of Oulu Researcher",
                "type": "Author",
                "details": "Sample author node from University of Oulu.",
            },
            {
                "id": "t1",
                "label": "Knowledge Graphs",
                "type": "Topic",
                "details": "Sample topic node for academic KG exploration.",
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "a1",
                "target": "p1",
                "label": "AUTHOR_OF",
            },
            {
                "id": "e2",
                "source": "p1",
                "target": "t1",
                "label": "HAS_TOPIC",
            },
        ],
    }


@app.get("/graph/openalex/oulu")
async def get_oulu_openalex_graph(
    limit: int = 10,
    search: str | None = None,
    from_year: int | None = None,
    to_year: int | None = None,
):
    api_key = os.getenv("OPENALEX_API_KEY")
    requested_limit = max(1, limit)
    _MAX_PAGES = 20   # safety cap: never more than 20 × 100 = 2000 results per call
    _PAGE_SIZE = 100  # OpenAlex per-page maximum

    # Graph search result cache — 1-hour TTL (search results are time-sensitive)
    _cache_key = (
        f"limit={requested_limit}|search={search or ''}|"
        f"from={from_year or ''}|to={to_year or ''}"
    )
    _cached = db.get_cached_raw("graph_search", _cache_key, ttl_hours=1)
    if _cached is not None:
        print(f"[graph] cache hit ({_cache_key[:70]})")
        return _cached

    filters = ["authorships.institutions.ror:https://ror.org/03yj89h83"]
    if from_year:
        filters.append(f"from_publication_date:{from_year}-01-01")
    if to_year:
        filters.append(f"to_publication_date:{to_year}-12-31")

    base_params: dict[str, Any] = {
        "filter": ",".join(filters),
        "per-page": _PAGE_SIZE,
    }
    if search:
        base_params["search"] = search
    else:
        base_params["sort"] = "cited_by_count:desc"
    if api_key:
        base_params["api_key"] = api_key

    # Cursor-based pagination — accumulate until limit, exhausted, or safety cap
    all_works: list[dict[str, Any]] = []
    _pages_fetched = 0
    _cursor = "*"
    _limit_reason = "no_more_results"

    async with httpx.AsyncClient(timeout=30.0) as client:
        while len(all_works) < requested_limit:
            _resp = await client.get(
                "https://api.openalex.org/works",
                params={**base_params, "cursor": _cursor},
            )
            _resp.raise_for_status()
            _page_data = _resp.json()

            _page_results = _page_data.get("results", [])
            all_works.extend(_page_results)
            _pages_fetched += 1

            _next_cursor = (_page_data.get("meta") or {}).get("next_cursor")
            if not _page_results or not _next_cursor:
                _limit_reason = "no_more_results"
                break

            if _pages_fetched >= _MAX_PAGES:
                _limit_reason = "safety_cap_reached"
                break

            _cursor = _next_cursor

    # Trim any overshoot from the last full page fetch
    all_works = all_works[:requested_limit]
    if len(all_works) >= requested_limit:
        _limit_reason = "exact_match"
    _actual_count = len(all_works)
    print(
        f"[graph] {_actual_count}/{requested_limit} works in {_pages_fetched} page(s)"
        f" — {_limit_reason}"
    )

    nodes = {}
    edges = []
    paper_to_references: dict[str, list[str]] = {}
    referenced_ids: set[str] = set()

    for work in all_works:
        paper_id = work.get("id", "").replace("https://openalex.org/", "")
        title = work.get("title") or "Untitled paper"
        year = work.get("publication_year")
        citations = work.get("cited_by_count", 0)

        if not paper_id:
            continue

        doi = work.get("doi")
        openalex_url = work.get("id")

        primary_location = work.get("primary_location") or {}
        source = primary_location.get("source") or {}
        venue = source.get("display_name") or "Unknown venue"

        author_names = []

        for authorship in work.get("authorships", [])[:5]:
            author = authorship.get("author", {})
            author_name = author.get("display_name")

            if author_name:
                author_names.append(author_name)

        nodes[paper_id] = {
            "id": paper_id,
            "label": title[:70],
            "title": title,
            "type": "Paper",
            "year": year,
            "citations": citations,
            "details": "Metadata retrieved from OpenAlex.",
            "doi": doi,
            "url": openalex_url,
            "venue": venue,
            "authors": author_names,
        }

        for authorship in work.get("authorships", [])[:3]:
            author = authorship.get("author", {})
            author_id = (author.get("id") or "").replace(
                "https://openalex.org/", ""
            )
            author_name = author.get("display_name")

            if not author_id or not author_name:
                continue

            nodes[author_id] = {
                "id": author_id,
                "label": author_name,
                "type": "Author",
                "details": f"Author connected to: {title}",
            }

            edges.append(
                {
                    "id": f"{author_id}-{paper_id}",
                    "source": author_id,
                    "target": paper_id,
                    "label": "AUTHOR_OF",
                }
            )

        for topic in work.get("topics", [])[:2]:
            topic_id = (topic.get("id") or "").replace("https://openalex.org/", "")
            topic_name = topic.get("display_name")

            if not topic_id or not topic_name:
                continue

            nodes[topic_id] = {
                "id": topic_id,
                "label": topic_name,
                "type": "Topic",
                "details": f"OpenAlex topic connected to: {title}",
            }

            edges.append(
                {
                    "id": f"{paper_id}-{topic_id}",
                    "source": paper_id,
                    "target": topic_id,
                    "label": "HAS_TOPIC",
                }
            )

        reference_ids_for_paper = []

        for ref_url in work.get("referenced_works", [])[:2]:
            ref_id = ref_url.replace("https://openalex.org/", "")

            if ref_id and ref_id != paper_id:
                reference_ids_for_paper.append(ref_id)
                referenced_ids.add(ref_id)

        paper_to_references[paper_id] = reference_ids_for_paper

    referenced_work_details = {}

    if referenced_ids:
        unique_referenced_ids = sorted(referenced_ids)

        async with httpx.AsyncClient(timeout=30.0) as client:
            for ref_id_chunk in chunk_list(unique_referenced_ids, 25):
                ref_params: dict[str, Any] = {
                    "filter": f"openalex:{'|'.join(ref_id_chunk)}",
                    "per-page": min(len(ref_id_chunk), 100),
                    "select": (
                        "id,title,publication_year,cited_by_count,"
                        "doi,primary_location"
                    ),
                }

                if api_key:
                    ref_params["api_key"] = api_key

                ref_response = await client.get(
                    "https://api.openalex.org/works",
                    params=ref_params,
                )

                if ref_response.status_code != 200:
                    print(
                        "OpenAlex referenced-paper fetch failed:",
                        ref_response.status_code,
                    )
                    print(ref_response.text)
                    continue

                ref_data = ref_response.json()

                for ref_work in ref_data.get("results", []):
                    ref_id = ref_work.get("id", "").replace(
                        "https://openalex.org/", ""
                    )

                    if ref_id:
                        referenced_work_details[ref_id] = ref_work

    for paper_id, ref_ids in paper_to_references.items():
        for ref_id in ref_ids:
            ref_work = referenced_work_details.get(ref_id, {})
            ref_title = ref_work.get("title") or f"Referenced work {ref_id}"
            ref_year = ref_work.get("publication_year")
            ref_citations = ref_work.get("cited_by_count", 0)
            ref_doi = ref_work.get("doi")
            ref_url = ref_work.get("id") or f"https://openalex.org/{ref_id}"

            primary_location = ref_work.get("primary_location") or {}
            source = primary_location.get("source") or {}
            ref_venue = source.get("display_name") or "Unknown venue"

            if ref_id not in nodes:
                nodes[ref_id] = {
                    "id": ref_id,
                    "label": ref_title[:70],
                    "title": ref_title,
                    "type": "ReferencedPaper",
                    "year": ref_year,
                    "citations": ref_citations,
                    "details": "Referenced paper retrieved from OpenAlex.",
                    "doi": ref_doi,
                    "url": ref_url,
                    "venue": ref_venue,
                }

            edges.append(
                {
                    "id": f"{paper_id}-cites-{ref_id}",
                    "source": paper_id,
                    "target": ref_id,
                    "label": "CITES",
                }
            )

    _graph_result = {
        "nodes": list(nodes.values()),
        "edges": edges,
        "requested_limit": requested_limit,
        "actual_count": _actual_count,
        "limit_reason": _limit_reason,
        "pages_fetched": _pages_fetched,
    }
    db.save_raw("graph_search", _cache_key, _graph_result)
    return _graph_result


@app.get("/author/openalex/{author_id}")
async def get_author_insight(author_id: str):
    """Return enriched author metadata and recent works from OpenAlex."""
    # Cache-first: serve from SQLite if the record is fresh
    cached = db.get_cached_author(author_id)
    if cached is not None:
        return cached

    api_key = os.getenv("OPENALEX_API_KEY")
    base_params: dict[str, Any] = {}
    if api_key:
        base_params["api_key"] = api_key

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Author record
        author_resp = await client.get(
            f"https://api.openalex.org/authors/{author_id}",
            params=base_params,
        )
        if author_resp.status_code != 200:
            raise HTTPException(
                status_code=author_resp.status_code,
                detail=f"OpenAlex author lookup failed for {author_id}",
            )
        author = author_resp.json()

        # 2. Recent 10 works by this author, newest first
        works_params: dict[str, Any] = {
            **base_params,
            "filter": f"authorships.author.id:{author_id}",
            "sort": "publication_date:desc",
            "per-page": 10,
            "select": (
                "id,title,publication_year,publication_date,"
                "cited_by_count,doi,primary_location"
            ),
        }
        works_resp = await client.get(
            "https://api.openalex.org/works",
            params=works_params,
        )
        works_results: list[Any] = []
        if works_resp.status_code == 200:
            works_results = works_resp.json().get("results", [])

    # Last known institutions
    institutions = [
        {
            "name": inst.get("display_name", ""),
            "country": inst.get("country_code"),
        }
        for inst in (author.get("last_known_institutions") or [])
    ]

    # Top 5 research topics
    topics = [
        {
            "name": t.get("display_name", ""),
            "score": round(float(t.get("score") or 0), 3),
        }
        for t in (author.get("topics") or [])[:5]
    ]

    # Counts by year — most recent 5 entries
    counts_by_year = (author.get("counts_by_year") or [])[:5]

    # Recent works
    recent_works = []
    for work in works_results:
        primary_location = work.get("primary_location") or {}
        source = primary_location.get("source") or {}
        recent_works.append(
            {
                "title": work.get("title") or "Untitled",
                "year": work.get("publication_year"),
                "publication_date": work.get("publication_date"),
                "cited_by_count": work.get("cited_by_count", 0),
                "doi": work.get("doi"),
                "url": work.get("id"),
                "venue": source.get("display_name"),
            }
        )

    result = {
        "id": author_id,
        "display_name": author.get("display_name", ""),
        "orcid": author.get("orcid"),
        "openalex_url": author.get("id"),
        "works_count": author.get("works_count", 0),
        "cited_by_count": author.get("cited_by_count", 0),
        "summary_stats": author.get("summary_stats"),
        "last_known_institutions": institutions,
        "topics": topics,
        "counts_by_year": counts_by_year,
        "recent_works": recent_works,
        "provenance": {f: "openalex" for f in (
            "display_name", "orcid", "works_count", "cited_by_count",
            "summary_stats", "last_known_institutions", "topics",
            "counts_by_year", "recent_works",
        )},
    }
    db.save_author(author_id, result, raw_openalex=author)
    return result


# ── PDF / full-text helpers ───────────────────────────────────────────────────

# In-memory session cache: work_id -> full response dict.  Cleared on restart.
_pdf_cache: dict[str, dict[str, Any]] = {}


def _best_pdf_url(work: dict) -> str | None:
    """Return the single best OA PDF URL for the has_full_text_available flag."""
    best = work.get("best_oa_location") or {}
    if best.get("pdf_url"):
        return str(best["pdf_url"])
    oa = work.get("open_access") or {}
    if oa.get("is_oa") and oa.get("oa_url"):
        return str(oa["oa_url"])
    primary = work.get("primary_location") or {}
    if primary.get("pdf_url"):
        return str(primary["pdf_url"])
    for loc in (work.get("locations") or []):
        if loc.get("pdf_url"):
            return str(loc["pdf_url"])
    return None


def _collect_oa_pdf_candidates(work: dict[str, Any]) -> list[str]:
    """Build an ordered list of OA PDF candidate URLs from all available locations.

    Direct PDF links are tried before landing pages (which often return HTML).
    OA locations are tried before non-OA ones.
    """
    pdf_urls: list[str] = []
    landing_urls: list[str] = []

    def _add_pdf(url: str | None) -> None:
        if url and url not in pdf_urls:
            pdf_urls.append(url)

    def _add_landing(url: str | None) -> None:
        if url and url not in landing_urls and url not in pdf_urls:
            landing_urls.append(url)

    best = work.get("best_oa_location") or {}
    _add_pdf(best.get("pdf_url"))
    _add_landing(best.get("landing_page_url"))

    for loc in (work.get("locations") or []):
        if loc.get("is_oa"):
            _add_pdf(loc.get("pdf_url"))
            _add_landing(loc.get("landing_page_url"))

    primary = work.get("primary_location") or {}
    _add_pdf(primary.get("pdf_url"))

    oa = work.get("open_access") or {}
    if oa.get("is_oa"):
        _add_landing(oa.get("oa_url"))

    for loc in (work.get("locations") or []):
        if not loc.get("is_oa"):
            _add_pdf(loc.get("pdf_url"))

    return pdf_urls + landing_urls


def _collect_urls_recursive(obj: Any, found: list[str], depth: int = 0) -> None:
    """Recursively extract string values from known URL-bearing keys."""
    if depth > 10 or len(found) >= 20:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("fulltext", "url", "URL", "pdf_url") and isinstance(v, str) and v.startswith("http"):
                found.append(v)
            else:
                _collect_urls_recursive(v, found, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _collect_urls_recursive(item, found, depth + 1)


async def _fetch_crossref_metadata(doi: str) -> dict[str, Any] | None:
    """Return the Crossref 'message' dict for a DOI; cache in source_raw_cache.

    Uses CROSSREF_MAILTO env var for the polite-pool User-Agent.
    Returns None on any failure so callers can gracefully skip enrichment.
    """
    cached = db.get_cached_raw("crossref", doi)
    if cached is not None:
        return cached
    mailto = os.getenv("CROSSREF_MAILTO", "research@oulu.fi")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://api.crossref.org/works/{doi}",
                headers={"User-Agent": f"AIRA-Scholar/1.0 (mailto:{mailto})"},
            )
        if resp.status_code != 200:
            print(f"[crossref] {doi} -> HTTP {resp.status_code}")
            return None
        msg = resp.json().get("message")
        if not isinstance(msg, dict):
            return None
        db.save_raw("crossref", doi, msg)
        print(f"[crossref] fetched metadata for {doi}")
        return msg
    except Exception as exc:
        print(f"[crossref] lookup skipped for {doi}: {type(exc).__name__}")
        return None


async def _fetch_crossref_tdm_urls(doi: str) -> list[str]:
    """Return TDM/full-text URLs from Crossref for the given DOI.  Empty on any error."""
    msg = await _fetch_crossref_metadata(doi)
    if msg is None:
        return []
    links = msg.get("link") or []
    urls: list[str] = []
    for lk in links:
        url = lk.get("URL")
        if not url:
            continue
        ct = lk.get("content-type", "")
        intended = lk.get("intended-application", "")
        if "text-mining" in intended or "pdf" in ct.lower():
            urls.append(url)
    print(f"[fulltext] Crossref found {len(urls)} TDM URL(s) for {doi}")
    return urls


def _extract_crossref_fields(msg: dict[str, Any]) -> dict[str, Any]:
    """Extract priority metadata fields from a Crossref 'message' dict."""
    titles = msg.get("title") or []
    title = titles[0].strip() if titles else None

    ct = msg.get("container-title") or []
    venue = ct[0].strip() if ct else None

    publisher = msg.get("publisher") or None

    issns = msg.get("ISSN") or []
    issn = issns[0] if issns else None

    licenses = msg.get("license") or []
    license_url = licenses[0].get("URL") if licenses else None

    funders: list[dict[str, Any]] = []
    for f in (msg.get("funder") or []):
        name = f.get("name")
        if name:
            funders.append({"name": name, "award": f.get("award") or []})

    pub_year: int | None = None
    try:
        date_src = msg.get("published") or msg.get("published-print") or {}
        parts = date_src.get("date-parts") or [[None]]
        raw = parts[0][0] if parts and parts[0] else None
        pub_year = int(raw) if raw is not None else None
    except (IndexError, TypeError, ValueError):
        pass

    work_type = msg.get("type") or None

    raw_doi = (msg.get("DOI") or "").lower()
    # Normalise to full URL to match OpenAlex convention
    doi_url = f"https://doi.org/{raw_doi}" if raw_doi else None

    is_referenced_by_count = msg.get("is-referenced-by-count")

    return {
        "title": title,
        "venue": venue,
        "publisher": publisher,
        "issn": issn,
        "license_url": license_url,
        "funder": funders if funders else None,
        "publication_year": pub_year,
        "type": work_type,
        "doi": doi_url,
        "is_referenced_by_count": is_referenced_by_count,
    }


def _merge_crossref_fields(
    oa_result: dict[str, Any], cr_fields: dict[str, Any]
) -> dict[str, Any]:
    """Merge Crossref priority fields into oa_result, updating provenance.

    Rules:
    - title, doi, venue, publication_year, type: Crossref wins when non-empty
    - publisher, issn, license_url, funder: Crossref only (no OA equivalent)
    - cited_by_count: OpenAlex always primary; crossref_citation_count added as
      a cross-check field alongside citation_count_diverges (>20% relative diff)
    """
    result = dict(oa_result)
    prov: dict[str, str] = dict(result.get("provenance") or {})

    for field in ("title", "doi", "venue", "publication_year", "type"):
        cr_val = cr_fields.get(field)
        if cr_val is not None and cr_val != "":
            result[field] = cr_val
            prov[field] = "crossref"

    for field in ("publisher", "issn", "license_url", "funder"):
        cr_val = cr_fields.get(field)
        if cr_val is not None:
            result[field] = cr_val
            prov[field] = "crossref"

    cr_count = cr_fields.get("is_referenced_by_count")
    if cr_count is not None:
        result["crossref_citation_count"] = cr_count
        prov["crossref_citation_count"] = "crossref"
        oa_count = oa_result.get("cited_by_count") or 0
        if oa_count > 0 and cr_count > 0:
            result["citation_count_diverges"] = (
                abs(oa_count - cr_count) / max(oa_count, cr_count) > 0.20
            )
        else:
            result["citation_count_diverges"] = False

    result["provenance"] = prov
    return result


# ── Semantic Scholar enrichment ───────────────────────────────────────────────

async def _fetch_semantic_scholar_metadata(doi: str) -> dict[str, Any] | None:
    """Return the Semantic Scholar paper record for a DOI; cache in source_raw_cache.

    Stores {"_not_found": True} on 404 so we don't re-fetch within the TTL window.
    Returns None on any failure (404, timeout, rate-limit, malformed response).
    """
    cached = db.get_cached_raw("semantic_scholar", doi)
    if cached is not None:
        return None if cached.get("_not_found") else cached

    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    headers: dict[str, str] = {"User-Agent": "AIRA-Scholar/1.0 (academic research tool)"}
    if api_key:
        headers["x-api-key"] = api_key

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
                params={"fields": "paperId,citationCount,referenceCount,abstract,externalIds"},
                headers=headers,
            )
        if resp.status_code == 404:
            db.save_raw("semantic_scholar", doi, {"_not_found": True})
            print(f"[s2] {doi} not in Semantic Scholar")
            return None
        if resp.status_code != 200:
            print(f"[s2] {doi} -> HTTP {resp.status_code}")
            return None
        data = resp.json()
        if not isinstance(data, dict) or not data.get("paperId"):
            return None
        db.save_raw("semantic_scholar", doi, data)
        key_note = f"(key ...{api_key[-4:]})" if api_key else "(unauthenticated)"
        print(f"[s2] fetched metadata for {doi} {key_note}")
        return data
    except Exception as exc:
        print(f"[s2] lookup skipped for {doi}: {type(exc).__name__}")
        return None


async def _fetch_semantic_scholar_recommendations(paper_id: str) -> list[dict[str, Any]] | None:
    """Return up to 5 recommended papers from Semantic Scholar; cached by paper_id."""
    cache_key = f"recs:{paper_id}"
    cached = db.get_cached_raw("semantic_scholar", cache_key)
    if cached is not None:
        return None if cached.get("_not_found") else cached.get("recommended_papers")

    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    headers: dict[str, str] = {"User-Agent": "AIRA-Scholar/1.0 (academic research tool)"}
    if api_key:
        headers["x-api-key"] = api_key

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://api.semanticscholar.org/recommendations/v1/papers/forpaper/{paper_id}",
                params={"fields": "title,year,externalIds", "limit": "5"},
                headers=headers,
            )
        if resp.status_code in (400, 404):
            db.save_raw("semantic_scholar", cache_key, {"_not_found": True})
            return None
        if resp.status_code != 200:
            return None
        papers = resp.json().get("recommendedPapers") or []
        if papers:
            db.save_raw("semantic_scholar", cache_key, {"recommended_papers": papers})
        return papers or None
    except Exception as exc:
        print(f"[s2] recommendations skipped for {paper_id}: {type(exc).__name__}")
        return None


def _extract_semantic_scholar_fields(data: dict[str, Any]) -> dict[str, Any]:
    """Extract fields of interest from a Semantic Scholar paper record."""
    return {
        "paper_id": data.get("paperId"),
        "citation_count": data.get("citationCount"),
        "reference_count": data.get("referenceCount"),
        "abstract": data.get("abstract"),
        "external_ids": data.get("externalIds") or {},
    }


def _merge_semantic_scholar_fields(
    result: dict[str, Any],
    ss_fields: dict[str, Any],
    related: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge Semantic Scholar cross-check data into result.

    Rules:
    - Never overrides title, doi, venue, publisher, license, funder
    - Abstract: fills gap only when both OpenAlex and Crossref left it empty
    - Citation count: stored as semantic_scholar_citation_count for comparison;
      recalculates citation_count_diverges and citation_count_sources across
      all available sources (OpenAlex primary — never overridden)
    - Related papers: stored under semantic_scholar_related, separate from
      the OpenAlex-based cited/referenced lists
    """
    result = dict(result)
    prov: dict[str, str] = dict(result.get("provenance") or {})

    if ss_fields.get("paper_id"):
        result["semantic_scholar_paper_id"] = ss_fields["paper_id"]
        prov["semantic_scholar_paper_id"] = "semantic_scholar"

    # Abstract gap-fill: only when neither OpenAlex nor Crossref supplied one
    ss_abstract = ss_fields.get("abstract")
    if ss_abstract and not result.get("abstract"):
        result["abstract"] = ss_abstract
        prov["abstract"] = "semantic_scholar"

    ss_count = ss_fields.get("citation_count")
    if ss_count is not None:
        result["semantic_scholar_citation_count"] = ss_count
        prov["semantic_scholar_citation_count"] = "semantic_scholar"

    # Always record that a cross-check was performed
    prov["citation_cross_check"] = "semantic_scholar"

    # Build complete citation_count_sources from all enrichment rounds
    oa_count: int = result.get("cited_by_count") or 0
    sources: dict[str, int] = {"openalex": oa_count}
    if result.get("crossref_citation_count") is not None:
        sources["crossref"] = int(result["crossref_citation_count"])
    if ss_count is not None:
        sources["semantic_scholar"] = int(ss_count)
    result["citation_count_sources"] = sources

    # Diverges if any non-OA source differs by >20% relative to the larger value
    diverging: list[str] = [
        src
        for src, cnt in sources.items()
        if src != "openalex" and oa_count > 0 and cnt > 0
        and abs(oa_count - cnt) / max(oa_count, cnt) > 0.20
    ]
    result["citation_count_diverges"] = len(diverging) > 0
    if diverging:
        result["citation_count_diverging_sources"] = diverging
    elif "citation_count_diverging_sources" in result:
        del result["citation_count_diverging_sources"]

    # Related papers — structurally separate from OpenAlex cited/referenced lists
    if related:
        result["semantic_scholar_related"] = [
            {
                "title": p.get("title") or "Untitled",
                "year": p.get("year"),
                "doi": (p.get("externalIds") or {}).get("DOI"),
                "paper_id": p.get("paperId"),
            }
            for p in related
        ]
        prov["semantic_scholar_related"] = "semantic_scholar"

    result["provenance"] = prov
    return result


# ── arXiv preprint enrichment ─────────────────────────────────────────────────

_ARXIV_ATOM_NS = "http://www.w3.org/2005/Atom"
_ARXIV_EXT_NS = "http://arxiv.org/schemas/atom"
# Matches new-format (2301.00000) and old-format (cs/0701001) arXiv IDs in URLs.
_ARXIV_ID_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf)/([a-z\-]+/\d{7}|[0-9]{4}\.[0-9]{4,5})",
    re.I,
)


def _extract_arxiv_id_from_url(url: str) -> str | None:
    """Return the bare arXiv ID (no version suffix) from any arxiv.org URL, or None."""
    m = _ARXIV_ID_RE.search(url)
    return m.group(1) if m else None


def _extract_arxiv_from_openalex(work: dict[str, Any]) -> dict[str, Any] | None:
    """Scan OpenAlex locations for an existing arXiv link — no API call needed.

    Returns a preprint dict with confidence='openalex_linked', or None if not found.
    """
    locations: list[Any] = list(work.get("locations") or [])
    for key in ("best_oa_location", "primary_location"):
        loc = work.get(key)
        if isinstance(loc, dict):
            locations.append(loc)

    for loc in locations:
        if not isinstance(loc, dict):
            continue
        for url_field in ("landing_page_url", "pdf_url"):
            url = loc.get(url_field) or ""
            if "arxiv.org" not in url:
                continue
            arxiv_id = _extract_arxiv_id_from_url(url)
            if arxiv_id:
                return {
                    "has_preprint": True,
                    "preprint_match_confidence": "openalex_linked",
                    "arxiv_id": arxiv_id,
                    "arxiv_url": f"https://arxiv.org/abs/{arxiv_id}",
                    "arxiv_pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
                }
    return None


def _parse_arxiv_entry(entry: ET.Element) -> dict[str, Any] | None:
    """Parse one <entry> from an arXiv Atom feed into a plain dict."""
    ns = _ARXIV_ATOM_NS
    raw_id = getattr(entry.find(f"{{{ns}}}id"), "text", "") or ""
    arxiv_id = _extract_arxiv_id_from_url(raw_id)
    if not arxiv_id:
        return None

    pdf_url: str | None = None
    for link in entry.findall(f"{{{ns}}}link"):
        if link.get("title") == "pdf" or "pdf" in (link.get("type") or ""):
            pdf_url = link.get("href")
            break

    title_elem = entry.find(f"{{{ns}}}title")
    title = (title_elem.text or "").strip() if title_elem is not None else ""

    # arXiv-specific doi element — used to verify DOI-based searches
    doi_elem = entry.find(f"{{{_ARXIV_EXT_NS}}}doi")
    linked_doi = (doi_elem.text or "").strip().lower() if doi_elem is not None else ""

    return {
        "arxiv_id": arxiv_id,
        "arxiv_url": f"https://arxiv.org/abs/{arxiv_id}",
        "arxiv_pdf_url": pdf_url or f"https://arxiv.org/pdf/{arxiv_id}",
        "arxiv_published_date": getattr(entry.find(f"{{{ns}}}published"), "text", None),
        "arxiv_updated_date": getattr(entry.find(f"{{{ns}}}updated"), "text", None),
        "_title": title,
        "_linked_doi": linked_doi,
    }


async def _search_arxiv(query: str, max_results: int = 3) -> list[dict[str, Any]]:
    """Run an arXiv API query; return parsed entries. Empty list on any failure."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "http://export.arxiv.org/api/query",
                params={"search_query": query, "max_results": str(max_results)},
                headers={"User-Agent": "AIRA-Scholar/1.0 (University of Oulu research tool)"},
            )
        if resp.status_code != 200:
            print(f"[arxiv] HTTP {resp.status_code} for query: {query[:70]}")
            return []
        root = ET.fromstring(resp.text)
        entries = root.findall(f"{{{_ARXIV_ATOM_NS}}}entry")
        results = [_parse_arxiv_entry(e) for e in entries]
        return [r for r in results if r is not None]
    except Exception as exc:
        print(f"[arxiv] search error ({type(exc).__name__}): {query[:70]}")
        return []


def _normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for similarity checks."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", "", title.lower())).strip()


async def _fetch_arxiv_preprint(doi: str | None, title: str | None) -> dict[str, Any]:
    """DOI-then-title arXiv search. Always returns a dict (never raises, never None).

    Result fields: has_preprint (bool), preprint_match_confidence, and when
    has_preprint=True: arxiv_id, arxiv_url, arxiv_pdf_url, arxiv_published_date,
    arxiv_updated_date.
    """
    doi_clean = (doi or "").replace("https://doi.org/", "").replace("http://doi.org/", "").strip() or None
    cache_key = (
        f"doi:{doi_clean}" if doi_clean
        else (f"title:{_normalize_title(title)[:100]}" if title else None)
    )
    _no_match: dict[str, Any] = {"has_preprint": False, "preprint_match_confidence": "none"}

    if cache_key:
        cached = db.get_cached_raw("arxiv", cache_key)
        if cached is not None:
            return cached

    # 1. DOI search — verify by matching the arxiv:doi element when present
    if doi_clean:
        entries = await _search_arxiv(f'all:"{doi_clean}"', max_results=5)
        for entry in entries:
            linked = entry.get("_linked_doi", "")
            # Accept if arXiv explicitly records this DOI, or if it's the only result
            if linked == doi_clean.lower() or (not linked and len(entries) == 1):
                result: dict[str, Any] = {
                    k: v for k, v in entry.items() if not k.startswith("_")
                }
                result["has_preprint"] = True
                result["preprint_match_confidence"] = "doi_match"
                if cache_key:
                    db.save_raw("arxiv", cache_key, result)
                print(f"[arxiv] DOI match: {doi_clean} -> {entry['arxiv_id']}")
                return result
        if entries:
            print(f"[arxiv] DOI search returned {len(entries)} result(s) but none verified for: {doi_clean}")
        else:
            print(f"[arxiv] DOI search found nothing for: {doi_clean}")

    # 2. Title fallback — require >=90% normalized similarity
    if title:
        title_norm = _normalize_title(title)
        entries = await _search_arxiv(f'ti:"{title}"', max_results=5)
        best_ratio = 0.0
        best_entry: dict[str, Any] | None = None
        for entry in entries:
            entry_norm = _normalize_title(entry.get("_title") or "")
            if not entry_norm:
                continue
            ratio = difflib.SequenceMatcher(None, title_norm, entry_norm).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_entry = entry

        if best_entry and best_ratio >= 0.90:
            result = {k: v for k, v in best_entry.items() if not k.startswith("_")}
            result["has_preprint"] = True
            result["preprint_match_confidence"] = "title_fuzzy_match"
            if cache_key:
                db.save_raw("arxiv", cache_key, result)
            print(f"[arxiv] title match (sim={best_ratio:.2f}): {best_entry['arxiv_id']}")
            return result

        if best_entry:
            print(f"[arxiv] title best sim={best_ratio:.2f} < 0.90, rejected: {title[:60]!r}")
        else:
            print(f"[arxiv] title search found nothing for: {title[:60]!r}")

    if cache_key:
        db.save_raw("arxiv", cache_key, _no_match)
    return _no_match


def _merge_arxiv_fields(result: dict[str, Any], arxiv_data: dict[str, Any]) -> dict[str, Any]:
    """Add arXiv preprint linkage fields to result. Purely additive — never overrides."""
    result = dict(result)
    prov: dict[str, str] = dict(result.get("provenance") or {})

    confidence = arxiv_data.get("preprint_match_confidence", "none")
    result["has_preprint"] = arxiv_data.get("has_preprint", False)
    result["preprint_match_confidence"] = confidence

    if result["has_preprint"]:
        for field in ("arxiv_id", "arxiv_url", "arxiv_pdf_url",
                      "arxiv_published_date", "arxiv_updated_date"):
            val = arxiv_data.get(field)
            if val is not None:
                result[field] = val
        prov["preprint_linkage"] = "openalex" if confidence == "openalex_linked" else "arxiv"

    result["provenance"] = prov
    return result


# ── OpenAIRE Graph API v2 enrichment ─────────────────────────────────────────

_SCHOLEX_UA = "AIRA-Scholar/1.0 (University of Oulu research tool; contact: research@oulu.fi)"


async def _fetch_scholexplorer_datasets(doi: str) -> dict[str, Any] | None:
    """Query OpenAIRE Scholexplorer v2 for linked datasets/software for a DOI.

    Cache key: "openaire_scholexplorer" / doi.
    Paginates up to 3 pages (300 links) and filters for target.Type "dataset" / "software".
    Returns {"datasets": [...], "software": [...]} on success;
    {"_not_found": True} (cached) when no linked outputs exist;
    None on network/HTTP errors (not cached — will retry next request). Never raises.
    """
    cached = db.get_cached_raw("openaire_scholexplorer", doi)
    if cached is not None:
        return None if cached.get("_not_found") else cached

    datasets: list[dict[str, Any]] = []
    software_list: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            page = 0
            max_pages = 3
            while page < max_pages:
                resp = await client.get(
                    "https://api.scholexplorer.openaire.eu/v2/Links",
                    params={"sourcePid": doi, "page": str(page), "size": "100"},
                    headers={"User-Agent": _SCHOLEX_UA},
                )
                if resp.status_code != 200:
                    print(f"[scholexplorer] {doi} -> HTTP {resp.status_code}")
                    break
                body = resp.json()
                links: list[dict[str, Any]] = body.get("result") or []
                total_pages: int = body.get("totalPages") or 1

                for link in links:
                    target = link.get("target") or {}
                    target_type = (target.get("Type") or "").lower()
                    if target_type not in ("dataset", "software"):
                        continue

                    identifiers: list[dict[str, Any]] = target.get("Identifier") or []
                    doi_entry = next(
                        (i for i in identifiers if isinstance(i, dict) and i.get("IDScheme", "").lower() == "doi"),
                        None,
                    )
                    # Prefer DOI as the stable identifier; fall back to openaireIdentifier.
                    openaire_id = (doi_entry["ID"] if doi_entry else None) or next(
                        (i["ID"] for i in identifiers if isinstance(i, dict) and "openaire" in i.get("IDScheme", "").lower()),
                        None,
                    )
                    if not openaire_id or openaire_id in seen_ids:
                        continue
                    seen_ids.add(openaire_id)

                    doi_value: str | None = doi_entry["ID"] if doi_entry else None
                    url: str | None = (doi_entry.get("IDURL") if doi_entry else None) or next(
                        (i.get("IDURL") for i in identifiers if isinstance(i, dict) and i.get("IDURL")),
                        None,
                    )
                    item = {
                        "id": openaire_id,
                        "title": target.get("Title") or None,
                        "url": url,
                        "doi": doi_value,
                    }
                    if target_type == "dataset":
                        datasets.append(item)
                    else:
                        software_list.append(item)

                page += 1
                if page >= total_pages:
                    break

        if not datasets and not software_list:
            db.save_raw("openaire_scholexplorer", doi, {"_not_found": True})
            print(f"[scholexplorer] {doi} — no linked datasets/software")
            return None

        result_payload = {"datasets": datasets, "software": software_list}
        db.save_raw("openaire_scholexplorer", doi, result_payload)
        print(
            f"[scholexplorer] {doi}: {len(datasets)} dataset(s), "
            f"{len(software_list)} software item(s)"
        )
        return result_payload

    except Exception as exc:
        print(f"[scholexplorer] lookup skipped for {doi}: {type(exc).__name__}: {exc}")
        return None


async def _fetch_openaire_funding(doi: str) -> dict[str, Any] | None:
    """Query OpenAIRE Graph API v2 for a DOI; return projects/funders/datasets/software.

    Cache key: "openaire_funding" / doi.
    Returns None on any failure; returns {"_not_found": True} (cached) when OpenAIRE
    has no record for the DOI so we don't re-fetch within the TTL window.
    Never raises.
    """
    cached = db.get_cached_raw("openaire_funding", doi)
    if cached is not None:
        return None if cached.get("_not_found") else cached

    _UA = "AIRA-Scholar/1.0 (University of Oulu research tool; contact: research@oulu.fi)"
    try:
        async with httpx.AsyncClient(timeout=9.0) as client:
            resp = await client.get(
                "https://api.openaire.eu/graph/v2/researchProducts",
                # OpenAIRE Graph API v2 uses "pid" for persistent-identifier lookup;
                # "doi" is not a valid parameter (confirmed from API error message).
                params={"pid": doi, "pageSize": "5"},
                headers={"User-Agent": _UA},
            )
        if resp.status_code != 200:
            print(f"[openaire] {doi} -> HTTP {resp.status_code}")
            return None

        body = resp.json()
        results: list[dict[str, Any]] = body.get("results") or []

        if not results:
            db.save_raw("openaire_funding", doi, {"_not_found": True})
            print(f"[openaire] {doi} — no results from Graph API v2")
            return None

        # Find best match: prefer publication with exact DOI in its pids list.
        # The field is "pids" (list) in the v2 response, not "pid".
        doi_lower = doi.lower()
        best: dict[str, Any] | None = None
        for item in results:
            for pid in (item.get("pids") or []):
                if (
                    isinstance(pid, dict)
                    and pid.get("scheme", "").lower() == "doi"
                    and pid.get("value", "").lower() == doi_lower
                ):
                    if item.get("type") == "publication" or best is None:
                        best = item
                    break
        if best is None:
            best = results[0]

        openaire_id: str | None = best.get("id")

        # ── Projects + funders ──────────────────────────────────────────────
        # In Graph API v2, each project's "funder" field is a plain string
        # (the funder name), not a nested dict — e.g. "European Commission".
        projects: list[dict[str, Any]] = []
        for proj in (best.get("projects") or []):
            if not isinstance(proj, dict):
                continue
            proj_id = proj.get("id")
            if not proj_id:
                continue
            funder_name: str | None = proj.get("funder") or None
            projects.append(
                {
                    "id": proj_id,
                    "name": proj.get("title"),
                    "acronym": proj.get("acronym"),
                    # start/end dates and URL are not in the embedded project object;
                    # they would require a separate /projects/{id} lookup.
                    "startDate": None,
                    "endDate": None,
                    "websiteUrl": None,
                    "funder": {
                        # No separate funder ID in embedded data — use name as stable key.
                        "id": funder_name,
                        "name": funder_name,
                        "shortName": None,
                    },
                }
            )

        # ── Related datasets + software ─────────────────────────────────────
        # Graph API v2 research-product response has no `relations` field; linked
        # datasets/software are not accessible from the embedded publication record.
        # Both lists are always empty from this endpoint.
        datasets: list[dict[str, Any]] = []
        software_list: list[dict[str, Any]] = []

        result = {
            "openaire_id": openaire_id,
            "projects": projects,
            "datasets": datasets,
            "software": software_list,
        }
        db.save_raw("openaire_funding", doi, result)
        print(
            f"[openaire] {doi}: {len(projects)} project(s), "
            f"{len(datasets)} dataset(s), {len(software_list)} software item(s)"
        )
        return result

    except Exception as exc:
        print(f"[openaire] funding lookup skipped for {doi}: {type(exc).__name__}: {exc}")
        return None


def _merge_openaire_fields(
    result: dict[str, Any], oa_data: dict[str, Any]
) -> dict[str, Any]:
    """Add OpenAIRE funding/linked-entity fields to result.

    Rules:
    - Never overrides any existing canonical_paper field.
    - Always sets funding_projects, linked_datasets, linked_software (empty lists
      when no data — never null, never omitted).
    - Adds openaire_id only when the paper doesn't already have one.
    """
    result = dict(result)
    prov: dict[str, str] = dict(result.get("provenance") or {})

    if not result.get("openaire_id") and oa_data.get("openaire_id"):
        result["openaire_id"] = oa_data["openaire_id"]
        prov["openaire_id"] = "openaire"

    projects = oa_data.get("projects") or []
    result["funding_projects"] = [
        {
            "name": p.get("name"),
            "acronym": p.get("acronym"),
            "funder": (p.get("funder") or {}).get("name"),
            "funder_id": (p.get("funder") or {}).get("id"),
            "start_date": p.get("startDate"),
            "end_date": p.get("endDate"),
            "url": p.get("websiteUrl"),
        }
        for p in projects
    ]
    prov["funding_projects"] = "openaire"

    result["linked_datasets"] = [
        {"title": d.get("title"), "url": d.get("url"), "doi": d.get("doi")}
        for d in (oa_data.get("datasets") or [])
    ]
    prov["linked_datasets"] = "openaire"

    result["linked_software"] = [
        {"title": s.get("title"), "url": s.get("url")}
        for s in (oa_data.get("software") or [])
    ]
    prov["linked_software"] = "openaire"

    result["provenance"] = prov
    return result


async def _fetch_openaire_urls(doi: str) -> list[str]:
    """Return alternate full-text URLs from OpenAIRE for the given DOI.  Empty on any error."""
    _PRIORITY_DOMAINS = (
        "jultika.oulu.fi", "pmc.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov/pmc",
        "zenodo.org", "arxiv.org/pdf", "europepmc.org",
        "core.ac.uk/download", "hdl.handle.net",
    )
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                "https://api.openaire.eu/search/publications",
                params={"doi": doi, "format": "json", "size": "5"},
                headers={"User-Agent": "AIRA-Scholar/1.0 (academic research tool)"},
            )
        if resp.status_code != 200:
            return []
        all_urls: list[str] = []
        _collect_urls_recursive(resp.json(), all_urls)
        priority = [u for u in all_urls if any(d in u for d in _PRIORITY_DOMAINS)]
        rest = [u for u in all_urls if u not in set(priority)]
        result = (priority + rest)[:8]
        if result:
            print(f"[fulltext] OpenAIRE found {len(result)} URL(s) for {doi}")
        return result
    except Exception as exc:
        print(f"[fulltext] OpenAIRE lookup skipped for {doi}: {type(exc).__name__}")
        return []


def _ft_result(
    work_id: str, status: str, text: str, source_url: str | None, tried: int
) -> dict[str, Any]:
    return {
        "work_id": work_id,
        "source_url": source_url,
        "text": text,
        "text_length": len(text) if status == "ok" else 0,
        "status": status,
        "candidates_tried": tried,
    }


@app.get("/paper/openalex/{work_id}")
async def get_paper_insight(work_id: str):
    """Return enriched paper metadata and references from OpenAlex."""
    # Cache-first: serve from SQLite if the record is fresh
    cached = db.get_cached_paper(work_id)
    if cached is not None:
        # Always overlay OpenAIRE enrichment from the link tables — they are the
        # ground truth and may have been populated by a later enrichment phase
        # (e.g. Phase 7 Scholexplorer) after the paper JSON was cached.
        cached = {**cached, **db.get_openaire_enrichment(work_id)}
        return cached

    api_key = os.getenv("OPENALEX_API_KEY")
    base_params: dict[str, Any] = {}
    if api_key:
        base_params["api_key"] = api_key

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"https://api.openalex.org/works/{work_id}",
            params=base_params,
        )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"OpenAlex work lookup failed for {work_id}",
            )
        work = resp.json()

    # Authors
    authors = []
    for authorship in (work.get("authorships") or []):
        author = authorship.get("author") or {}
        name = author.get("display_name")
        oa_id = (author.get("id") or "").replace("https://openalex.org/", "") or None
        if name:
            authors.append({"name": name, "id": oa_id})

    # Venue
    primary_location = work.get("primary_location") or {}
    source = primary_location.get("source") or {}
    venue = source.get("display_name")

    # Open access
    oa = work.get("open_access") or {}
    pdf_url = _best_pdf_url(work)

    # Topics — top 5
    topics = [
        {
            "name": t.get("display_name", ""),
            "score": round(float(t.get("score") or 0), 3),
        }
        for t in (work.get("topics") or [])[:5]
    ]

    # Abstract from inverted index
    abstract = reconstruct_abstract(work.get("abstract_inverted_index")) or None

    paper_year = work.get("publication_year")

    # Referenced works — fetch details for first 10
    ref_ids = [
        url.replace("https://openalex.org/", "")
        for url in (work.get("referenced_works") or [])[:10]
    ]
    referenced_works: list[Any] = []
    if ref_ids:
        async with httpx.AsyncClient(timeout=30.0) as client:
            ref_params: dict[str, Any] = {
                **base_params,
                "filter": f"openalex:{'|'.join(ref_ids)}",
                "per-page": 10,
                "select": "id,title,publication_year,cited_by_count,doi,primary_location",
            }
            ref_resp = await client.get(
                "https://api.openalex.org/works",
                params=ref_params,
            )
            if ref_resp.status_code == 200:
                for ref in ref_resp.json().get("results", []):
                    ref_primary = ref.get("primary_location") or {}
                    ref_source = ref_primary.get("source") or {}
                    ref_venue = ref_source.get("display_name")
                    ref_year = ref.get("publication_year")

                    reasons: list[str] = ["This paper cites this work."]
                    if ref_venue and ref_venue == venue:
                        reasons.append(f"Same venue: {ref_venue}.")
                    if paper_year and ref_year and abs(paper_year - ref_year) <= 2:
                        reasons.append(f"Published in nearby years ({ref_year} vs {paper_year}).")

                    referenced_works.append(
                        {
                            "title": ref.get("title") or "Untitled",
                            "year": ref_year,
                            "cited_by_count": ref.get("cited_by_count", 0),
                            "doi": ref.get("doi"),
                            "url": ref.get("id"),
                            "venue": ref_venue,
                            "connection_reasons": reasons,
                        }
                    )

    # Citing works — papers that cite this one (max 5, sorted by citation count)
    citing_works: list[Any] = []
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            cite_params: dict[str, Any] = {
                **base_params,
                "filter": f"cites:{work_id}",
                "per-page": 5,
                "select": "id,title,publication_year,cited_by_count,doi,primary_location",
                "sort": "cited_by_count:desc",
            }
            cite_resp = await client.get(
                "https://api.openalex.org/works",
                params=cite_params,
            )
            if cite_resp.status_code == 200:
                for cw in cite_resp.json().get("results", []):
                    cw_primary = cw.get("primary_location") or {}
                    cw_source = cw_primary.get("source") or {}
                    citing_works.append(
                        {
                            "title": cw.get("title") or "Untitled",
                            "year": cw.get("publication_year"),
                            "cited_by_count": cw.get("cited_by_count", 0),
                            "doi": cw.get("doi"),
                            "url": cw.get("id"),
                            "venue": cw_source.get("display_name"),
                            "connection_reasons": ["Cites the selected paper."],
                        }
                    )
    except Exception as exc:
        print(f"Citing works fetch skipped for {work_id}: {type(exc).__name__}")
        # citing_works stays empty — graceful degradation

    result = {
        "id": work_id,
        "title": work.get("title") or "Untitled",
        "publication_year": paper_year,
        "publication_date": work.get("publication_date"),
        "type": work.get("type"),
        "language": work.get("language"),
        "cited_by_count": work.get("cited_by_count", 0),
        "doi": work.get("doi"),
        "openalex_url": work.get("id"),
        "venue": venue,
        "is_oa": oa.get("is_oa", False),
        "oa_status": oa.get("oa_status"),
        "oa_url": oa.get("oa_url"),
        "pdf_url": pdf_url,
        "has_full_text_available": pdf_url is not None,
        "authors": authors,
        "topics": topics,
        "abstract": abstract,
        "referenced_works_count": len(work.get("referenced_works") or []),
        "referenced_works": referenced_works,
        "citing_works": citing_works,
        "provenance": {f: "openalex" for f in (
            "title", "abstract", "publication_year", "publication_date", "type",
            "language", "cited_by_count", "doi", "venue", "is_oa", "oa_status",
            "oa_url", "pdf_url", "authors", "topics", "referenced_works", "citing_works",
        )},
    }

    # Secondary enrichment: Crossref + Semantic Scholar
    raw_doi = result.get("doi") or ""
    doi_for_enrich = raw_doi.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()
    if doi_for_enrich:
        # Crossref metadata enrichment
        try:
            cr_msg = await _fetch_crossref_metadata(doi_for_enrich)
            if cr_msg is not None:
                result = _merge_crossref_fields(result, _extract_crossref_fields(cr_msg))
        except Exception as exc:
            print(f"[crossref] enrichment skipped for {work_id}: {type(exc).__name__}: {exc}")

        # Semantic Scholar citation cross-check
        try:
            ss_data = await _fetch_semantic_scholar_metadata(doi_for_enrich)
            if ss_data is not None:
                ss_fields = _extract_semantic_scholar_fields(ss_data)
                related = None
                if ss_fields.get("paper_id"):
                    related = await _fetch_semantic_scholar_recommendations(ss_fields["paper_id"])
                result = _merge_semantic_scholar_fields(result, ss_fields, related)
        except Exception as exc:
            print(f"[s2] enrichment skipped for {work_id}: {type(exc).__name__}: {exc}")

    # arXiv preprint matching — check OpenAlex locations first, then API fallback
    try:
        oa_arxiv = _extract_arxiv_from_openalex(work)
        if oa_arxiv is not None:
            print(f"[arxiv] OpenAlex-linked preprint for {work_id}: {oa_arxiv['arxiv_id']}")
            result = _merge_arxiv_fields(result, oa_arxiv)
        else:
            arxiv_data = await _fetch_arxiv_preprint(
                doi_for_enrich or None,
                result.get("title"),
            )
            result = _merge_arxiv_fields(result, arxiv_data)
    except Exception as exc:
        print(f"[arxiv] enrichment skipped for {work_id}: {type(exc).__name__}: {exc}")

    # OpenAIRE funding / linked-entity enrichment (Phase 6)
    # Always adds funding_projects, linked_datasets, linked_software (empty lists when
    # no DOI or no OpenAIRE record) — never modifies any existing canonical_paper field.
    oa_funding: dict[str, Any] = {}
    if doi_for_enrich:
        try:
            fetched = await _fetch_openaire_funding(doi_for_enrich)
            if fetched is not None:
                oa_funding = fetched
        except Exception as exc:
            print(f"[openaire] enrichment skipped for {work_id}: {type(exc).__name__}: {exc}")

    result = _merge_openaire_fields(result, oa_funding)

    # Scholexplorer dataset/software linkage (Phase 7)
    # Queries OpenAIRE Scholexplorer v2 for linked datasets/software and overrides the
    # (always-empty) lists that _merge_openaire_fields set from the Graph API.
    schol_data: dict[str, Any] = {}
    if doi_for_enrich:
        try:
            fetched_schol = await _fetch_scholexplorer_datasets(doi_for_enrich)
            if fetched_schol is not None:
                schol_data = fetched_schol
        except Exception as exc:
            print(f"[scholexplorer] enrichment skipped for {work_id}: {type(exc).__name__}: {exc}")

    if schol_data:
        prov = dict(result.get("provenance") or {})
        result["linked_datasets"] = [
            {"title": d.get("title"), "url": d.get("url"), "doi": d.get("doi")}
            for d in schol_data.get("datasets") or []
        ]
        prov["linked_datasets"] = "openaire_scholexplorer"
        result["linked_software"] = [
            {"title": s.get("title"), "url": s.get("url")}
            for s in schol_data.get("software") or []
        ]
        prov["linked_software"] = "openaire_scholexplorer"
        result["provenance"] = prov

    if doi_for_enrich:
        db.save_openaire_enrichment(
            openalex_id=work_id,
            projects=oa_funding.get("projects") or [],
            datasets=schol_data.get("datasets") or [],
            software=schol_data.get("software") or [],
        )

    db.save_paper(work_id, result, raw_openalex=work)
    return result


@app.get("/enrich/crossref/{doi:path}")
async def enrich_crossref(doi: str):
    """Return raw Crossref-extracted fields for a DOI (manual testing endpoint)."""
    doi_clean = doi.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()
    msg = await _fetch_crossref_metadata(doi_clean)
    if msg is None:
        raise HTTPException(status_code=404, detail=f"Crossref has no record for DOI: {doi_clean}")
    return {
        "doi": doi_clean,
        "source": "crossref",
        "fields": _extract_crossref_fields(msg),
    }


@app.get("/enrich/arxiv/{doi:path}")
async def enrich_arxiv(doi: str):
    """Return arXiv preprint match data for a DOI (manual testing endpoint)."""
    doi_clean = doi.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()
    data = await _fetch_arxiv_preprint(doi_clean, title=None)
    return {"doi": doi_clean, "source": "arxiv", "match": data}


@app.get("/enrich/semantic-scholar/{doi:path}")
async def enrich_semantic_scholar(doi: str):
    """Return raw Semantic Scholar-extracted fields for a DOI (manual testing endpoint)."""
    doi_clean = doi.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    data = await _fetch_semantic_scholar_metadata(doi_clean)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=f"Semantic Scholar has no record for DOI: {doi_clean}",
        )
    ss_fields = _extract_semantic_scholar_fields(data)
    related = None
    if ss_fields.get("paper_id"):
        related = await _fetch_semantic_scholar_recommendations(ss_fields["paper_id"])
    return {
        "doi": doi_clean,
        "source": "semantic_scholar",
        "api_key_used": bool(api_key),
        "fields": ss_fields,
        "semantic_scholar_related": related,
    }


@app.get("/enrich/openaire/{doi:path}")
async def enrich_openaire(doi: str):
    """Return raw OpenAIRE-extracted projects/funders/datasets/software for a DOI.

    Uses Graph API v2.  Responses are cached in source_raw_cache with key
    "openaire_funding"/doi — a second identical request will be served from cache
    (visible in the server log as no new [openaire] fetch line).
    """
    doi_clean = doi.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()
    if not doi_clean:
        raise HTTPException(status_code=400, detail="DOI must not be empty")

    data = await _fetch_openaire_funding(doi_clean)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=f"OpenAIRE has no funding record for DOI: {doi_clean}",
        )
    return {
        "doi": doi_clean,
        "source": "openaire",
        "openaire_id": data.get("openaire_id"),
        "funding_projects": [
            {
                "name": p.get("name"),
                "acronym": p.get("acronym"),
                "funder": (p.get("funder") or {}).get("name"),
                "funder_id": (p.get("funder") or {}).get("id"),
                "start_date": p.get("startDate"),
                "end_date": p.get("endDate"),
                "url": p.get("websiteUrl"),
            }
            for p in (data.get("projects") or [])
        ],
        "linked_datasets": [
            {"title": d.get("title"), "url": d.get("url"), "doi": d.get("doi")}
            for d in (data.get("datasets") or [])
        ],
        "linked_software": [
            {"title": s.get("title"), "url": s.get("url")}
            for s in (data.get("software") or [])
        ],
    }


@app.get("/enrich/openaire-datasets/{doi:path}")
async def enrich_openaire_datasets(doi: str):
    """Return linked datasets/software from OpenAIRE Scholexplorer v2 for a DOI.

    Responses are cached under "openaire_scholexplorer"/doi.  A second identical
    request within the TTL window is served from cache (check server logs for the
    [scholexplorer] line to distinguish live fetch vs cache hit).
    """
    doi_clean = doi.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()
    if not doi_clean:
        raise HTTPException(status_code=400, detail="DOI must not be empty")

    data = await _fetch_scholexplorer_datasets(doi_clean)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=f"Scholexplorer has no linked datasets/software for DOI: {doi_clean}",
        )
    return {
        "doi": doi_clean,
        "source": "openaire_scholexplorer",
        "linked_datasets": [
            {"title": d.get("title"), "url": d.get("url"), "doi": d.get("doi")}
            for d in (data.get("datasets") or [])
        ],
        "linked_software": [
            {"title": s.get("title"), "url": s.get("url")}
            for s in (data.get("software") or [])
        ],
    }


@app.get("/debug/db")
def debug_db():
    """Return SQLite cache status and record counts (no secrets exposed)."""
    return db.db_status()


@app.get("/debug/cache/{entity_type}/{entity_id}")
def debug_cache(entity_type: str, entity_id: str):
    """Return the raw canonical row for a cached paper or author.

    entity_type : 'paper' | 'author'
    entity_id   : OpenAlex ID, e.g. W2963403868 or A5023888391

    The response includes field_provenance and last_refreshed_at so you can
    verify which source supplied which field and when the record was last fetched.
    The full cached_json blob is replaced by its character count.
    """
    if entity_type == "paper":
        record = db.get_debug_paper(entity_id)
    elif entity_type == "author":
        record = db.get_debug_author(entity_id)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown entity_type '{entity_type}'. Use 'paper' or 'author'.",
        )

    if record is None:
        status = db.db_status()
        if status.get("status") != "ok":
            raise HTTPException(status_code=503, detail=f"DB unavailable: {status}")
        raise HTTPException(
            status_code=404,
            detail=f"No cached record found for {entity_type}/{entity_id}",
        )

    return record


@app.get("/paper/openalex/{work_id}/fulltext")
async def get_paper_fulltext(work_id: str):
    """Try multiple OA sources for a paper's full text; fail with a specific message."""

    # 1. In-memory session cache
    if work_id in _pdf_cache:
        return _pdf_cache[work_id]

    # 2. DB persistent cache
    db_cached = db.get_cached_fulltext(work_id)
    if db_cached is not None:
        _pdf_cache[work_id] = db_cached
        return db_cached

    # 3. Check PyMuPDF is installed before any network call
    try:
        import fitz  # type: ignore[import]  # PyMuPDF
    except ImportError:
        return _ft_result(
            work_id, "missing_dependency",
            "PyMuPDF is not installed. Run: pip install pymupdf",
            None, 0,
        )

    # 4. Fetch the OpenAlex work record
    api_key = os.getenv("OPENALEX_API_KEY")
    params: dict[str, Any] = {"api_key": api_key} if api_key else {}
    async with httpx.AsyncClient(timeout=30.0) as client:
        oa_resp = await client.get(
            f"https://api.openalex.org/works/{work_id}", params=params
        )
        if oa_resp.status_code != 200:
            return _ft_result(
                work_id, "error",
                "OpenAlex lookup failed. Cannot retrieve PDF.",
                None, 0,
            )
        work = oa_resp.json()

    # 5. Build ordered candidate list
    candidates: list[str] = _collect_oa_pdf_candidates(work)

    doi_raw: str = work.get("doi") or ""
    doi_clean = doi_raw.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()
    if doi_clean:
        crossref_urls, openaire_urls = await asyncio.gather(
            _fetch_crossref_tdm_urls(doi_clean),
            _fetch_openaire_urls(doi_clean),
        )
        for u in crossref_urls + openaire_urls:
            if u not in candidates:
                candidates.append(u)

    print(f"[fulltext] {work_id}: {len(candidates)} candidate(s) to try")

    if not candidates:
        result = _ft_result(
            work_id, "no_readable_source",
            "No open-access sources found for this paper.",
            None, 0,
        )
        db.save_fulltext(work_id, result)
        return result

    # 6. Try each candidate in order
    last_status = "no_readable_source"
    tried = 0

    for i, url in enumerate(candidates):
        print(f"[fulltext] {work_id} [{i+1}/{len(candidates)}] {url[:80]}")
        tried += 1

        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                pdf_resp = await client.get(
                    url,
                    headers={"User-Agent": "AIRA-Scholar/1.0 (academic research tool)"},
                )
        except Exception as exc:
            print(f"[fulltext]   [{i+1}] network error: {type(exc).__name__}")
            continue

        if pdf_resp.status_code != 200:
            print(f"[fulltext]   [{i+1}] HTTP {pdf_resp.status_code}")
            continue

        # Skip HTML landing pages without feeding them to the PDF parser
        ct = (pdf_resp.headers.get("content-type") or "").lower()
        if "pdf" not in ct:
            print(f"[fulltext]   [{i+1}] skipped (content-type={ct!r})")
            continue

        try:
            doc = fitz.open(stream=pdf_resp.content, filetype="pdf")
            page_texts = [page.get_text() for page in doc]
            doc.close()
            raw = "\n".join(page_texts)
            full_text = "\n".join(line for line in raw.splitlines() if line.strip())
        except Exception as exc:
            print(f"[fulltext]   [{i+1}] extraction error: {type(exc).__name__}")
            last_status = "unreadable_pdf"
            continue

        if len(full_text.strip()) < 100:
            print(f"[fulltext]   [{i+1}] too little text — likely scanned/image PDF")
            last_status = "unreadable_pdf"
            continue

        if len(full_text) > 30_000:
            full_text = full_text[:30_000]
        print(f"[fulltext]   [{i+1}] SUCCESS: {len(full_text):,} chars from {url[:60]}")
        result = _ft_result(work_id, "ok", full_text, url, tried)
        _pdf_cache[work_id] = result
        db.save_fulltext(work_id, result)
        return result

    # 7. All candidates exhausted
    if last_status == "unreadable_pdf":
        msg = (
            "A PDF was found but couldn't be read as text "
            "(it may be a scanned or image-only document)."
        )
    else:
        noun = "source" if tried == 1 else "sources"
        msg = (
            f"Full text isn't accessible for automated reading "
            f"(tried {tried} {noun}). "
            "You can still read it directly using the Open Access PDF link above."
        )
    result = _ft_result(work_id, last_status, msg, None, tried)
    db.save_fulltext(work_id, result)
    return result


# ── AIRA Assistant chat ──────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are AIRA Assistant, a research assistant for an academic knowledge graph.
Answer ONLY from the provided GRAPH CONTEXT. Be concise: 2–3 sentences maximum.

CRITICAL DISTINCTION:
  METADATA  : year, authors, venue, citation count, reference count, h-index.
              These are bibliographic facts — NEVER treat them as research results.
  CONTENT   : findings, numerical results, accuracy, percentages, scores, methods,
              limitations — found in the abstract or full paper text sections.

RULES:
1. NUMERIC RESULTS — user asks about results, accuracy, percentage, score, metric,
   performance, sample size, p-value, Sharpe ratio, return, risk, comparison, etc.:
   - If a "Full paper text" section IS present in the context:
       Look ONLY in that section for research numbers, percentages, scores, metrics.
       Do NOT report citation count, year, reference count as research results.
       If you find numeric research results there, report them. Use (source: full text).
   - If NO "Full paper text" section is present in the context:
       Say: "Full text has not been loaded. I cannot report research findings without it."
       Do NOT use citation count or publication year as a substitute for research results.
2. A section labeled "Full paper text (intent-matched excerpt ...)" means the full text IS loaded.
   Prefer it for questions about methods, limitations, sample size, findings, and conclusions.
   Use (source: full text) when answering from it.
3. If the full text IS present but the specific answer is not visible in the excerpt, say:
   "The loaded excerpt does not clearly show [topic]. The full paper may contain this
    information outside the extracted sections."
   Do NOT say "full text has not been loaded" when the full text section is present.
4. If NO full text section is present and user asks about methods/limitations/findings, say:
   "Full text has not been loaded for this paper. I only have the abstract and metadata."
5. Treat each question independently. Do not carry over intent from previous questions.
6. If the user asks about authors, venue, year, or citation count, answer from metadata.
7. End every answer with the source in parentheses:
   (source: full text) or (source: paper abstract) or (source: paper metadata) etc.
8. If context is insufficient, say so briefly. Do not invent facts.\
"""

# Model/URL constants — all read from env so they can be changed without code edits.
_OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip().rstrip("/")
_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2").strip()
_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-lite").strip()
_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o").strip()


def _ollama_is_running() -> bool:
    """Synchronous localhost probe — fast enough (2 s timeout) for status checks."""
    try:
        with httpx.Client(timeout=2.0) as client:
            return client.get(f"{_OLLAMA_BASE_URL}/api/tags").status_code == 200
    except Exception:
        return False


def _resolve_provider() -> tuple[str, str]:
    """Return (provider, credential) where credential is the api_key or base_url for Ollama.

    Explicit LLM_PROVIDER wins. Without it, auto-detect priority:
      Ollama (if running) → Gemini (if key present) → OpenAI (if key present) → none
    """
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()

    if provider == "ollama":
        return ("ollama", _OLLAMA_BASE_URL)
    if provider == "gemini":
        return ("gemini", gemini_key) if gemini_key else ("none", "")
    if provider == "openai":
        return ("openai", openai_key) if openai_key else ("none", "")

    # Auto-detect
    if _ollama_is_running():
        return ("ollama", _OLLAMA_BASE_URL)
    if gemini_key:
        return ("gemini", gemini_key)
    if openai_key:
        return ("openai", openai_key)
    return ("none", "")


class GraphSummaryIn(BaseModel):
    papers: int = 0
    authors: int = 0
    topics: int = 0
    citedPapers: int = 0
    edges: int = 0


class ChatRequest(BaseModel):
    question: str
    selected_node: dict[str, Any] | None = None
    graph_summary: GraphSummaryIn = GraphSummaryIn()
    paper_insight: dict[str, Any] | None = None
    author_insight: dict[str, Any] | None = None
    visible_nodes: list[dict[str, Any]] = []
    visible_links: list[dict[str, Any]] = []
    history: list[dict[str, str]] = []  # last N user/assistant turns for context
    full_text: str | None = None  # full extracted PDF text for selected paper


def _build_chat_context(req: ChatRequest) -> tuple[str, list[str]]:
    lines: list[str] = []
    sources: list[str] = []

    # Graph summary — always included, very compact
    gs = req.graph_summary
    lines.append(
        f"Graph: {gs.papers} papers, {gs.authors} authors, "
        f"{gs.topics} topics, {gs.citedPapers} cited papers, {gs.edges} edges."
    )
    sources.append("graph_summary")

    # Visible nodes — id, label, type only; max 15
    if req.visible_nodes:
        node_parts = [
            f"{n.get('type', '?')}:{n.get('label', n.get('id', '?'))}"
            for n in req.visible_nodes[:15]
        ]
        lines.append("Nodes: " + " | ".join(node_parts))

    # Visible links — source, target, label only; max 15
    if req.visible_links:
        link_parts = [
            f"{lk.get('source', '?')}→{lk.get('target', '?')}"
            + (f"({lk['label']})" if lk.get("label") else "")
            for lk in req.visible_links[:15]
        ]
        lines.append("Links: " + " | ".join(link_parts))

    # Selected node — id, label, type, year, citations, venue, details only
    if req.selected_node:
        sn = req.selected_node
        lines.append(f"\nSelected ({sn.get('type', '?')}): {sn.get('label', '')} [id={sn.get('id', '')}]")
        if sn.get("year"):
            lines.append(f"  Year: {sn['year']}")
        if sn.get("citations") is not None:
            lines.append(f"  Citations: {sn['citations']}")
        if sn.get("venue"):
            lines.append(f"  Venue: {sn['venue']}")
        if sn.get("details"):
            lines.append(f"  Details: {sn['details']}")
        sources.append("selected_node")

    # Paper insight — metadata always; abstract only when no full text is loaded
    if req.paper_insight:
        pi = req.paper_insight
        lines.append("\nPaper:")
        lines.append(f"  Title: {pi.get('title', '')}")
        lines.append(f"  Year: {pi.get('publication_year', '')}  Venue: {pi.get('venue', '')}")
        author_names = [str(a.get("name", "")) for a in (pi.get("authors") or [])[:5]]
        if author_names:
            lines.append(f"  Authors: {', '.join(author_names)}")
        topic_names = [str(t.get("name", "")) for t in (pi.get("topics") or [])[:5]]
        if topic_names:
            lines.append(f"  Topics: {', '.join(topic_names)}")
        # Skip abstract when full text is available — it's usually the start of the full text
        if pi.get("abstract") and not req.full_text:
            lines.append(f"  Abstract: {pi['abstract'][:1200]}")
        ref_titles = [str(r.get("title", "")) for r in (pi.get("referenced_works") or [])[:5]]
        if ref_titles:
            lines.append(f"  Refs: {' | '.join(ref_titles)}")
        citing_titles = [str(r.get("title", "")) for r in (pi.get("citing_works") or [])[:5]]
        if citing_titles:
            lines.append(f"  Cited by: {' | '.join(citing_titles)}")
        sources.append("paper_insight")

    # Full paper text — intent-matched excerpt so the right section reaches the LLM
    if req.full_text:
        total = len(req.full_text)
        excerpt = _extract_intent_chunk(req.full_text, req.question, max_chars=3600).strip()
        lines.append(f"\nFull paper text (intent-matched excerpt, {total:,} chars total):")
        lines.append(excerpt)
        sources.append("full_text")

    # Author insight — name, affiliation, works_count, cited_by_count, top 5 topics, recent 5 works
    if req.author_insight:
        ai = req.author_insight
        lines.append("\nAuthor:")
        lines.append(f"  Name: {ai.get('display_name', '')}")
        lines.append(
            f"  Works: {ai.get('works_count', 0)}, Citations: {ai.get('cited_by_count', 0)}"
        )
        inst_names = [
            str(i.get("name", ""))
            for i in (ai.get("last_known_institutions") or [])[:1]
        ]
        if inst_names:
            lines.append(f"  Affiliation: {inst_names[0]}")
        t_names = [str(t.get("name", "")) for t in (ai.get("topics") or [])[:5]]
        if t_names:
            lines.append(f"  Topics: {', '.join(t_names)}")
        w_titles = [str(w.get("title", "")) for w in (ai.get("recent_works") or [])[:5]]
        if w_titles:
            lines.append(f"  Recent works: {' | '.join(w_titles)}")
        sources.append("author_insight")

    return "\n".join(lines), sources


# ── Intent detection ──────────────────────────────────────────────────────────

_KW_NUMERIC = re.compile(
    r"\bnumeric(al)?\b|\bresult(s)?\b|\bvalue(s)?\b|\baccuracy\b|\bpercentage\b"
    r"|\bpercent\b|\bmetric(s)?\b|\bsample size\b|\bperformance\b|\bscore(s)?\b"
    r"|\bf1\b|\bprecision\b|\brecall\b|\bbenchmark\b|\boutcome(s)?\b|\bfinding(s)?\b"
    r"|\bmeasurement(s)?\b|\bnumber(s)?\b|\bsharpe\b|\bp-value\b|\bAUC\b|\bROC\b"
    r"|\bRMSE\b|\bMAE\b|\bcomparison\b|\bcompared\b|\bachieved\b|\bincrease\b"
    r"|\bdecrease\b|\boutperform\b|\bimprove(d|ment)?\b|\brisk\b|\breturn(s)?\b",
    re.IGNORECASE,
)
_KW_AUTHOR = re.compile(
    r"\bauthor(s)?\b|\bresearcher(s)?\b|\bwrote\b|\bwritten by\b|\bby whom\b",
    re.IGNORECASE,
)
_KW_CITATION = re.compile(
    r"\bcitation(s)?\b|\bcited by\b|\bcite count\b|\bhow many cit",
    re.IGNORECASE,
)
_KW_YEAR = re.compile(
    r"\byear\b|\bwhen\b|\bpublished\b|\brelease date\b",
    re.IGNORECASE,
)
_KW_VENUE = re.compile(
    r"\bvenue\b|\bjournal\b|\bconference\b|\bproceedings\b|\bwhere.*publish",
    re.IGNORECASE,
)
_KW_LIMITATION = re.compile(
    r"\blimitation(s)?\b|\bdrawback(s)?\b|\bfuture work\b|\bfuture research\b"
    r"|\bchallenge(s)?\b|\bweakness(es)?\b|\bconstrained\b|\blimitation of this study\b"
    r"|\bshortcoming(s)?\b",
    re.IGNORECASE,
)
_KW_METHOD = re.compile(
    r"\bmethod(s)?\b|\bmethodology\b|\bapproach(es)?\b|\balgorithm(s)?\b"
    r"|\bframework\b|\barchitecture\b|\bimplementation\b|\bpipeline\b|\bproposed\b",
    re.IGNORECASE,
)
# Sentence-level filters for the numeric-result extractor.
# A sentence is a RESEARCH candidate if it matches _RESEARCH_SIGNAL.
# A sentence is excluded even with a digit if it matches _META_ONLY (pure bibliography).
_RESEARCH_SIGNAL = re.compile(
    r"\d+\.?\d*\s*%"                                        # percentages
    r"|\d+\.\d+"                                            # decimals
    r"|\bp\s*[<=>]+\s*0\.\d+"                              # p-values
    r"|\bn\s*=\s*\d+"                                      # sample sizes
    r"|\b(?:accuracy|precision|recall|F[-_]?1|AUC|RMSE|MAE|MSE|ROC|Sharpe)\b"
    r"|\bscore\b"                                           # score (context gives it meaning)
    r"|\bratio\b"
    r"|\b(?:increase|decrease|improv|outperform|achiev)\w*\s+(?:by|to|from)?\s*\d",
    re.IGNORECASE,
)
_META_ONLY = re.compile(
    r"\bcit(?:ation|ed)[^.]*\d"      # "cited by 45", "45 citations"
    r"|\bh[-_]?index\b"
    r"|\bworks[- ]?count\b"
    r"|\breference[- ]?count\b"
    r"|\bDOI\b|\bISSN\b|\bISBN\b"
    r"|\bpublication[- ]?(?:date|year)\b"
    r"|\bpage\s+\d+"                 # page numbers
    r"|\bOrcid\b|\bOpenAlex\b",
    re.IGNORECASE,
)


def _score_chunk(chunk: str, patterns: list, q_words: set) -> float:
    """Score a text chunk by regex pattern hits and question-word overlap."""
    score = sum(len(pat.findall(chunk)) * 2.0 for pat in patterns)
    cl = chunk.lower()
    score += sum(0.5 for w in q_words if w in cl)
    return score


def _extract_intent_chunk(full_text: str, question: str, max_chars: int = 3600) -> str:
    """Split full_text into ~1200-char chunks, rank by intent keywords, return top-3 in doc order.

    For limitation/method/result questions: scores each chunk by keyword density,
    then reassembles the top-3 highest-scoring chunks in their original order so
    the LLM sees coherent, in-context excerpts from the right part of the paper.
    """
    is_limitation = bool(_KW_LIMITATION.search(question))
    is_method = bool(_KW_METHOD.search(question))
    is_result = bool(_KW_NUMERIC.search(question)) and not bool(_KW_CITATION.search(question))

    if is_limitation:
        primary_pat = _KW_LIMITATION
    elif is_method:
        primary_pat = _KW_METHOD
    elif is_result:
        primary_pat = re.compile(
            r"\bresult(s)?\b|\bperformance\b|\bexperiment(s)?\b|\bevaluation\b"
            r"|\baccuracy\b|\bfinding(s)?\b|\b%\b|\btable\b|\bfigure\b"
            r"|\bmetric(s)?\b|\bscore(s)?\b|\bvalue(s)?\b|\bpercentage\b|\bpercent\b"
            r"|\bsample\b|\bp-value\b|\bcompar(ed|ison)\b|\bachiev(ed|ement)\b"
            r"|\bincrease\b|\bdecrease\b|\boutperform\b|\bimprove\b"
            r"|\bsharpe\b|\bratio\b|\brisk\b|\breturn\b|\bROC\b|\bAUC\b"
            r"|\bRMSE\b|\bMAE\b|\bF1\b|\bproposed method\b",
            re.IGNORECASE,
        )
    else:
        return full_text[:max_chars]

    # Split on newline boundaries into ~1200-char chunks
    chunk_size = 1200
    chunks: list[str] = []
    start = 0
    text_len = len(full_text)
    while start < text_len:
        end = min(start + chunk_size, text_len)
        if end < text_len:
            nl = full_text.rfind("\n", start, end)
            if nl > start:
                end = nl + 1
        chunks.append(full_text[start:end])
        start = end

    if not chunks:
        return full_text[:max_chars]

    q_words = {w.lower() for w in re.findall(r"\w+", question) if len(w) > 3}
    scored = [
        (i, _score_chunk(chunk, [primary_pat], q_words))
        for i, chunk in enumerate(chunks)
    ]
    scored.sort(key=lambda x: (-x[1], x[0]))

    # Take top-3 highest-scoring chunks; reassemble in original document order
    top_indices = sorted(s[0] for s in scored[:3])
    result = "\n\n[...]\n\n".join(chunks[i] for i in top_indices)
    return result[:max_chars]


def _try_direct_answer(req: ChatRequest) -> str | None:
    """Return a fast direct answer for simple factual queries without calling the LLM.
    Returns None to fall through to the LLM for anything complex."""
    q = req.question
    sn = req.selected_node or {}
    pi = req.paper_insight or {}

    # Detect all intents independently from the CURRENT question only
    is_numeric = bool(_KW_NUMERIC.search(q))
    is_author = bool(_KW_AUTHOR.search(q))
    is_cite = bool(_KW_CITATION.search(q))
    is_year = bool(_KW_YEAR.search(q))
    is_venue = bool(_KW_VENUE.search(q))
    is_limitation = bool(_KW_LIMITATION.search(q))
    is_method = bool(_KW_METHOD.search(q))

    # Numeric-result questions are handled before _try_direct_answer is called
    # (see _handle_numeric_result in /chat endpoint). This branch is never reached
    # for numeric questions without citation/author intent.

    # Limitation questions — check whether full text is loaded and whether it contains
    # the limitations section before letting the LLM attempt to extract them.
    if is_limitation and not is_author and not is_cite:
        full_text = req.full_text or ""
        if not full_text:
            return (
                "Full text has not been loaded for this paper. "
                "I only have the abstract and metadata. "
                "Click 'Load Full Text' if available. (source: metadata only)"
            )
        if not _KW_LIMITATION.search(full_text):
            return (
                "The loaded full-text excerpt does not clearly mention limitations. "
                "The limitations section may appear beyond the extracted portion. "
                "(source: full text)"
            )
        return None  # limitations found in text — let LLM quote them

    # Method questions — same pattern as limitations
    if is_method and not is_author and not is_cite and not is_limitation:
        full_text = req.full_text or ""
        if not full_text:
            abstract = (pi.get("abstract") or "")
            if abstract:
                return None  # let LLM answer from abstract
            return (
                "Full text has not been loaded for this paper. "
                "I only have metadata. (source: metadata only)"
            )
        return None  # let LLM answer from full text

    # Author questions
    if is_author:
        authors = [a.get("name", "") for a in (pi.get("authors") or [])]
        if not authors:
            authors = [str(a) for a in (sn.get("authors") or [])]
        clean = [a for a in authors[:5] if a]
        if clean:
            src = "paper insight" if pi.get("authors") else "selected node"
            return f"Authors ({src}): {', '.join(clean)}"
        if pi or sn:
            return "Author information is not available in the current metadata. (source: paper insight / selected node)"
        return None

    # Citation-count questions
    if is_cite:
        count = pi.get("cited_by_count") if pi else None
        if count is None:
            count = sn.get("citations")
        if count is not None:
            src = "paper insight" if pi.get("cited_by_count") is not None else "selected node"
            return f"Citation count ({src}): {count}"
        if pi or sn:
            return "Citation count is not available in the current metadata."
        return None

    # Year questions (skip when combined with numeric-result intent — already handled above)
    if is_year and not is_numeric:
        year = pi.get("publication_year") or sn.get("year")
        if year:
            src = "paper insight" if pi.get("publication_year") else "selected node"
            return f"Publication year ({src}): {year}"
        if pi or sn:
            return "Publication year is not available in the current metadata."
        return None

    # Venue questions
    if is_venue:
        venue = pi.get("venue") or sn.get("venue")
        if venue:
            src = "paper insight" if pi.get("venue") else "selected node"
            return f"Venue ({src}): {venue}"
        if pi or sn:
            return "Venue information is not available in the current metadata."
        return None

    return None  # fall through to LLM


@observe(as_type="generation", name="ollama-chat", capture_input=False)
async def _call_ollama(
    base_url: str, context: str, question: str, history: list[dict[str, str]]
) -> str:
    """Call Ollama local API and return the answer text."""
    langfuse.update_current_generation(
        model=_OLLAMA_MODEL,
        input={"context": context, "question": question, "history": history},
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": _SYSTEM_PROMPT}]
    for turn in history[-2:]:  # at most 2 prior user+assistant pairs
        role = turn.get("role", "")
        content = str(turn.get("content", ""))
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({
        "role": "user",
        "content": f"GRAPH CONTEXT:\n{context}\n\nUSER QUESTION:\n{question}",
    })
    payload = {
        "model": _OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": 512,
            "num_ctx": 8192,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{base_url}/api/chat", json=payload)
    except httpx.ConnectError:
        print("AIRA Assistant error: Ollama not reachable (ConnectError)")
        return f"Ollama is not running. Start Ollama and run: ollama pull {_OLLAMA_MODEL}"
    except Exception as exc:
        print(f"AIRA Assistant error: Ollama network error — {type(exc).__name__}")
        return "Could not reach Ollama. Check that it is running on the configured port."

    status = resp.status_code
    if status == 404:
        print(f"AIRA Assistant error: Ollama model not found (404): {resp.text[:200]}")
        return f"Ollama model is missing. Run: ollama pull {_OLLAMA_MODEL}"
    if status != 200:
        print(f"AIRA Assistant error: Ollama returned HTTP {status}: {resp.text[:200]}")
        return f"Ollama returned an unexpected error (HTTP {status}). Please try again."

    try:
        content = str(resp.json()["message"]["content"])
    except Exception as exc:
        print(f"AIRA Assistant error: could not parse Ollama response — {type(exc).__name__}")
        return "Received an unexpected response format from Ollama. Please try again."

    if not content.strip():
        print(f"AIRA Assistant warning: Ollama returned empty content for model={_OLLAMA_MODEL}")
        return (
            "The model returned an empty response. "
            "This can happen when the context is too large or the model is confused. "
            "Try a more specific question."
        )
    return content


@observe(as_type="generation", name="gemini-chat", capture_input=False)
async def _call_gemini(
    api_key: str, context: str, question: str, history: list[dict[str, str]]
) -> str:
    """Call Gemini REST API and return the answer text."""
    langfuse.update_current_generation(
        model=_GEMINI_MODEL,
        input={"context": context, "question": question, "history": history},
    )
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{_GEMINI_MODEL}:generateContent?key={api_key}"
    )
    # Gemini uses "model" instead of "assistant" for the role name
    contents: list[dict] = []
    for turn in history[-2:]:
        role = "model" if turn.get("role") == "assistant" else "user"
        content = str(turn.get("content", ""))
        if content:
            contents.append({"role": role, "parts": [{"text": content}]})
    contents.append({
        "role": "user",
        "parts": [{"text": f"GRAPH CONTEXT:\n{context}\n\nUSER QUESTION:\n{question}"}],
    })
    payload = {
        "system_instruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 350},
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=payload)

    status = resp.status_code
    if status == 400:
        print(f"AIRA Assistant error: Gemini bad request (400): {resp.text[:200]}")
        return "Gemini rejected the request (400). The question or context may be malformed."
    if status in (401, 403):
        print(f"AIRA Assistant error: Gemini authentication failed ({status})")
        return (
            "Gemini authentication failed. "
            "Check that GEMINI_API_KEY in backend/.env is correct "
            "and has no leading or trailing spaces."
        )
    if status == 429:
        print("AIRA Assistant error: Gemini rate limit (429)")
        return (
            "Gemini free-tier quota is temporarily exhausted. "
            "Try again later, reduce context, or use GEMINI_MODEL=gemini-2.0-flash-lite."
        )
    if status != 200:
        print(f"AIRA Assistant error: Gemini returned HTTP {status}")
        return f"Gemini returned an unexpected error (HTTP {status}). Please try again."

    try:
        return str(resp.json()["candidates"][0]["content"]["parts"][0]["text"])
    except Exception as exc:
        print(f"AIRA Assistant error: could not parse Gemini response — {type(exc).__name__}")
        return "Received an unexpected response format from Gemini. Please try again."


@observe(as_type="generation", name="openai-chat", capture_input=False)
async def _call_openai(
    api_key: str, context: str, question: str, history: list[dict[str, str]]
) -> str:
    """Call OpenAI Chat Completions API and return the answer text."""
    langfuse.update_current_generation(
        model=_OPENAI_MODEL,
        input={"context": context, "question": question, "history": history},
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": _SYSTEM_PROMPT}]
    for turn in history[-2:]:
        role = turn.get("role", "")
        content = str(turn.get("content", ""))
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({
        "role": "user",
        "content": f"GRAPH CONTEXT:\n{context}\n\nUSER QUESTION:\n{question}",
    })
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": _OPENAI_MODEL,
                "messages": messages,
                "max_tokens": 350,
                "temperature": 0.1,
            },
        )

    status = resp.status_code
    if status == 401:
        print("AIRA Assistant error: OpenAI authentication failed (401)")
        return (
            "OpenAI authentication failed. "
            "Check that OPENAI_API_KEY in backend/.env is correct "
            "and has no leading or trailing spaces."
        )
    if status == 429:
        print("AIRA Assistant error: OpenAI rate limit / quota (429)")
        return (
            "OpenAI rate limit or quota exceeded. "
            "Check your billing at platform.openai.com and try again."
        )
    if status != 200:
        print(f"AIRA Assistant error: OpenAI returned HTTP {status}")
        return f"OpenAI returned an unexpected error (HTTP {status}). Please try again."

    try:
        return str(resp.json()["choices"][0]["message"]["content"])
    except Exception as exc:
        print(f"AIRA Assistant error: could not parse OpenAI response — {type(exc).__name__}")
        return "Received an unexpected response format from OpenAI. Please try again."


def _extract_numeric_sentences(full_text: str, max_sentences: int = 15) -> list[str]:
    """Return sentences from full_text that contain research-numeric signals
    and are not purely about bibliographic metadata (citations, years, DOI, etc.)."""
    raw = re.split(r"(?<=[.!?])\s+|\n{2,}", full_text)
    candidates: list[str] = []
    seen: set[str] = set()
    for sent in raw:
        sent = sent.strip()
        if len(sent) < 25 or len(sent) > 600:
            continue
        if not _RESEARCH_SIGNAL.search(sent):
            continue
        if _META_ONLY.search(sent):
            continue
        norm = " ".join(sent.split())
        if norm in seen:
            continue
        seen.add(norm)
        candidates.append(sent)
        if len(candidates) >= max_sentences:
            break
    return candidates


@observe(name="chat-numeric-handler", capture_input=False)
async def _handle_numeric_result(req: ChatRequest, provider: str, credential: str) -> dict:
    """Dedicated handler for numeric-result questions.

    Extracts research-numeric sentences from full text before the LLM is called,
    so metadata values (citation count, year, references) can never leak in as results.
    """
    langfuse.update_current_span(input={"question": req.question, "provider": provider})
    if not req.full_text:
        pi = req.paper_insight or {}
        sn = req.selected_node or {}
        year = pi.get("publication_year") or sn.get("year")
        cites = pi.get("cited_by_count") if pi else sn.get("citations")
        meta_parts = []
        if year:
            meta_parts.append(f"year={year}")
        if cites is not None:
            meta_parts.append(f"citations={cites}")
        meta_note = f" (available metadata: {', '.join(meta_parts)})" if meta_parts else ""
        return {
            "answer": (
                "Full text has not been loaded for this paper. "
                "I cannot report research findings without it. "
                f"Click 'Load Full Text' if available.{meta_note} (source: metadata only)"
            ),
            "sources_used": [],
        }

    candidates = _extract_numeric_sentences(req.full_text, max_sentences=15)

    if not candidates:
        return {
            "answer": (
                "The loaded full text does not clearly show specific numerical research results. "
                "Results may be in tables, figures, or appendices not captured in the extracted text. "
                "(source: full text)"
            ),
            "sources_used": ["full_text"],
        }

    # Send ONLY the candidate sentences to the LLM — no metadata can leak in
    candidate_block = "\n".join(f"• {s}" for s in candidates)
    focused_context = (
        "CANDIDATE RESULT SENTENCES extracted from the full paper text:\n\n"
        f"{candidate_block}"
    )
    focused_question = (
        f"{req.question}\n\n"
        "Based ONLY on the candidate sentences above, summarize the numerical research "
        "results. Do NOT use citation count, year, reference count, or any publication "
        "metadata. End your answer with (source: full text)."
    )

    try:
        if provider == "ollama":
            answer = await _call_ollama(credential, focused_context, focused_question, [])
        elif provider == "gemini":
            answer = await _call_gemini(credential, focused_context, focused_question, [])
        else:
            answer = await _call_openai(credential, focused_context, focused_question, [])
    except Exception as exc:
        print(f"AIRA numeric handler error ({provider}): {type(exc).__name__}")
        return {
            "answer": (
                f"Could not reach the {provider.capitalize()} service. "
                "Check your connection and try again."
            ),
            "sources_used": [],
        }

    return {"answer": answer, "sources_used": ["full_text"]}


@app.get("/chat/status")
def chat_status():
    """Return provider configuration status for AIRA Assistant (no secrets exposed)."""
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    ollama_running = _ollama_is_running()
    provider, _ = _resolve_provider()

    model = (
        _OLLAMA_MODEL if provider == "ollama"
        else _GEMINI_MODEL if provider == "gemini"
        else _OPENAI_MODEL if provider == "openai"
        else ""
    )
    return {
        "provider": provider,
        "model": model,
        "ollama_available": ollama_running,
        "ollama_model": _OLLAMA_MODEL,
        "gemini_key_configured": bool(gemini_key),
        "openai_key_configured": bool(openai_key),
    }


@app.post("/chat")
@observe(name="chat-endpoint")
async def chat(req: ChatRequest):
    """Answer a question about the current graph using the configured LLM provider."""
    provider, credential = _resolve_provider()
    initial: _ChatGraphState = {
        "req": req,
        "provider": provider,
        "credential": credential,
        "route": "",
        "context": "",
        "focused_context": "",
        "focused_question": "",
        "sources_used": [],
        "answer": "",
    }
    result = await _chat_graph.ainvoke(initial)
    return {"answer": result["answer"], "sources_used": result["sources_used"]}


# ── LangGraph chat workflow ────────────────────────────────────────────────────


class _ChatGraphState(TypedDict):
    req: ChatRequest
    provider: str
    credential: str
    # "numeric" | "numeric_no_text" | "numeric_no_candidates"
    # | "direct" | "general" | "no_provider" | ""
    route: str
    context: str
    focused_context: str
    focused_question: str
    sources_used: list[str]
    answer: str


def _graph_route_question(state: _ChatGraphState) -> dict:
    provider = state["provider"]
    credential = state["credential"]
    q = state["req"].question

    if provider == "none" or not credential:
        return {
            "route": "no_provider",
            "answer": (
                "AI assistant is not configured. "
                "Start Ollama, or add GEMINI_API_KEY / OPENAI_API_KEY to backend/.env "
                "and restart the backend."
            ),
            "sources_used": [],
        }

    if (
        bool(_KW_NUMERIC.search(q))
        and not bool(_KW_CITATION.search(q))
        and not bool(_KW_AUTHOR.search(q))
    ):
        return {"route": "numeric"}

    return {"route": "pending"}


def _graph_build_context(state: _ChatGraphState) -> dict:
    route = state["route"]
    req = state["req"]

    if route == "numeric":
        if not req.full_text:
            pi = req.paper_insight or {}
            sn = req.selected_node or {}
            year = pi.get("publication_year") or sn.get("year")
            cites = pi.get("cited_by_count") if pi else sn.get("citations")
            meta_parts = []
            if year:
                meta_parts.append(f"year={year}")
            if cites is not None:
                meta_parts.append(f"citations={cites}")
            meta_note = f" (available metadata: {', '.join(meta_parts)})" if meta_parts else ""
            return {
                "route": "numeric_no_text",
                "answer": (
                    "Full text has not been loaded for this paper. "
                    "I cannot report research findings without it. "
                    f"Click 'Load Full Text' if available.{meta_note} (source: metadata only)"
                ),
                "sources_used": [],
            }

        candidates = _extract_numeric_sentences(req.full_text, max_sentences=15)
        if not candidates:
            return {
                "route": "numeric_no_candidates",
                "answer": (
                    "The loaded full text does not clearly show specific numerical research results. "
                    "Results may be in tables, figures, or appendices not captured in the extracted text. "
                    "(source: full text)"
                ),
                "sources_used": ["full_text"],
            }

        candidate_block = "\n".join(f"• {s}" for s in candidates)
        focused_context = (
            "CANDIDATE RESULT SENTENCES extracted from the full paper text:\n\n"
            f"{candidate_block}"
        )
        focused_question = (
            f"{req.question}\n\n"
            "Based ONLY on the candidate sentences above, summarize the numerical research "
            "results. Do NOT use citation count, year, reference count, or any publication "
            "metadata. End your answer with (source: full text)."
        )
        return {"focused_context": focused_context, "focused_question": focused_question}

    # pending → resolve to "direct" or "general"
    context, sources_used = _build_chat_context(req)
    direct = _try_direct_answer(req)
    if direct is not None:
        return {
            "route": "direct",
            "context": context,
            "sources_used": sources_used,
            "answer": direct,
        }
    return {"route": "general", "context": context, "sources_used": sources_used}


async def _graph_generate_answer(state: _ChatGraphState) -> dict:
    route = state["route"]
    provider = state["provider"]
    credential = state["credential"]
    req = state["req"]

    if route == "numeric":
        context = state["focused_context"]
        question = state["focused_question"]
        history: list[dict[str, str]] = []
        sources: list[str] = ["full_text"]
    else:  # general
        context = state["context"]
        question = req.question
        history = req.history
        sources = state["sources_used"]

    try:
        if provider == "ollama":
            answer = await _call_ollama(credential, context, question, history)
        elif provider == "gemini":
            answer = await _call_gemini(credential, context, question, history)
        else:
            answer = await _call_openai(credential, context, question, history)
    except Exception as exc:
        print(f"AIRA graph error ({provider}): {type(exc).__name__}")
        return {
            "answer": (
                f"Could not reach the {provider.capitalize()} service. "
                "Check your connection and try again."
            ),
            "sources_used": [],
        }

    return {"answer": answer, "sources_used": sources}


def _after_routing(state: _ChatGraphState) -> str:
    return END if state["route"] == "no_provider" else "build_context"


def _after_context(state: _ChatGraphState) -> str:
    if state["route"] in ("direct", "numeric_no_text", "numeric_no_candidates"):
        return END
    return "generate_answer"


_chat_graph_builder = StateGraph(_ChatGraphState)
_chat_graph_builder.add_node("route_question", _graph_route_question)
_chat_graph_builder.add_node("build_context", _graph_build_context)
_chat_graph_builder.add_node("generate_answer", _graph_generate_answer)
_chat_graph_builder.add_edge(START, "route_question")
_chat_graph_builder.add_conditional_edges("route_question", _after_routing)
_chat_graph_builder.add_conditional_edges("build_context", _after_context)
_chat_graph_builder.add_edge("generate_answer", END)
_chat_graph = _chat_graph_builder.compile()


# ── Semantic Search ───────────────────────────────────────────────────────────

_qdrant_client: Any = None
_embed_model: Any = None
_QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION") or "oulucris_publications"


def _get_qdrant_client() -> Any:
    global _qdrant_client
    if _qdrant_client is None:
        from qdrant_client import QdrantClient  # type: ignore[import]
        host = os.getenv("QDRANT_HOST", "localhost")
        port = int(os.getenv("QDRANT_PORT", "6333"))
        api_key = os.getenv("QDRANT_API_KEY") or None
        use_https = os.getenv("QDRANT_HTTPS", "false").lower() in ("1", "true", "yes")
        _qdrant_client = QdrantClient(host=host, port=port, api_key=api_key, https=use_https, timeout=60)
    return _qdrant_client


def _get_embed_model() -> Any:
    global _embed_model
    if _embed_model is None:
        from sentence_transformers import SentenceTransformer  # type: ignore[import]
        print("[semantic] loading all-mpnet-base-v2 model…")
        _embed_model = SentenceTransformer("all-mpnet-base-v2")
        print("[semantic] model ready")
    return _embed_model


def _oa_full_to_short(full_url: str) -> str:
    return full_url.removeprefix("https://openalex.org/")


async def _run_semantic_search(query: str, limit: int) -> list[dict[str, Any]]:
    loop = asyncio.get_event_loop()

    model = await loop.run_in_executor(None, _get_embed_model)
    vector: list[float] = await loop.run_in_executor(
        None, lambda: model.encode(query, normalize_embeddings=True).tolist()
    )

    client = _get_qdrant_client()
    response = await loop.run_in_executor(
        None,
        lambda: client.query_points(
            collection_name=_QDRANT_COLLECTION,
            query=vector,
            using="abstract_embedding",
            limit=limit,
            with_payload=True,
        ),
    )

    results: list[dict[str, Any]] = []
    for hit in response.points:
        payload = hit.payload or {}
        full_oa_id: str = payload.get("openalex_id") or ""
        short_id = _oa_full_to_short(full_oa_id)
        local_paper = db.get_cached_paper(short_id) is not None

        authors_raw = payload.get("authors") or []
        author_names: list[str] = []
        for a in authors_raw[:5]:
            if isinstance(a, dict):
                name = (
                    a.get("display_name")
                    or (a.get("author") or {}).get("display_name")
                    or ""
                )
            else:
                name = str(a)
            if name:
                author_names.append(name)

        results.append({
            "id": short_id,
            "openalex_id": full_oa_id,
            "title": payload.get("title"),
            "authors": author_names,
            "year": payload.get("year"),
            "venue": payload.get("venue"),
            "score": round(float(hit.score), 4),
            "is_oa": bool(payload.get("is_open_access")),
            "local_paper_available": local_paper,
        })
    return results


_SEMANTIC_MAX = 200


@app.get("/search/semantic")
async def semantic_search_endpoint(q: str, limit: int = 10):
    """Search Oulucris publications by semantic similarity to the query."""
    if not q.strip():
        raise HTTPException(status_code=400, detail="Query 'q' must not be empty")
    requested_limit = max(1, limit)
    actual_limit = min(requested_limit, _SEMANTIC_MAX)
    limit_reason = "exact_match" if requested_limit <= _SEMANTIC_MAX else "capped_at_max"
    try:
        results = await _run_semantic_search(q, actual_limit)
    except Exception as exc:
        print(f"[semantic_search] {type(exc).__name__}: {exc}")
        raise HTTPException(
            status_code=503,
            detail=f"Semantic search unavailable: {type(exc).__name__}",
        )
    return {
        "query": q,
        "requested_limit": requested_limit,
        "actual_count": len(results),
        "limit_reason": limit_reason,
        "results": results,
    }
