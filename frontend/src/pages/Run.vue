<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'

const pairs = ref([])
const batches = ref([])
const settings = ref({})
const pairId = ref(null)
const note = ref('')
const last = ref(null)
const busy = ref(false)
const err = ref('')
const refused = ref(null)

const injectSide = ref('')
const injectAfter = ref(2)

const policy = computed(() => settings.value.extract_failure_policy || 'void_batch')
const policyLabel = computed(() =>
  policy.value === 'void_batch' ? '整批作废（void_batch）' : '冻结成功侧（freeze_success）'
)

async function refresh() {
  pairs.value = await getJSON('/api/pairs')
  batches.value = await getJSON('/api/batches?limit=20')
  settings.value = await getJSON('/api/settings')
  if (pairId.value == null && pairs.value.length) pairId.value = pairs.value[0].id
}

function statusClass(status) {
  if (status === 'completed') return 'ok'
  if (status === 'voided') return 'danger'
  if (status === 'aborted_extract') return 'warn'
  return ''
}

function statusLabel(status) {
  return {
    completed: '完成',
    voided: '作废',
    aborted_extract: '抽取中断',
    running: '运行中',
  }[status] || status
}

async function run() {
  err.value = ''
  refused.value = null
  last.value = null
  busy.value = true
  try {
    const body = { pair_id: Number(pairId.value), note: note.value || null }
    if (injectSide.value) {
      body.inject_side = injectSide.value
      body.inject_after = Number(injectAfter.value)
    }
    last.value = await postJSON('/api/reconcile', body)
    await refresh()
  } catch (e) {
    if (e.payload && e.payload.refused) refused.value = e.payload
    else err.value = String(e.message || e)
    await refresh().catch(() => {})
  } finally {
    busy.value = false
  }
}

async function tryReuseSnapshots() {
  if (!last.value) return
  err.value = ''
  refused.value = null
  busy.value = true
  try {
    await postJSON('/api/reconcile/from-snapshots', {
      pair_id: last.value.pair_id,
      left_snapshot_id: last.value.left_snapshot_id,
      right_snapshot_id: last.value.right_snapshot_id,
    })
    err.value = '意外：失败批次的快照竟被接受用于对齐'
  } catch (e) {
    if (e.payload && e.payload.refused) refused.value = e.payload
    else err.value = String(e.message || e)
  } finally {
    busy.value = false
    await refresh().catch(() => {})
  }
}

onMounted(async () => {
  try { await refresh() } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>对账台</h1>
    <p class="muted">
      选一对账对，抽取左右夹具并对齐。当前抽取失败策略：
      <span class="tag" :class="policy === 'void_batch' ? 'danger' : 'warn'">{{ policyLabel }}</span>
      <router-link to="/settings" style="margin-left:0.4rem">切换策略 →</router-link>
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>

    <div class="panel">
      <h3 style="margin-top:0">发起对账</h3>
      <div style="display:flex;flex-wrap:wrap;gap:0.75rem;align-items:flex-end">
        <label>
          对账对
          <select v-model.number="pairId" style="display:block;min-width:18rem;margin-top:0.25rem">
            <option v-for="p in pairs" :key="p.id" :value="p.id">
              {{ p.title }}{{ p.active_failure_id ? '（有未清理失败态）' : '' }}
            </option>
          </select>
        </label>
        <label>
          备注（可选）
          <input v-model="note" style="display:block;width:14rem;margin-top:0.25rem" placeholder="本趟说明" />
        </label>
        <label>
          注入抽取失败
          <select v-model="injectSide" style="display:block;margin-top:0.25rem">
            <option value="">不注入</option>
            <option value="left">左端中途失败</option>
            <option value="right">右端中途失败</option>
          </select>
        </label>
        <label v-if="injectSide">
          读到第 N 行后断
          <input v-model.number="injectAfter" type="number" min="0" style="display:block;width:7rem;margin-top:0.25rem" />
        </label>
        <button :disabled="busy || !pairId" @click="run">{{ busy ? '对账中…' : '抽取并对齐' }}</button>
      </div>
      <p class="muted" style="margin-bottom:0">
        注入只影响本趟：任一端抽到第 N 行后模拟连接中断。失败时两种策略都不写差异，仅处置路径不同。
      </p>
    </div>

    <div class="panel" v-if="refused">
      <h3 style="margin-top:0">对齐入口拒绝本次请求</h3>
      <p><span class="tag danger">拒绝</span> {{ refused.reason }}</p>
      <p class="mono muted">
        策略 {{ refused.policy }} · failure #{{ refused.failure_id }} · 拒绝对齐次数 {{ refused.refusal_count }}
      </p>
      <p><router-link to="/extractions">去抽取异常处置 →</router-link></p>
    </div>

    <div class="panel" v-if="last">
      <h3 style="margin-top:0">最近一趟</h3>
      <p>
        批次 <span class="mono">{{ last.batch_no }}</span> · {{ last.pair_title }} ·
        <span class="tag" :class="statusClass(last.status)">{{ statusLabel(last.status) }}</span>
        <span class="tag">{{ last.extraction_policy }}</span>
      </p>

      <div v-if="last.status !== 'completed'" class="panel" style="background:#fbf0f0;border-color:#d9b3b3">
        <p>
          <strong>抽取失败：{{ last.failure_side }} 端</strong> ·
          {{ last.failure_reason }}
        </p>
        <p>
          快照状态：左
          <span class="mono">{{ last.left_snapshot_id || '—' }}</span> · 右
          <span class="mono">{{ last.right_snapshot_id || '—' }}</span>
          · 写出差异 <span class="tag danger">{{ last.diffs_written ? '是' : '否' }}</span>
          · 差异合计 <span class="mono">{{ last.diff_total }}</span>
        </p>
        <p v-if="last.extraction_policy === 'void_batch'">
          整批作废：失败端半截快照与成功端已写快照都已标无效，后续批次不会再把它们当合法输入。
        </p>
        <p v-else>
          冻结成功侧：成功端快照冻结，失败端记抽取失败；清理失败态之前对齐入口一律拒绝。
        </p>
        <p>
          <router-link :to="{ path: '/audit', query: { batch_id: last.id } }">审计摘要 →</router-link>
          ·
          <router-link :to="{ path: '/extractions', query: { pair_id: last.pair_id } }">查看失败态与拒绝记录 →</router-link>
        </p>
        <p v-if="last.left_snapshot_id || last.right_snapshot_id">
          <button
            class="secondary"
            :disabled="busy"
            @click="tryReuseSnapshots"
          >用本批快照直接对齐（预期被拒）</button>
        </p>
      </div>

      <div class="stat-grid" v-if="last.status === 'completed'">
        <div class="stat"><div class="stat-num">{{ last.matched }}</div><div class="stat-label">一致</div></div>
        <div class="stat"><div class="stat-num">{{ last.left_only }}</div><div class="stat-label">仅左有</div></div>
        <div class="stat"><div class="stat-num">{{ last.right_only }}</div><div class="stat-label">仅右有</div></div>
        <div class="stat"><div class="stat-num">{{ last.field_mismatch }}</div><div class="stat-label">字段不一致</div></div>
        <div class="stat"><div class="stat-num">{{ last.diff_total }}</div><div class="stat-label">差异合计</div></div>
      </div>
      <p style="margin-top:0.75rem" v-if="last.status === 'completed'">
        <router-link :to="{ path: '/diffs', query: { batch_id: last.id } }">查看本批差异 →</router-link>
        ·
        <router-link :to="{ path: '/audit', query: { batch_id: last.id } }">审计摘要 →</router-link>
      </p>
    </div>

    <div class="panel">
      <h3 style="margin-top:0">历史批次</h3>
      <table>
        <thead>
          <tr>
            <th>批次</th><th>对账对</th><th>状态</th><th>策略</th>
            <th>左/右行</th><th>一致</th><th>差异合计</th><th>写差异</th><th>失败端</th><th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="b in batches" :key="b.id">
            <td class="mono">{{ b.batch_no }}</td>
            <td>{{ b.pair_title }}</td>
            <td><span class="tag" :class="statusClass(b.status)">{{ statusLabel(b.status) }}</span></td>
            <td class="mono">{{ b.extraction_policy || '—' }}</td>
            <td class="mono">{{ b.left_rows }} / {{ b.right_rows }}</td>
            <td>{{ b.matched }}</td>
            <td>{{ b.diff_total }}</td>
            <td><span class="tag" :class="b.diffs_written ? 'ok' : 'danger'">{{ b.diffs_written ? '是' : '否' }}</span></td>
            <td class="mono">{{ b.failure_side || '—' }}</td>
            <td class="mono">{{ b.created_at }}</td>
          </tr>
          <tr v-if="!batches.length"><td colspan="10" class="muted">还没有批次，先跑一趟。</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
