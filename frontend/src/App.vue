<template>
  <div class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">流批一体数据集成平台</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>
    <main class="app-main">
      <header class="app-head">
        <span class="head-desc">面向实时流接入、批流合并、变更捕获与分区治理的流批一体数据集成平台。</span>
        <span class="head-user">当前值班：数据工程组</span>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
const navItems = [{ label: "运营概览", path: "/" }, { label: "流式数据源", path: "/stream_source" }, { label: "批次分片", path: "/batch_slice" }, { label: "变更捕获", path: "/change_capture" }, { label: "Schema治理", path: "/schema_govern" }, { label: "水位管理", path: "/watermark" }, { label: "迟到数据", path: "/late_data" }, { label: "流式关联", path: "/stream_lookup" }, { label: "合并策略", path: "/merge_plan" }, { label: "分区扩缩", path: "/partition_scale" }, { label: "快照管理", path: "/snapshot_mgr" }, { label: "数据对账", path: "/data_reconcile" }, { label: "回填作业", path: "/backfill_job" }, { label: "冷热分层", path: "/cold_archive" }, { label: "压缩合并", path: "/compaction" }, { label: "血缘图", path: "/lineage" }, { label: "历史血缘影响", path: "/lineage-impact" }, { label: "变更日志", path: "/changelog" }, { label: "保留管理", path: "/retention" }, { label: "表格式", path: "/table_format" }]
</script>
