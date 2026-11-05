"""
B2B Portfolio — Enterprise Client / Projects / Contacts module.

Logical entities (SQL tables kept as portfolio_* for backward compatibility):
  portfolio_clients   ≈ b2b_portfolio_clients   (Group Account / Developer)
  portfolio_projects  ≈ b2b_portfolio_projects  (Construction objects)
  portfolio_contacts  ≈ b2b_portfolio_contacts  (Decision makers)

Endpoints under /api/b2b/...
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

log = logging.getLogger("leads.portfolio")

_MODULE_DIR = Path(__file__).resolve().parent
_DEFAULT_DB = _MODULE_DIR / "data" / "portfolio.db"

FINAL_FUNNEL_STAGE = "implementation"

CONSTRUCTION_STAGES = (
    "zero_cycle",
    "facade",
    "interior",
    "handover",
)
PROJECT_STATUSES = ("planning", "supply", "in_progress", "completed", "active")
CLIENT_CATEGORIES = ("A", "B", "C")

router = APIRouter(prefix="/api/b2b", tags=["b2b-portfolio"])


def _db_path() -> Path:
    raw = (os.getenv("PORTFOLIO_DB_PATH") or "").strip()
    return Path(raw) if raw else _DEFAULT_DB


def _now_ms() -> int:
    return int(time.time() * 1000)


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(r[1]) for r in rows}


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = _table_columns(conn, table)
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_portfolio_db() -> Path:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS portfolio_clients (
                id TEXT PRIMARY KEY,
                source_deal_id TEXT,
                title TEXT,
                company TEXT,
                contact_name TEXT,
                phone TEXT,
                email TEXT,
                client_type TEXT,
                responsible_login TEXT,
                responsible_name TEXT,
                products_json TEXT,
                budget REAL DEFAULT 0,
                offer_amount REAL DEFAULT 0,
                activity_status TEXT DEFAULT 'active',
                source_snapshot_json TEXT,
                history_json TEXT,
                created_at INTEGER,
                updated_at INTEGER,
                transformed_at INTEGER,
                transformed_by TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_portfolio_clients_deal
                ON portfolio_clients(source_deal_id);
            CREATE INDEX IF NOT EXISTS idx_portfolio_clients_resp
                ON portfolio_clients(responsible_login);

            CREATE TABLE IF NOT EXISTS portfolio_projects (
                id TEXT PRIMARY KEY,
                portfolio_client_id TEXT NOT NULL,
                name TEXT NOT NULL,
                deal_date TEXT,
                volume REAL DEFAULT 0,
                volume_unit TEXT DEFAULT '',
                contract_amount REAL DEFAULT 0,
                partner_commission REAL DEFAULT 0,
                details TEXT DEFAULT '',
                status TEXT DEFAULT 'planning',
                created_at INTEGER,
                updated_at INTEGER,
                created_by TEXT,
                FOREIGN KEY (portfolio_client_id)
                    REFERENCES portfolio_clients(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_portfolio_projects_client
                ON portfolio_projects(portfolio_client_id);

            CREATE TABLE IF NOT EXISTS portfolio_contacts (
                id TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                project_id TEXT,
                full_name TEXT NOT NULL,
                role_position TEXT DEFAULT '',
                phone TEXT DEFAULT '',
                telegram TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_at INTEGER,
                updated_at INTEGER,
                FOREIGN KEY (client_id)
                    REFERENCES portfolio_clients(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_portfolio_contacts_client
                ON portfolio_contacts(client_id);

            CREATE TABLE IF NOT EXISTS portfolio_shipments (
                id TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                project_id TEXT,
                product_name TEXT DEFAULT '',
                volume REAL DEFAULT 0,
                volume_unit TEXT DEFAULT 'л',
                unit_price REAL DEFAULT 0,
                total_amount REAL DEFAULT 0,
                date TEXT DEFAULT '',
                invoice_name TEXT DEFAULT '',
                note TEXT DEFAULT '',
                created_at INTEGER,
                created_by TEXT DEFAULT '',
                FOREIGN KEY (client_id)
                    REFERENCES portfolio_clients(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_portfolio_shipments_client
                ON portfolio_shipments(client_id);

            CREATE TABLE IF NOT EXISTS portfolio_transactions (
                id TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                project_id TEXT,
                type TEXT NOT NULL,
                amount REAL DEFAULT 0,
                method TEXT DEFAULT 'transfer',
                date TEXT DEFAULT '',
                purpose TEXT DEFAULT '',
                note TEXT DEFAULT '',
                created_at INTEGER,
                created_by TEXT DEFAULT '',
                FOREIGN KEY (client_id)
                    REFERENCES portfolio_clients(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_portfolio_transactions_client
                ON portfolio_transactions(client_id);
            """
        )
        # Enterprise columns on clients (b2b_portfolio_clients fields)
        for col, ddl in (
            ("inn", "TEXT DEFAULT ''"),
            ("address", "TEXT DEFAULT ''"),
            ("client_category", "TEXT DEFAULT 'B'"),
            ("main_contact_person", "TEXT DEFAULT ''"),
            ("responsible_id", "TEXT DEFAULT ''"),
            ("ownership_form", "TEXT DEFAULT ''"),
            ("ceo_name", "TEXT DEFAULT ''"),
            ("annual_paint_volume", "REAL DEFAULT 0"),
            ("hq_phone", "TEXT DEFAULT ''"),
        ):
            _ensure_column(conn, "portfolio_clients", col, ddl)

        # Enterprise columns on projects
        for col, ddl in (
            ("address_orientir", "TEXT DEFAULT ''"),
            ("construction_stage", "TEXT DEFAULT 'zero_cycle'"),
            ("painting_area_sqm", "REAL DEFAULT 0"),
            ("budget_sum", "REAL DEFAULT 0"),
            ("products_spec", "TEXT DEFAULT ''"),
            ("start_date", "TEXT DEFAULT ''"),
            ("end_date", "TEXT DEFAULT ''"),
            ("site_manager_contact", "TEXT DEFAULT ''"),
        ):
            _ensure_column(conn, "portfolio_projects", col, ddl)

        # Friendly views matching prompt table names
        conn.executescript(
            """
            DROP VIEW IF EXISTS b2b_portfolio_clients;
            CREATE VIEW b2b_portfolio_clients AS SELECT * FROM portfolio_clients;
            DROP VIEW IF EXISTS b2b_portfolio_projects;
            CREATE VIEW b2b_portfolio_projects AS SELECT * FROM portfolio_projects;
            DROP VIEW IF EXISTS b2b_portfolio_contacts;
            CREATE VIEW b2b_portfolio_contacts AS SELECT * FROM portfolio_contacts;
            DROP VIEW IF EXISTS b2b_portfolio_shipments;
            CREATE VIEW b2b_portfolio_shipments AS SELECT * FROM portfolio_shipments;
            DROP VIEW IF EXISTS b2b_portfolio_transactions;
            CREATE VIEW b2b_portfolio_transactions AS SELECT * FROM portfolio_transactions;
            """
        )
        conn.commit()
    log.info("Portfolio SQLite ready: %s", path)
    return path


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _parse_json(raw: Any, default: Any) -> Any:
    if raw is None or raw == "":
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _contact_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "clientId": row.get("client_id"),
        "projectId": row.get("project_id"),
        "fullName": row.get("full_name") or "",
        "rolePosition": row.get("role_position") or "",
        "phone": row.get("phone") or "",
        "telegram": row.get("telegram") or "",
        "notes": row.get("notes") or "",
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def _project_public(row: dict[str, Any]) -> dict[str, Any]:
    budget = float(row.get("budget_sum") or 0) or float(row.get("contract_amount") or 0)
    area = float(row.get("painting_area_sqm") or 0) or float(row.get("volume") or 0)
    return {
        "id": row["id"],
        "portfolioClientId": row["portfolio_client_id"],
        "clientId": row["portfolio_client_id"],
        "name": row.get("name") or "",
        "projectName": row.get("name") or "",
        "dealDate": row.get("deal_date") or "",
        "volume": float(row.get("volume") or 0),
        "volumeUnit": row.get("volume_unit") or "",
        "contractAmount": float(row.get("contract_amount") or 0),
        "partnerCommission": float(row.get("partner_commission") or 0),
        "details": row.get("details") or "",
        "status": row.get("status") or "planning",
        "addressOrientir": row.get("address_orientir") or "",
        "constructionStage": row.get("construction_stage") or "zero_cycle",
        "paintingAreaSqm": area,
        "budgetSum": budget,
        "productsSpec": row.get("products_spec") or "",
        "startDate": row.get("start_date") or "",
        "endDate": row.get("end_date") or "",
        "siteManagerContact": row.get("site_manager_contact") or "",
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
        "createdBy": row.get("created_by") or "",
    }


def _client_stats(projects: list[dict[str, Any]]) -> dict[str, Any]:
    active_statuses = {"planning", "supply", "in_progress", "active"}
    total_budget = sum(float(p.get("budgetSum") or p.get("contractAmount") or 0) for p in projects)
    total_area = sum(float(p.get("paintingAreaSqm") or p.get("volume") or 0) for p in projects)
    active_count = sum(1 for p in projects if (p.get("status") or "") in active_statuses)
    return {
        "projectsCount": len(projects),
        "activeProjectsCount": active_count,
        "totalBudgetSum": total_budget,
        "totalPaintingAreaSqm": total_area,
        "totalContractAmount": total_budget,
    }


def _client_public(
    row: dict[str, Any],
    *,
    projects: Optional[list] = None,
    contacts: Optional[list] = None,
) -> dict[str, Any]:
    projs = projects if projects is not None else []
    stats = _client_stats(projs) if projects is not None else None
    return {
        "id": row["id"],
        "sourceDealId": row.get("source_deal_id"),
        "transformedFromLeadId": row.get("source_deal_id"),
        "title": row.get("title") or "",
        "company": row.get("company") or row.get("title") or "",
        "inn": row.get("inn") or "",
        "address": row.get("address") or "",
        "clientCategory": row.get("client_category") or "B",
        "mainContactPerson": row.get("main_contact_person")
        or row.get("contact_name")
        or "",
        "contactName": row.get("contact_name") or "",
        "phone": row.get("phone") or row.get("hq_phone") or "",
        "hqPhone": row.get("hq_phone") or row.get("phone") or "",
        "email": row.get("email") or "",
        "clientType": row.get("client_type") or "",
        "ownershipForm": row.get("ownership_form") or "",
        "ceoName": row.get("ceo_name") or "",
        "annualPaintVolume": float(row.get("annual_paint_volume") or 0),
        "responsibleId": row.get("responsible_id") or row.get("responsible_login") or "",
        "responsibleLogin": row.get("responsible_login") or "",
        "responsibleName": row.get("responsible_name") or "",
        "products": _parse_json(row.get("products_json"), []),
        "budget": float(row.get("budget") or 0),
        "offerAmount": float(row.get("offer_amount") or 0),
        "activityStatus": row.get("activity_status") or "active",
        "sourceSnapshot": _parse_json(row.get("source_snapshot_json"), {}),
        "history": _parse_json(row.get("history_json"), []),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
        "transformedAt": row.get("transformed_at"),
        "transformedBy": row.get("transformed_by") or "",
        "projects": projects if projects is not None else None,
        "contacts": contacts if contacts is not None else None,
        "stats": stats,
    }


def _load_client_bundle(conn: sqlite3.Connection, client_id: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM portfolio_clients WHERE id = ?", (client_id,)
    ).fetchone()
    if not row:
        return None
    d = _row_to_dict(row) or {}
    projs = [
        _project_public(_row_to_dict(p) or {})
        for p in conn.execute(
            "SELECT * FROM portfolio_projects WHERE portfolio_client_id = ? "
            "ORDER BY updated_at DESC, created_at DESC",
            (client_id,),
        ).fetchall()
    ]
    contacts = [
        _contact_public(_row_to_dict(c) or {})
        for c in conn.execute(
            "SELECT * FROM portfolio_contacts WHERE client_id = ? "
            "ORDER BY created_at DESC",
            (client_id,),
        ).fetchall()
    ]
    return _client_public(d, projects=projs, contacts=contacts)


# ---------- Pydantic ----------


class TransformBody(BaseModel):
    actor: str = ""
    actor_login: str = ""
    actor_id: str = ""
    deal: Optional[dict[str, Any]] = None


class ClientUpdate(BaseModel):
    title: Optional[str] = None
    company: Optional[str] = None
    inn: Optional[str] = None
    address: Optional[str] = None
    clientCategory: Optional[str] = None
    mainContactPerson: Optional[str] = None
    contactName: Optional[str] = None
    phone: Optional[str] = None
    hqPhone: Optional[str] = None
    email: Optional[str] = None
    ownershipForm: Optional[str] = None
    ceoName: Optional[str] = None
    annualPaintVolume: Optional[float] = None
    activityStatus: Optional[str] = None
    responsibleLogin: Optional[str] = None
    responsibleName: Optional[str] = None
    responsibleId: Optional[str] = None


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1)
    projectName: str = ""
    dealDate: str = ""
    volume: float = 0
    volumeUnit: str = "м²"
    contractAmount: float = 0
    partnerCommission: float = 0
    details: str = ""
    status: str = "planning"
    addressOrientir: str = ""
    constructionStage: str = "zero_cycle"
    paintingAreaSqm: float = 0
    budgetSum: float = 0
    productsSpec: str = ""
    startDate: str = ""
    endDate: str = ""
    siteManagerContact: str = ""
    createdBy: str = ""


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    projectName: Optional[str] = None
    dealDate: Optional[str] = None
    volume: Optional[float] = None
    volumeUnit: Optional[str] = None
    contractAmount: Optional[float] = None
    partnerCommission: Optional[float] = None
    details: Optional[str] = None
    status: Optional[str] = None
    addressOrientir: Optional[str] = None
    constructionStage: Optional[str] = None
    paintingAreaSqm: Optional[float] = None
    budgetSum: Optional[float] = None
    productsSpec: Optional[str] = None
    startDate: Optional[str] = None
    endDate: Optional[str] = None
    siteManagerContact: Optional[str] = None


class ContactCreate(BaseModel):
    fullName: str = Field(..., min_length=1)
    rolePosition: str = ""
    phone: str = ""
    telegram: str = ""
    notes: str = ""
    projectId: Optional[str] = None


class ContactUpdate(BaseModel):
    fullName: Optional[str] = None
    rolePosition: Optional[str] = None
    phone: Optional[str] = None
    telegram: Optional[str] = None
    notes: Optional[str] = None
    projectId: Optional[str] = None


# ---------- Funnel / transform ----------


def _normalize_stage(stage: Any) -> str:
    raw = str(stage or "").strip().lower()
    aliases = {
        "реализация": FINAL_FUNNEL_STAGE,
        "implementation": FINAL_FUNNEL_STAGE,
        "client": FINAL_FUNNEL_STAGE,
        "клиент": FINAL_FUNNEL_STAGE,
        "успешно реализовано": FINAL_FUNNEL_STAGE,
    }
    return aliases.get(raw, raw.replace(" ", "_"))


def validate_deal_funnel_complete(deal: Mapping[str, Any]) -> tuple[bool, str]:
    stage = _normalize_stage(deal.get("stage") or deal.get("stageId"))
    if stage != FINAL_FUNNEL_STAGE:
        return False, (
            "Сделка должна быть на финальном этапе «Клиент / Реализация» "
            f"(сейчас: {deal.get('stage') or stage or '—'})"
        )
    return True, "ok"


async def _fetch_deal_from_firebase(deal_id: str) -> Optional[dict[str, Any]]:
    try:
        import httpx
        from api import _fb_get, crm_configured
    except ImportError:
        return None
    if not crm_configured():
        return None
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            doc = await _fb_get(client, f"deals/{deal_id}")
        if isinstance(doc, dict):
            doc.setdefault("id", deal_id)
            return doc
    except Exception as exc:  # noqa: BLE001
        log.warning("Firebase deal fetch failed id=%s: %s", deal_id, exc)
    return None


def _deal_address(deal: Mapping[str, Any]) -> str:
    qual = deal.get("qualification") or {}
    if isinstance(qual, dict):
        addr = str(qual.get("addressLandmark") or "").strip()
        if addr:
            return addr
    return str(
        deal.get("addressLandmark")
        or deal.get("address")
        or deal.get("objectAddress")
        or ""
    ).strip()


def _deal_object_name(deal: Mapping[str, Any]) -> str:
    qual = deal.get("qualification") or {}
    if isinstance(qual, dict):
        obj = str(qual.get("object") or "").strip()
        if obj:
            return obj
    return str(deal.get("title") or deal.get("company") or "Объект").strip()


def _build_client_from_deal(
    deal: dict[str, Any],
    *,
    actor: str,
    actor_login: str,
    actor_id: str,
) -> dict[str, Any]:
    now = _now_ms()
    products = deal.get("products") or deal.get("productTypes") or []
    if isinstance(products, str):
        products = [products]
    history = deal.get("statusHistory") or deal.get("activity") or []
    if not isinstance(history, list):
        history = []
    contact = deal.get("contacts") or deal.get("contact") or {}
    if not isinstance(contact, dict):
        contact = {}
    qual = deal.get("qualification") or {}
    if not isinstance(qual, dict):
        qual = {}
    resp_login = str(
        deal.get("responsibleLogin")
        or deal.get("responsible_login")
        or deal.get("authorLogin")
        or actor_login
        or ""
    )
    resp_id = str(
        deal.get("responsibleId")
        or deal.get("responsible_id")
        or actor_id
        or resp_login
        or ""
    )
    contact_name = str(
        deal.get("contactName")
        or qual.get("contact")
        or deal.get("name")
        or contact.get("name")
        or ""
    )
    return {
        "id": "pc_" + uuid.uuid4().hex[:12],
        "source_deal_id": str(deal.get("id") or ""),
        "title": str(deal.get("title") or deal.get("company") or "Без названия"),
        "company": str(deal.get("company") or deal.get("title") or ""),
        "contact_name": contact_name,
        "main_contact_person": contact_name or str(qual.get("lpr") or ""),
        "phone": str(deal.get("phone") or contact.get("phone") or ""),
        "hq_phone": str(deal.get("phone") or contact.get("phone") or ""),
        "email": str(deal.get("email") or contact.get("email") or ""),
        "inn": str(qual.get("inn") or deal.get("inn") or ""),
        "address": _deal_address(deal),
        "client_category": "B",
        "client_type": str(deal.get("clientType") or ""),
        "ownership_form": "",
        "ceo_name": "",
        "annual_paint_volume": 0,
        "responsible_id": resp_id,
        "responsible_login": resp_login,
        "responsible_name": str(
            deal.get("responsibleName")
            or deal.get("responsible_name")
            or deal.get("responsible")
            or deal.get("author")
            or actor
            or ""
        ),
        "products_json": json.dumps(list(products), ensure_ascii=False),
        "budget": float(deal.get("budget") or 0),
        "offer_amount": float(deal.get("offerAmount") or deal.get("budget") or 0),
        "activity_status": "active",
        "source_snapshot_json": json.dumps(deal, ensure_ascii=False, default=str),
        "history_json": json.dumps(history, ensure_ascii=False, default=str),
        "created_at": now,
        "updated_at": now,
        "transformed_at": now,
        "transformed_by": actor_login or actor or "",
    }


def _insert_seed_project(
    conn: sqlite3.Connection,
    *,
    client_id: str,
    deal: dict[str, Any],
    client_row: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    now = _now_ms()
    proj_id = "pp_" + uuid.uuid4().hex[:12]
    address = _deal_address(deal)
    obj_name = _deal_object_name(deal)
    products = deal.get("products") or []
    if isinstance(products, list):
        products_spec = ", ".join(str(p) for p in products)
    else:
        products_spec = str(products or "")
    deal_date = ""
    dates = deal.get("dates") or {}
    if isinstance(dates, dict):
        deal_date = str(
            dates.get("Реализация")
            or dates.get("implementation")
            or dates.get("closed")
            or ""
        )[:32]
    budget = float(client_row.get("offer_amount") or client_row.get("budget") or 0)
    qual = deal.get("qualification") or {}
    site_mgr = ""
    if isinstance(qual, dict):
        site_mgr = str(qual.get("lpr") or qual.get("contact") or "")
    row = {
        "id": proj_id,
        "portfolio_client_id": client_id,
        "name": obj_name,
        "deal_date": deal_date,
        "volume": 0,
        "volume_unit": "м²",
        "contract_amount": budget,
        "partner_commission": float(
            deal.get("commission") or deal.get("partnerCommission") or 0
        ),
        "details": "Создано при трансформации из сделки " + str(deal.get("id") or ""),
        "status": "supply",
        "address_orientir": address,
        "construction_stage": "facade",
        "painting_area_sqm": 0,
        "budget_sum": budget,
        "products_spec": products_spec,
        "start_date": deal_date,
        "end_date": "",
        "site_manager_contact": site_mgr,
        "created_at": now,
        "updated_at": now,
        "created_by": actor,
    }
    cols = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    conn.execute(
        f"INSERT INTO portfolio_projects ({cols}) VALUES ({placeholders})",
        tuple(row.values()),
    )
    return row


# ---------- Routes ----------


@router.get("/portfolio")
@router.get("/portfolio/")
def portfolio_root() -> dict[str, Any]:
    """Alias health + empty-safe client list for CRM probes."""
    init_portfolio_db()
    result = list_portfolio_clients(
        responsible="",
        responsible_id="",
        status="",
        category="",
        q="",
        construction_stage="",
        date_from="",
        date_to="",
    )
    return {
        "ok": True,
        "clients": result.get("clients") or [],
        "count": result.get("count") or 0,
        "db": str(_db_path()),
    }


@router.post("/portfolio/clients")
async def create_portfolio_client(body: TransformBody) -> dict[str, Any]:
    deal = body.deal if isinstance(body.deal, dict) else None
    if not deal or not deal.get("id"):
        raise HTTPException(400, "Body must include deal with id")
    return await transform_deal_to_portfolio(str(deal["id"]), body)


@router.post("/leads/{deal_id}/transform")
async def transform_deal_to_portfolio(deal_id: str, body: TransformBody) -> dict[str, Any]:
    init_portfolio_db()
    deal = body.deal if isinstance(body.deal, dict) else None
    if not deal:
        deal = await _fetch_deal_from_firebase(deal_id)
    if not deal:
        raise HTTPException(404, f"Deal not found: {deal_id}")
    deal.setdefault("id", deal_id)

    ok, reason = validate_deal_funnel_complete(deal)
    if not ok:
        raise HTTPException(400, reason)

    # Firebase deal flags must NOT be written unless SQLite commit succeeds.
    with _connect() as conn:
        existing = conn.execute(
            "SELECT id FROM portfolio_clients WHERE source_deal_id = ? LIMIT 1",
            (deal_id,),
        ).fetchone()
        if existing:
            bundled = _load_client_bundle(conn, existing["id"])
            return {"ok": True, "alreadyTransformed": True, "client": bundled}

        row = _build_client_from_deal(
            deal,
            actor=body.actor,
            actor_login=body.actor_login,
            actor_id=body.actor_id,
        )
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        conn.execute(
            f"INSERT INTO portfolio_clients ({cols}) VALUES ({placeholders})",
            tuple(row.values()),
        )

        # Auto-create first object from address / object fields
        proj_row = _insert_seed_project(
            conn,
            client_id=row["id"],
            deal=deal,
            client_row=row,
            actor=body.actor_login or body.actor or "",
        )

        # Seed company contact from qualification LPR / contact
        qual = deal.get("qualification") or {}
        if not isinstance(qual, dict):
            qual = {}
        contact_name = row.get("main_contact_person") or row.get("contact_name") or ""
        if contact_name:
            now = _now_ms()
            conn.execute(
                """
                INSERT INTO portfolio_contacts (
                    id, client_id, project_id, full_name, role_position,
                    phone, telegram, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "pct_" + uuid.uuid4().hex[:12],
                    row["id"],
                    None,
                    contact_name,
                    "ЛПР" if qual.get("lpr") else "Контакт",
                    row.get("phone") or "",
                    "",
                    "Импорт из сделки " + deal_id,
                    now,
                    now,
                ),
            )

        conn.commit()
        client = _load_client_bundle(conn, row["id"])

    try:
        import httpx
        from api import _fb_patch, crm_configured

        if crm_configured():
            async with httpx.AsyncClient(timeout=20.0) as client_http:
                await _fb_patch(
                    client_http,
                    f"deals/{deal_id}",
                    {
                        "portfolioClientId": row["id"],
                        "portfolioTransformedAt": row["transformed_at"],
                        "updatedAt": _now_ms(),
                    },
                )
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not mark deal portfolioTransformed: %s", exc)

    return {
        "ok": True,
        "alreadyTransformed": False,
        "client": client,
        "seedProjectId": proj_row.get("id"),
    }


@router.get("/portfolio/clients")
def list_portfolio_clients(
    responsible: str = Query(""),
    responsible_id: str = Query("", alias="responsibleId"),
    status: str = Query(""),
    category: str = Query(""),
    q: str = Query("", description="Search title/inn/project"),
    construction_stage: str = Query("", alias="constructionStage"),
    date_from: str = Query("", alias="dateFrom"),
    date_to: str = Query("", alias="dateTo"),
) -> dict[str, Any]:
    init_portfolio_db()
    sql = "SELECT * FROM portfolio_clients WHERE 1=1"
    params: list[Any] = []
    if responsible_id.strip():
        sql += " AND (responsible_id = ? OR responsible_login = ?)"
        params.extend([responsible_id.strip(), responsible_id.strip()])
    elif responsible.strip():
        sql += " AND (responsible_login = ? OR responsible_name = ? OR responsible_id = ?)"
        params.extend([responsible.strip(), responsible.strip(), responsible.strip()])
    if status.strip():
        sql += " AND activity_status = ?"
        params.append(status.strip())
    if category.strip():
        sql += " AND client_category = ?"
        params.append(category.strip().upper())
    sql += " ORDER BY updated_at DESC"

    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        clients = []
        q_norm = q.strip().lower()
        stage_filter = construction_stage.strip()
        for r in rows:
            d = _row_to_dict(r) or {}
            # date filter on transformed_at (ms)
            if date_from.strip() or date_to.strip():
                ts = int(d.get("transformed_at") or d.get("created_at") or 0)
                if date_from.strip():
                    try:
                        from_ms = int(
                            datetime.strptime(date_from[:10], "%Y-%m-%d")
                            .replace(tzinfo=timezone.utc)
                            .timestamp()
                            * 1000
                        )
                        if ts and ts < from_ms:
                            continue
                    except ValueError:
                        pass
                if date_to.strip():
                    try:
                        to_ms = int(
                            datetime.strptime(date_to[:10], "%Y-%m-%d")
                            .replace(tzinfo=timezone.utc)
                            .timestamp()
                            * 1000
                        ) + 86400000
                        if ts and ts > to_ms:
                            continue
                    except ValueError:
                        pass

            projs = [
                _project_public(_row_to_dict(p) or {})
                for p in conn.execute(
                    "SELECT * FROM portfolio_projects WHERE portfolio_client_id = ? "
                    "ORDER BY updated_at DESC",
                    (d["id"],),
                ).fetchall()
            ]
            if stage_filter:
                projs_match = [
                    p for p in projs if (p.get("constructionStage") or "") == stage_filter
                ]
                if not projs_match:
                    continue
            if q_norm:
                hay = " ".join(
                    [
                        str(d.get("title") or ""),
                        str(d.get("company") or ""),
                        str(d.get("inn") or ""),
                        str(d.get("phone") or ""),
                        " ".join(p.get("name") or "" for p in projs),
                        " ".join(p.get("addressOrientir") or "" for p in projs),
                    ]
                ).lower()
                if q_norm not in hay:
                    continue
            contacts = [
                _contact_public(_row_to_dict(c) or {})
                for c in conn.execute(
                    "SELECT * FROM portfolio_contacts WHERE client_id = ?",
                    (d["id"],),
                ).fetchall()
            ]
            clients.append(_client_public(d, projects=projs, contacts=contacts))

    return {"ok": True, "clients": clients, "count": len(clients)}


@router.get("/portfolio/clients/{client_id}")
def get_portfolio_client(client_id: str) -> dict[str, Any]:
    with _connect() as conn:
        client = _load_client_bundle(conn, client_id)
    if not client:
        raise HTTPException(404, "Portfolio client not found")
    return {"ok": True, "client": client}


@router.put("/portfolio/clients/{client_id}")
def update_portfolio_client(client_id: str, body: ClientUpdate) -> dict[str, Any]:
    mapping = {
        "title": body.title,
        "company": body.company,
        "inn": body.inn,
        "address": body.address,
        "client_category": body.clientCategory,
        "main_contact_person": body.mainContactPerson,
        "contact_name": body.contactName,
        "phone": body.phone,
        "hq_phone": body.hqPhone,
        "email": body.email,
        "ownership_form": body.ownershipForm,
        "ceo_name": body.ceoName,
        "annual_paint_volume": body.annualPaintVolume,
        "activity_status": body.activityStatus,
        "responsible_login": body.responsibleLogin,
        "responsible_name": body.responsibleName,
        "responsible_id": body.responsibleId,
    }
    sets = []
    params: list[Any] = []
    for col, val in mapping.items():
        if val is not None:
            sets.append(f"{col} = ?")
            params.append(val)
    if not sets:
        raise HTTPException(400, "No fields to update")
    sets.append("updated_at = ?")
    params.append(_now_ms())
    params.append(client_id)
    with _connect() as conn:
        cur = conn.execute(
            f"UPDATE portfolio_clients SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Portfolio client not found")
        conn.commit()
        client = _load_client_bundle(conn, client_id)
    return {"ok": True, "client": client}


@router.post("/portfolio/clients/{client_id}/projects")
def create_portfolio_project(client_id: str, body: ProjectCreate) -> dict[str, Any]:
    with _connect() as conn:
        exists = conn.execute(
            "SELECT id FROM portfolio_clients WHERE id = ?", (client_id,)
        ).fetchone()
        if not exists:
            raise HTTPException(404, "Portfolio client not found")
        now = _now_ms()
        pid = "pp_" + uuid.uuid4().hex[:12]
        name = (body.projectName or body.name or "").strip()
        area = float(body.paintingAreaSqm or body.volume or 0)
        budget = float(body.budgetSum or body.contractAmount or 0)
        conn.execute(
            """
            INSERT INTO portfolio_projects (
                id, portfolio_client_id, name, deal_date, volume, volume_unit,
                contract_amount, partner_commission, details, status,
                address_orientir, construction_stage, painting_area_sqm,
                budget_sum, products_spec, start_date, end_date,
                site_manager_contact, created_at, updated_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pid,
                client_id,
                name,
                body.dealDate or body.startDate,
                area,
                body.volumeUnit or "м²",
                budget,
                float(body.partnerCommission or 0),
                body.details,
                body.status or "planning",
                body.addressOrientir,
                body.constructionStage or "zero_cycle",
                area,
                budget,
                body.productsSpec,
                body.startDate,
                body.endDate,
                body.siteManagerContact,
                now,
                now,
                body.createdBy,
            ),
        )
        conn.execute(
            "UPDATE portfolio_clients SET updated_at = ? WHERE id = ?",
            (now, client_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM portfolio_projects WHERE id = ?", (pid,)
        ).fetchone()
    return {"ok": True, "project": _project_public(_row_to_dict(row) or {})}


@router.get("/portfolio/projects")
def list_portfolio_projects(
    client_id: str = Query("", alias="portfolioClientId"),
) -> dict[str, Any]:
    sql = "SELECT * FROM portfolio_projects WHERE 1=1"
    params: list[Any] = []
    if client_id.strip():
        sql += " AND portfolio_client_id = ?"
        params.append(client_id.strip())
    sql += " ORDER BY updated_at DESC, created_at DESC"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return {
        "ok": True,
        "projects": [_project_public(_row_to_dict(r) or {}) for r in rows],
    }


@router.put("/portfolio/projects/{project_id}")
def update_portfolio_project(project_id: str, body: ProjectUpdate) -> dict[str, Any]:
    name = body.projectName if body.projectName is not None else body.name
    mapping = {
        "name": name,
        "deal_date": body.dealDate,
        "volume": body.volume if body.volume is not None else body.paintingAreaSqm,
        "volume_unit": body.volumeUnit,
        "contract_amount": body.contractAmount
        if body.contractAmount is not None
        else body.budgetSum,
        "partner_commission": body.partnerCommission,
        "details": body.details,
        "status": body.status,
        "address_orientir": body.addressOrientir,
        "construction_stage": body.constructionStage,
        "painting_area_sqm": body.paintingAreaSqm,
        "budget_sum": body.budgetSum,
        "products_spec": body.productsSpec,
        "start_date": body.startDate,
        "end_date": body.endDate,
        "site_manager_contact": body.siteManagerContact,
    }
    sets = []
    params: list[Any] = []
    for col, val in mapping.items():
        if val is not None:
            sets.append(f"{col} = ?")
            params.append(val)
    if not sets:
        raise HTTPException(400, "No fields to update")
    sets.append("updated_at = ?")
    params.append(_now_ms())
    params.append(project_id)
    with _connect() as conn:
        cur = conn.execute(
            f"UPDATE portfolio_projects SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Project not found")
        conn.commit()
        row = conn.execute(
            "SELECT * FROM portfolio_projects WHERE id = ?", (project_id,)
        ).fetchone()
    return {"ok": True, "project": _project_public(_row_to_dict(row) or {})}


@router.get("/portfolio/clients/{client_id}/contacts")
def list_contacts(client_id: str) -> dict[str, Any]:
    with _connect() as conn:
        exists = conn.execute(
            "SELECT id FROM portfolio_clients WHERE id = ?", (client_id,)
        ).fetchone()
        if not exists:
            raise HTTPException(404, "Portfolio client not found")
        rows = conn.execute(
            "SELECT * FROM portfolio_contacts WHERE client_id = ? ORDER BY created_at DESC",
            (client_id,),
        ).fetchall()
    return {
        "ok": True,
        "contacts": [_contact_public(_row_to_dict(r) or {}) for r in rows],
    }


@router.post("/portfolio/clients/{client_id}/contacts")
def create_contact(client_id: str, body: ContactCreate) -> dict[str, Any]:
    with _connect() as conn:
        exists = conn.execute(
            "SELECT id FROM portfolio_clients WHERE id = ?", (client_id,)
        ).fetchone()
        if not exists:
            raise HTTPException(404, "Portfolio client not found")
        now = _now_ms()
        cid = "pct_" + uuid.uuid4().hex[:12]
        conn.execute(
            """
            INSERT INTO portfolio_contacts (
                id, client_id, project_id, full_name, role_position,
                phone, telegram, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cid,
                client_id,
                body.projectId,
                body.fullName.strip(),
                body.rolePosition,
                body.phone,
                body.telegram,
                body.notes,
                now,
                now,
            ),
        )
        conn.execute(
            "UPDATE portfolio_clients SET updated_at = ? WHERE id = ?",
            (now, client_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM portfolio_contacts WHERE id = ?", (cid,)
        ).fetchone()
    return {"ok": True, "contact": _contact_public(_row_to_dict(row) or {})}


@router.put("/portfolio/contacts/{contact_id}")
def update_contact(contact_id: str, body: ContactUpdate) -> dict[str, Any]:
    mapping = {
        "full_name": body.fullName,
        "role_position": body.rolePosition,
        "phone": body.phone,
        "telegram": body.telegram,
        "notes": body.notes,
        "project_id": body.projectId,
    }
    sets = []
    params: list[Any] = []
    for col, val in mapping.items():
        if val is not None:
            sets.append(f"{col} = ?")
            params.append(val)
    if not sets:
        raise HTTPException(400, "No fields to update")
    sets.append("updated_at = ?")
    params.append(_now_ms())
    params.append(contact_id)
    with _connect() as conn:
        cur = conn.execute(
            f"UPDATE portfolio_contacts SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Contact not found")
        conn.commit()
        row = conn.execute(
            "SELECT * FROM portfolio_contacts WHERE id = ?", (contact_id,)
        ).fetchone()
    return {"ok": True, "contact": _contact_public(_row_to_dict(row) or {})}


@router.get("/portfolio/monitoring")
def portfolio_monitoring(
    responsible: str = Query(""),
    responsible_id: str = Query("", alias="responsibleId"),
) -> dict[str, Any]:
    with _connect() as conn:
        sql = "SELECT * FROM portfolio_clients WHERE 1=1"
        params: list[Any] = []
        key = responsible_id.strip() or responsible.strip()
        if key:
            sql += (
                " AND (responsible_login = ? OR responsible_name = ? OR responsible_id = ?)"
            )
            params.extend([key, key, key])
        sql += " ORDER BY updated_at DESC"
        clients = conn.execute(sql, params).fetchall()
        rows_out = []
        totals = {
            "active": 0,
            "pause": 0,
            "churn": 0,
            "volume": 0.0,
            "amount": 0.0,
            "paintingArea": 0.0,
            "activeProjects": 0,
        }
        for c in clients:
            cd = _row_to_dict(c) or {}
            proj_list = [
                _project_public(_row_to_dict(p) or {})
                for p in conn.execute(
                    "SELECT * FROM portfolio_projects WHERE portfolio_client_id = ?",
                    (cd["id"],),
                ).fetchall()
            ]
            last_date = ""
            total_vol = 0.0
            total_amt = 0.0
            total_area = 0.0
            chronology = []
            for p in proj_list:
                total_vol += float(p.get("volume") or 0)
                total_amt += float(p.get("budgetSum") or p.get("contractAmount") or 0)
                total_area += float(p.get("paintingAreaSqm") or 0)
                if (p.get("status") or "") in ("planning", "supply", "in_progress", "active"):
                    totals["activeProjects"] += 1
                dt_key = p.get("dealDate") or p.get("startDate") or ""
                if dt_key and (not last_date or dt_key > last_date):
                    last_date = dt_key
                chronology.append(
                    {
                        "type": "project",
                        "id": p["id"],
                        "name": p["name"],
                        "date": dt_key,
                        "volume": p.get("paintingAreaSqm") or p.get("volume"),
                        "amount": p.get("budgetSum") or p.get("contractAmount"),
                        "status": p.get("status"),
                        "constructionStage": p.get("constructionStage"),
                    }
                )
            chronology.sort(key=lambda x: x.get("date") or "", reverse=True)

            status = cd.get("activity_status") or "active"
            if status == "active" and last_date:
                try:
                    dt = datetime.strptime(last_date[:10], "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    )
                    days = (datetime.now(timezone.utc) - dt).days
                    if days > 180:
                        status = "churn"
                    elif days > 90:
                        status = "pause"
                except ValueError:
                    pass

            totals[status] = totals.get(status, 0) + 1
            totals["volume"] += total_vol
            totals["amount"] += total_amt
            totals["paintingArea"] += total_area
            rows_out.append(
                {
                    "clientId": cd["id"],
                    "title": cd.get("title"),
                    "company": cd.get("company"),
                    "inn": cd.get("inn"),
                    "clientCategory": cd.get("client_category"),
                    "phone": cd.get("phone"),
                    "responsibleId": cd.get("responsible_id"),
                    "responsibleLogin": cd.get("responsible_login"),
                    "responsibleName": cd.get("responsible_name"),
                    "activityStatus": status,
                    "storedActivityStatus": cd.get("activity_status"),
                    "lastOrderDate": last_date,
                    "projectsCount": len(proj_list),
                    "totalVolume": total_vol,
                    "totalPaintingAreaSqm": total_area,
                    "totalContractAmount": total_amt,
                    "chronology": chronology[:20],
                    "transformedAt": cd.get("transformed_at"),
                }
            )

    return {
        "ok": True,
        "summary": totals,
        "clients": rows_out,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/portfolio/analytics")
def portfolio_analytics() -> dict[str, Any]:
    """Dashboard block: Portfolio & Repeat Sales."""
    with _connect() as conn:
        clients = [
            _row_to_dict(r) or {}
            for r in conn.execute("SELECT * FROM portfolio_clients").fetchall()
        ]
        all_projects = [
            _project_public(_row_to_dict(p) or {})
            for p in conn.execute("SELECT * FROM portfolio_projects").fetchall()
        ]

    by_resp: dict[str, dict[str, Any]] = {}
    for c in clients:
        key = c.get("responsible_name") or c.get("responsible_login") or "—"
        bucket = by_resp.setdefault(
            key,
            {
                "name": key,
                "login": c.get("responsible_login") or "",
                "clients": 0,
                "activeProjects": 0,
                "budget": 0.0,
                "area": 0.0,
            },
        )
        bucket["clients"] += 1

    client_volumes: list[dict[str, Any]] = []
    for c in clients:
        cid = c["id"]
        projs = [p for p in all_projects if p.get("portfolioClientId") == cid]
        budget = sum(float(p.get("budgetSum") or 0) for p in projs)
        area = sum(float(p.get("paintingAreaSqm") or 0) for p in projs)
        active = sum(
            1
            for p in projs
            if (p.get("status") or "") in ("planning", "supply", "in_progress", "active")
        )
        key = c.get("responsible_name") or c.get("responsible_login") or "—"
        if key in by_resp:
            by_resp[key]["activeProjects"] += active
            by_resp[key]["budget"] += budget
            by_resp[key]["area"] += area
        client_volumes.append(
            {
                "clientId": cid,
                "title": c.get("title") or c.get("company"),
                "category": c.get("client_category") or "B",
                "responsibleName": c.get("responsible_name"),
                "totalBudget": budget,
                "totalAreaSqm": area,
                "projectsCount": len(projs),
            }
        )

    client_volumes.sort(key=lambda x: x["totalBudget"], reverse=True)
    active_projects = sum(
        1
        for p in all_projects
        if (p.get("status") or "") in ("planning", "supply", "in_progress", "active")
    )
    total_budget = sum(float(p.get("budgetSum") or 0) for p in all_projects)
    total_area = sum(float(p.get("paintingAreaSqm") or 0) for p in all_projects)

    return {
        "ok": True,
        "summary": {
            "clientsCount": len(clients),
            "activeProjectsCount": active_projects,
            "projectsCount": len(all_projects),
            "totalBudgetSum": total_budget,
            "totalPaintingAreaSqm": total_area,
        },
        "byResponsible": sorted(
            by_resp.values(), key=lambda x: x["activeProjects"], reverse=True
        ),
        "topClients": client_volumes[:5],
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }
