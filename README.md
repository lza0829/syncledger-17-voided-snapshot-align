# 03-syncledger（双核对账）

Syncledger — 跨库/跨系统对齐对账：双端抽取 → 对齐键对账 → 差异处置 → 批次互证。

与 Feedstitch（多源 Feed 归并）、Batchgate（作业依赖编排）主链路不同。夹具全本地，不连活库。

## 启动

```bash
docker compose up --build
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:5300 |
| API | http://localhost:10300 |

## 主链

配置对账对与对齐键 → 触发双端抽取并对齐 → 落差异行与批次计数 → 页面互证

## 种子对账对

- **inventory_hq_vs_store**：总部库存 ↔ 门店库存（`sku_id`+`store_id`，比对 `qty`）
- **payment_vs_ledger**：支付网关 ↔ 账务流水（`txn_id`，比对 `amount`/`currency`）
- **sku_catalog_a_vs_b**：目录 A ↔ 目录 B（`sku_code`，比对 `name`/`category`）

底座默认只**记差异、不自动盖写**；差异处置/契约/审计页留表与入口，深逻辑留给后题。侧栏不叫「工单」，避免撞禁名单「工单客服」。

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。

## 0-1 切口（未实现，见出题规划）

`master_slave_pick` / `side_only_policy` / `window_buffer` / `diff_disposition_flow` / `batch_idempotency` / …

## 抽取中途失败策略（已实现）

任一端抽取可在第 N 行后注入连接中断（对账台「注入抽取失败」）。两种策略互斥，设置页可切换，
`extract_failure_policy` 存于 settings，**默认 `void_batch`**：

| 策略 | 失败端半截快照 | 成功端快照 | 本批差异 | 后续对齐 |
| --- | --- | --- | --- | --- |
| `void_batch` 整批作废 | 标 `voided` | 已写快照回滚标 `voided` | **不写**（`diff_total=0`） | 无效快照不得作为合法输入，直接拒绝并计数 |
| `freeze_success` 冻结成功侧 | 标 `failed` | 标 `frozen` 并保持 | **不写** | 失败态 `active` 期间对齐入口一律拒绝；清理后才放行 |

- 清理失败态（仅冻结策略）：`POST /api/extraction/failures/{id}/discard` 丢弃冻结快照，
  或 `POST .../recover` 重抽失败端（重抽仍失败则失败态保持 `active`，入口继续拒绝）。
- 互证：`GET /api/extraction/failures/{id}/evidence` 返回失败原因、两端快照有效性、
  本批差异行数、拒绝对齐次数与逐项 `checks`；审计页/「抽取异常处置」页同步展示。
- 复用快照对齐入口 `POST /api/reconcile/from-snapshots` 只接受 `valid`（或冻结清理时的 `frozen`）快照；
  任一侧快照为 `voided`/`failed`/`discarded` 都直接拒绝并计数，即使对端是后来成功的合法快照也不例外。
  冻结策略下存在 `active` 失败态时，该入口与开跑入口同走失败态闸门，一律拒绝并累计拒绝次数，清理后才放行。

相关端点：`GET /api/extraction/failures|snapshots|refusals`、`PUT /api/settings`。测试：`pytest backend/tests`。
