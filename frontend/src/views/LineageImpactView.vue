<template>
  <section class="page impact-page" data-module="lineage_impact">
    <header class="page-head">
      <div>
        <h2>历史血缘影响分析</h2>
        <p class="page-desc">
          按表与时刻查询有效血缘路径，关联变更前后两个版本的有效边；试算确认后回写表格式影响清单，
          历史评估不可变，当前边的废弃与回滚不会改掉已有评估。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn ghost" type="button" data-testid="reset-demo" @click="resetDemo">
          重置示例图
        </button>
        <button class="btn" type="button" @click="loadAll">刷新数据</button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="impact-error">{{ errorMessage }}</p>

    <!-- ① 按表 + 时刻查询影响范围 -->
    <article class="impact-card">
      <h3>① 按表与时刻筛选影响范围</h3>
      <form class="filter-bar" @submit.prevent="runQuery">
        <label class="filter-item">
          <span>源表</span>
          <input v-model="queryForm.sourceTable" data-testid="query-source" placeholder="如 A" required />
        </label>
        <label class="filter-item">
          <span>查询时刻</span>
          <input v-model="queryForm.at" data-testid="query-at" placeholder="时间戳或 YYYY-MM-DD HH:MM:SS" required />
        </label>
        <label class="filter-item">
          <span>更正策略</span>
          <select v-model="queryForm.policy" data-testid="query-policy">
            <option value="original_graph">继续沿用原图</option>
            <option value="corrected_graph">采用已确认更正</option>
          </select>
        </label>
        <button class="btn primary" data-testid="query-submit" type="submit">查询路径</button>
        <button class="btn" type="button" data-testid="create-trial" @click="saveTrial">生成试算单</button>
      </form>

      <div v-if="evaluation" class="eval-block" data-testid="eval-result">
        <p class="policy-note">
          策略：{{ evaluation.policy_label }} ｜ 有效边 {{ evaluation.effective_edges.length }} 条 ｜
          到达路径 {{ evaluation.path_total }} 条 ｜ 受影响表 {{ evaluation.affected_table_count }} 张（同表只计一次）
        </p>
        <p v-if="evaluation.corrections_available?.length" class="warn-text">
          该时刻存在 {{ evaluation.corrections_available.length }} 条历史更正边，请在试算单中确认沿用原图或采用更正。
        </p>
        <table class="data-table">
          <thead>
            <tr><th>受影响表</th><th>到达路径数</th><th>可复核路径</th></tr>
          </thead>
          <tbody>
            <tr v-for="t in evaluation.affected_tables" :key="t.table" :data-testid="`affected-${t.table}`">
              <td>{{ t.table }}</td>
              <td>{{ t.path_count }}</td>
              <td>
                <span v-for="pid in t.paths" :key="pid" class="path-tag">
                  {{ pathText(pid) }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
        <details class="edge-details">
          <summary>查看每条边的生效 / 替换时间</summary>
          <table class="data-table">
            <thead>
              <tr><th>边</th><th>状态</th><th>生效时间</th><th>替换时间</th><th>更正边</th></tr>
            </thead>
            <tbody>
              <tr v-for="e in evaluation.effective_edges" :key="e.edge_id">
                <td>{{ e.upstream }} → {{ e.downstream }}（{{ e.edge_id }}）</td>
                <td>{{ e.status }}</td>
                <td>{{ e.effective_at_text ?? '—' }}</td>
                <td>{{ e.replaced_at_text ?? '仍生效' }}</td>
                <td>{{ e.is_correction ? (e.confirmed ? '已确认更正' : '待确认更正') : '否' }}</td>
              </tr>
            </tbody>
          </table>
        </details>
      </div>
    </article>

    <!-- ② 版本化边管理 -->
    <article class="impact-card">
      <h3>② 版本化血缘边（生效 / 替换时间）</h3>
      <form class="filter-bar" @submit.prevent="addEdge">
        <label class="filter-item"><span>上游表</span><input v-model="edgeForm.upstream" data-testid="edge-up" required /></label>
        <label class="filter-item"><span>下游表</span><input v-model="edgeForm.downstream" data-testid="edge-down" required /></label>
        <label class="filter-item"><span>生效时刻</span><input v-model="edgeForm.effectiveAt" data-testid="edge-at" placeholder="时间戳或日期时间" required /></label>
        <label class="filter-item">
          <span>更正边</span>
          <input v-model="edgeForm.isCorrection" type="checkbox" data-testid="edge-correction" />
        </label>
        <button class="btn primary" type="submit">登记边</button>
      </form>
      <table class="data-table">
        <thead>
          <tr><th>边编号</th><th>上游→下游</th><th>版本</th><th>状态</th><th>生效时间</th><th>替换时间</th><th>版本关联</th><th>更正</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="e in edges" :key="e.edge_id" :data-status="e.status">
            <td>{{ e.edge_id }}</td>
            <td>{{ e.upstream }} → {{ e.downstream }}</td>
            <td>v{{ e.version }}</td>
            <td>{{ e.status }}</td>
            <td>{{ e.effective_at_text ?? '—' }}</td>
            <td>{{ e.replaced_at_text ?? '仍生效' }}</td>
            <td class="note-cell">
              <span v-if="e.replaces_edge_id">接替 {{ e.replaces_edge_id }}</span>
              <span v-if="e.successor_edge_id">→ {{ e.successor_edge_id }}</span>
            </td>
            <td>{{ e.is_correction ? (e.confirmed ? '已确认' : '待确认') : '—' }}</td>
            <td class="row-actions">
              <button class="link" type="button" @click="deprecate(e)">废弃</button>
              <button class="link" type="button" @click="replace(e)">变更替换</button>
              <button class="link" type="button" @click="rollback(e)">回滚</button>
              <button v-if="e.is_correction && !e.confirmed" class="link" type="button" @click="confirmCorrection(e)">
                确认更正
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </article>

    <!-- ③ 试算单 -->
    <article class="impact-card">
      <h3>③ 影响试算单（草稿冻结，确认后不可变）</h3>
      <table class="data-table">
        <thead>
          <tr><th>试算单号</th><th>源表</th><th>评估时刻</th><th>策略</th><th>状态</th><th>更正策略说明</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="t in trials" :key="t.trial_id" :data-testid="`trial-${t.trial_id}`">
            <td>{{ t.trial_id }}</td>
            <td>{{ t.source_table }}</td>
            <td>{{ t.query_ts_text }}</td>
            <td>{{ t.policy_label }}</td>
            <td>{{ t.status }}</td>
            <td class="note-cell">{{ t.correction_policy_note }}</td>
            <td class="row-actions">
              <button class="link" type="button" @click="showTrial(t)">查看冻结路径</button>
              <button
                v-if="t.status === '试算中'"
                class="link"
                type="button"
                :data-testid="`confirm-${t.trial_id}`"
                @click="publishTrial(t)"
              >
                确认发布
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="openedTrial" class="eval-block" data-testid="trial-detail">
        <p class="policy-note">
          {{ openedTrial.trial_id }} 冻结范围：
          {{ openedTrial.frozen_evaluation.affected_tables.map((t) => t.table).join('、') || '无可达表' }}
          <span v-if="openedTrial.drift_report">
            ｜ 发布时当前图新增：{{ openedTrial.drift_report.new_tables_in_current_graph.join('、') || '无' }}
          </span>
        </p>
        <ul class="path-list">
          <li v-for="p in openedTrial.frozen_evaluation.paths" :key="p.path_id">{{ p.path_text }}</li>
        </ul>
      </div>
    </article>

    <!-- ④ 表格式影响清单 -->
    <article class="impact-card">
      <h3>④ 表格式影响清单（已发布评估，回滚不改写）</h3>
      <form class="filter-bar" @submit.prevent="loadEntries">
        <label class="filter-item">
          <span>按目标表筛选</span>
          <input v-model="entryFilter" data-testid="entry-filter" placeholder="如 D" />
        </label>
        <button class="btn" type="submit">筛选</button>
      </form>
      <table class="data-table">
        <thead>
          <tr><th>清单编号</th><th>源表</th><th>目标表</th><th>评估时刻</th><th>策略</th><th>路径数</th><th>到达路径</th><th>试算单</th></tr>
        </thead>
        <tbody>
          <tr v-for="e in entries" :key="e.id" :data-testid="`entry-${e.id}`">
            <td>{{ e.清单编号 }}</td>
            <td>{{ e.源表 }}</td>
            <td>{{ e.目标表 }}</td>
            <td>{{ e.评估时刻 }}</td>
            <td>{{ e.试算策略 }}</td>
            <td>{{ e.到达路径数 }}</td>
            <td class="note-cell">{{ e.到达路径 }}</td>
            <td>{{ e.试算单号 }}</td>
          </tr>
        </tbody>
      </table>
    </article>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  actOnTemporalEdge,
  confirmImpactTrial,
  createImpactTrial,
  listImpactEntries,
  listImpactTrials,
  listTemporalEdges,
  queryLineageImpact,
  registerTemporalEdge,
  replaceTemporalEdge,
  resetImpactDemo,
} from '@/api/backend-service'
import type {
  ImpactEntry,
  ImpactEvaluation,
  ImpactTrial,
  TemporalEdge,
} from '@/api/backend-service'

const edges = ref<TemporalEdge[]>([])
const trials = ref<ImpactTrial[]>([])
const entries = ref<ImpactEntry[]>([])
const evaluation = ref<ImpactEvaluation | null>(null)
const openedTrial = ref<ImpactTrial | null>(null)
const errorMessage = ref('')
const entryFilter = ref('')

const queryForm = ref({ sourceTable: 'A', at: '', policy: 'original_graph' })
const edgeForm = ref({
  upstream: '',
  downstream: '',
  effectiveAt: '',
  isCorrection: false,
})

function fail(error: unknown) {
  errorMessage.value = error instanceof Error ? error.message : '操作失败'
}

function pathText(pathId: number): string {
  const path = evaluation.value?.paths.find((p) => p.path_id === pathId)
  return path ? path.path_text : `#${pathId}`
}

async function loadEdges() {
  const res = await listTemporalEdges()
  edges.value = res.items
}

async function loadTrials() {
  const res = await listImpactTrials()
  trials.value = res.items
}

async function loadEntries() {
  const res = await listImpactEntries(entryFilter.value.trim() || undefined)
  entries.value = res.items
}

async function loadAll() {
  errorMessage.value = ''
  try {
    await Promise.all([loadEdges(), loadTrials(), loadEntries()])
  } catch (error) {
    fail(error)
  }
}

async function runQuery() {
  errorMessage.value = ''
  try {
    const result = await queryLineageImpact(
      queryForm.value.sourceTable.trim(),
      queryForm.value.at.trim(),
      queryForm.value.policy,
    )
    if (result.ok === false || (result as { reason?: string }).reason) {
      errorMessage.value = result.reason ?? '查询失败'
      evaluation.value = null
      return
    }
    evaluation.value = result
  } catch (error) {
    fail(error)
  }
}

async function saveTrial() {
  errorMessage.value = ''
  try {
    const result = await createImpactTrial(
      queryForm.value.sourceTable.trim(),
      queryForm.value.at.trim(),
      queryForm.value.policy,
    )
    if (!result.ok) {
      errorMessage.value = result.reason ?? '试算单生成失败'
      return
    }
    await loadTrials()
  } catch (error) {
    fail(error)
  }
}

async function addEdge() {
  errorMessage.value = ''
  try {
    const result = await registerTemporalEdge({
      upstream: edgeForm.value.upstream.trim(),
      downstream: edgeForm.value.downstream.trim(),
      effective_at: edgeForm.value.effectiveAt.trim(),
      is_correction: edgeForm.value.isCorrection,
      confirmed: !edgeForm.value.isCorrection,
    })
    if (!result.ok) {
      errorMessage.value = result.reason ?? '边登记失败'
      return
    }
    edgeForm.value = { upstream: '', downstream: '', effectiveAt: '', isCorrection: false }
    await loadEdges()
  } catch (error) {
    fail(error)
  }
}

async function deprecate(edge: TemporalEdge) {
  const at = window.prompt(`废弃 ${edge.upstream}→${edge.downstream} 的时刻（时间戳或日期时间）`)
  if (!at) return
  const res = await actOnTemporalEdge(edge.edge_id, 'deprecate', at)
  if (!res.ok) errorMessage.value = res.reason ?? '废弃失败'
  await loadEdges()
}

async function replace(edge: TemporalEdge) {
  const replacedAt = window.prompt(
    `${edge.upstream}→${edge.downstream} 旧版本 v${edge.version} 的替换时刻`,
  )
  if (!replacedAt) return
  const newEffectiveAt = window.prompt('新版本边的生效时刻')
  if (!newEffectiveAt) return
  const res = await replaceTemporalEdge(edge.edge_id, replacedAt, newEffectiveAt)
  if (!res.ok) {
    errorMessage.value = res.reason ?? '变更替换失败'
  }
  await loadEdges()
}

async function rollback(edge: TemporalEdge) {
  const at = window.prompt(`回滚 ${edge.upstream}→${edge.downstream} 的时刻（时间戳或日期时间）`)
  if (!at) return
  const res = await actOnTemporalEdge(edge.edge_id, 'rollback', at)
  if (!res.ok) errorMessage.value = res.reason ?? '回滚失败'
  await Promise.all([loadEdges(), loadEntries()])
}

async function confirmCorrection(edge: TemporalEdge) {
  const res = await actOnTemporalEdge(edge.edge_id, 'confirm-correction', '')
  if (!res.ok) errorMessage.value = res.reason ?? '更正确认失败'
  await loadEdges()
}

function showTrial(trial: ImpactTrial) {
  openedTrial.value = trial
}

async function publishTrial(trial: ImpactTrial) {
  errorMessage.value = ''
  try {
    const res = await confirmImpactTrial(trial.trial_id)
    if (!res.ok) {
      errorMessage.value = res.reason ?? '发布失败'
      return
    }
    await Promise.all([loadTrials(), loadEntries()])
  } catch (error) {
    fail(error)
  }
}

async function resetDemo() {
  errorMessage.value = ''
  try {
    await resetImpactDemo(true)
    evaluation.value = null
    openedTrial.value = null
    await loadAll()
  } catch (error) {
    fail(error)
  }
}

onMounted(loadAll)
</script>

<style scoped>
.impact-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.impact-card {
  background: var(--card-bg, #fff);
  border: 1px solid var(--border-color, #e3e6ee);
  border-radius: 10px;
  padding: 16px 18px;
}

.impact-card h3 {
  margin: 0 0 12px;
  font-size: 16px;
}

.eval-block {
  margin-top: 12px;
  padding: 12px;
  background: var(--muted-bg, #f7f8fb);
  border-radius: 8px;
}

.policy-note {
  margin: 0 0 8px;
  font-weight: 600;
}

.warn-text {
  color: #b26a00;
}

.path-tag {
  display: inline-block;
  margin: 2px 6px 2px 0;
  padding: 2px 8px;
  border-radius: 999px;
  background: #eef3ff;
  font-size: 12px;
}

.note-cell {
  max-width: 320px;
  font-size: 12px;
  color: #555;
}

.edge-details {
  margin-top: 10px;
}

.path-list {
  margin: 8px 0 0;
  padding-left: 18px;
}

select {
  padding: 6px 8px;
  border: 1px solid var(--border-color, #d6d9e0);
  border-radius: 6px;
}
</style>
