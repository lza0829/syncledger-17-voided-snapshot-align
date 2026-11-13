<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { getJSON, postJSON } from '../api'

const route = useRoute()
const failures = ref([])
const snapshots = ref([])
const refusals = ref([])
const evidence = ref(null)
const evidenceId = ref(null)
const err = ref('')
const msg = ref('')
const busy = ref(false)

const policy = ref('void_batch')

const activeFailure = computed(() => failures.value.find((f) => f.status === 'active') || null)

function snapClass(status) {
  return {
    valid: 'ok',
    frozen: 'warn',
    voided: 'danger',
    failed: 'danger',
    discarded: 'danger',
  }[status] || ''
}

function failClass(status) {
  return { active: 'danger', voided: 'danger', cleared: 'ok' }[status] || ''
}

async function refresh() {
  const q = route.query.pair_id ? `?pair_id=${route.query.pair_id}` : ''
  const [s, f, r] = await Promise.all([
    getJSON('/api/settings'),
    getJSON(`/api/extraction/failures${q}`),
    getJSON(`/api/extraction/refusals${q}`),
  ])
  policy.value = s.extract_failure_policy
  failures.value = f
  refusals.value = r
  snapshots.value = await getJSON(`/api/extraction/snapshots${q}`)
  if (!evidenceId.value && f.length) await showEvidence(f[0].id)
}

async function showEvidence(id) {
  evidenceId.value = id
  evidence.value = await getJSON(`/api/extraction/failures/${id}/evidence`)
}

async function act(fn, id) {
  err.value = ''
  msg.value = ''
  busy.value = true
  try {
    const res = await fn(id)
    msg.value = res && res.cleared === false
      ? '重抽仍然失败，失败态保持 active，对齐入口继续拒绝'
      : '失败态已清理'
    await refresh()
    if (evidenceId.value) await showEvidence(evidenceId.value)
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

const discard = (id) => act((x) => postJSON(`/api/extraction/failures/${x}/discard`), id)
const recover = (id) => act((x) => postJSON(`/api/extraction/failures/${x}/recover`), id)

onMounted(async () => {
  try { await refresh() } catch (e) { err.value = String(e.message || e) }
})
watch(() => route.query.pair_id, async () => {
  try { await refresh() } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>抽取异常处置</h1>
    <p class="muted">
      当前策略
      <span class="tag" :class="policy === 'void_batch' ? 'danger' : 'warn'">{{ policy }}</span>
      · 抽取失败原因、快照有效与否、是否写出差异、拒绝对齐次数在此互证。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <p v-if="msg" class="tag ok">{{ msg }}</p>

    <div class="panel" v-if="activeFailure">
      <h3 style="margin-top:0">未清理失败态</h3>
      <p>
        failure #{{ activeFailure.id }} · {{ activeFailure.failed_side }} 端 · {{ activeFailure.reason }}
      </p>
      <p class="mono muted">
        冻结快照 #{{ activeFailure.frozen_snapshot_id || '—' }} ·
        失败快照 #{{ activeFailure.failed_snapshot_id || '—' }} ·
        已拒绝对齐 {{ activeFailure.refusal_count }} 次
      </p>
      <p>
        冻结策略下，清理之前对齐入口对本对账对直接拒绝。清理方式二选一：
      </p>
      <div style="display:flex;gap:0.6rem;flex-wrap:wrap">
        <button class="secondary" :disabled="busy" @click="discard(activeFailure.id)">丢弃冻结快照</button>
        <button :disabled="busy" @click="recover(activeFailure.id)">重抽失败端</button>
      </div>
    </div>

    <div class="panel">
      <h3 style="margin-top:0">失败记录</h3>
      <table>
        <thead>
          <tr><th>ID</th><th>对账对</th><th>批次</th><th>失败端</th><th>策略</th><th>状态</th><th>清理方式</th><th>拒绝对齐</th><th>原因</th></tr>
        </thead>
        <tbody>
          <tr v-for="f in failures" :key="f.id">
            <td>{{ f.id }}</td>
            <td class="mono">{{ f.pair_id }}</td>
            <td class="mono">{{ f.batch_id }}</td>
            <td><span class="tag danger">{{ f.failed_side }}</span></td>
            <td class="mono">{{ f.policy }}</td>
            <td><span class="tag" :class="failClass(f.status)">{{ f.status }}</span></td>
            <td class="mono">{{ f.clear_method || '—' }}</td>
            <td>
              <a href="#" @click.prevent="showEvidence(f.id)">{{ f.refusal_count }} 次</a>
            </td>
            <td class="mono">{{ f.reason }}</td>
          </tr>
          <tr v-if="!failures.length"><td colspan="9" class="muted">还没有抽取失败记录。</td></tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3 style="margin-top:0">快照有效性与写出差异</h3>
      <table>
        <thead><tr><th>快照</th><th>对账对</th><th>批次</th><th>端</th><th>状态</th><th>行数</th><th>无效原因</th></tr></thead>
        <tbody>
          <tr v-for="s in snapshots" :key="s.id">
            <td>{{ s.id }}</td>
            <td class="mono">{{ s.pair_id }}</td>
            <td class="mono">{{ s.batch_id }}</td>
            <td>{{ s.side }}</td>
            <td><span class="tag" :class="snapClass(s.status)">{{ s.status }}</span></td>
            <td>{{ s.row_count }}</td>
            <td class="mono">{{ s.invalid_reason || '—' }}</td>
          </tr>
          <tr v-if="!snapshots.length"><td colspan="7" class="muted">还没有快照。</td></tr>
        </tbody>
      </table>
    </div>

    <div class="panel" v-if="evidence">
      <h3 style="margin-top:0">互证 · failure #{{ evidence.failure.id }}</h3>
      <p>
        失败原因：<span class="mono">{{ evidence.failure.reason }}</span>
      </p>
      <p>
        批次 <span class="mono">{{ evidence.batch?.batch_no }}</span> 状态
        <span class="tag" :class="evidence.batch?.status === 'completed' ? 'ok' : 'danger'">{{ evidence.batch?.status }}</span>
        · 本批差异行 <span class="mono">{{ evidence.diff_count_for_batch }}</span>
        · 写出差异
        <span class="tag" :class="evidence.diffs_written ? 'danger' : 'ok'">{{ evidence.diffs_written ? '是（违规）' : '否' }}</span>
        · 拒绝对齐 <span class="mono">{{ evidence.refusal_count }}</span> 次
      </p>
      <table>
        <thead><tr><th>核对项</th><th>结果</th></tr></thead>
        <tbody>
          <tr v-for="(v, k) in evidence.checks" :key="k">
            <td class="mono">{{ k }}</td>
            <td><span class="tag" :class="v ? 'ok' : 'danger'">{{ v ? '通过' : '不成立' }}</span></td>
          </tr>
        </tbody>
      </table>
      <table style="margin-top:0.7rem">
        <thead><tr><th>拒绝时间</th><th>来源</th><th>原因</th></tr></thead>
        <tbody>
          <tr v-for="r in evidence.refusals" :key="r.id">
            <td class="mono">{{ r.created_at }}</td>
            <td class="mono">{{ r.source }}</td>
            <td class="mono">{{ r.reason }}</td>
          </tr>
          <tr v-if="!evidence.refusals.length"><td colspan="3" class="muted">暂无拒绝记录。</td></tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3 style="margin-top:0">对齐拒绝记录（全部）</h3>
      <table>
        <thead><tr><th>ID</th><th>对账对</th><th>failure</th><th>来源</th><th>原因</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="r in refusals" :key="r.id">
            <td>{{ r.id }}</td>
            <td class="mono">{{ r.pair_id }}</td>
            <td class="mono">{{ r.failure_id || '—' }}</td>
            <td class="mono">{{ r.source }}</td>
            <td class="mono">{{ r.reason }}</td>
            <td class="mono">{{ r.created_at }}</td>
          </tr>
          <tr v-if="!refusals.length"><td colspan="6" class="muted">暂无拒绝记录。</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
