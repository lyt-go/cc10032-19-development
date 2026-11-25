"""lineage_impact 领域服务：历史时刻血缘路径查询与影响范围试算。

核心能力：
1. 按“表 + 时刻”筛选影响范围，只取该时刻的有效边
   （effective_at <= 查询时刻，且 replaced_at 为空或晚于查询时刻）。
2. 枚举同一张下游表的多条到达路径，每条边都带生效时间与替换时间。
3. 试算单（草稿）冻结查询时刻的图快照；确认发布后回写表格式影响清单，
   已发布评估不可变——当前边废弃/回滚不会改掉历史评估。
4. 历史时刻的更正边在发布评估前到达时，试算单必须显式写明策略：
   - original_graph：继续沿用原图（不含更正边）；
   - corrected_graph：采用“已确认”的更正边重新查出有效路径；
     更正边未确认时不允许采用，防止把未确认更正写进发布评估。

数据全部落在 app.store.Store 的时间化边注册表中。
"""
from __future__ import annotations

import copy
import time
from typing import Any

from app.store import format_ts, parse_ts, store

POLICY_ORIGINAL = "original_graph"
POLICY_CORRECTED = "corrected_graph"
POLICY_LABELS = {
    POLICY_ORIGINAL: "继续沿用原图",
    POLICY_CORRECTED: "采用已确认更正重新查出的有效路径",
}

TRIAL_DRAFT = "试算中"
TRIAL_CONFIRMED = "已确认"


class LineageImpactService:
    MODULE = "lineage_impact"

    # ------------------------------------------------------------------
    # 边视图：对外输出统一带时间文本，隔离内部 dict
    # ------------------------------------------------------------------
    @staticmethod
    def _edge_view(edge: dict[str, Any]) -> dict[str, Any]:
        return {
            "edge_id": edge["edge_id"],
            "upstream": edge["upstream"],
            "downstream": edge["downstream"],
            "transform": edge.get("transform", "direct"),
            "edge_type": edge.get("edge_type", "etl"),
            "effective_at": edge["effective_at"],
            "effective_at_text": format_ts(edge["effective_at"]),
            "replaced_at": edge.get("replaced_at"),
            "replaced_at_text": format_ts(edge.get("replaced_at")),
            "status": edge.get("status", "生效"),
            "is_correction": edge.get("is_correction", False),
            "confirmed": edge.get("confirmed", True),
            "version": edge.get("version", 1),
            "replaces_edge_id": edge.get("replaces_edge_id"),
            "successor_edge_id": edge.get("successor_edge_id"),
        }

    def _edges_for_policy(self, at_ts: float, policy: str) -> list[dict[str, Any]]:
        edges = store.effective_edges_at(at_ts)
        if policy == POLICY_ORIGINAL:
            edges = [e for e in edges if not e.get("is_correction")]
        elif policy == POLICY_CORRECTED:
            # effective_edges_at 已过滤未确认更正，这里再做一道显式防御
            edges = [e for e in edges if not e.get("is_correction") or e.get("confirmed")]
        else:
            raise ValueError(f"未知试算策略: {policy}")
        return edges

    def _corrections_at(self, at_ts: float) -> list[dict[str, Any]]:
        """查询时刻已生效的全部更正边（含未确认），用于试算单写明选择。"""
        return [
            e for e in store.temporal_edges()
            if e.get("is_correction")
            and e["effective_at"] <= at_ts
            and (e.get("replaced_at") is None or e.get("replaced_at") > at_ts)
        ]

    # ------------------------------------------------------------------
    # 路径枚举：DFS 收集全部简单路径，同一张表的多条路径都保留
    # ------------------------------------------------------------------
    @staticmethod
    def _enumerate_paths(
        source: str,
        edges: list[dict[str, Any]],
    ) -> list[list[dict[str, Any]]]:
        adjacency: dict[str, list[dict[str, Any]]] = {}
        for edge in edges:
            adjacency.setdefault(edge["upstream"], []).append(edge)

        paths: list[list[dict[str, Any]]] = []

        def walk(node: str, chain: list[dict[str, Any]], visited: set[str]) -> None:
            for edge in adjacency.get(node, []):
                nxt = edge["downstream"]
                if nxt in visited:  # 防环，只枚举简单路径
                    continue
                new_chain = chain + [edge]
                paths.append(new_chain)
                walk(nxt, new_chain, visited | {nxt})

        walk(source, [], {source})
        # 稳定排序：先按终点再按长度，方便验收复核
        paths.sort(key=lambda p: (p[-1]["downstream"], len(p), [e["edge_id"] for e in p]))
        return paths

    def _build_evaluation(self, source: str, at_ts: float, policy: str) -> dict[str, Any]:
        edges = self._edges_for_policy(at_ts, policy)
        paths_raw = self._enumerate_paths(source, edges)

        path_views: list[dict[str, Any]] = []
        affected: dict[str, dict[str, Any]] = {}
        for idx, chain in enumerate(paths_raw, start=1):
            edge_views = [self._edge_view(e) for e in chain]
            tables = [source, *[e["downstream"] for e in chain]]
            path_view = {
                "path_id": idx,
                "tables": tables,
                "path_text": " → ".join(tables),
                "edges": edge_views,
            }
            path_views.append(path_view)
            target = tables[-1]
            bucket = affected.setdefault(target, {
                "table": target,
                "path_count": 0,
                "paths": [],
            })
            bucket["path_count"] += 1
            bucket["paths"].append(idx)

        return {
            "source_table": source,
            "query_ts": at_ts,
            "query_ts_text": format_ts(at_ts),
            "policy": policy,
            "policy_label": POLICY_LABELS[policy],
            "effective_edges": [self._edge_view(e) for e in edges],
            "paths": path_views,
            # 同一张表只计一次，但保留全部到达路径用于复核
            "affected_tables": [affected[name] for name in sorted(affected)],
            "affected_table_count": len(affected),
            "path_total": len(path_views),
        }

    # ------------------------------------------------------------------
    # 即时查询：不落地，用于页面试算与验收复核
    # ------------------------------------------------------------------
    def query_impact(
        self,
        source_table: str,
        at: Any,
        policy: str = POLICY_ORIGINAL,
    ) -> dict[str, Any]:
        if not str(source_table or "").strip():
            return {"ok": False, "reason": "源表不能为空"}
        if policy not in POLICY_LABELS:
            return {"ok": False, "reason": f"未知试算策略: {policy}"}
        try:
            at_ts = parse_ts(at)
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}

        if policy == POLICY_CORRECTED:
            unconfirmed = [
                e for e in self._corrections_at(at_ts) if not e.get("confirmed")
            ]
            if unconfirmed:
                return {
                    "ok": False,
                    "reason": "存在尚未确认的更正边，无法按更正图评估，请先确认更正或改用原图策略",
                    "unconfirmed_corrections": [self._edge_view(e) for e in unconfirmed],
                }

        evaluation = self._build_evaluation(source_table, at_ts, policy)
        corrections = self._corrections_at(at_ts)
        evaluation["ok"] = True
        evaluation["corrections_available"] = [self._edge_view(e) for e in corrections]
        return evaluation

    # ------------------------------------------------------------------
    # 试算单：创建（冻结快照）、查询、列表
    # ------------------------------------------------------------------
    def create_trial(
        self,
        source_table: str,
        at: Any,
        policy: str = POLICY_ORIGINAL,
    ) -> dict[str, Any]:
        result = self.query_impact(source_table, at, policy)
        if not result.get("ok"):
            return result
        at_ts = result["query_ts"]

        corrections = self._corrections_at(at_ts)
        if policy == POLICY_ORIGINAL and corrections:
            note = (
                "发布评估前有历史更正边到达，本试算继续沿用原图（不含更正边）；"
                "如需采用已确认更正，请以 corrected_graph 策略重新试算。"
            )
        elif policy == POLICY_CORRECTED:
            note = "本试算采用已确认更正边重新查出的有效路径。"
        else:
            note = "查询时刻无更正边，按原图评估。"

        trial = {
            "trial_id": "",
            "source_table": source_table,
            "query_ts": at_ts,
            "query_ts_text": format_ts(at_ts),
            "policy": policy,
            "policy_label": POLICY_LABELS[policy],
            "status": TRIAL_DRAFT,
            "created_at": time.time(),
            "created_at_text": format_ts(time.time()),
            "confirmed_at": None,
            "correction_policy_note": note,
            "corrections_available": [self._edge_view(e) for e in corrections],
            # 深拷贝冻结：之后边的废弃/回滚/补登记都不影响试算快照
            "frozen_evaluation": copy.deepcopy(
                {k: v for k, v in result.items() if k != "ok"}
            ),
            "published_entry_ids": [],
            "drift_report": None,
        }
        return {"ok": True, "trial": store.save_impact_trial(trial)}

    def list_trials(self) -> dict[str, Any]:
        return {"ok": True, "items": list(reversed(store.impact_trials())),
                "total": len(store.impact_trials())}

    def get_trial(self, trial_id: str) -> dict[str, Any]:
        trial = store.find_impact_trial(trial_id)
        if trial is None:
            return {"ok": False, "reason": f"试算单 {trial_id} 不存在"}
        return {"ok": True, "trial": trial}

    # ------------------------------------------------------------------
    # 确认发布：冻结结果回写表格式影响清单，并输出与当前图的漂移
    # ------------------------------------------------------------------
    def confirm_trial(self, trial_id: str) -> dict[str, Any]:
        trial = store.find_impact_trial(trial_id)
        if trial is None:
            return {"ok": False, "reason": f"试算单 {trial_id} 不存在"}
        if trial["status"] == TRIAL_CONFIRMED:
            return {
                "ok": False,
                "reason": "试算单已确认发布，评估不可重复发布或修改",
                "entry_ids": trial.get("published_entry_ids", []),
            }

        frozen = trial["frozen_evaluation"]
        # 用“当前边注册表”按原时刻、原策略重查一次，仅做漂移核对，不参与写入
        current = self._build_evaluation(
            trial["source_table"], trial["query_ts"], trial["policy"]
        )
        frozen_tables = {t["table"] for t in frozen["affected_tables"]}
        current_tables = {t["table"] for t in current["affected_tables"]}
        drift = {
            "new_tables_in_current_graph": sorted(current_tables - frozen_tables),
            "missing_tables_in_current_graph": sorted(frozen_tables - current_tables),
            "path_count_changed": [
                {
                    "table": t["table"],
                    "frozen_path_count": t["path_count"],
                    "current_path_count": next(
                        (c["path_count"] for c in current["affected_tables"]
                         if c["table"] == t["table"]),
                        0,
                    ),
                }
                for t in frozen["affected_tables"]
                if t["path_count"] != next(
                    (c["path_count"] for c in current["affected_tables"]
                     if c["table"] == t["table"]),
                    0,
                )
            ],
        }
        trial["drift_report"] = drift

        # 回写表格式影响清单：按试算所选时刻的有效路径评估，一张受影响表一行
        frozen_paths = {p["path_id"]: p for p in frozen["paths"]}
        published_ids: list[int] = []
        for affected in frozen["affected_tables"]:
            review_paths = [frozen_paths[pid] for pid in affected["paths"]]
            entry = {
                "清单编号": f"IMPACT-{trial['trial_id'].split('-')[1]}-{affected['table']}",
                "源表": trial["source_table"],
                "目标表": affected["table"],
                "评估时刻": trial["query_ts_text"],
                "评估时刻ts": trial["query_ts"],
                "试算策略": trial["policy_label"],
                "到达路径数": affected["path_count"],
                "到达路径": "；".join(p["path_text"] for p in review_paths),
                "路径明细": copy.deepcopy(review_paths),
                "试算单号": trial["trial_id"],
                "发布时间": format_ts(time.time()),
                "status": "已生效",
                "immutable": True,
            }
            saved = store.add_impact_entry(entry)
            published_ids.append(saved["id"])

        trial["status"] = TRIAL_CONFIRMED
        trial["confirmed_at"] = time.time()
        trial["published_entry_ids"] = published_ids
        return {
            "ok": True,
            "trial_id": trial_id,
            "entry_ids": published_ids,
            "published_count": len(published_ids),
            "drift_report": drift,
        }

    # ------------------------------------------------------------------
    # 表格式影响清单查询
    # ------------------------------------------------------------------
    def list_impact_entries(self, target_table: str | None = None) -> dict[str, Any]:
        items = store.impact_entries()
        if target_table:
            items = [e for e in items if e.get("目标表") == target_table]
        return {"ok": True, "items": items, "total": len(items)}

    # ------------------------------------------------------------------
    # 边管理：登记 / 废弃 / 回滚 / 更正确认（薄封装，参数校验放这里）
    # ------------------------------------------------------------------
    def register_edge(
        self,
        upstream: str,
        downstream: str,
        effective_at: Any,
        *,
        transform: str = "direct",
        edge_type: str = "etl",
        is_correction: bool = False,
        confirmed: bool = True,
        correction_note: str = "",
    ) -> dict[str, Any]:
        if not str(upstream or "").strip() or not str(downstream or "").strip():
            return {"ok": False, "reason": "上游表和下游表不能为空"}
        try:
            eff_ts = parse_ts(effective_at)
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        edge = store.register_temporal_edge(
            upstream.strip(), downstream.strip(), eff_ts,
            transform=transform, edge_type=edge_type,
            is_correction=is_correction, confirmed=confirmed,
            correction_note=correction_note,
        )
        return {"ok": True, "edge": self._edge_view(edge)}

    def deprecate_edge(self, edge_id: str, replaced_at: Any) -> dict[str, Any]:
        try:
            ts = parse_ts(replaced_at)
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        try:
            edge = store.deprecate_temporal_edge(edge_id, ts)
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        if edge is None:
            return {"ok": False, "reason": f"边 {edge_id} 不存在"}
        return {"ok": True, "edge": self._edge_view(edge)}

    def replace_edge(
        self,
        edge_id: str,
        replaced_at: Any,
        new_effective_at: Any,
        *,
        transform: str | None = None,
        edge_type: str | None = None,
    ) -> dict[str, Any]:
        """变更边：关联变更前后两个版本的有效边（旧版 vN ↔ 新版 vN+1）。"""
        try:
            old_ts = parse_ts(replaced_at)
            new_ts = parse_ts(new_effective_at)
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        try:
            old, new = store.replace_temporal_edge(
                edge_id, old_ts, new_ts, transform=transform, edge_type=edge_type
            )
        except KeyError:
            return {"ok": False, "reason": f"边 {edge_id} 不存在"}
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        return {
            "ok": True,
            "old_edge": self._edge_view(old),
            "new_edge": self._edge_view(new),
        }

    def rollback_edge(self, edge_id: str, rollback_at: Any) -> dict[str, Any]:
        try:
            ts = parse_ts(rollback_at)
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        edge = store.rollback_temporal_edge(edge_id, ts)
        if edge is None:
            return {"ok": False, "reason": f"边 {edge_id} 不存在"}
        return {"ok": True, "edge": self._edge_view(edge)}

    def confirm_correction(self, edge_id: str) -> dict[str, Any]:
        edge = store.confirm_correction_edge(edge_id)
        if edge is None:
            return {"ok": False, "reason": f"边 {edge_id} 不存在"}
        if not edge.get("is_correction"):
            return {"ok": False, "reason": f"边 {edge_id} 不是更正边"}
        return {"ok": True, "edge": self._edge_view(edge)}

    def list_edges(self) -> dict[str, Any]:
        return {"ok": True, "items": [self._edge_view(e) for e in store.temporal_edges()],
                "total": len(store.temporal_edges())}


lineage_impact_service = LineageImpactService()
