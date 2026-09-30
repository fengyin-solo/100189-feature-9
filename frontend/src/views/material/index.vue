<template>
  <section class="page" data-module="material">
    <header class="page-head">
      <div>
        <h2>养护材料管理</h2>
        <p class="page-desc">勾选多张卡片一次提交领用；冻结、耗尽材料自动拦下，结存掉到储备下限以下自动标为临近不足。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记养护材料</button>
        <button class="btn" type="button" @click="goInventory">库存看板</button>
        <button class="btn" type="button" @click="exportRows">导出养护材料清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>材料编号</span>
        <input v-model="keyword" placeholder="按材料编号检索" />
      </label>
      <label class="filter-item">
        <span>材料状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <div class="card-grid">
      <article
        v-for="row in rows"
        :key="String(row.id)"
        class="material-card"
        :class="{ checked: isSelected(row.id), disabled: !canConsume(row), [statusClass(row)]: true }"
      >
        <header class="card-top">
          <label class="pick" v-if="canConsume(row)">
            <input
              type="checkbox"
              :checked="isSelected(row.id)"
              @change="togglePick(row, ($event.target as HTMLInputElement).checked)"
            />
            <span>选择领用</span>
          </label>
          <span class="pick muted" v-else>{{ row.status === '已耗尽' ? '已耗尽，自动跳过' : '已冻结，不可领用' }}</span>
          <span class="status-tag">{{ row.status }}</span>
        </header>

        <h3 class="card-name">{{ row['材料名称'] }}</h3>
        <p class="card-code">{{ row['材料编号'] }} · {{ row['规格型号'] }}</p>

        <dl class="card-meta">
          <div><dt>结存数量</dt><dd :class="{ low: isLow(row) }">{{ row['结存数量'] }} {{ row['计量单位'] }}</dd></div>
          <div><dt>可用数量</dt><dd>{{ availableOf(row) }} {{ row['计量单位'] }}</dd></div>
          <div><dt>储备下限</dt><dd>{{ row['储备下限'] }} {{ row['计量单位'] }}</dd></div>
          <div><dt>存放场地</dt><dd>{{ row['存放场地'] || '—' }}</dd></div>
          <div><dt>保管人员</dt><dd>{{ row['保管人员'] || '—' }}</dd></div>
        </dl>

        <div class="card-foot">
          <label class="qty" v-if="canConsume(row)">
            <span>领用数量</span>
            <input
              type="number"
              min="1"
              :max="availableOf(row)"
              :value="pickedQty(row.id)"
              :disabled="!isSelected(row.id)"
              @input="setQty(row.id, ($event.target as HTMLInputElement).value)"
            />
          </label>
          <div class="row-actions">
            <button
              v-for="action in actionsFor(row)"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </div>
        </div>
      </article>
    </div>

    <p v-if="!rows.length" class="empty-state card-empty">暂无养护材料数据，可先登记养护材料</p>

    <div v-if="selected.length" class="batch-bar">
      <span>已勾选 <strong>{{ selected.length }}</strong> 条，合计领用
        <strong>{{ pickedTotal }}</strong> 件</span>
      <div class="batch-btns">
        <button class="btn ghost" type="button" @click="clearPicks">清空选择</button>
        <button class="btn primary" type="button" :disabled="submitting" @click="submitBatch">
          {{ submitting ? '提交中…' : '一次提交领用' }}
        </button>
      </div>
    </div>

    <section v-if="batchResult" class="result-panel">
      <header class="result-head">
        <h3>批次 {{ batchResult.batch_no }} 处理结果
          <em v-if="batchResult.replayed">（重复提交，已回放首次结果，库存未重复扣减）</em>
        </h3>
        <span>成功 {{ batchResult.succeeded }} 条 · 失败 {{ batchResult.failed }} 条</span>
        <button class="link" type="button" @click="batchResult = null">关闭</button>
      </header>
      <ul class="result-list">
        <li v-for="(item, idx) in batchResult.results" :key="idx" :class="item.ok ? 'ok' : 'fail'">
          <span class="result-mark">{{ item.ok ? '✓' : '✕' }}</span>
          <span class="result-material">{{ item['材料编号'] }}<template v-if="item['材料名称']"> · {{ item['材料名称'] }}</template></span>
          <span class="result-reason">{{ item.reason }}</span>
          <span v-if="item.ok" class="result-qty">扣减 {{ item.consumed }}：{{ item.before }} → {{ item.after }}</span>
        </li>
      </ul>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条养护材料记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { request } from '@/api/client'

const router = useRouter()

type Row = Record<string, string | number | null>
type BatchItemResult = {
  ok: boolean
  reason: string
  consumed?: number
  before?: number
  after?: number
  status?: string
  '材料编号'?: string
  '材料名称'?: string
}
type BatchResult = {
  batch_no: string
  replayed: boolean
  total: number
  succeeded: number
  failed: number
  results: BatchItemResult[]
}

const ENDPOINT = '/api/material'
const statuses = ['正常可用', '临近不足', '已冻结', '已耗尽']
const consumableStatuses = new Set(['正常可用', '临近不足'])
// 各状态卡片上仍可执行的单条动作
const actionMap: Record<string, string[]> = {
  正常可用: ['冻结材料', '登记耗尽'],
  临近不足: ['冻结材料', '登记耗尽'],
  已冻结: ['解冻材料', '登记耗尽'],
  已耗尽: [],
}

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const keyword = ref('')
const statusFilter = ref('')
const submitting = ref(false)
const batchResult = ref<BatchResult | null>(null)
// 勾选状态：材料 id -> 领用数量；同一张卡片只能勾一次，天然不重复
const picks = ref<Map<number, number>>(new Map())

const selected = computed(() => rows.value.filter(row => picks.value.has(Number(row.id))))
const pickedTotal = computed(() =>
  [...picks.value.values()].reduce((sum, qty) => sum + qty, 0),
)
const stats = computed(() => [
  { label: '可用材料', value: rows.value.filter(r => consumableStatuses.has(String(r.status))).length },
  { label: '储备不足材料', value: rows.value.filter(r => r.status === '临近不足').length },
  { label: '已冻结材料', value: rows.value.filter(r => r.status === '已冻结').length },
  { label: '已耗尽材料', value: rows.value.filter(r => r.status === '已耗尽').length },
])

function num(value: Row[string]): number {
  const n = Number(value)
  return Number.isFinite(n) ? n : 0
}
function availableOf(row: Row): number {
  return consumableStatuses.has(String(row.status)) ? num(row['结存数量']) : 0
}
function canConsume(row: Row): boolean {
  return consumableStatuses.has(String(row.status)) && num(row['结存数量']) > 0
}
function isLow(row: Row): boolean {
  return num(row['结存数量']) < num(row['储备下限'])
}
function isSelected(id: string | number | null): boolean {
  return picks.value.has(Number(id))
}
function pickedQty(id: string | number | null): number {
  return picks.value.get(Number(id)) ?? 1
}
function statusClass(row: Row): string {
  return `st-${String(row.status)}`
}
function actionsFor(row: Row): string[] {
  return actionMap[String(row.status)] ?? []
}

function togglePick(row: Row, checked: boolean) {
  const id = Number(row.id)
  const next = new Map(picks.value)
  if (checked) {
    // 默认领 1 件，但不超过当前可用数量
    next.set(id, Math.min(1, Math.max(1, availableOf(row))))
  } else {
    next.delete(id)
  }
  picks.value = next
}
function setQty(id: string | number | null, raw: string) {
  const qty = Math.max(1, Math.min(Number.parseInt(raw, 10) || 1, Number.MAX_SAFE_INTEGER))
  const next = new Map(picks.value)
  next.set(Number(id), qty)
  picks.value = next
}
function clearPicks() {
  picks.value = new Map()
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}
function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}
function goInventory() {
  void router.push('/material/inventory')
}
function openCreate() {
  errorMessage.value = '养护材料登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    if (!response.ok) {
      throw new Error('养护材料动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '养护材料操作失败'
  }
}

async function submitBatch() {
  if (!selected.value.length || submitting.value) return
  submitting.value = true
  errorMessage.value = ''
  // 勾选之外的条目不会出现；超量、冻结、耗尽等都交给后端逐条判定并回原因
  const items = selected.value.map(row => ({
    id: Number(row.id),
    quantity: pickedQty(row.id),
  }))
  // 一次提交一个批次号；网络重试/重复点击都带同一号，后端保证只扣一次
  const batchNo = `REQ-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  try {
    const response = await request(`${ENDPOINT}/batch-consume`, {
      method: 'POST',
      body: JSON.stringify({ batch_no: batchNo, items }),
    })
    if (!response.ok) {
      const payload = await response.json().catch(() => null)
      throw new Error(payload?.detail ?? '批量领用未生效，请稍后重试')
    }
    batchResult.value = (await response.json()) as BatchResult
    clearPicks()
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批量领用失败'
  } finally {
    submitting.value = false
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (keyword.value.trim()) query.set('keyword', keyword.value.trim())
  if (statusFilter.value) query.set('status', statusFilter.value)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('养护材料列表读取失败')
    }
    const payload = await response.json()
    rows.value = (payload.items ?? []) as Row[]
    total.value = payload.total ?? rows.value.length
    // 已不可领用的勾选（例如被别人冻结）顺手清掉，避免提交无效条目
    const validIds = new Set(rows.value.filter(canConsume).map(r => Number(r.id)))
    const next = new Map<number, number>()
    for (const [id, qty] of picks.value) {
      if (validIds.has(id)) next.set(id, qty)
    }
    picks.value = next
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '养护材料列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}
.material-card {
  background: #fff;
  border: 1px solid var(--border);
  border-left: 4px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.material-card.checked { box-shadow: 0 0 0 2px rgba(22, 119, 255, 0.25); }
.material-card.st-正常可用 { border-left-color: #12b76a; }
.material-card.st-临近不足 { border-left-color: #f79009; }
.material-card.st-已冻结 { border-left-color: #667085; background: #f9fafb; }
.material-card.st-已耗尽 { border-left-color: #d92d20; background: #fef3f2; }
.card-top { display: flex; justify-content: space-between; align-items: center; }
.pick { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--brand); cursor: pointer; }
.pick.muted { color: var(--muted); cursor: default; }
.status-tag { font-size: 12px; padding: 2px 8px; border-radius: 999px; background: #f2f4f7; color: #344054; }
.card-name { margin: 0; font-size: 16px; }
.card-code { margin: 0; font-size: 12px; color: var(--muted); }
.card-meta { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 12px; margin: 0; }
.card-meta dt { font-size: 12px; color: var(--muted); }
.card-meta dd { margin: 0; font-size: 13px; }
.card-meta dd.low { color: #b54708; font-weight: 600; }
.card-foot { display: flex; justify-content: space-between; align-items: flex-end; margin-top: auto; }
.qty span { display: block; font-size: 12px; color: var(--muted); }
.qty input { width: 88px; padding: 4px 6px; border: 1px solid var(--border); border-radius: 6px; }
.row-actions { display: flex; gap: 10px; }
.card-empty { padding: 24px; background: #fff; border: 1px dashed var(--border); border-radius: 8px; }
.batch-bar {
  position: sticky;
  bottom: 12px;
  margin-top: 12px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 16px;
  background: #101828;
  color: #fff;
  border-radius: 8px;
  font-size: 13px;
}
.batch-bar strong { color: #6ce9a6; }
.batch-btns { display: flex; gap: 8px; }
.batch-btns .ghost { color: #fff; border-color: #475467; background: transparent; }
.result-panel {
  margin-top: 12px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 16px;
}
.result-head { display: flex; align-items: center; gap: 12px; font-size: 13px; color: var(--muted); }
.result-head h3 { margin: 0; font-size: 15px; }
.result-head em { font-style: normal; color: #b54708; font-size: 12px; }
.result-head .link { margin-left: auto; }
.result-list { list-style: none; margin: 8px 0 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.result-list li { display: flex; align-items: center; gap: 10px; font-size: 13px; padding: 6px 8px; border-radius: 6px; }
.result-list li.ok { background: #ecfdf3; }
.result-list li.fail { background: #fef3f2; }
.result-mark { width: 18px; text-align: center; font-weight: 700; }
.result-list li.ok .result-mark { color: #039855; }
.result-list li.fail .result-mark { color: #d92d20; }
.result-material { min-width: 200px; }
.result-reason { color: #344054; }
.result-qty { margin-left: auto; color: var(--muted); }
.filter-item select { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
</style>
