import { createRouter, createWebHistory } from 'vue-router'

import LineageImpactView from '@/views/LineageImpactView.vue'
import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/lineage-impact', name: 'lineage-impact', component: LineageImpactView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
