# 流批一体数据集成平台

面向实时流接入、批流合并、变更捕获与分区治理的流批一体数据集成平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/routers/lineage_impact.py  历史血缘影响（版本化边/时刻路径/试算单）
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/lineage_impact.py  历史时刻路径查询与影响范围试算
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 流式数据源 | `stream_source` | 流式源配置 | 源标识、消息中间件、Topic名称 |
| 批次分片 | `batch_slice` | 分片批次 | 批次编号、源表名称、分片键列 |
| 变更捕获 | `change_capture` | 变更捕获配置 | 配置编号、源数据库、目标表 |
| Schema治理 | `schema_govern` | Schema版本 | 主题名称、Schema定义、版本号 |
| 水位管理 | `watermark` | 消费水位 | 分区标识、消费组、当前位点 |
| 迟到数据 | `late_data` | 迟到数据策略 | 策略编号、关联源、允许延迟秒数 |
| 流式关联 | `stream_lookup` | 关联规则 | 规则编号、左流来源、右表来源 |
| 合并策略 | `merge_plan` | 合并计划 | 计划编号、流式来源、批式来源 |
| 分区扩缩 | `partition_scale` | 分区策略 | 策略编号、目标表、分区字段 |
| 快照管理 | `snapshot_mgr` | 数据快照 | 快照编号、源表名称、快照时间 |
| 数据对账 | `data_reconcile` | 对账任务 | 任务编号、左源表、右源表 |
| 回填作业 | `backfill_job` | 回填任务 | 任务编号、目标表、回填起始时间 |
| 冷热分层 | `cold_archive` | 归档策略 | 策略编号、源表名称、热数据天数 |
| 压缩合并 | `compaction` | 压缩任务 | 任务编号、目标表、目标分区 |
| 血缘图 | `lineage` | 血缘关系 | 关系编号、上游表、下游表 |
| 变更日志 | `changelog` | 变更记录 | 记录编号、目标表、变更类型 |
| 保留管理 | `retention` | 保留策略 | 策略编号、目标表、保留天数 |
| 表格式 | `table_format` | 表格式配置 | 配置编号、目标表、表格式类型 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 历史血缘影响分析（lineage-impact）

血缘调整前按“表 + 时刻”评估影响范围，独立于18个通用模块的 `/api/lineage-impact` 接口：

- **版本化时间边**：每条边带 `effective_at` / `replaced_at`，支持登记、废弃（deprecate）、
  变更替换（replace，新旧版本以 `replaces_edge_id` / `successor_edge_id` 显式关联）、
  回滚（rollback）和更正边登记/确认。
- **按时刻路径查询** `POST /api/lineage-impact/query`：只取该时刻有效边
  （`effective_at <= 时刻` 且未被替换），枚举同一张表的全部到达路径；
  同一张表在影响清单口径只计一次，但每条路径与每条边的生效/替换时间都可复核。
- **影响试算单** `POST /api/lineage-impact/trials`：创建即深拷贝冻结当时的有效路径。
  历史更正边在发布前到达时，试算单必须显式写明策略——`original_graph`（继续沿用原图）
  或 `corrected_graph`（采用已确认更正重查，未确认更正会被拒绝）。
- **表格式影响清单** `POST /trials/{id}/confirm`：按试算所选时刻的有效路径回写，
  每张受影响表一行并保留完整路径明细；已发布评估不可变，当前边废弃/回滚不会改写历史评估，
  确认时同时输出与当前图的漂移报告（新增/缺失表、路径数变化）。
- 前端入口为侧边栏「历史血缘影响」（`/lineage-impact`，`LineageImpactView.vue`）。
- 验收用例：`backend/tests/test_lineage_impact.py`，覆盖 A→B→D 与 A→C→D 双路径、
  两个时间点废弃 B→D / 新增 D→E、试算期补登记 C→F、更正边策略与回滚不可变性。

领域：数据工程
子方向：数仓与数据湖
