"""历史血缘影响范围验收测试。

完整复现需求场景：
- 图：A→B、A→C、B→D、C→D，D 有两条到达路径 A→B→D 和 A→C→D。
- 先以当前图试算；随后在两个时间点分别“废弃 B→D”和“新增 D→E”。
- 按原时刻查询：D 只计一次，但两条路径均能复核（路径明细保留）。
- 试算尚未确认时补登记一条在原时刻已生效的 C→F：
  * 已发布评估按试算冻结的范围核对，F 不进入采用范围；
  * 原试算仍可查；按原时刻重新查询时 F 才出现。
- 更正边在发布评估前到达：试算单必须写明沿用原图或采用已确认更正。
- 当前边回滚不会改掉已有评估。
"""
from __future__ import annotations

import pytest

from app.services.lineage_impact import (
    POLICY_CORRECTED,
    POLICY_ORIGINAL,
    TRIAL_CONFIRMED,
    lineage_impact_service as svc,
)
from app.store import store


# 固定时刻，保证测试确定可复现
T0 = 1_000_000          # 图全部生效的基准时刻
T1 = 2_000_000          # 第一个变更时间点：废弃 B→D
T2 = 3_000_000          # 第二个变更时间点：新增 D→E
T_LATE = 4_000_000      # 补登记 C→F 的登记时刻（晚于试算创建）


@pytest.fixture(autouse=True)
def clean_graph():
    store.reset_temporal_data(reseed=False)
    # 自建确定时刻的图：A→B→D 与 A→C→D
    edges = [
        ("A", "B"),
        ("A", "C"),
        ("B", "D"),
        ("C", "D"),
    ]
    registered = {}
    for idx, (up, down) in enumerate(edges, start=1):
        # 全部在基准时刻 T0 生效，T1/T2 作为后续变更时刻，保证历史查询语义清晰
        edge = store.register_temporal_edge(
            up, down, effective_at=T0, registered_at=T0
        )
        registered[f"{up}->{down}"] = edge["edge_id"]
    yield registered
    store.reset_temporal_data(reseed=False)


def _paths_for(evaluation: dict, table: str) -> list[str]:
    return [p["path_text"] for p in evaluation["paths"] if p["tables"][-1] == table]


# ----------------------------------------------------------------------
# 1. 当前图试算：A→B→D 与 A→C→D 两条路径都要能查到，D 只计一次
# ----------------------------------------------------------------------
class TestCurrentGraphTrial:
    def test_two_arrival_paths_but_d_counted_once(self, clean_graph):
        result = svc.query_impact("A", T1, POLICY_ORIGINAL)
        assert result["ok"] is True

        # 两条到达路径都可复核
        d_paths = _paths_for(result, "D")
        assert d_paths == ["A → B → D", "A → C → D"]

        # D 在影响清单口径里只计一次，但路径数为 2
        d_bucket = next(t for t in result["affected_tables"] if t["table"] == "D")
        assert d_bucket["path_count"] == 2
        affected_names = [t["table"] for t in result["affected_tables"]]
        assert affected_names.count("D") == 1
        assert sorted(affected_names) == ["B", "C", "D"]

        # 每条边都带生效与替换时间
        for edge in result["effective_edges"]:
            assert "effective_at" in edge and "replaced_at" in edge
            assert edge["effective_at_text"] is not None

    def test_create_trial_freezes_snapshot(self, clean_graph):
        created = svc.create_trial("A", T1, POLICY_ORIGINAL)
        assert created["ok"] is True
        trial = created["trial"]
        assert trial["status"] == "试算中"
        assert trial["frozen_evaluation"]["affected_table_count"] == 3


# ----------------------------------------------------------------------
# 2. 两个时间点分别废弃 B→D、新增 D→E；按原时刻查询不受影响
# ----------------------------------------------------------------------
class TestEdgeVersioningAtTimestamps:
    def test_deprecate_and_add_edges_then_historical_query(self, clean_graph):
        bd_id = clean_graph["B->D"]

        # 时间点 1：废弃 B→D
        dep = svc.deprecate_edge(bd_id, T1)
        assert dep["ok"] is True
        assert dep["edge"]["replaced_at"] == T1
        assert dep["edge"]["status"] == "已废弃"

        # 时间点 2：新增 D→E（在 T2 生效）
        add = svc.register_edge("D", "E", T2)
        assert add["ok"] is True

        # 按原时刻 T0+2（早于 T1）查询：B→D 仍有效，D→E 尚未生效
        historical = svc.query_impact("A", T0 + 2, POLICY_ORIGINAL)
        assert historical["ok"] is True
        assert sorted(_paths_for(historical, "D")) == ["A → B → D", "A → C → D"]
        assert all(t["table"] != "E" for t in historical["affected_tables"])

        # T1 之后查询：B→D 已失效，只剩 A→C→D；D→E 已生效
        latest = svc.query_impact("A", T2 + 1, POLICY_ORIGINAL)
        assert latest["ok"] is True
        assert _paths_for(latest, "D") == ["A → C → D"]
        assert any(t["table"] == "E" for t in latest["affected_tables"])

        # 废弃边仍可在边清单中按替换时间追溯
        bd = store.find_temporal_edge(bd_id)
        assert bd["replaced_at"] == T1
        assert bd["status"] == "已废弃"


# ----------------------------------------------------------------------
# 3. 试算未确认时补登记“原时刻已生效”的 C→F
# ----------------------------------------------------------------------
class TestLateRegisteredEdge:
    def test_late_edge_excluded_from_published_but_visible_in_fresh_query(self, clean_graph):
        # 先以当前图（T1）试算
        created = svc.create_trial("A", T1, POLICY_ORIGINAL)
        trial_id = created["trial"]["trial_id"]

        # 试算尚未确认时，补登记 C→F：生效时刻在原时刻 T1 之前，但登记时刻更晚
        late = store.register_temporal_edge(
            "C", "F", effective_at=T0, registered_at=T_LATE
        )
        assert late["registered_at"] == T_LATE
        assert late["effective_at"] <= T1

        # 原试算仍可查，且冻结范围不含 F
        fetched = svc.get_trial(trial_id)
        assert fetched["ok"] is True
        frozen_tables = [
            t["table"] for t in fetched["trial"]["frozen_evaluation"]["affected_tables"]
        ]
        assert "F" not in frozen_tables

        # 按原时刻重新查询：补登记边已进入有效路径，F 出现
        fresh = svc.query_impact("A", T1, POLICY_ORIGINAL)
        assert fresh["ok"] is True
        assert any(t["table"] == "F" for t in fresh["affected_tables"])
        assert _paths_for(fresh, "F") == ["A → C → F"]

        # 确认发布：以冻结评估为准，F 不进入采用范围
        confirmed = svc.confirm_trial(trial_id)
        assert confirmed["ok"] is True
        entries = store.impact_entries_for_trial(trial_id)
        entry_tables = {e["目标表"] for e in entries}
        assert "F" not in entry_tables
        assert entry_tables == {"B", "C", "D"}

        # 漂移报告要指出当前图比冻结图多出 F
        assert "F" in confirmed["drift_report"]["new_tables_in_current_graph"]

        # 发布后再按原时刻查，F 依旧在当前图查询结果里，但不影响已发布清单
        again = svc.query_impact("A", T1, POLICY_ORIGINAL)
        assert any(t["table"] == "F" for t in again["affected_tables"])
        assert {e["目标表"] for e in store.impact_entries_for_trial(trial_id)} == entry_tables

    def test_new_trial_after_late_edge_adopts_f(self, clean_graph):
        svc.create_trial("A", T1, POLICY_ORIGINAL)
        store.register_temporal_edge("C", "F", effective_at=T0, registered_at=T_LATE)

        # 补登记之后新建的试算，F 应进入采用范围
        second = svc.create_trial("A", T1, POLICY_ORIGINAL)
        assert second["ok"] is True
        tables = [
            t["table"]
            for t in second["trial"]["frozen_evaluation"]["affected_tables"]
        ]
        assert "F" in tables
        confirmed = svc.confirm_trial(second["trial"]["trial_id"])
        assert confirmed["ok"] is True
        assert "F" in {e["目标表"] for e in store.impact_entries_for_trial(
            second["trial"]["trial_id"])}


# ----------------------------------------------------------------------
# 4. 历史更正边在发布评估前到达：必须显式写明策略
# ----------------------------------------------------------------------
class TestCorrectionEdgePolicy:
    def _make_correction(self) -> str:
        # 更正边：原时刻已生效的 B→F，先不确认
        edge = store.register_temporal_edge(
            "B", "F", effective_at=T0, registered_at=T_LATE,
            is_correction=True, confirmed=False, correction_note="补录历史血缘",
        )
        return edge["edge_id"]

    def test_trial_notes_original_graph_policy(self, clean_graph):
        trial = svc.create_trial("A", T1, POLICY_ORIGINAL)["trial"]
        # 此时还没有更正边
        assert "无更正边" in trial["correction_policy_note"]

        correction_id = self._make_correction()

        # 新建原图策略试算：试算单要写明继续沿用原图
        original_trial = svc.create_trial("A", T1, POLICY_ORIGINAL)["trial"]
        assert original_trial["policy"] == POLICY_ORIGINAL
        assert "继续沿用原图" in original_trial["correction_policy_note"]
        assert original_trial["corrections_available"][0]["edge_id"] == correction_id
        frozen_tables = [
            t["table"]
            for t in original_trial["frozen_evaluation"]["affected_tables"]
        ]
        assert "F" not in frozen_tables

        # 确认后发布的清单同样不含 F
        confirmed = svc.confirm_trial(original_trial["trial_id"])
        assert confirmed["ok"] is True
        assert "F" not in {
            e["目标表"] for e in store.impact_entries_for_trial(original_trial["trial_id"])
        }

    def test_corrected_policy_rejected_until_confirmed(self, clean_graph):
        correction_id = self._make_correction()

        # 未确认更正：不允许按更正图评估
        rejected = svc.query_impact("A", T1, POLICY_CORRECTED)
        assert rejected["ok"] is False
        assert "尚未确认" in rejected["reason"]
        rejected_trial = svc.create_trial("A", T1, POLICY_CORRECTED)
        assert rejected_trial["ok"] is False

        # 确认更正后重新查：F 经 A→B→F 进入有效路径
        confirm = svc.confirm_correction(correction_id)
        assert confirm["ok"] is True
        corrected = svc.query_impact("A", T1, POLICY_CORRECTED)
        assert corrected["ok"] is True
        assert _paths_for(corrected, "F") == ["A → B → F"]

        trial = svc.create_trial("A", T1, POLICY_CORRECTED)["trial"]
        assert "采用已确认更正" in trial["correction_policy_note"]
        confirmed = svc.confirm_trial(trial["trial_id"])
        assert confirmed["ok"] is True
        assert "F" in {
            e["目标表"] for e in store.impact_entries_for_trial(trial["trial_id"])
        }


# ----------------------------------------------------------------------
# 5. 当前边回滚不会改掉已有评估；历史任务按原时刻保留路径与策略
# ----------------------------------------------------------------------
class TestRollbackImmutability:
    def test_rollback_does_not_change_published_evaluation(self, clean_graph):
        created = svc.create_trial("A", T1, POLICY_ORIGINAL)
        trial_id = created["trial"]["trial_id"]
        confirmed = svc.confirm_trial(trial_id)
        assert confirmed["ok"] is True
        before = store.impact_entries_for_trial(trial_id)
        before_snapshot = [
            (e["目标表"], e["到达路径"], e["评估时刻"], e["试算策略"]) for e in before
        ]

        # 回滚当前边 A→C：当前图查询立刻少一条路径
        ac_id = clean_graph["A->C"]
        rollback = svc.rollback_edge(ac_id, T2)
        assert rollback["ok"] is True
        assert rollback["edge"]["status"] == "已回滚"

        current = svc.query_impact("A", T2 + 1, POLICY_ORIGINAL)
        assert _paths_for(current, "D") == ["A → B → D"]

        # 已发布评估原样保留
        after = store.impact_entries_for_trial(trial_id)
        after_snapshot = [
            (e["目标表"], e["到达路径"], e["评估时刻"], e["试算策略"]) for e in after
        ]
        assert after_snapshot == before_snapshot

        # 试算单状态保持已确认，重复发布被拒绝
        again = svc.confirm_trial(trial_id)
        assert again["ok"] is False
        fetched = svc.get_trial(trial_id)
        assert fetched["trial"]["status"] == TRIAL_CONFIRMED

        # 按原时刻查询仍能复核两条路径（回滚发生在 T2，晚于原时刻）
        historical = svc.query_impact("A", T1, POLICY_ORIGINAL)
        assert sorted(_paths_for(historical, "D")) == ["A → B → D", "A → C → D"]

    def test_impact_entry_keeps_full_path_and_edge_timings(self, clean_graph):
        created = svc.create_trial("A", T1, POLICY_ORIGINAL)
        trial_id = created["trial"]["trial_id"]
        svc.confirm_trial(trial_id)
        d_entry = next(
            e for e in store.impact_entries_for_trial(trial_id) if e["目标表"] == "D"
        )
        # 两条路径都写进表格式影响清单
        assert d_entry["到达路径数"] == 2
        assert "A → B → D" in d_entry["到达路径"]
        assert "A → C → D" in d_entry["到达路径"]
        # 路径明细保留每条边的生效/替换时间
        for path in d_entry["路径明细"]:
            for edge in path["edges"]:
                assert "effective_at" in edge and "replaced_at" in edge


# ----------------------------------------------------------------------
# 6. 边变更：关联变更前后两个版本的有效边
# ----------------------------------------------------------------------
class TestEdgeVersionReplacement:
    def test_replace_links_old_and_new_versions(self, clean_graph):
        bd_id = clean_graph["B->D"]
        result = svc.replace_edge(bd_id, T1, T2, transform="SELECT 重写后逻辑")
        assert result["ok"] is True
        old, new = result["old_edge"], result["new_edge"]

        # 两个版本显式关联
        assert old["successor_edge_id"] == new["edge_id"]
        assert new["replaces_edge_id"] == old["edge_id"]
        assert new["version"] == old["version"] + 1
        assert old["status"] == "已变更" and old["replaced_at"] == T1
        assert new["effective_at"] == T2 and new["replaced_at"] is None
        assert new["transform"] == "SELECT 重写后逻辑"

        # 变更前时刻取旧版本，变更后时刻取新版本，同一对表始终有有效边
        before = svc.query_impact("A", T1 - 1, POLICY_ORIGINAL)
        after = svc.query_impact("A", T2 + 1, POLICY_ORIGINAL)
        assert sorted(_paths_for(before, "D")) == ["A → B → D", "A → C → D"]
        assert _paths_for(after, "D") == ["A → B → D", "A → C → D"]
        before_bd = next(e for p in before["paths"] for e in p["edges"]
                         if e["upstream"] == "B" and e["downstream"] == "D")
        after_bd = next(e for p in after["paths"] for e in p["edges"]
                        if e["upstream"] == "B" and e["downstream"] == "D")
        assert before_bd["edge_id"] == bd_id
        assert after_bd["edge_id"] == new["edge_id"]
        assert after_bd["replaces_edge_id"] == bd_id

        # 替换时刻的间隔窗口内（T1..T2）旧边已失效、新边未生效，B→D 暂时断开
        gap = svc.query_impact("A", (T1 + T2) / 2, POLICY_ORIGINAL)
        assert _paths_for(gap, "D") == ["A → C → D"]

    def test_replace_rejects_illegal_timestamps(self, clean_graph):
        bd_id = clean_graph["B->D"]
        bad = svc.replace_edge(bd_id, T0 - 10, T2)
        assert bad["ok"] is False and "替换时刻" in bad["reason"]
        bad2 = svc.replace_edge(bd_id, T2, T1)
        assert bad2["ok"] is False and "新边生效时刻" in bad2["reason"]
        missing = svc.replace_edge("EDGE-9999", T1, T2)
        assert missing["ok"] is False and "不存在" in missing["reason"]


# ----------------------------------------------------------------------
# 7. table_format 消费影响清单
# ----------------------------------------------------------------------
class TestTableFormatConsumption:
    def test_impact_list_and_write_strategy(self):
        from app.services import service_for

        tf = service_for("table_format")
        created = svc.create_trial("A", T1, POLICY_ORIGINAL)
        svc.confirm_trial(created["trial"]["trial_id"])

        impact = tf.impact_list_for_table("D")
        assert impact["ok"] is True
        assert impact["evaluation_count"] >= 1
        assert impact["entries"][0]["到达路径数"] == 2

        entry = store.find("table_format", 1)
        entry["status"] = "已生效"
        entry["目标表"] = "D"
        strategy = tf.write_strategy_for_evaluation(1)
        assert strategy["ok"] is True
        assert strategy["max_arrival_paths"] == 2
        assert strategy["recommended_write_mode"] == "upsert"
