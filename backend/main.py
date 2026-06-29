import os
import re
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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

    # OpenAlex per-page max is 100.
    safe_limit = max(1, min(limit, 100))

    filters = ["authorships.institutions.ror:https://ror.org/03yj89h83"]

    if from_year:
        filters.append(f"from_publication_date:{from_year}-01-01")

    if to_year:
        filters.append(f"to_publication_date:{to_year}-12-31")

    params: dict[str, Any] = {
        "filter": ",".join(filters),
        "per-page": safe_limit,
    }

    if search:
        params["search"] = search
    else:
        params["sort"] = "cited_by_count:desc"

    if api_key:
        params["api_key"] = api_key

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            "https://api.openalex.org/works",
            params=params,
        )
        response.raise_for_status()
        data = response.json()

    nodes = {}
    edges = []
    paper_to_references: dict[str, list[str]] = {}
    referenced_ids: set[str] = set()

    for work in data.get("results", []):
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

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
    }


@app.get("/author/openalex/{author_id}")
async def get_author_insight(author_id: str):
    """Return enriched author metadata and recent works from OpenAlex."""
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

    return {
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
    }


# ── PDF / full-text helpers ───────────────────────────────────────────────────

# In-memory cache: work_id → extracted text.  Survives the session; cleared on restart.
_pdf_cache: dict[str, str] = {}


def _best_pdf_url(work: dict) -> str | None:
    """Return the best open-access PDF URL from an OpenAlex work record, or None."""
    # 1. best_oa_location.pdf_url
    best = work.get("best_oa_location") or {}
    if best.get("pdf_url"):
        return str(best["pdf_url"])
    # 2. open_access.oa_url (may be a direct PDF or a landing page)
    oa = work.get("open_access") or {}
    if oa.get("is_oa") and oa.get("oa_url"):
        return str(oa["oa_url"])
    # 3. primary_location.pdf_url
    primary = work.get("primary_location") or {}
    if primary.get("pdf_url"):
        return str(primary["pdf_url"])
    # 4. first location that has a pdf_url
    for loc in (work.get("locations") or []):
        if loc.get("pdf_url"):
            return str(loc["pdf_url"])
    return None


@app.get("/paper/openalex/{work_id}")
async def get_paper_insight(work_id: str):
    """Return enriched paper metadata and references from OpenAlex."""
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

    # Referenced works — fetch details for first 5
    ref_ids = [
        url.replace("https://openalex.org/", "")
        for url in (work.get("referenced_works") or [])[:5]
    ]
    referenced_works: list[Any] = []
    if ref_ids:
        async with httpx.AsyncClient(timeout=30.0) as client:
            ref_params: dict[str, Any] = {
                **base_params,
                "filter": f"openalex:{'|'.join(ref_ids)}",
                "per-page": 5,
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
                    referenced_works.append(
                        {
                            "title": ref.get("title") or "Untitled",
                            "year": ref.get("publication_year"),
                            "cited_by_count": ref.get("cited_by_count", 0),
                            "doi": ref.get("doi"),
                            "url": ref.get("id"),
                            "venue": ref_source.get("display_name"),
                        }
                    )

    return {
        "id": work_id,
        "title": work.get("title") or "Untitled",
        "publication_year": work.get("publication_year"),
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
    }


@app.get("/paper/openalex/{work_id}/fulltext")
async def get_paper_fulltext(work_id: str):
    """Download an OA PDF and return extracted plain text (max 30 000 chars)."""
    # Serve from cache if already fetched this session
    if work_id in _pdf_cache:
        text = _pdf_cache[work_id]
        return {
            "work_id": work_id,
            "source_url": "(cached)",
            "text": text,
            "text_length": len(text),
            "status": "ok",
        }

    # Re-fetch the work record to find the PDF URL
    api_key = os.getenv("OPENALEX_API_KEY")
    params: dict[str, Any] = {"api_key": api_key} if api_key else {}
    async with httpx.AsyncClient(timeout=30.0) as client:
        oa_resp = await client.get(
            f"https://api.openalex.org/works/{work_id}", params=params
        )
        if oa_resp.status_code != 200:
            return {
                "work_id": work_id,
                "source_url": None,
                "text": "OpenAlex lookup failed. Cannot retrieve PDF.",
                "text_length": 0,
                "status": "error",
            }
        work = oa_resp.json()

    pdf_url = _best_pdf_url(work)
    if not pdf_url:
        return {
            "work_id": work_id,
            "source_url": None,
            "text": "No open-access PDF was found for this paper.",
            "text_length": 0,
            "status": "no_pdf",
        }

    # Download the PDF
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            pdf_resp = await client.get(
                pdf_url,
                headers={"User-Agent": "AIRA-Scholar/1.0 (academic research tool)"},
            )
        if pdf_resp.status_code != 200:
            return {
                "work_id": work_id,
                "source_url": pdf_url,
                "text": f"PDF download failed (HTTP {pdf_resp.status_code}).",
                "text_length": 0,
                "status": "download_error",
            }
        pdf_bytes = pdf_resp.content
    except Exception as exc:
        print(f"PDF download error for {work_id}: {type(exc).__name__}: {exc}")
        return {
            "work_id": work_id,
            "source_url": pdf_url,
            "text": "Open-access PDF found, but could not be downloaded.",
            "text_length": 0,
            "status": "download_error",
        }

    # Extract text with PyMuPDF
    try:
        import fitz  # type: ignore[import]  # PyMuPDF
    except ImportError:
        return {
            "work_id": work_id,
            "source_url": pdf_url,
            "text": "PDF found but PyMuPDF is not installed. Run: pip install pymupdf",
            "text_length": 0,
            "status": "missing_dependency",
        }

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page_texts = [page.get_text() for page in doc]
        doc.close()
        raw = "\n".join(page_texts)
        full_text = "\n".join(line for line in raw.splitlines() if line.strip())
    except Exception as exc:
        print(f"PDF extraction error for {work_id}: {type(exc).__name__}: {exc}")
        return {
            "work_id": work_id,
            "source_url": pdf_url,
            "text": "Open-access PDF found, but text extraction failed.",
            "text_length": 0,
            "status": "extraction_error",
        }

    if len(full_text.strip()) < 100:
        return {
            "work_id": work_id,
            "source_url": pdf_url,
            "text": "Open-access PDF found, but text extraction failed.",
            "text_length": 0,
            "status": "extraction_error",
        }

    if len(full_text) > 30_000:
        full_text = full_text[:30_000]

    _pdf_cache[work_id] = full_text
    return {
        "work_id": work_id,
        "source_url": pdf_url,
        "text": full_text,
        "text_length": len(full_text),
        "status": "ok",
    }


# ── AIRA Assistant chat ──────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are AIRA Assistant, a research assistant for an academic knowledge graph.
Answer ONLY from the provided GRAPH CONTEXT. Be concise: 2–3 sentences maximum.

CRITICAL DISTINCTION — two different types of information:
  METADATA  : year, authors, venue, citation count, reference count, works count, h-index.
  CONTENT   : findings, numerical results, accuracy, percentages, scores — found in the abstract only.

RULES:
1. If the user asks about "numeric results", "accuracy", "percentage", "metrics", "performance",
   "sample size", "score", "outcome", or similar research results:
   - Look ONLY in the abstract or full paper text for numbers / percentages / scores.
   - Do NOT use citation count, year, reference count, or works count as research results.
   - If neither abstract nor full text contains numerical results, say:
     "The available abstract does not show specific numerical research results.
      Metadata values available: year=[X], citations=[Y]."
2. A section labeled "Full paper text (intent-matched excerpt ...)" means the full text IS loaded.
   Prefer it for questions about methods, limitations, sample size, findings, and conclusions.
   Use (source: full text) when answering from it.
3. If the labeled full text section IS present in the context but the specific answer
   (e.g. limitations, methods) is not visible in that excerpt, say:
   "The loaded excerpt for this paper does not clearly mention [topic]. The full paper may
    contain this information outside the shown excerpt."
   Do NOT say "full text has not been loaded" when the full text section is present.
4. If NO full text section is present in the context at all, and the user asks about
   methods/limitations/findings, say:
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
_OPENAI_MODEL = "gpt-4o-mini"


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
        sources.append("paper_insight")

    # Full paper text — intent-matched excerpt so the right section reaches the LLM
    if req.full_text:
        total = len(req.full_text)
        excerpt = _extract_intent_chunk(req.full_text, req.question, max_chars=3000).strip()
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
    r"|\bmeasurement(s)?\b|\bnumber(s)?\b",
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
# Numbers that look like research results: percentages, decimals, or 2+ digit integers
_RE_RESULT_NUM = re.compile(r"\d+\.?\d*\s*%|\d+\.\d+|\b[1-9]\d+\b")


def _extract_intent_chunk(full_text: str, question: str, max_chars: int = 3000) -> str:
    """Return the most relevant section of full_text for the given question.

    Detects whether the question asks about limitations, methods, or results, then
    searches the text for the corresponding section header and extracts surrounding content.
    Falls back to the beginning of the document when no match is found.
    """
    is_limitation = bool(_KW_LIMITATION.search(question))
    is_method = bool(_KW_METHOD.search(question))
    is_result = bool(_KW_NUMERIC.search(question)) and not bool(_KW_CITATION.search(question))

    if is_limitation:
        kw = _KW_LIMITATION
    elif is_method:
        kw = _KW_METHOD
    elif is_result:
        # section headers common in empirical papers
        kw = re.compile(
            r"\bresult(s)?\b|\bperformance\b|\bexperiment(s)?\b|\bevaluation\b|\baccuracy\b",
            re.IGNORECASE,
        )
    else:
        return full_text[:max_chars]

    match = kw.search(full_text)
    if match:
        start = max(0, match.start() - 150)
        end = min(len(full_text), start + max_chars)
        return full_text[start:end]

    # No matching section found — return beginning
    return full_text[:max_chars]


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

    # Numeric / research-result questions (but not "how many citations" which has is_cite too)
    if is_numeric and not is_cite and not is_author:
        full_text = req.full_text or ""
        abstract = pi.get("abstract") or ""
        # Prefer full text when loaded; fall back to abstract
        primary = full_text[:10_000] if full_text else abstract
        source_label = "full text" if full_text else "abstract"

        year = pi.get("publication_year") or sn.get("year")
        cites = pi.get("cited_by_count") if pi else None
        if cites is None:
            cites = sn.get("citations")
        meta_parts = []
        if year:
            meta_parts.append(f"year={year}")
        if cites is not None:
            meta_parts.append(f"citations={cites}")
        meta_str = ", ".join(meta_parts)

        if primary:
            if not _RE_RESULT_NUM.search(primary):
                suffix = f" Metadata values available: {meta_str}." if meta_str else ""
                return (
                    f"The available {source_label} does not show specific numerical research results."
                    f"{suffix} (source: paper {source_label})"
                )
            return None  # source has numbers — let LLM extract them
        if pi or sn:
            suffix = f" Metadata values available: {meta_str}." if meta_str else ""
            return (
                "The available metadata/abstract does not show specific numerical results."
                f"{suffix} (source: paper insight / selected node)"
            )
        return None  # no context at all — let LLM reply

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


async def _call_ollama(
    base_url: str, context: str, question: str, history: list[dict[str, str]]
) -> str:
    """Call Ollama local API and return the answer text."""
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
            "num_predict": 350,
            "num_ctx": 4096,
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
        return str(resp.json()["message"]["content"])
    except Exception as exc:
        print(f"AIRA Assistant error: could not parse Ollama response — {type(exc).__name__}")
        return "Received an unexpected response format from Ollama. Please try again."


async def _call_gemini(
    api_key: str, context: str, question: str, history: list[dict[str, str]]
) -> str:
    """Call Gemini REST API and return the answer text."""
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


async def _call_openai(
    api_key: str, context: str, question: str, history: list[dict[str, str]]
) -> str:
    """Call OpenAI Chat Completions API and return the answer text."""
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
async def chat(req: ChatRequest):
    """Answer a question about the current graph using the configured LLM provider."""
    provider, credential = _resolve_provider()

    if provider == "none" or not credential:
        return {
            "answer": (
                "AI assistant is not configured. "
                "Start Ollama, or add GEMINI_API_KEY / OPENAI_API_KEY to backend/.env "
                "and restart the backend."
            ),
            "sources_used": [],
        }

    context, sources_used = _build_chat_context(req)

    # Fast path: simple factual queries answered directly without LLM
    direct = _try_direct_answer(req)
    if direct is not None:
        return {"answer": direct, "sources_used": sources_used}

    history = req.history  # already validated as list[dict[str, str]] by Pydantic

    try:
        if provider == "ollama":
            answer = await _call_ollama(credential, context, req.question, history)
        elif provider == "gemini":
            answer = await _call_gemini(credential, context, req.question, history)
        else:
            answer = await _call_openai(credential, context, req.question, history)
    except Exception as exc:
        print(f"AIRA Assistant network error ({provider}): {type(exc).__name__}")
        return {
            "answer": (
                f"Could not reach the {provider.capitalize()} service. "
                "Check your connection and try again."
            ),
            "sources_used": [],
        }

    return {"answer": answer, "sources_used": sources_used}