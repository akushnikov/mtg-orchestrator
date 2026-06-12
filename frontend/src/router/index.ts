import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router';
import { retrieveLaunchParams } from '@telegram-apps/sdk';

function hasTelegramInitData(): boolean {
  try {
    const params = retrieveLaunchParams(true) as { initDataRaw?: string };
    return Boolean(params.initDataRaw);
  } catch {
    return false;
  }
}

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'boot',
    component: () => import('../views/BootScreen.vue'),
  },
  {
    path: '/list',
    name: 'list',
    component: () => import('../views/ProxyList.vue'),
  },
  {
    path: '/proxy/:id',
    name: 'detail',
    component: () => import('../views/ProxyDetail.vue'),
  },
  {
    path: '/create',
    name: 'create',
    component: () => import('../views/CreateProxy.vue'),
  },
  {
    path: '/create/progress',
    name: 'create-progress',
    component: () => import('../views/CreateProgress.vue'),
  },
  {
    path: '/decoy',
    name: 'decoy',
    component: () => import('../views/DecoyPage.vue'),
  },
  {
    path: '/reopen',
    name: 'reopen',
    component: () => import('../views/ReopenScreen.vue'),
  },
  {
    path: '/boot',
    redirect: '/',
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach((to) => {
  if (['decoy', 'reopen', 'boot'].includes(String(to.name))) {
    return true;
  }
  if (!hasTelegramInitData()) {
    return { name: 'decoy' };
  }
  return true;
});

export default router;
