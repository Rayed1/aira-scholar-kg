import os
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def chunk_list(items, chunk_size=25):
    for index in range(0, len(items), chunk_size):
        yield items[index : index + chunk_size]


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