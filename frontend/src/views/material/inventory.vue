<template>
  <section class="page" data-module="material-inventory">
    <header class="page-head">
      <div>
        <h2>库存看板</h2>
        <p class="page-desc">养护材料结存实时同步；这里的可用数量与领用界面逐条一致，冻结、耗尽材料不计入可用。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="goConsume">去领用界面</button>
        <button class="btn ghost" type="button" @click="reload">刷新看板</button>
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
        <tr>
          <th>材料编号</th>
          <th>材料名称</th>
          <th>规格型号</th>
          <th>结存数量</th>
          <th>可用数量</th>
          <th>储备下限</th>
          <th>计量单位</th>
          <th>材料状态</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in items" :key="String(row.id)" :class="`st-${row.status}`">
          <td>{{ row['材料编号'] }}</td>
          <td>{{ row['材料名称'] }}</td>
          <td>{{ row['规格型号'] }}</td>
          <td :class="{ low: row['结存数量'] < row['储备下限'] }">{{ row['结存数量'] }}</td>
          <td>{{ row['可用数量'] }}</td>
          <td>{{ row['储备下限'] }}</td>
          <td>{{ row['计量单位'] }}</td>
          <td><span class="status-tag">{{ row.status }}</span></td>
        </tr>
        <tr v-if="!items.length">
          <td colspan="8" class="empty-state">暂无库存数据</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>数据来源：GET /api/material/inventory，与领用界面共用同一份结存口径</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { fetchJson } from '@/api/client'

type InventoryItem = {
  id: number
  '材料编号': string
  '材料名称': string
  '规格型号': string
  '计量单位': string
  '结存数量': number
  '可用数量': number
  '储备下限': number
  status: string
}
type InventoryBoard = {
  items: InventoryItem[]
  cards: { label: string; value: number }[]
}

const items = ref<InventoryItem[]>([])
const cards = ref<{ label: string; value: number }[]>([])
const errorMessage = ref('')

const router = useRouter()

function goConsume() {
  void router.push('/material')
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await fetchJson<InventoryBoard>('/api/material/inventory')
    items.value = payload.items
    cards.value = payload.cards
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '库存看板读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
td.low { color: #b54708; font-weight: 600; }
tr.st-已耗尽 td { color: #98a2b3; }
.status-tag { font-size: 12px; padding: 2px 8px; border-radius: 999px; background: #f2f4f7; color: #344054; }
tr.st-临近不足 .status-tag { background: #fffaeb; color: #b54708; }
tr.st-已冻结 .status-tag { background: #f2f4f7; color: #475467; }
tr.st-已耗尽 .status-tag { background: #fef3f2; color: #b42318; }
</style>
