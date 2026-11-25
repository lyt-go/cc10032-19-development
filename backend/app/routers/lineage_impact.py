"""历史血缘影响范围接口：版本化边管理、按时刻路径查询、试算单与表格式影响清单。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.lineage_impact import (
    POLICY_CORRECTED,
    POLICY_ORIGINAL,
    lineage_impact_service as svc,
)
from app.store import store

router = APIRouter(prefix="/api/lineage-impact", tags=["历史血缘影响"])


# ----------------------------------------------------------------------
# 请求契约
# ----------------------------------------------------------------------
class EdgePayload(BaseModel):
    upstream: str
    downstream: str
    effective_at: Any
    transform: str = "direct"
    edge_type: str = "etl"
    is_correction: bool = False
    confirmed: bool = True
    correction_note: str = ""


class EdgeActionPayload(BaseModel):
    at: Any


class EdgeReplacePayload(BaseModel):
    replaced_at: Any
    new_effective_at: Any
    transform: str | None = None
    edge_type: str | None = None


class CorrectionConfirmPayload(BaseModel):
    note: str | None = None


class QueryPayload(BaseModel):
    source_table: str
    at: Any
    policy: str = POLICY_ORIGINAL


class TrialCreatePayload(BaseModel):
    source_table: str
    at: Any
    policy: str = POLICY_ORIGINAL


# ----------------------------------------------------------------------
# 版本化时间边
# ----------------------------------------------------------------------
@router.get("/edges")
def list_edges() -> dict[str, Any]:
    return svc.list_edges()


@router.post("/edges")
def register_edge(payload: EdgePayload) -> dict[str, Any]:
    return svc.register_edge(
        payload.upstream,
        payload.downstream,
        payload.effective_at,
        transform=payload.transform,
        edge_type=payload.edge_type,
        is_correction=payload.is_correction,
        confirmed=payload.confirmed,
        correction_note=payload.correction_note,
    )


@router.post("/edges/{edge_id}/deprecate")
def deprecate_edge(edge_id: str, payload: EdgeActionPayload) -> dict[str, Any]:
    return svc.deprecate_edge(edge_id, payload.at)


@router.post("/edges/{edge_id}/replace")
def replace_edge(edge_id: str, payload: EdgeReplacePayload) -> dict[str, Any]:
    return svc.replace_edge(
        edge_id,
        payload.replaced_at,
        payload.new_effective_at,
        transform=payload.transform,
        edge_type=payload.edge_type,
    )


@router.post("/edges/{edge_id}/rollback")
def rollback_edge(edge_id: str, payload: EdgeActionPayload) -> dict[str, Any]:
    return svc.rollback_edge(edge_id, payload.at)


@router.post("/edges/{edge_id}/confirm-correction")
def confirm_correction(edge_id: str, payload: CorrectionConfirmPayload) -> dict[str, Any]:
    return svc.confirm_correction(edge_id)


# ----------------------------------------------------------------------
# 按表 + 时刻的即时路径查询（不落地）
# ----------------------------------------------------------------------
@router.post("/query")
def query_impact(payload: QueryPayload) -> dict[str, Any]:
    return svc.query_impact(payload.source_table, payload.at, payload.policy)


# ----------------------------------------------------------------------
# 影响试算单
# ----------------------------------------------------------------------
@router.get("/trials")
def list_trials() -> dict[str, Any]:
    return svc.list_trials()


@router.get("/trials/{trial_id}")
def get_trial(trial_id: str) -> dict[str, Any]:
    return svc.get_trial(trial_id)


@router.post("/trials")
def create_trial(payload: TrialCreatePayload) -> dict[str, Any]:
    return svc.create_trial(payload.source_table, payload.at, payload.policy)


@router.post("/trials/{trial_id}/confirm")
def confirm_trial(trial_id: str) -> dict[str, Any]:
    return svc.confirm_trial(trial_id)


# ----------------------------------------------------------------------
# 表格式影响清单（已发布、不可变）
# ----------------------------------------------------------------------
@router.get("/impact-entries")
def list_impact_entries(target_table: str | None = None) -> dict[str, Any]:
    return svc.list_impact_entries(target_table)


@router.post("/reset")
def reset_demo_graph(reseed: bool = True) -> dict[str, Any]:
    """验收/演示辅助：清空时间边与试算；reseed=true 时重建预置示例图。"""
    store.reset_temporal_data(reseed=reseed)
    return {
        "ok": True,
        "message": "时间化血缘图已重置" + ("为预置示例图" if reseed else "（空白图）"),
    }
