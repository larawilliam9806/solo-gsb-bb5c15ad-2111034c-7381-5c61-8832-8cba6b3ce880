"""FastAPI 入口：陶片拼接 Web 应用。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db as dbmod
from . import seed, store
from .store import DomainError

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DB_PATH = os.getenv("POTTERY_DB", str(BASE_DIR / "data" / "pottery.db"))

app = FastAPI(title="陶片拼接工作台", version="1.0.0")


@app.on_event("startup")
def _startup() -> None:
    app.state.conn = dbmod.connect(DB_PATH)
    dbmod.init_schema(app.state.conn)
    seeded = seed.seed_if_empty(app.state.conn)
    app.state.seeded = seeded


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"error": {"code": exc.code, "message": exc.message,
                           "details": exc.details}},
    )


def conn(request: Request):
    return request.app.state.conn


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------

class MarkerIn(BaseModel):
    label: str = Field(..., description="标记名称")
    x: float = Field(..., description="局部轮廓坐标 x（毫米）")
    y: float = Field(..., description="局部轮廓坐标 y（毫米）")


class FragmentIn(BaseModel):
    name: str
    contour_mm: list[list[float]] = Field(..., description="毫米制闭合轮廓顶点")
    markers: list[MarkerIn] = Field(default_factory=list)


class MarkerAddIn(BaseModel):
    label: str
    x: float
    y: float


class CandidateIn(BaseModel):
    frag_a: int
    frag_b: int
    pairs: list[list[int]] = Field(
        ..., description="[[A片标记id, B片标记id], ...]，至少两组")


class AdoptIn(BaseModel):
    version: int = Field(..., description="客户端所见关系版本")
    state_version: int | None = Field(
        None, description="预览时返回的全局状态版本（可选，用于过期检测）")


class UndoIn(BaseModel):
    version: int


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/api/config")
def get_config() -> dict[str, Any]:
    return {
        "join_tolerance_mm": store.JOIN_TOL_MM,
        "marker_tolerance_mm": store.MARKER_TOL_MM,
        "database": DB_PATH,
        "units": "millimeter",
        "transform": "translation+rotation only",
    }


@app.get("/api/state")
def get_state(request: Request) -> dict[str, Any]:
    return store.full_state(conn(request))


@app.post("/api/fragments", status_code=201)
def post_fragment(request: Request, body: FragmentIn) -> dict[str, Any]:
    fid = store.create_fragment(
        conn(request), body.name, body.contour_mm,
        [m.model_dump() for m in body.markers])
    return {"id": fid, "state": store.full_state(conn(request))}


@app.post("/api/fragments/{frag_id}/markers", status_code=201)
def post_marker(request: Request, frag_id: int, body: MarkerAddIn
                ) -> dict[str, Any]:
    mid = store.add_marker(conn(request), frag_id, body.label, body.x, body.y)
    return {"id": mid, "state": store.full_state(conn(request))}


@app.post("/api/candidates", status_code=201)
def post_candidate(request: Request, body: CandidateIn) -> dict[str, Any]:
    rid = store.create_candidate(
        conn(request), body.frag_a, body.frag_b, body.pairs)
    return {"id": rid, "preview": store.preview(conn(request), rid),
            "state": store.full_state(conn(request))}


@app.get("/api/relations/{rel_id}/preview")
def get_preview(request: Request, rel_id: int) -> dict[str, Any]:
    return store.preview(conn(request), rel_id)


@app.post("/api/relations/{rel_id}/adopt")
def post_adopt(request: Request, rel_id: int, body: AdoptIn) -> dict[str, Any]:
    result = store.adopt(conn(request), rel_id, body.version,
                         body.state_version)
    result["state"] = store.full_state(conn(request))
    return result


@app.post("/api/relations/{rel_id}/undo")
def post_undo(request: Request, rel_id: int, body: UndoIn) -> dict[str, Any]:
    result = store.undo(conn(request), rel_id, body.version)
    result["state"] = store.full_state(conn(request))
    return result


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
