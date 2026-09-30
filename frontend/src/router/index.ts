import { createRouter, createWebHistory } from 'vue-router'

import Dashboard from '@/views/Dashboard.vue'
const Pipe = () => import('@/views/pipe/index.vue')
const Manhole = () => import('@/views/manhole/index.vue')
const Valve = () => import('@/views/valve/index.vue')
const Pumpstation = () => import('@/views/pumpstation/index.vue')
const Patrol = () => import('@/views/patrol/index.vue')
const Defect = () => import('@/views/defect/index.vue')
const Cctv = () => import('@/views/cctv/index.vue')
const Repair = () => import('@/views/repair/index.vue')
const Pressure = () => import('@/views/pressure/index.vue')
const Flow = () => import('@/views/flow/index.vue')
const Leak = () => import('@/views/leak/index.vue')
const Dredge = () => import('@/views/dredge/index.vue')
const Material = () => import('@/views/material/index.vue')
const MaterialInventory = () => import('@/views/material/inventory.vue')
const Equip = () => import('@/views/equip/index.vue')
const Traffic = () => import('@/views/traffic/index.vue')
const Complaint = () => import('@/views/complaint/index.vue')
const Fund = () => import('@/views/fund/index.vue')
const Archive = () => import('@/views/archive/index.vue')

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: Dashboard },
    { path: '/pipe', name: 'pipe', component: Pipe },
    { path: '/manhole', name: 'manhole', component: Manhole },
    { path: '/valve', name: 'valve', component: Valve },
    { path: '/pumpstation', name: 'pumpstation', component: Pumpstation },
    { path: '/patrol', name: 'patrol', component: Patrol },
    { path: '/defect', name: 'defect', component: Defect },
    { path: '/cctv', name: 'cctv', component: Cctv },
    { path: '/repair', name: 'repair', component: Repair },
    { path: '/pressure', name: 'pressure', component: Pressure },
    { path: '/flow', name: 'flow', component: Flow },
    { path: '/leak', name: 'leak', component: Leak },
    { path: '/dredge', name: 'dredge', component: Dredge },
    { path: '/material', name: 'material', component: Material },
    { path: '/material/inventory', name: 'material-inventory', component: MaterialInventory },
    { path: '/equip', name: 'equip', component: Equip },
    { path: '/traffic', name: 'traffic', component: Traffic },
    { path: '/complaint', name: 'complaint', component: Complaint },
    { path: '/fund', name: 'fund', component: Fund },
    { path: '/archive', name: 'archive', component: Archive },
  ],
})

export default router
