<template>
  <section class="page" data-module="material">
    <header class="page-head">
      <div>
        <h2>养护材料管理</h2>
        <p class="page-desc">勾选多条养护材料一次提交领用或冻结；自动跳过已耗尽材料，逐条反馈成功与失败原因，结存数量实时同步到库存看板。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记养护材料</button>
        <button class="btn" type="button" @click="exportRows">导出养护材料清单</button>
      </div>
    </header>

    <!-- 库存看板：数据来自 /api/material/inventory，与下方领用界面同源 -->
    <div class="stat-row">
      <article v-for="item in inventoryCards" :key="item.label" class="stat-card" :class="cardClass(item.label)">
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
          <option v-for="item in statuses" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <!-- 批量操作条 -->
    <div class="batch-bar">
      <label class="check-all">
        <input
          type="checkbox"
          :checked="allVisibleSelected"
          :indeterminate.prop="someVisibleSelected"
          @change="toggleSelectAll"
        />
        全选本页
      </label>
      <span class="select-count">已勾选 <strong>{{ selectedIds.size }}</strong> 条</span>
      <div class="batch-actions">
        <button class="btn primary" type="button" :disabled="submitting || !selectedIds.size" @click="submitBatch('领用出库')">
          批量领用
        </button>
        <button class="btn" type="button" :disabled="submitting || !selectedIds.size" @click="submitBatch('冻结材料')">批量冻结</button>
        <button class="btn" type="button" :disabled="submitting || !selectedIds.size" @click="submitBatch('解冻材料')">批量解冻</button>
        <button class="btn" type="button" :disabled="submitting || !selectedIds.size" @click="submitBatch('登记耗尽')">批量登记耗尽</button>
      </div>
      <span v-if="submitting" class="batch-hint">正在提交…</span>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th class="col-check">选择</th>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>领用数量</th>
          <th>单条动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="rowClass(row)">
          <td class="col-check">
            <input type="checkbox" :value="row.id" v-model="selectedIdsModel" />
          </td>
          <td v-for="column in columns" :key="column">
            <span v-if="column === '材料状态'" class="status-pill" :class="statusClass(String(row[column]))">{{ row[column] ?? '—' }}</span>
            <span v-else-if="column === '可用数量'">
              <strong :class="{ 'available-zero': Number(row[column]) === 0 }">{{ row[column] ?? '—' }}</strong>
            </span>
            <span v-else>{{ row[column] ?? '—' }}</span>
          </td>
          <td class="col-qty">
            <input
              v-model.number="quantities[String(row.id)]"
              type="number"
              min="1"
              :disabled="Number(row['可用数量']) === 0"
              placeholder="1"
              aria-label="领用数量"
            />
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 3" class="empty-state">暂无符合条件的养护材料</td>
        </tr>
      </tbody>
    </table>

    <!-- 批量处理结果：按条列出成功 / 自动跳过 / 失败及原因 -->
    <section v-if="batchResult" class="result-panel" :class="{ 'has-failed': batchResult.failed_count > 0 }">
      <header class="result-head">
        <div>
          <h3>本次处理结果{{ batchResult.replayed ? '（重复提交，回放首次结果）' : '' }}</h3>
          <p>{{ batchResult.message }}</p>
          <p class="result-meta">批次号：{{ batchResult.batch_no }}</p>
        </div>
        <div class="result-summary">
          <span class="tag success">成功 {{ batchResult.success_count }}</span>
          <span class="tag skipped">自动跳过 {{ batchResult.skipped_count }}</span>
          <span class="tag failed">失败 {{ batchResult.failed_count }}</span>
          <button class="btn ghost" type="button" @click="closeResult">关闭</button>
        </div>
      </header>
      <table class="data-table result-table">
        <thead>
          <tr><th>结论</th><th>材料编号</th><th>材料名称</th><th>数量</th><th>扣减前结存</th><th>扣减后结存</th><th>原因说明</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in batchResult.results" :key="item.id" :class="`outcome-${item.outcome}`">
            <td><span class="tag" :class="item.outcome">{{ outcomeLabel(item.outcome) }}</span></td>
            <td>{{ item.code ?? `#${item.id}` }}</td>
            <td>{{ item.name ?? '—' }}</td>
            <td>{{ item.quantity ?? '—' }}</td>
            <td>{{ item.before_balance ?? '—' }}</td>
            <td>{{ item.after_balance ?? '—' }}</td>
            <td>{{ item.reason }}</td>
          </tr>
        </tbody>
      </table>
      <div v-if="failedItems.length" class="retry-box">
        <button class="btn" type="button" @click="retryFailed">仅勾选失败条目（{{ failedItems.length }}）并保留数量</button>
      </div>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条养护材料记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type Outcome = 'success' | 'skipped' | 'failed'

type BatchItemResult = {
  id: number
  code: string | null
  name: string | null
  outcome: Outcome
  reason: string
  quantity: number | null
  before_balance: number | null
  after_balance: number | null
}

type BatchResult = {
  ok: boolean
  message: string
  action: string
  batch_no: string
  replayed: boolean
  success_count: number
  skipped_count: number
  failed_count: number
  results: BatchItemResult[]
  entries: Row[]
}

type InventoryCard = { label: string; value: number }

const ENDPOINT = '/api/material'
const columns = ['材料编号', '材料名称', '规格型号', '结存数量', '储备下限', '可用数量', '计量单位', '存放场地', '保管人员', '材料状态']
const actions = ['冻结材料', '解冻材料', '登记耗尽']
const statuses = ['正常可用', '临近不足', '已冻结', '已耗尽']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const keyword = ref('')
const statusFilter = ref('')
const inventoryCards = ref<InventoryCard[]>([])
const selectedIds = ref<Set<number>>(new Set())
const quantities = ref<Record<string, number>>({})
const submitting = ref(false)
const batchResult = ref<BatchResult | null>(null)

// v-model 绑定 Set 的适配层：复选框 :value + 数组模型。
const selectedIdsModel = computed<number[]>({
  get: () => [...selectedIds.value],
  set: (values: number[]) => {
    selectedIds.value = new Set(values)
  },
})

const allVisibleSelected = computed(() => rows.value.length > 0 && rows.value.every((row) => selectedIds.value.has(Number(row.id))))
const someVisibleSelected = computed(
  () => !allVisibleSelected.value && rows.value.some((row) => selectedIds.value.has(Number(row.id))),
)

const failedItems = computed(() => (batchResult.value?.results ?? []).filter((item) => item.outcome === 'failed'))

function outcomeLabel(outcome: Outcome): string {
  return outcome === 'success' ? '成功' : outcome === 'skipped' ? '自动跳过' : '失败'
}

function statusClass(status: string): string {
  if (status === '正常可用') return 'is-normal'
  if (status === '临近不足') return 'is-low'
  if (status === '已冻结') return 'is-frozen'
  return 'is-empty'
}

function rowClass(row: Row): Record<string, boolean> {
  return {
    'row-selected': selectedIds.value.has(Number(row.id)),
    'row-low': row['材料状态'] === '临近不足',
    'row-frozen': row['材料状态'] === '已冻结',
    'row-empty': row['材料状态'] === '已耗尽',
  }
}

function cardClass(label: string): string {
  if (label === '临近不足' || label === '已耗尽') return 'card-warning'
  if (label === '已冻结') return 'card-frozen'
  return ''
}

function toggleSelectAll(event: Event): void {
  const checked = (event.target as HTMLInputElement).checked
  const next = new Set(selectedIds.value)
  rows.value.forEach((row) => {
    const id = Number(row.id)
    if (checked) next.add(id)
    else next.delete(id)
  })
  selectedIds.value = next
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '养护材料登记入口尚未接入审批流'
}

async function loadInventory() {
  try {
    const response = await request(`${ENDPOINT}/inventory`)
    if (!response.ok) return
    const payload = await response.json()
    inventoryCards.value = payload.cards ?? []
  } catch {
    // 看板加载失败不阻塞列表操作；下次批量提交后会再同步一次。
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  if (statusFilter.value) query.set('status', statusFilter.value)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('养护材料列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '养护材料列表读取失败'
  }
}

/** 生成一次提交的批次号；重复提交同一批时后端据此只扣一次库存。 */
function makeBatchNo(): string {
  const stamp = Date.now().toString(36)
  const rand = Math.random().toString(36).slice(2, 8)
  return `BATCH-${stamp}-${rand}`
}

async function submitBatch(action: string) {
  const ids = [...selectedIds.value]
  if (!ids.length) return
  errorMessage.value = ''
  submitting.value = true
  const items = ids.map((id) => {
    const qty = quantities.value[String(id)]
    return action === '领用出库' ? { id, quantity: Number.isFinite(qty) ? qty : null } : { id }
  })
  try {
    const response = await request(`${ENDPOINT}/batch-actions`, {
      method: 'POST',
      body: JSON.stringify({ action, items, batch_no: makeBatchNo() }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(payload?.detail ? String(payload.detail) : '批量处理未生效，请稍后重试')
    }
    batchResult.value = payload as BatchResult
    // 成功条目取消勾选并清空其领用数量；失败条目保留勾选，方便修正后重提。
    const failedIds = new Set((payload.results as BatchItemResult[]).filter((r) => r.outcome === 'failed').map((r) => r.id))
    selectedIds.value = new Set(ids.filter((id) => failedIds.has(id)))
    ids.forEach((id) => {
      if (!failedIds.has(id)) delete quantities.value[String(id)]
    })
    await Promise.all([reload(), loadInventory()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批量处理失败'
  } finally {
    submitting.value = false
  }
}

function retryFailed() {
  if (!batchResult.value) return
  selectedIds.value = new Set(failedItems.value.map((item) => item.id))
  failedItems.value.forEach((item) => {
    if (item.quantity != null) quantities.value[String(item.id)] = item.quantity
  })
  window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' })
}

function closeResult() {
  batchResult.value = null
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload) {
      throw new Error('养护材料动作未生效，请稍后重试')
    }
    errorMessage.value = payload.ok ? '' : String(payload.message ?? '操作未生效')
    await Promise.all([reload(), loadInventory()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '养护材料操作失败'
  }
}

onMounted(async () => {
  await Promise.all([reload(), loadInventory()])
})
</script>

<style scoped>
.col-check { width: 42px; text-align: center; }
.col-check input { transform: scale(1.1); }
.col-qty { width: 96px; }
.col-qty input { width: 72px; padding: 4px 6px; border: 1px solid var(--border); border-radius: 4px; }

.batch-bar {
  display: flex;
  align-items: center;
  gap: 14px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 12px;
  margin-bottom: 10px;
}
.check-all { font-size: 13px; display: flex; align-items: center; gap: 6px; }
.select-count { font-size: 13px; color: var(--muted); }
.batch-actions { display: flex; gap: 8px; margin-left: auto; }
.batch-hint { font-size: 12px; color: var(--muted); }

.card-warning strong { color: #b54708; }
.card-frozen strong { color: #1d4ed8; }

.available-zero { color: #b42318; }

.row-low td { background: #fffaeb; }
.row-frozen td { background: #eff4ff; }
.row-empty td { background: #f2f4f7; color: var(--muted); }
.row-selected td { outline: inset 2px rgba(31, 111, 235, 0.35); outline-offset: -2px; }

.status-pill {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 18px;
}
.status-pill.is-normal { background: #ecfdf3; color: #027a48; }
.status-pill.is-low { background: #fffaeb; color: #b54708; }
.status-pill.is-frozen { background: #eff4ff; color: #1d4ed8; }
.status-pill.is-empty { background: #f2f4f7; color: #667085; }

.result-panel {
  margin-top: 14px;
  border: 1px solid #84caff;
  border-radius: 8px;
  background: #fff;
  overflow: hidden;
}
.result-panel.has-failed { border-color: #fda29b; }
.result-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 14px;
  background: #f0f7ff;
}
.result-panel.has-failed .result-head { background: #fff5f4; }
.result-head h3 { margin: 0 0 4px; font-size: 15px; }
.result-head p { margin: 0; font-size: 12px; color: var(--muted); }
.result-meta { margin-top: 2px; }
.result-summary { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }

.tag {
  display: inline-block;
  padding: 1px 10px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 20px;
}
.tag.success, .tag.skipped, .tag.failed { color: #fff; }
.tag.success { background: #12b76a; }
.tag.skipped { background: #f79009; }
.tag.failed { background: #f04438; }

.result-table th, .result-table td { font-size: 12px; }
.outcome-skipped td { background: #fffaeb; }
.outcome-failed td { background: #fef3f2; }

.retry-box { padding: 10px 14px; text-align: right; }
</style>
