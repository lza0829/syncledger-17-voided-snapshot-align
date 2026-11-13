<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJSON, putJSON } from '../api'

const settings = ref({})
const err = ref('')
const msg = ref('')
const busy = ref(false)

const policy = computed(() => settings.value.extract_failure_policy || 'void_batch')

const policies = [
  {
    value: 'void_batch',
    title: '整批作废（默认）',
    detail: '任一端抽取失败即整批作废：失败端的半截快照与已成功端快照全部回滚/标无效，本批不写任何差异；无效快照不得被后续批次当合法输入。',
  },
  {
    value: 'freeze_success',
    title: '冻结成功侧',
    detail: '成功端快照冻结、失败端记抽取失败，本批不写差异；清理失败态（丢弃冻结快照或重抽成功）之前，对齐入口对本对账对直接拒绝。',
  },
]

async function refresh() {
  settings.value = await getJSON('/api/settings')
}

async function choose(value) {
  if (value === policy.value) return
  err.value = ''
  msg.value = ''
  busy.value = true
  try {
    settings.value = await putJSON('/api/settings', { key: 'extract_failure_policy', value })
    msg.value = `已切换为 ${value}`
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  try { await refresh() } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>设置</h1>
    <p class="muted">全局策略键值。抽取失败策略二选一、互斥；自动盖写默认关闭。</p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <p v-if="msg" class="tag ok">{{ msg }}</p>

    <div class="panel">
      <h3 style="margin-top:0">抽取失败策略</h3>
      <div class="policy-grid">
        <button
          v-for="p in policies"
          :key="p.value"
          class="policy-card"
          :class="{ active: policy === p.value }"
          :disabled="busy"
          @click="choose(p.value)"
        >
          <div class="policy-title">
            {{ p.title }}
            <span v-if="policy === p.value" class="tag ok">当前</span>
          </div>
          <div class="policy-detail">{{ p.detail }}</div>
          <div class="mono muted">{{ p.value }}</div>
        </button>
      </div>
    </div>

    <div class="panel">
      <h3 style="margin-top:0">全部键值</h3>
      <table>
        <thead><tr><th>键</th><th>值</th></tr></thead>
        <tbody>
          <tr v-for="(v, k) in settings" :key="k">
            <td class="mono">{{ k }}</td>
            <td class="mono">{{ v }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
