"""
Local SQLite persistence layer for AIRA Scholar-KG.

Phase 2: source-provenance foundation.
All DB operations are fire-and-forget from the caller's perspective — any
exception is caught here and printed; the app falls back to live fetches.

Tables
------
canonical_paper     — parsed paper metadata + field provenance
canonical_author    — parsed author metadata + field provenance
source_raw_cache    — raw API response bodies for debugging
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ── Configuration ─────────────────────────────────────────────────────────────

CACHE_TTL_HOURS: int = int(os.getenv("CACHE_TTL_HOURS", "24"))

# DB lives at backend/data/aira_scholar.db  (created automatically on startup)
_DB_DIR = Path(__file__).parent / "data"
_DB_PATH = _DB_DIR / "aira_scholar.db"

# Module-level connection — None until init_db() succeeds
_conn: sqlite3.Connection | None = None

# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS canonical_paper (
    openalex_id         TEXT PRIMARY KEY,
    doi                 TEXT,
    semantic_scholar_id TEXT,
    arxiv_id            TEXT,
    openaire_id         TEXT,
    oulucris_id         TEXT,
    title               TEXT,
    abstract            TEXT,
    publication_year    INTEGER,
    venue               TEXT,
    oa_status           TEXT,
    pdf_url             TEXT,
    cited_by_count      INTEGER,
    cached_json         TEXT NOT NULL,
    field_provenance    TEXT NOT NULL DEFAULT '{}',
    last_refreshed_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS canonical_author (
    openalex_id         TEXT PRIMARY KEY,
    orcid               TEXT,
    display_name        TEXT,
    institution         TEXT,
    department          TEXT,
    cached_json         TEXT NOT NULL,
    field_provenance    TEXT NOT NULL DEFAULT '{}',
    last_refreshed_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_raw_cache (
    source_name   TEXT NOT NULL,
    entity_id     TEXT NOT NULL,
    raw_json      TEXT NOT NULL,
    fetched_at    TEXT NOT NULL,
    PRIMARY KEY (source_name, entity_id)
);

CREATE TABLE IF NOT EXISTS fulltext_cache (
    work_id             TEXT PRIMARY KEY,
    status              TEXT NOT NULL,
    text                TEXT,
    source_url          TEXT,
    candidates_tried    INTEGER NOT NULL DEFAULT 0,
    fetched_at          TEXT NOT NULL
);

-- ── Phase 6: OpenAIRE funding / linked-entity tables ─────────────────────────

CREATE TABLE IF NOT EXISTS canonical_project (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    openaire_id       TEXT UNIQUE NOT NULL,
    name              TEXT,
    acronym           TEXT,
    funder_name       TEXT,
    funder_id         TEXT,
    start_date        TEXT,
    end_date          TEXT,
    url               TEXT,
    field_provenance  TEXT NOT NULL DEFAULT '{}',
    last_refreshed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS canonical_funder (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    openaire_id       TEXT UNIQUE NOT NULL,
    name              TEXT,
    short_name        TEXT,
    field_provenance  TEXT NOT NULL DEFAULT '{}',
    last_refreshed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS canonical_dataset (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    openaire_id       TEXT UNIQUE NOT NULL,
    title             TEXT,
    url               TEXT,
    doi               TEXT,
    field_provenance  TEXT NOT NULL DEFAULT '{}',
    last_refreshed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS canonical_software (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    openaire_id       TEXT UNIQUE NOT NULL,
    title             TEXT,
    url               TEXT,
    field_provenance  TEXT NOT NULL DEFAULT '{}',
    last_refreshed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_project_link (
    paper_id   TEXT    NOT NULL,
    project_id INTEGER NOT NULL,
    source     TEXT    NOT NULL DEFAULT 'openaire',
    PRIMARY KEY (paper_id, project_id)
);

CREATE TABLE IF NOT EXISTS paper_dataset_link (
    paper_id   TEXT    NOT NULL,
    dataset_id INTEGER NOT NULL,
    source     TEXT    NOT NULL DEFAULT 'openaire',
    PRIMARY KEY (paper_id, dataset_id)
);

CREATE TABLE IF NOT EXISTS paper_software_link (
    paper_id    TEXT    NOT NULL,
    software_id INTEGER NOT NULL,
    source      TEXT    NOT NULL DEFAULT 'openaire',
    PRIMARY KEY (paper_id, software_id)
);

CREATE TABLE IF NOT EXISTS project_funder_link (
    project_id INTEGER NOT NULL,
    funder_id  INTEGER NOT NULL,
    PRIMARY KEY (project_id, funder_id)
);

"""


# ── Lifecycle ─────────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create the data directory and run schema migrations.  Called once on startup.

    If anything fails the module-level connection stays None and the app
    continues in live-only mode — no user-facing impact.
    """
    global _conn
    try:
        _DB_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        _conn = conn
        print(f"[DB] SQLite cache ready: {_DB_PATH}  (TTL={CACHE_TTL_HOURS}h)")
    except Exception as exc:
        print(f"[DB] WARNING: could not initialise SQLite cache ({type(exc).__name__}: {exc}). "
              "All requests will hit OpenAlex directly.")
        _conn = None


def _get_conn() -> sqlite3.Connection | None:
    return _conn


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_fresh_ttl(ts: str | None, hours: int) -> bool:
    """Return True if ts is within `hours` of now."""
    if not ts:
        return False
    try:
        recorded = datetime.fromisoformat(ts)
        return datetime.now(timezone.utc) - recorded < timedelta(hours=hours)
    except Exception:
        return False


def _is_fresh(ts: str | None) -> bool:
    """Return True if ts is within CACHE_TTL_HOURS of now."""
    return _is_fresh_ttl(ts, CACHE_TTL_HOURS)


def _save_raw(source_name: str, entity_id: str, raw: dict[str, Any], fetched_at: str) -> None:
    """Upsert a raw API response into source_raw_cache.  Errors are swallowed."""
    conn = _get_conn()
    if conn is None:
        return
    try:
        conn.execute(
            """
            INSERT INTO source_raw_cache (source_name, entity_id, raw_json, fetched_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source_name, entity_id) DO UPDATE SET
                raw_json   = excluded.raw_json,
                fetched_at = excluded.fetched_at
            """,
            (source_name, entity_id, json.dumps(raw, ensure_ascii=False), fetched_at),
        )
        conn.commit()
    except Exception as exc:
        print(f"[DB] raw cache write error ({source_name}/{entity_id}): {type(exc).__name__}: {exc}")


def get_cached_raw(
    source_name: str,
    entity_id: str,
    ttl_hours: int | None = None,
) -> dict[str, Any] | None:
    """Return a fresh raw cache entry for source_name/entity_id, else None.

    ttl_hours overrides the global CACHE_TTL_HOURS for this lookup only.
    """
    conn = _get_conn()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT raw_json, fetched_at FROM source_raw_cache "
            "WHERE source_name = ? AND entity_id = ?",
            (source_name, entity_id),
        ).fetchone()
        if row:
            fresh = (
                _is_fresh_ttl(row["fetched_at"], ttl_hours)
                if ttl_hours is not None
                else _is_fresh(row["fetched_at"])
            )
            if fresh:
                return json.loads(row["raw_json"])
    except Exception as exc:
        print(f"[DB] raw cache read error ({source_name}/{entity_id}): {type(exc).__name__}: {exc}")
    return None


def save_raw(source_name: str, entity_id: str, raw: dict[str, Any]) -> None:
    """Upsert a raw API response with the current timestamp."""
    _save_raw(source_name, entity_id, raw, _now_iso())


# ── canonical_paper ───────────────────────────────────────────────────────────

_PAPER_PROVENANCE_FIELDS = (
    "title", "abstract", "publication_year", "publication_date", "type",
    "language", "cited_by_count", "doi", "venue", "is_oa", "oa_status",
    "oa_url", "pdf_url", "authors", "topics", "referenced_works", "citing_works",
)


def get_cached_paper(openalex_id: str) -> dict[str, Any] | None:
    """Return the cached paper response if it is fresh, else None.

    Returns None on any DB error so the caller can fall through to a live fetch.
    """
    conn = _get_conn()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT cached_json, last_refreshed_at FROM canonical_paper WHERE openalex_id = ?",
            (openalex_id,),
        ).fetchone()
        if row and _is_fresh(row["last_refreshed_at"]):
            print(f"[DB] cache hit: paper {openalex_id}")
            return json.loads(row["cached_json"])
    except Exception as exc:
        print(f"[DB] read error (canonical_paper/{openalex_id}): {type(exc).__name__}: {exc}")
    return None


def save_paper(
    openalex_id: str,
    response: dict[str, Any],
    raw_openalex: dict[str, Any] | None = None,
) -> None:
    """Upsert a paper into canonical_paper and (optionally) source_raw_cache.

    Errors are swallowed — the caller never needs to handle them.
    """
    conn = _get_conn()
    if conn is None:
        return
    try:
        now = _now_iso()
        provenance = response.get("provenance") or {f: "openalex" for f in _PAPER_PROVENANCE_FIELDS}
        conn.execute(
            """
            INSERT INTO canonical_paper
                (openalex_id, doi, title, abstract, publication_year, venue,
                 oa_status, pdf_url, cited_by_count, cached_json,
                 field_provenance, last_refreshed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(openalex_id) DO UPDATE SET
                doi               = excluded.doi,
                title             = excluded.title,
                abstract          = excluded.abstract,
                publication_year  = excluded.publication_year,
                venue             = excluded.venue,
                oa_status         = excluded.oa_status,
                pdf_url           = excluded.pdf_url,
                cited_by_count    = excluded.cited_by_count,
                cached_json       = excluded.cached_json,
                field_provenance  = excluded.field_provenance,
                last_refreshed_at = excluded.last_refreshed_at
            """,
            (
                openalex_id,
                response.get("doi"),
                response.get("title"),
                response.get("abstract"),
                response.get("publication_year"),
                response.get("venue"),
                response.get("oa_status"),
                response.get("pdf_url"),
                response.get("cited_by_count"),
                json.dumps(response, ensure_ascii=False),
                json.dumps(provenance),
                now,
            ),
        )
        conn.commit()
        print(f"[DB] cache saved: paper {openalex_id}")
        if raw_openalex is not None:
            _save_raw("openalex", f"work:{openalex_id}", raw_openalex, now)
    except Exception as exc:
        print(f"[DB] write error (canonical_paper/{openalex_id}): {type(exc).__name__}: {exc}")


# ── canonical_author ──────────────────────────────────────────────────────────

_AUTHOR_PROVENANCE_FIELDS = (
    "display_name", "orcid", "works_count", "cited_by_count",
    "summary_stats", "last_known_institutions", "topics",
    "counts_by_year", "recent_works",
)


def get_cached_author(openalex_id: str) -> dict[str, Any] | None:
    """Return the cached author response if it is fresh, else None."""
    conn = _get_conn()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT cached_json, last_refreshed_at FROM canonical_author WHERE openalex_id = ?",
            (openalex_id,),
        ).fetchone()
        if row and _is_fresh(row["last_refreshed_at"]):
            print(f"[DB] cache hit: author {openalex_id}")
            return json.loads(row["cached_json"])
    except Exception as exc:
        print(f"[DB] read error (canonical_author/{openalex_id}): {type(exc).__name__}: {exc}")
    return None


def save_author(
    openalex_id: str,
    response: dict[str, Any],
    raw_openalex: dict[str, Any] | None = None,
) -> None:
    """Upsert an author into canonical_author and (optionally) source_raw_cache."""
    conn = _get_conn()
    if conn is None:
        return
    try:
        now = _now_iso()
        provenance = {f: "openalex" for f in _AUTHOR_PROVENANCE_FIELDS}
        insts = response.get("last_known_institutions") or []
        institution = insts[0].get("name", "") if insts else ""
        conn.execute(
            """
            INSERT INTO canonical_author
                (openalex_id, orcid, display_name, institution,
                 cached_json, field_provenance, last_refreshed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(openalex_id) DO UPDATE SET
                orcid             = excluded.orcid,
                display_name      = excluded.display_name,
                institution       = excluded.institution,
                cached_json       = excluded.cached_json,
                field_provenance  = excluded.field_provenance,
                last_refreshed_at = excluded.last_refreshed_at
            """,
            (
                openalex_id,
                response.get("orcid"),
                response.get("display_name"),
                institution,
                json.dumps(response, ensure_ascii=False),
                json.dumps(provenance),
                now,
            ),
        )
        conn.commit()
        print(f"[DB] cache saved: author {openalex_id}")
        if raw_openalex is not None:
            _save_raw("openalex", f"author:{openalex_id}", raw_openalex, now)
    except Exception as exc:
        print(f"[DB] write error (canonical_author/{openalex_id}): {type(exc).__name__}: {exc}")


# ── Debug helpers ─────────────────────────────────────────────────────────────

def get_debug_paper(openalex_id: str) -> dict[str, Any] | None:
    """Return the canonical_paper row as a plain dict (for the debug endpoint).

    The full cached_json blob is replaced with its character length to keep
    the response readable.
    """
    conn = _get_conn()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT * FROM canonical_paper WHERE openalex_id = ?",
            (openalex_id,),
        ).fetchone()
        if row:
            d = dict(row)
            d["field_provenance"] = json.loads(d.get("field_provenance") or "{}")
            d["cached_json_chars"] = len(d.pop("cached_json", "") or "")
            return d
    except Exception as exc:
        print(f"[DB] debug read error (paper/{openalex_id}): {type(exc).__name__}: {exc}")
    return None


def get_debug_author(openalex_id: str) -> dict[str, Any] | None:
    """Return the canonical_author row as a plain dict (for the debug endpoint)."""
    conn = _get_conn()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT * FROM canonical_author WHERE openalex_id = ?",
            (openalex_id,),
        ).fetchone()
        if row:
            d = dict(row)
            d["field_provenance"] = json.loads(d.get("field_provenance") or "{}")
            d["cached_json_chars"] = len(d.pop("cached_json", "") or "")
            return d
    except Exception as exc:
        print(f"[DB] debug read error (author/{openalex_id}): {type(exc).__name__}: {exc}")
    return None


# ── fulltext_cache ────────────────────────────────────────────────────────────

def get_cached_fulltext(work_id: str) -> dict[str, Any] | None:
    """Return a cached full-text result if fresh, else None.

    Returns None on any DB error so the caller falls back to live extraction.
    """
    conn = _get_conn()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT status, text, source_url, candidates_tried, fetched_at "
            "FROM fulltext_cache WHERE work_id = ?",
            (work_id,),
        ).fetchone()
        if row and _is_fresh(row["fetched_at"]):
            text = row["text"] or ""
            return {
                "work_id": work_id,
                "status": row["status"],
                "text": text,
                "text_length": len(text),
                "source_url": row["source_url"],
                "candidates_tried": row["candidates_tried"],
            }
    except Exception as exc:
        print(f"[DB] read error (fulltext_cache/{work_id}): {type(exc).__name__}: {exc}")
    return None


def save_fulltext(work_id: str, result: dict[str, Any]) -> None:
    """Upsert a full-text extraction result (success or failure) into fulltext_cache."""
    conn = _get_conn()
    if conn is None:
        return
    try:
        # Store None instead of empty string for failed extractions so the table stays compact.
        text_val = result.get("text") or None
        if result.get("status") != "ok":
            text_val = None
        conn.execute(
            """
            INSERT INTO fulltext_cache
                (work_id, status, text, source_url, candidates_tried, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(work_id) DO UPDATE SET
                status           = excluded.status,
                text             = excluded.text,
                source_url       = excluded.source_url,
                candidates_tried = excluded.candidates_tried,
                fetched_at       = excluded.fetched_at
            """,
            (
                work_id,
                result.get("status", "error"),
                text_val,
                result.get("source_url"),
                result.get("candidates_tried", 0),
                _now_iso(),
            ),
        )
        conn.commit()
        print(f"[DB] fulltext cache saved: {work_id} ({result.get('status')})")
    except Exception as exc:
        print(f"[DB] write error (fulltext_cache/{work_id}): {type(exc).__name__}: {exc}")


# ── db_status ─────────────────────────────────────────────────────────────────

def db_status() -> dict[str, Any]:
    """Return connectivity status and record counts.  Safe to call at any time."""
    conn = _get_conn()
    if conn is None:
        return {
            "status": "unavailable",
            "db_path": str(_DB_PATH),
            "reason": "init_db() did not complete successfully",
        }
    try:
        paper_count = conn.execute("SELECT COUNT(*) FROM canonical_paper").fetchone()[0]
        author_count = conn.execute("SELECT COUNT(*) FROM canonical_author").fetchone()[0]
        raw_count = conn.execute("SELECT COUNT(*) FROM source_raw_cache").fetchone()[0]
        ft_count = conn.execute("SELECT COUNT(*) FROM fulltext_cache").fetchone()[0]
        proj_count = conn.execute("SELECT COUNT(*) FROM canonical_project").fetchone()[0]
        funder_count = conn.execute("SELECT COUNT(*) FROM canonical_funder").fetchone()[0]
        dataset_count = conn.execute("SELECT COUNT(*) FROM canonical_dataset").fetchone()[0]
        software_count = conn.execute("SELECT COUNT(*) FROM canonical_software").fetchone()[0]
        return {
            "status": "ok",
            "db_path": str(_DB_PATH),
            "cache_ttl_hours": CACHE_TTL_HOURS,
            "cached_papers": paper_count,
            "cached_authors": author_count,
            "raw_cache_entries": raw_count,
            "cached_fulltexts": ft_count,
            "canonical_projects": proj_count,
            "canonical_funders": funder_count,
            "canonical_datasets": dataset_count,
            "canonical_software": software_count,
        }
    except Exception as exc:
        return {
            "status": "error",
            "db_path": str(_DB_PATH),
            "error": f"{type(exc).__name__}: {exc}",
        }


# ── OpenAIRE enrichment (Phase 6) ─────────────────────────────────────────────

def save_openaire_enrichment(
    openalex_id: str,
    projects: list[dict[str, Any]],
    datasets: list[dict[str, Any]],
    software: list[dict[str, Any]],
) -> None:
    """Persist OpenAIRE funding/linked-entity data.  Purely additive — never
    touches canonical_paper.  All errors are swallowed.

    Upsert order per project: canonical_funder → canonical_project →
    paper_project_link → project_funder_link.
    """
    conn = _get_conn()
    if conn is None:
        return
    try:
        now = _now_iso()
        prov = json.dumps({"source": "openaire"})

        for proj in projects:
            p_id = proj.get("id")
            if not p_id:
                continue

            # In Graph API v2 the per-project "funder" dict carries {id: name, name: name}
            # because the API only returns the funder name string — we use the name itself
            # as the stable openaire_id for canonical_funder (no real funder ID is provided).
            funder = proj.get("funder") or {}
            funder_name: str | None = funder.get("name") or None
            # Use the funder name as the canonical_funder key; strip to avoid whitespace dups.
            f_oa_id: str | None = funder_name.strip() if funder_name else None
            funder_internal: int | None = None

            if f_oa_id:
                conn.execute(
                    """
                    INSERT INTO canonical_funder
                        (openaire_id, name, short_name, field_provenance, last_refreshed_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(openaire_id) DO UPDATE SET
                        name              = excluded.name,
                        last_refreshed_at = excluded.last_refreshed_at
                    """,
                    (f_oa_id, funder_name, None, prov, now),
                )
                row = conn.execute(
                    "SELECT id FROM canonical_funder WHERE openaire_id = ?", (f_oa_id,)
                ).fetchone()
                if row:
                    funder_internal = row["id"]

            conn.execute(
                """
                INSERT INTO canonical_project
                    (openaire_id, name, acronym, funder_name, funder_id,
                     start_date, end_date, url, field_provenance, last_refreshed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(openaire_id) DO UPDATE SET
                    name              = excluded.name,
                    acronym           = excluded.acronym,
                    funder_name       = excluded.funder_name,
                    funder_id         = excluded.funder_id,
                    last_refreshed_at = excluded.last_refreshed_at
                """,
                (
                    p_id,
                    proj.get("name"),
                    proj.get("acronym"),
                    funder_name,
                    f_oa_id,
                    proj.get("startDate"),
                    proj.get("endDate"),
                    proj.get("websiteUrl"),
                    prov,
                    now,
                ),
            )
            proj_row = conn.execute(
                "SELECT id FROM canonical_project WHERE openaire_id = ?", (p_id,)
            ).fetchone()
            if proj_row:
                proj_internal = proj_row["id"]
                conn.execute(
                    "INSERT OR IGNORE INTO paper_project_link (paper_id, project_id, source) "
                    "VALUES (?, ?, 'openaire')",
                    (openalex_id, proj_internal),
                )
                if funder_internal:
                    conn.execute(
                        "INSERT OR IGNORE INTO project_funder_link (project_id, funder_id) "
                        "VALUES (?, ?)",
                        (proj_internal, funder_internal),
                    )

        for ds in datasets:
            ds_id = ds.get("id")
            if not ds_id:
                continue
            conn.execute(
                """
                INSERT INTO canonical_dataset
                    (openaire_id, title, url, doi, field_provenance, last_refreshed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(openaire_id) DO UPDATE SET
                    title             = excluded.title,
                    url               = excluded.url,
                    doi               = excluded.doi,
                    last_refreshed_at = excluded.last_refreshed_at
                """,
                (ds_id, ds.get("title"), ds.get("url"), ds.get("doi"), prov, now),
            )
            ds_row = conn.execute(
                "SELECT id FROM canonical_dataset WHERE openaire_id = ?", (ds_id,)
            ).fetchone()
            if ds_row:
                conn.execute(
                    "INSERT OR IGNORE INTO paper_dataset_link (paper_id, dataset_id, source) "
                    "VALUES (?, ?, 'openaire')",
                    (openalex_id, ds_row["id"]),
                )

        for sw in software:
            sw_id = sw.get("id")
            if not sw_id:
                continue
            conn.execute(
                """
                INSERT INTO canonical_software
                    (openaire_id, title, url, field_provenance, last_refreshed_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(openaire_id) DO UPDATE SET
                    title             = excluded.title,
                    url               = excluded.url,
                    last_refreshed_at = excluded.last_refreshed_at
                """,
                (sw_id, sw.get("title"), sw.get("url"), prov, now),
            )
            sw_row = conn.execute(
                "SELECT id FROM canonical_software WHERE openaire_id = ?", (sw_id,)
            ).fetchone()
            if sw_row:
                conn.execute(
                    "INSERT OR IGNORE INTO paper_software_link (paper_id, software_id, source) "
                    "VALUES (?, ?, 'openaire')",
                    (openalex_id, sw_row["id"]),
                )

        conn.commit()
        print(
            f"[DB] openaire enrichment saved: {openalex_id} "
            f"({len(projects)} project(s), {len(datasets)} dataset(s), "
            f"{len(software)} software item(s))"
        )
    except Exception as exc:
        print(
            f"[DB] write error (openaire/{openalex_id}): {type(exc).__name__}: {exc}"
        )


def get_openaire_enrichment(openalex_id: str) -> dict[str, Any]:
    """Return linked OpenAIRE entities for a paper from the normalised tables.

    Returns {"funding_projects": [...], "linked_datasets": [...], "linked_software": [...]}
    Always returns a dict with empty lists — never raises.
    """
    empty: dict[str, Any] = {
        "funding_projects": [],
        "linked_datasets": [],
        "linked_software": [],
    }
    conn = _get_conn()
    if conn is None:
        return empty
    try:
        proj_rows = conn.execute(
            """
            SELECT cp.openaire_id, cp.name, cp.acronym, cp.funder_name,
                   cp.funder_id, cp.start_date, cp.end_date, cp.url
            FROM   canonical_project cp
            JOIN   paper_project_link ppl ON cp.id = ppl.project_id
            WHERE  ppl.paper_id = ?
            """,
            (openalex_id,),
        ).fetchall()

        ds_rows = conn.execute(
            """
            SELECT cd.openaire_id, cd.title, cd.url, cd.doi
            FROM   canonical_dataset cd
            JOIN   paper_dataset_link pdl ON cd.id = pdl.dataset_id
            WHERE  pdl.paper_id = ?
            """,
            (openalex_id,),
        ).fetchall()

        sw_rows = conn.execute(
            """
            SELECT cs.openaire_id, cs.title, cs.url
            FROM   canonical_software cs
            JOIN   paper_software_link psl ON cs.id = psl.software_id
            WHERE  psl.paper_id = ?
            """,
            (openalex_id,),
        ).fetchall()

        return {
            "funding_projects": [
                {
                    "name": r["name"],
                    "acronym": r["acronym"],
                    "funder": r["funder_name"],
                    "funder_id": r["funder_id"],
                    "start_date": r["start_date"],
                    "end_date": r["end_date"],
                    "url": r["url"],
                    "openaire_id": r["openaire_id"],
                }
                for r in proj_rows
            ],
            "linked_datasets": [
                {
                    "title": r["title"],
                    "url": r["url"],
                    "doi": r["doi"],
                    "openaire_id": r["openaire_id"],
                }
                for r in ds_rows
            ],
            "linked_software": [
                {
                    "title": r["title"],
                    "url": r["url"],
                    "openaire_id": r["openaire_id"],
                }
                for r in sw_rows
            ],
        }
    except Exception as exc:
        print(
            f"[DB] read error (openaire/{openalex_id}): {type(exc).__name__}: {exc}"
        )
        return empty

