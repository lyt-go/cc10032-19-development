"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据，并支持跨模块数据流水线。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime
from typing import Any

from app.seed import SEED_ROWS


def parse_ts(value: Any) -> float:
    """把入参时刻统一成 epoch 秒。

    支持：epoch 数字（int/float）、纯数字字符串、
    "YYYY-MM-DD HH:MM:SS" / "YYYY-MM-DDTHH:MM:SS" 文本。
    """
    if value is None:
        raise ValueError("时刻不能为空")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        raise ValueError("时刻不能为空")
    try:
        return float(text)
    except ValueError:
        pass
    normalized = text.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(normalized, fmt).timestamp()
        except ValueError:
            continue
    raise ValueError(f"无法解析的时刻: {value}")


def format_ts(ts: float | None) -> str | None:
    """把 epoch 秒格式化成可读时间，None 表示仍在生效（未被替换）。"""
    if ts is None:
        return None
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


class DataPipeline:
    """跨模块数据流水线引擎：支持 输入→转换→版本化产物→下游消费 的完整链路。

    每条流水线定义了源模块、目标模块、转换函数和验证规则。
    产物存储为可版本化的数据快照，支持脏行检测、重复批次拒绝、冲突版本检测、中断恢复。
    """

    def __init__(self, name: str, sources: list[str], target_module: str) -> None:
        self.name = name
        self.sources = sources
        self.target_module = target_module
        self._artifacts: list[dict[str, Any]] = []
        self._versions: list[str] = []  # 版本号列表
        self._dirty_log: list[dict[str, Any]] = []
        self._batch_registry: set[str] = set()  # 防重复批次
        self._last_checkpoint: dict[str, Any] = {}  # 中断恢复点
        self._interrupted = False

    def ingest(self, rows: list[dict[str, Any]], batch_id: str) -> tuple[int, list[str]]:
        """摄入数据行，返回 (成功行数, 脏行详情列表)"""
        if batch_id in self._batch_registry:
            return 0, [f"重复批次 {batch_id} 已存在，拒绝摄入"]
        self._batch_registry.add(batch_id)
        clean_rows = []
        dirty_details = []
        for i, row in enumerate(rows):
            issues = self._validate_row(row, i)
            if issues:
                dirty_details.append(f"行 {i}: {'; '.join(issues)}")
                self._dirty_log.append({"batch": batch_id, "row_index": i, "issues": issues, "row": row, "ts": time.time()})
            else:
                clean_rows.append(row)
        self._artifacts.extend(clean_rows)
        return len(clean_rows), dirty_details

    def transform(self, transform_fn, *args, **kwargs) -> list[dict[str, Any]]:
        """对当前产物执行转换函数"""
        self._save_checkpoint("transform_enter", len(self._artifacts))
        try:
            result = transform_fn(self._artifacts, *args, **kwargs)
            version = self._make_version("transform")
            self._versions.append(version)
            self._save_checkpoint("transform_exit", len(result))
            return result
        except Exception:
            self._interrupted = True
            raise

    def versioned_snapshot(self) -> dict[str, Any]:
        """生成版本化产物快照"""
        version = self._versions[-1] if self._versions else self._make_version("snapshot")
        return {
            "pipeline": self.name,
            "version": version,
            "artifact_count": len(self._artifacts),
            "artifact_sample": self._artifacts[:5],
            "dirty_count": len(self._dirty_log),
            "batch_count": len(self._batch_registry),
            "interrupted": self._interrupted,
            "checkpoint": self._last_checkpoint,
        }

    def health_report(self) -> dict[str, Any]:
        """流水线健康报告：用于外部模块消费和容量材料生成"""
        return {
            "pipeline": self.name,
            "source_modules": self.sources,
            "target_module": self.target_module,
            "total_ingested": sum(1 for a in self._artifacts),
            "dirty_rows": len(self._dirty_log),
            "versions": len(self._versions),
            "batches_processed": len(self._batch_registry),
            "interrupted": self._interrupted,
            "last_artifact_ts": max((a.get("ts", 0) for a in self._artifacts), default=0) if self._artifacts else 0,
        }

    def _validate_row(self, row: dict[str, Any], index: int) -> list[str]:
        issues = []
        for key, val in row.items():
            if val is None or (isinstance(val, str) and not val.strip()):
                issues.append(f"空值字段 '{key}'")
        if any(row.get(k) is None for k in ["id"]):
            issues.append("缺少主键 id")
        return issues

    def _make_version(self, tag: str) -> str:
        ts = str(int(time.time() * 1000))
        raw = f"{self.name}:{tag}:{ts}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    def _save_checkpoint(self, stage: str, count: int) -> None:
        self._last_checkpoint = {"stage": stage, "count": count, "ts": time.time()}

    def recover_checkpoint(self) -> dict[str, Any]:
        """从最近检查点恢复"""
        return {
            "pipeline": self.name,
            "interrupted": self._interrupted,
            "last_checkpoint": self._last_checkpoint,
            "artifacts_before_interrupt": len(self._artifacts),
        }


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        # 跨模块数据流水线
        self._pipelines: dict[str, DataPipeline] = {}
        # 全局变更日志（用于 changelog 跨模块追溯）
        self._global_changelog: list[dict[str, Any]] = []
        # Schema 注册表（cross-module schema registry）
        self._schema_registry: dict[str, list[dict[str, Any]]] = {}
        # 水位偏移记录（watermark → late_data 共享）
        self._watermark_offsets: dict[str, dict[str, Any]] = {}
        # 快照存储表（snapshot_mgr → backfill_job 共享）
        self._snapshot_store: dict[str, dict[str, Any]] = {}
        # 血缘图（lineage → table_format 共享，只保存“当前图”，不带时间维度）
        self._lineage_graph: dict[str, list[dict[str, Any]]] = {}
        # 归档清单（cold_archive → retention 共享）
        self._archive_manifest: list[dict[str, Any]] = []
        # ---------- 历史血缘：版本化边、影响试算、表格式影响清单 ----------
        # 时间化边注册表：每条边带 effective_at / replaced_at，支持历史路径查询
        self._temporal_edges: list[dict[str, Any]] = []
        # 影响试算单：草稿态快照，确认后冻结成表格式影响清单
        self._impact_trials: list[dict[str, Any]] = []
        # 表格式影响清单：已发布（确认）的不可变评估
        self._impact_entries: list[dict[str, Any]] = []
        self._seed_temporal_graph()

    # ---------- 基础 CRUD ----------
    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        # 跨模块流水线状态
        pipeline_stats = []
        for pname, pl in self._pipelines.items():
            pipeline_stats.append(pl.health_report())
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
            {"label": "流水线", "value": len(pipeline_stats)},
        ]
        return {"cards": cards, "modules": modules, "pipelines": pipeline_stats}

    # ---------- 跨模块流水线 ----------
    def create_pipeline(self, name: str, sources: list[str], target_module: str) -> DataPipeline:
        pl = DataPipeline(name, sources, target_module)
        self._pipelines[name] = pl
        return pl

    def pipeline(self, name: str) -> DataPipeline | None:
        return self._pipelines.get(name)

    def all_pipelines(self) -> dict[str, DataPipeline]:
        return dict(self._pipelines)

    # ---------- Schema 注册表 ----------
    def register_schema(self, topic: str, schema_def: dict[str, Any], version: str) -> dict[str, Any]:
        versions = self._schema_registry.setdefault(topic, [])
        entry = {"topic": topic, "definition": schema_def, "version": version, "ts": time.time()}
        versions.append(entry)
        return entry

    def schema_versions(self, topic: str) -> list[dict[str, Any]]:
        return self._schema_registry.get(topic, [])

    def check_schema_compatibility(self, topic: str, new_fields: list[str]) -> dict[str, Any]:
        versions = self.schema_versions(topic)
        if not versions:
            return {"compatible": True, "reason": "新主题，无兼容性问题"}
        latest = versions[-1]
        old_fields = set(latest.get("definition", {}).get("fields", []))
        missing_required = [f for f in old_fields if f not in new_fields]
        if missing_required:
            return {"compatible": False, "reason": f"缺少已有字段: {missing_required}"}
        return {"compatible": True, "reason": "向后兼容"}

    # ---------- 水位记录 ----------
    def update_watermark(self, partition: str, offset: dict[str, Any]) -> None:
        self._watermark_offsets[partition] = offset

    def get_watermark(self, partition: str) -> dict[str, Any] | None:
        return self._watermark_offsets.get(partition)

    # ---------- 快照存储 ----------
    def save_snapshot(self, snap_id: str, data: dict[str, Any]) -> None:
        self._snapshot_store[snap_id] = data

    def get_snapshot(self, snap_id: str) -> dict[str, Any] | None:
        return self._snapshot_store.get(snap_id)

    # ---------- 变更日志 ----------
    def append_changelog(self, entry: dict[str, Any]) -> None:
        entry["_ts"] = time.time()
        self._global_changelog.append(entry)

    def changelog_for_table(self, table_name: str) -> list[dict[str, Any]]:
        return [e for e in self._global_changelog if e.get("目标表") == table_name]

    # ---------- 血缘图 ----------
    def add_lineage_edge(self, upstream: str, downstream: str, edge_data: dict[str, Any]) -> None:
        edges = self._lineage_graph.setdefault(upstream, [])
        edges.append({"downstream": downstream, **edge_data, "ts": time.time()})

    def lineage_downstream(self, table_name: str) -> list[dict[str, Any]]:
        return self._lineage_graph.get(table_name, [])

    # ---------- 归档清单 ----------
    def add_archive_record(self, record: dict[str, Any]) -> None:
        self._archive_manifest.append(record)

    def archive_manifest_for_table(self, table_name: str) -> list[dict[str, Any]]:
        return [r for r in self._archive_manifest if r.get("源表名称") == table_name]

    # ============================================================
    # 历史血缘：版本化时间边
    # ============================================================
    def _seed_temporal_graph(self) -> None:
        """预置一张带时刻的示例图：A→B→D 与 A→C→D 两条路径。

        时刻取“当前时间之前”的相对值，保证起服务后按当前图试算即可命中。
        验收场景可以用 reset_temporal_data 清空后自建确定时刻的图。
        """
        now = time.time()
        base = now - 3600  # 一小时前全部生效
        seed_edges = [
            ("A", "B"),
            ("A", "C"),
            ("B", "D"),
            ("C", "D"),
        ]
        for idx, (up, down) in enumerate(seed_edges, start=1):
            self._temporal_edges.append({
                "edge_id": f"EDGE-{idx:04d}",
                "upstream": up,
                "downstream": down,
                "transform": "direct",
                "edge_type": "etl",
                "effective_at": base + idx,
                "replaced_at": None,
                "status": "生效",          # 生效 / 已废弃 / 已回滚
                "is_correction": False,
                "confirmed": True,
                "correction_note": "",
                "registered_at": base + idx,
                "version": 1,
            })

    def reset_temporal_data(self, *, reseed: bool = False) -> None:
        """清空时间边、试算单和影响清单；验收测试用它保证场景隔离。"""
        self._temporal_edges.clear()
        self._impact_trials.clear()
        self._impact_entries.clear()
        if reseed:
            self._seed_temporal_graph()

    def _next_edge_id(self) -> str:
        max_seq = 0
        for edge in self._temporal_edges:
            raw = str(edge.get("edge_id", ""))
            if raw.startswith("EDGE-"):
                try:
                    max_seq = max(max_seq, int(raw.split("-", 1)[1]))
                except ValueError:
                    continue
        return f"EDGE-{max_seq + 1:04d}"

    def register_temporal_edge(
        self,
        upstream: str,
        downstream: str,
        effective_at: float,
        *,
        transform: str = "direct",
        edge_type: str = "etl",
        is_correction: bool = False,
        confirmed: bool = True,
        correction_note: str = "",
        registered_at: float | None = None,
        version: int = 1,
        replaces_edge_id: str | None = None,
    ) -> dict[str, Any]:
        """登记一条时间化边。补登记（registered_at 晚于 effective_at）也允许，
        因为历史时刻的更正边可能在试算期间才到达。"""
        edge = {
            "edge_id": self._next_edge_id(),
            "upstream": upstream,
            "downstream": downstream,
            "transform": transform,
            "edge_type": edge_type,
            "effective_at": float(effective_at),
            "replaced_at": None,
            "status": "生效",
            "is_correction": bool(is_correction),
            "confirmed": bool(confirmed),
            "correction_note": correction_note,
            "registered_at": float(registered_at if registered_at is not None else time.time()),
            "version": version,
            "replaces_edge_id": replaces_edge_id,
            "successor_edge_id": None,
        }
        self._temporal_edges.append(edge)
        return edge

    def replace_temporal_edge(
        self,
        edge_id: str,
        replaced_at: float,
        new_effective_at: float,
        *,
        transform: str | None = None,
        edge_type: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """边变更：在 replaced_at 废弃旧版本，同时登记同上下游的新版本边。

        新旧版本通过 replaces_edge_id / successor_edge_id 显式关联，
        历史查询取旧版本，当前查询取新版本，两个版本的有效边都可追溯。
        """
        old = self.find_temporal_edge(edge_id)
        if old is None:
            raise KeyError(edge_id)
        if replaced_at < old["effective_at"]:
            raise ValueError("替换时刻不能早于旧边的生效时刻")
        if new_effective_at < replaced_at:
            raise ValueError("新边生效时刻不能早于替换时刻")
        old["replaced_at"] = float(replaced_at)
        old["status"] = "已变更"
        new_edge = self.register_temporal_edge(
            old["upstream"],
            old["downstream"],
            new_effective_at,
            transform=old["transform"] if transform is None else transform,
            edge_type=old["edge_type"] if edge_type is None else edge_type,
            version=old.get("version", 1) + 1,
            replaces_edge_id=old["edge_id"],
        )
        old["successor_edge_id"] = new_edge["edge_id"]
        return old, new_edge

    def temporal_edges(self) -> list[dict[str, Any]]:
        return list(self._temporal_edges)

    def find_temporal_edge(self, edge_id: str) -> dict[str, Any] | None:
        for edge in self._temporal_edges:
            if edge.get("edge_id") == edge_id:
                return edge
        return None

    def effective_edges_at(
        self,
        at_ts: float,
        *,
        include_unconfirmed_corrections: bool = False,
    ) -> list[dict[str, Any]]:
        """返回某时刻的有效边：effective_at <= at 且未被替换（replaced_at 为空或晚于该时刻）。

        更正边默认只有 confirmed=True 才计入；include_unconfirmed_corrections
        给“采用更正图”的显式策略在更正尚未确认时留出可见性（仍由服务层决定是否拒绝）。
        """
        result = []
        for edge in self._temporal_edges:
            if edge["effective_at"] > at_ts:
                continue
            replaced_at = edge.get("replaced_at")
            if replaced_at is not None and replaced_at <= at_ts:
                continue
            if edge.get("is_correction") and not edge.get("confirmed"):
                if not include_unconfirmed_corrections:
                    continue
            result.append(edge)
        return result

    def deprecate_temporal_edge(self, edge_id: str, replaced_at: float) -> dict[str, Any] | None:
        """在 replaced_at 时刻废弃一条边：历史时刻仍可见，当前图不再有效。"""
        edge = self.find_temporal_edge(edge_id)
        if edge is None:
            return None
        if replaced_at < edge["effective_at"]:
            raise ValueError("废弃时刻不能早于边的生效时刻")
        edge["replaced_at"] = float(replaced_at)
        edge["status"] = "已废弃"
        return edge

    def rollback_temporal_edge(self, edge_id: str, rollback_at: float) -> dict[str, Any] | None:
        """回滚当前边：从 rollback_at 起失效，但替换时间被保留，
        已发布评估引用的 edge_id/version 不受影响。"""
        edge = self.find_temporal_edge(edge_id)
        if edge is None:
            return None
        if edge.get("replaced_at") is None:
            edge["replaced_at"] = float(rollback_at)
        edge["status"] = "已回滚"
        return edge

    def confirm_correction_edge(self, edge_id: str) -> dict[str, Any] | None:
        edge = self.find_temporal_edge(edge_id)
        if edge is None:
            return None
        edge["confirmed"] = True
        return edge

    # ---------- 影响试算单 ----------
    def _next_trial_id(self) -> str:
        max_seq = 0
        for trial in self._impact_trials:
            raw = str(trial.get("trial_id", ""))
            if raw.startswith("TRIAL-"):
                try:
                    max_seq = max(max_seq, int(raw.split("-", 1)[1]))
                except ValueError:
                    continue
        return f"TRIAL-{max_seq + 1:04d}"

    def save_impact_trial(self, trial: dict[str, Any]) -> dict[str, Any]:
        if not trial.get("trial_id"):
            trial["trial_id"] = self._next_trial_id()
        if not any(t.get("trial_id") == trial["trial_id"] for t in self._impact_trials):
            self._impact_trials.append(trial)
        return trial

    def impact_trials(self) -> list[dict[str, Any]]:
        return list(self._impact_trials)

    def find_impact_trial(self, trial_id: str) -> dict[str, Any] | None:
        for trial in self._impact_trials:
            if trial.get("trial_id") == trial_id:
                return trial
        return None

    # ---------- 表格式影响清单（已发布、不可变） ----------
    def _next_impact_entry_id(self) -> int:
        return max((int(row.get("id", 0)) for row in self._impact_entries), default=0) + 1

    def add_impact_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        entry["id"] = self._next_impact_entry_id()
        self._impact_entries.append(entry)
        return entry

    def impact_entries(self) -> list[dict[str, Any]]:
        return list(self._impact_entries)

    def impact_entries_for_trial(self, trial_id: str) -> list[dict[str, Any]]:
        return [e for e in self._impact_entries if e.get("试算单号") == trial_id]


store = Store()