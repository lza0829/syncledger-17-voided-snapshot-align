<script setup>
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { getJSON } from '../api'

const route = useRoute()
const batches = ref([])
const batchId = ref(null)
const summary = ref(null)
const err = ref('')

async function loadBatches() {
  batches.value = await getJSON('/api/batches?limit=30')
  if (route.query.batch_id) batchId.value = Number(route.query.batch_id)
  else if (!batchId.value && batches.value.length) batchId.value = batches.value[0].id
}

async function loadSummary() {
  summary.value = null
  if (!batchId.value) return
  summary.value = await getJSON(`/api/audit/batches/${batchId.value}`)
}

onMounted(async () => {
  try {
    await loadBatches()
    await loadSummary()
  } catch (e) { err.value = String(e.message || e) }
})
watch(batchId, async () => {
  try { await loadSummary() } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>审计摘要</h1>
    <p class="muted">按批次查看快照计数。导出包与清单哈希留给后题；当前 export_ready=false。</p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel">
      <label>
        批次
        <select v-model.number="batchId" style="display:block;margin-top:0.35rem;min-width:20rem">
          <option v-for="b in batches" :key="b.id" :value="b.id">{{ b.batch_no }} · {{ b.pair_title }}</option>
        </select>
      </label>
    </div>
    <div class="panel" v-if="summary">
      <p>批次 <span class="mono">{{ summary.batch.batch_no }}</span> · {{ summary.pair?.title }}</p>
      <div class="stat-grid">
        <div class="stat"><div class="stat-num">{{ summary.diff_count }}</div><div class="stat-label">差异行</div></div>
        <div class="stat"><div class="stat-num">{{ summary.kinds.left_only }}</div><div class="stat-label">仅左有</div></div>
        <div class="stat"><div class="stat-num">{{ summary.kinds.right_only }}</div><div class="stat-label">仅右有</div></div>
        <div class="stat"><div class="stat-num">{{ summary.kinds.field_mismatch }}</div><div class="stat-label">字段不一致</div></div>
      </div>
      <p style="margin-top:0.85rem" class="muted">{{ summary.note }}</p>
      <p>可导出 <span class="tag" :class="summary.export_ready ? 'ok' : 'warn'">{{ summary.export_ready ? '是' : '否' }}</span></p>
    </div>

    <div class="panel" v-if="summary && summary.batch.failure_id">
      <h3 style="margin-top:0">抽取失败互证</h3>
      <p>
        失败端 <span class="tag danger">{{ summary.batch.failure_side }}</span> ·
        {{ summary.batch.failure_reason }}
      </p>
      <p>
        本批差异行 <span class="mono">{{ summary.diff_count }}</span> ·
        写出差异 <span class="tag" :class="summary.diffs_written ? 'danger' : 'ok'">{{ summary.diffs_written ? '是' : '否' }}</span>
        · 拒绝对齐 <span class="mono">{{ summary.refusal_count }}</span> 次
        · 策略 <span class="mono">{{ summary.batch.extraction_policy }}</span>
      </p>
      <table>
        <thead><tr><th>抽取端</th><th>结果</th><th>期望行</th><th>实取行</th><th>注入</th><th>原因</th></tr></thead>
        <tbody>
          <tr v-for="r in summary.extraction_runs" :key="r.id">
            <td>{{ r.side }}</td>
            <td><span class="tag" :class="r.status === 'succeeded' ? 'ok' : 'danger'">{{ r.status }}</span></td>
            <td>{{ r.expected_rows }}</td>
            <td>{{ r.extracted_rows }}</td>
            <td>{{ r.injected ? '是' : '否' }}</td>
            <td class="mono">{{ r.reason || '—' }}</td>
          </tr>
        </tbody>
      </table>
      <table style="margin-top:0.7rem">
        <thead><tr><th>快照</th><th>端</th><th>状态</th><th>行数</th><th>无效原因</th></tr></thead>
        <tbody>
          <tr v-for="s in summary.snapshots" :key="s.id">
            <td>{{ s.id }}</td>
            <td>{{ s.side }}</td>
            <td><span class="tag" :class="s.status === 'valid' ? 'ok' : 'danger'">{{ s.status }}</span></td>
            <td>{{ s.row_count }}</td>
            <td class="mono">{{ s.invalid_reason || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
