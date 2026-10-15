"""
B2B Client Portfolio — SQLite storage + FastAPI routes.

Tables:
  portfolio_clients   — transformed clients from won B2B deals
  portfolio_projects  — objects/projects under a portfolio client

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
_SITE_DIR = _MODULE_DIR.parent if _MODULE_DIR.name == "app" else _MODULE_DIR
_DEFAULT_DB = _MODULE_DIR / "data" / "portfolio.db"

# Final successful B2B kanban stage (CRM: "Реализация" / Client)
FINAL_FUNNEL_STAGE = "implementation"
FUNNEL_STAGE_ORDER = (
    "cold",
    "qualification",
    "negotiation",
    "commercial",
    "contract",
    "implementation",
)

router = APIRouter(prefix="/api/b2b", tags=["b2b-portfolio"])


def _db_path() -> Path:
    raw = (os.getenv("PORTFOLIO_DB_PATH") or "").strip()
    if raw:
        return Path(raw)
    return _DEFAULT_DB


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
                status TEXT DEFAULT 'active',
                created_at INTEGER,
                updated_at INTEGER,
                created_by TEXT,
                FOREIGN KEY (portfolio_client_id)
                    REFERENCES portfolio_clients(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_portfolio_projects_client
                ON portfolio_projects(portfolio_client_id);
            """
        )
        conn.commit()
    log.info("Portfolio SQLite ready: %s", path)
    return path


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


def _now_ms() -> int:
    return int(time.time() * 1000)


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


def _client_public(row: dict[str, Any], *, projects: Optional[list] = None) -> dict[str, Any]:
    return {
        "id": row["id"],
        "sourceDealId": row.get("source_deal_id"),
        "title": row.get("title") or "",
        "company": row.get("company") or "",
        "contactName": row.get("contact_name") or "",
        "phone": row.get("phone") or "",
        "email": row.get("email") or "",
        "clientType": row.get("client_type") or "",
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
    }


def _project_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "portfolioClientId": row["portfolio_client_id"],
        "name": row.get("name") or "",
        "dealDate": row.get("deal_date") or "",
        "volume": float(row.get("volume") or 0),
        "volumeUnit": row.get("volume_unit") or "",
        "contractAmount": float(row.get("contract_amount") or 0),
        "partnerCommission": float(row.get("partner_commission") or 0),
        "details": row.get("details") or "",
        "status": row.get("status") or "active",
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
        "createdBy": row.get("created_by") or "",
    }


# ---------- Pydantic models ----------


class TransformBody(BaseModel):
    actor: str = ""
    actor_login: str = ""
    deal: Optional[dict[str, Any]] = None  # optional snapshot from CRM if Firebase fetch fails


class ClientUpdate(BaseModel):
    title: Optional[str] = None
    company: Optional[str] = None
    contactName: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    activityStatus: Optional[str] = None
    responsibleLogin: Optional[str] = None
    responsibleName: Optional[str] = None


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1)
    dealDate: str = ""
    volume: float = 0
    volumeUnit: str = ""
    contractAmount: float = 0
    partnerCommission: float = 0
    details: str = ""
    status: str = "active"
    createdBy: str = ""


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    dealDate: Optional[str] = None
    volume: Optional[float] = None
    volumeUnit: Optional[str] = None
    contractAmount: Optional[float] = None
    partnerCommission: Optional[float] = None
    details: Optional[str] = None
    status: Optional[str] = None


# ---------- Funnel / transform helpers ----------


def _normalize_stage(stage: Any) -> str:
    raw = str(stage or "").strip().lower()
    aliases = {
        "реализация": FINAL_FUNNEL_STAGE,
        "implementation": FINAL_FUNNEL_STAGE,
        "client": FINAL_FUNNEL_STAGE,
        "клиент": FINAL_FUNNEL_STAGE,
        "cold": "cold",
        "холодный контакт": "cold",
        "qualification": "qualification",
        "квалификация": "qualification",
        "квалификация лида": "qualification",
        "negotiation": "negotiation",
        "переговоры": "negotiation",
        "commercial": "commercial",
        "коммерческое предложение": "commercial",
        "contract": "contract",
        "контрактация": "contract",
    }
    return aliases.get(raw, raw.replace(" ", "_"))


def validate_deal_funnel_complete(deal: Mapping[str, Any]) -> tuple[bool, str]:
    """Deal must be on final stage (implementation / Client)."""
    stage = _normalize_stage(deal.get("stage") or deal.get("stageId"))
    if stage != FINAL_FUNNEL_STAGE:
        return False, (
            f"Сделка должна быть на финальном этапе «Клиент / Реализация» "
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


def _build_client_from_deal(
    deal: dict[str, Any],
    *,
    actor: str,
    actor_login: str,
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
    return {
        "id": "pc_" + uuid.uuid4().hex[:12],
        "source_deal_id": str(deal.get("id") or ""),
        "title": str(deal.get("title") or deal.get("company") or "Без названия"),
        "company": str(deal.get("company") or deal.get("title") or ""),
        "contact_name": str(
            deal.get("contactName")
            or deal.get("name")
            or contact.get("name")
            or ""
        ),
        "phone": str(deal.get("phone") or contact.get("phone") or ""),
        "email": str(deal.get("email") or contact.get("email") or ""),
        "client_type": str(deal.get("clientType") or ""),
        "responsible_login": str(
            deal.get("responsibleLogin")
            or deal.get("responsible_login")
            or deal.get("authorLogin")
            or ""
        ),
        "responsible_name": str(
            deal.get("responsibleName")
            or deal.get("responsible_name")
            or deal.get("responsible")
            or deal.get("author")
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


# ---------- Routes ----------


@router.post("/portfolio/clients")
async def create_portfolio_client(body: TransformBody) -> dict[str, Any]:
    """Create portfolio client from an embedded deal snapshot (deal.id required)."""
    deal = body.deal if isinstance(body.deal, dict) else None
    if not deal or not deal.get("id"):
        raise HTTPException(400, "Body must include deal with id")
    return await transform_deal_to_portfolio(str(deal["id"]), body)


@router.post("/leads/{deal_id}/transform")
async def transform_deal_to_portfolio(deal_id: str, body: TransformBody) -> dict[str, Any]:
    """
    Validate funnel completion and copy deal → portfolio_clients.
    Idempotent: if already transformed, returns existing client.
    """
    deal = body.deal if isinstance(body.deal, dict) else None
    if not deal:
        deal = await _fetch_deal_from_firebase(deal_id)
    if not deal:
        raise HTTPException(404, f"Deal not found: {deal_id}")
    deal.setdefault("id", deal_id)

    ok, reason = validate_deal_funnel_complete(deal)
    if not ok:
        raise HTTPException(400, reason)

    with _connect() as conn:
        existing = conn.execute(
            "SELECT * FROM portfolio_clients WHERE source_deal_id = ? LIMIT 1",
            (deal_id,),
        ).fetchone()
        if existing:
            client = _client_public(_row_to_dict(existing) or {})
            return {
                "ok": True,
                "alreadyTransformed": True,
                "client": client,
            }

        row = _build_client_from_deal(
            deal, actor=body.actor, actor_login=body.actor_login
        )
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        conn.execute(
            f"INSERT INTO portfolio_clients ({cols}) VALUES ({placeholders})",
            tuple(row.values()),
        )
        # Seed first project from the won deal
        proj_id = "pp_" + uuid.uuid4().hex[:12]
        now = _now_ms()
        deal_date = ""
        dates = deal.get("dates") or {}
        if isinstance(dates, dict):
            deal_date = str(
                dates.get("Реализация")
                or dates.get("implementation")
                or dates.get("closed")
                or ""
            )[:32]
        conn.execute(
            """
            INSERT INTO portfolio_projects (
                id, portfolio_client_id, name, deal_date, volume, volume_unit,
                contract_amount, partner_commission, details, status,
                created_at, updated_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                proj_id,
                row["id"],
                row["title"],
                deal_date,
                0,
                "",
                float(row["offer_amount"] or row["budget"] or 0),
                float(deal.get("commission") or deal.get("partnerCommission") or 0),
                "Создано при трансформации из сделки " + deal_id,
                "active",
                now,
                now,
                body.actor_login or body.actor or "",
            ),
        )
        conn.commit()
        client = _client_public(row)
        client["projects"] = [
            _project_public(
                {
                    "id": proj_id,
                    "portfolio_client_id": row["id"],
                    "name": row["title"],
                    "deal_date": deal_date,
                    "volume": 0,
                    "volume_unit": "",
                    "contract_amount": float(row["offer_amount"] or row["budget"] or 0),
                    "partner_commission": float(
                        deal.get("commission") or deal.get("partnerCommission") or 0
                    ),
                    "details": "Создано при трансформации из сделки " + deal_id,
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                    "created_by": body.actor_login or body.actor or "",
                }
            )
        ]

    # Best-effort: mark deal in Firebase
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

    return {"ok": True, "alreadyTransformed": False, "client": client}


@router.get("/portfolio/clients")
def list_portfolio_clients(
    responsible: str = Query("", description="Filter by responsible login or name"),
    status: str = Query("", description="active|pause|churn"),
) -> dict[str, Any]:
    sql = "SELECT * FROM portfolio_clients WHERE 1=1"
    params: list[Any] = []
    if responsible.strip():
        sql += " AND (responsible_login = ? OR responsible_name = ?)"
        params.extend([responsible.strip(), responsible.strip()])
    if status.strip():
        sql += " AND activity_status = ?"
        params.append(status.strip())
    sql += " ORDER BY updated_at DESC"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        clients = []
        for r in rows:
            d = _row_to_dict(r) or {}
            projs = conn.execute(
                "SELECT * FROM portfolio_projects WHERE portfolio_client_id = ? "
                "ORDER BY deal_date DESC, created_at DESC",
                (d["id"],),
            ).fetchall()
            clients.append(
                _client_public(
                    d,
                    projects=[_project_public(_row_to_dict(p) or {}) for p in projs],
                )
            )
    return {"ok": True, "clients": clients, "count": len(clients)}


@router.get("/portfolio/clients/{client_id}")
def get_portfolio_client(client_id: str) -> dict[str, Any]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM portfolio_clients WHERE id = ?", (client_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Portfolio client not found")
        d = _row_to_dict(row) or {}
        projs = conn.execute(
            "SELECT * FROM portfolio_projects WHERE portfolio_client_id = ? "
            "ORDER BY deal_date DESC, created_at DESC",
            (client_id,),
        ).fetchall()
    return {
        "ok": True,
        "client": _client_public(
            d, projects=[_project_public(_row_to_dict(p) or {}) for p in projs]
        ),
    }


@router.put("/portfolio/clients/{client_id}")
def update_portfolio_client(client_id: str, body: ClientUpdate) -> dict[str, Any]:
    mapping = {
        "title": body.title,
        "company": body.company,
        "contact_name": body.contactName,
        "phone": body.phone,
        "email": body.email,
        "activity_status": body.activityStatus,
        "responsible_login": body.responsibleLogin,
        "responsible_name": body.responsibleName,
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
        row = conn.execute(
            "SELECT * FROM portfolio_clients WHERE id = ?", (client_id,)
        ).fetchone()
    return {"ok": True, "client": _client_public(_row_to_dict(row) or {})}


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
        conn.execute(
            """
            INSERT INTO portfolio_projects (
                id, portfolio_client_id, name, deal_date, volume, volume_unit,
                contract_amount, partner_commission, details, status,
                created_at, updated_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pid,
                client_id,
                body.name.strip(),
                body.dealDate,
                float(body.volume or 0),
                body.volumeUnit,
                float(body.contractAmount or 0),
                float(body.partnerCommission or 0),
                body.details,
                body.status or "active",
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
    sql += " ORDER BY deal_date DESC, created_at DESC"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return {
        "ok": True,
        "projects": [_project_public(_row_to_dict(r) or {}) for r in rows],
    }


@router.put("/portfolio/projects/{project_id}")
def update_portfolio_project(project_id: str, body: ProjectUpdate) -> dict[str, Any]:
    mapping = {
        "name": body.name,
        "deal_date": body.dealDate,
        "volume": body.volume,
        "volume_unit": body.volumeUnit,
        "contract_amount": body.contractAmount,
        "partner_commission": body.partnerCommission,
        "details": body.details,
        "status": body.status,
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


@router.get("/portfolio/monitoring")
def portfolio_monitoring(
    responsible: str = Query(""),
) -> dict[str, Any]:
    """
    Aggregate client activity for repeat-sales monitoring:
    last order date, volume dynamics, activity status.
    """
    with _connect() as conn:
        sql = "SELECT * FROM portfolio_clients WHERE 1=1"
        params: list[Any] = []
        if responsible.strip():
            sql += " AND (responsible_login = ? OR responsible_name = ?)"
            params.extend([responsible.strip(), responsible.strip()])
        sql += " ORDER BY updated_at DESC"
        clients = conn.execute(sql, params).fetchall()
        rows_out = []
        totals = {"active": 0, "pause": 0, "churn": 0, "volume": 0.0, "amount": 0.0}
        for c in clients:
            cd = _row_to_dict(c) or {}
            projs = conn.execute(
                "SELECT * FROM portfolio_projects WHERE portfolio_client_id = ?",
                (cd["id"],),
            ).fetchall()
            proj_list = [_project_public(_row_to_dict(p) or {}) for p in projs]
            last_date = ""
            total_vol = 0.0
            total_amt = 0.0
            chronology = []
            for p in proj_list:
                total_vol += float(p.get("volume") or 0)
                total_amt += float(p.get("contractAmount") or 0)
                if p.get("dealDate") and (not last_date or p["dealDate"] > last_date):
                    last_date = p["dealDate"]
                chronology.append(
                    {
                        "type": "project",
                        "id": p["id"],
                        "name": p["name"],
                        "date": p.get("dealDate") or "",
                        "volume": p.get("volume"),
                        "amount": p.get("contractAmount"),
                        "status": p.get("status"),
                    }
                )
            chronology.sort(key=lambda x: x.get("date") or "", reverse=True)

            # Auto-suggest activity if not manually set beyond default
            status = cd.get("activity_status") or "active"
            if status == "active" and last_date:
                try:
                    # dealDate may be YYYY-MM-DD
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
            rows_out.append(
                {
                    "clientId": cd["id"],
                    "title": cd.get("title"),
                    "company": cd.get("company"),
                    "phone": cd.get("phone"),
                    "responsibleLogin": cd.get("responsible_login"),
                    "responsibleName": cd.get("responsible_name"),
                    "activityStatus": status,
                    "storedActivityStatus": cd.get("activity_status"),
                    "lastOrderDate": last_date,
                    "projectsCount": len(proj_list),
                    "totalVolume": total_vol,
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
