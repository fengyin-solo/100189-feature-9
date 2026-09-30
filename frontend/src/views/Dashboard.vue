<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h2>运营概览</h2>
        <p class="page-desc">汇总各业务模块的关键指标，先看总量再看异常；养护材料结存与领用界面实时一致。</p>
      </div>
    </header>
    <div class="stat-row">
      <article v-for="card in cards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.value }}</strong>
      </article>
    </div>
    <table class="data-table">
      <thead>
        <tr><th>业务模块</th><th>今日新增</th><th>待处理</th><th>异常量</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in moduleRows" :key="row.name">
          <td>{{ row.name }}</td>
          <td>{{ row.created }}</td>
          <td>{{ row.pending }}</td>
          <td>{{ row.abnormal }}</td>
        </tr>
      </tbody>
    </table>

    <section class="board-section">
      <div class="board-head">
        <h3>养护材料库存看板</h3>
        <p class="page-desc">结存数量随领用、冻结、耗尽实时同步，可用数量与「养护材料」领用界面完全一致。</p>
      </div>
      <div class="stat-row">
        <article v-for="card in materialCards" :key="card.label" class="stat-card" :class="cardClass(card.label)">
          <span class="stat-label">{{ card.label }}</span>
          <strong class="stat-value">{{ card.value }}</strong>
        </article>
      </div>
      <table class="data-table">
        <thead>
          <tr><th>材料编号</th><th>材料名称</th><th>规格型号</th><th>结存数量</th><th>储备下限</th><th>可用数量</th><th>材料状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in materialRows" :key="String(item.id)" :class="rowClass(String(item['材料状态']))">
            <td>{{ item['材料编号'] }}</td>
            <td>{{ item['材料名称'] }}</td>
            <td>{{ item['规格型号'] }}</td>
            <td>{{ item['结存数量'] }}</td>
            <td>{{ item['储备下限'] }}</td>
            <td>
              <strong :class="{ 'available-zero': Number(item['可用数量']) === 0 }">{{ item['可用数量'] }}</strong>
            </td>
            <td><span class="status-pill" :class="pillClass(String(item['材料状态']))">{{ item['材料状态'] }}</span></td>
          </tr>
        </tbody>
      </table>
    </section>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { fetchJson } from '@/api/client'

type Overview = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

type InventoryBoard = {
  cards: { label: string; value: number }[]
  items: Record<string, string | number | null>[]
}

const cards = ref<Overview['cards']>([])
const moduleRows = ref<Overview['modules']>([])
const materialCards = ref<InventoryBoard['cards']>([])
const materialRows = ref<InventoryBoard['items']>([])

function cardClass(label: string): string {
  if (label === '临近不足' || label === '已耗尽') return 'card-warning'
  if (label === '已冻结') return 'card-frozen'
  return ''
}

function rowClass(status: string): string {
  if (status === '临近不足') return 'row-low'
  if (status === '已冻结') return 'row-frozen'
  if (status === '已耗尽') return 'row-empty'
  return ''
}

function pillClass(status: string): string {
  if (status === '正常可用') return 'is-normal'
  if (status === '临近不足') return 'is-low'
  if (status === '已冻结') return 'is-frozen'
  return 'is-empty'
}

onMounted(async () => {
  try {
    const payload = await fetchJson<Overview>('/api/overview')
    cards.value = payload.cards
    moduleRows.value = payload.modules
  } catch {
    cards.value = [{"label": "业务模块", "value": 0}, {"label": "今日新增", "value": 0}]
    moduleRows.value = [{"name": "管段档案", "created": 0, "pending": 0, "abnormal": 0}, {"name": "检查井", "created": 0, "pending": 0, "abnormal": 0}, {"name": "阀门井室", "created": 0, "pending": 0, "abnormal": 0}, {"name": "泵站设施", "created": 0, "pending": 0, "abnormal": 0}, {"name": "巡查任务", "created": 0, "pending": 0, "abnormal": 0}, {"name": "缺陷登记", "created": 0, "pending": 0, "abnormal": 0}, {"name": "内窥检测", "created": 0, "pending": 0, "abnormal": 0}, {"name": "修复施工", "created": 0, "pending": 0, "abnormal": 0}, {"name": "压力监测", "created": 0, "pending": 0, "abnormal": 0}, {"name": "流量监测", "created": 0, "pending": 0, "abnormal": 0}, {"name": "泄漏排查", "created": 0, "pending": 0, "abnormal": 0}, {"name": "清淤疏浚", "created": 0, "pending": 0, "abnormal": 0}, {"name": "养护材料", "created": 0, "pending": 0, "abnormal": 0}, {"name": "养护机械", "created": 0, "pending": 0, "abnormal": 0}, {"name": "占道许可", "created": 0, "pending": 0, "abnormal": 0}, {"name": "公众诉求", "created": 0, "pending": 0, "abnormal": 0}, {"name": "养护资金", "created": 0, "pending": 0, "abnormal": 0}, {"name": "管网档案", "created": 0, "pending": 0, "abnormal": 0}]
  }

  // 库存看板与养护材料领用界面取同一个接口，保证结存/可用数量口径一致。
  try {
    const board = await fetchJson<InventoryBoard>('/api/material/inventory')
    materialCards.value = board.cards
    materialRows.value = board.items
  } catch {
    materialCards.value = []
    materialRows.value = []
  }
})
</script>

<style scoped>
.board-section { margin-top: 24px; }
.board-head h3 { margin: 0 0 4px; font-size: 16px; }
.card-warning strong { color: #b54708; }
.card-frozen strong { color: #1d4ed8; }
.available-zero { color: #b42318; }
.row-low td { background: #fffaeb; }
.row-frozen td { background: #eff4ff; }
.row-empty td { background: #f2f4f7; color: var(--muted); }
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
</style>
