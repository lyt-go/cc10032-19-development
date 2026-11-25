import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ============================================================
// 历史血缘影响范围：版本化边、按时刻路径查询、试算单、影响清单
// ============================================================
const IMPACT_BASE = '/api/lineage-impact'

export type TemporalEdge = {
  edge_id: string
  upstream: string
  downstream: string
  transform: string
  edge_type: string
  effective_at: number
  effective_at_text: string | null
  replaced_at: number | null
  replaced_at_text: string | null
  status: string
  is_correction: boolean
  confirmed: boolean
  version: number
  replaces_edge_id: string | null
  successor_edge_id: string | null
}

export type ImpactPath = {
  path_id: number
  tables: string[]
  path_text: string
  edges: TemporalEdge[]
}

export type AffectedTable = {
  table: string
  path_count: number
  paths: number[]
}

export type ImpactEvaluation = {
  ok?: boolean
  source_table: string
  query_ts: number
  query_ts_text: string
  policy: string
  policy_label: string
  effective_edges: TemporalEdge[]
  paths: ImpactPath[]
  affected_tables: AffectedTable[]
  affected_table_count: number
  path_total: number
  corrections_available?: TemporalEdge[]
  reason?: string
  unconfirmed_corrections?: TemporalEdge[]
}

export type ImpactTrial = {
  trial_id: string
  source_table: string
  query_ts: number
  query_ts_text: string
  policy: string
  policy_label: string
  status: string
  created_at_text: string
  confirmed_at: number | null
  correction_policy_note: string
  corrections_available: TemporalEdge[]
  frozen_evaluation: ImpactEvaluation
  published_entry_ids: number[]
  drift_report: {
    new_tables_in_current_graph: string[]
    missing_tables_in_current_graph: string[]
    path_count_changed: { table: string; frozen_path_count: number; current_path_count: number }[]
  } | null
}

export type ImpactEntry = {
  id: number
  清单编号: string
  源表: string
  目标表: string
  评估时刻: string
  试算策略: string
  到达路径数: number
  到达路径: string
  试算单号: string
  发布时间: string
  status: string
  immutable: boolean
}

async function impactCall<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await request(`${IMPACT_BASE}${path}`, init)
  return (await response.json()) as T
}

export function listTemporalEdges(): Promise<{ ok: boolean; items: TemporalEdge[] }> {
  return impactCall('/edges')
}

export function registerTemporalEdge(payload: {
  upstream: string
  downstream: string
  effective_at: string | number
  is_correction?: boolean
  confirmed?: boolean
  correction_note?: string
}): Promise<{ ok: boolean; edge?: TemporalEdge; reason?: string }> {
  return impactCall('/edges', { method: 'POST', body: JSON.stringify(payload) })
}

export function actOnTemporalEdge(
  edgeId: string,
  action: 'deprecate' | 'rollback' | 'confirm-correction',
  at: string | number,
): Promise<{ ok: boolean; edge?: TemporalEdge; reason?: string }> {
  const body = action === 'confirm-correction' ? '{}' : JSON.stringify({ at })
  return impactCall(`/edges/${edgeId}/${action}`, { method: 'POST', body })
}

export function replaceTemporalEdge(
  edgeId: string,
  replacedAt: string | number,
  newEffectiveAt: string | number,
): Promise<{
  ok: boolean
  old_edge?: TemporalEdge
  new_edge?: TemporalEdge
  reason?: string
}> {
  return impactCall(`/edges/${edgeId}/replace`, {
    method: 'POST',
    body: JSON.stringify({ replaced_at: replacedAt, new_effective_at: newEffectiveAt }),
  })
}

export function queryLineageImpact(
  sourceTable: string,
  at: string,
  policy: string,
): Promise<ImpactEvaluation> {
  return impactCall('/query', {
    method: 'POST',
    body: JSON.stringify({ source_table: sourceTable, at, policy }),
  })
}

export function listImpactTrials(): Promise<{ ok: boolean; items: ImpactTrial[] }> {
  return impactCall('/trials')
}

export function createImpactTrial(
  sourceTable: string,
  at: string,
  policy: string,
): Promise<{ ok: boolean; trial?: ImpactTrial; reason?: string }> {
  return impactCall('/trials', {
    method: 'POST',
    body: JSON.stringify({ source_table: sourceTable, at, policy }),
  })
}

export function confirmImpactTrial(
  trialId: string,
): Promise<{ ok: boolean; entry_ids?: number[]; published_count?: number; reason?: string }> {
  return impactCall(`/trials/${trialId}/confirm`, { method: 'POST' })
}

export function listImpactEntries(
  targetTable?: string,
): Promise<{ ok: boolean; items: ImpactEntry[]; total: number }> {
  const query = targetTable ? `?target_table=${encodeURIComponent(targetTable)}` : ''
  return impactCall(`/impact-entries${query}`)
}

export function resetImpactDemo(reseed = true): Promise<{ ok: boolean; message: string }> {
  return impactCall(`/reset?reseed=${reseed}`, { method: 'POST' })
}
