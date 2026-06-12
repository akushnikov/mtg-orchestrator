import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router';
import { retrieveLaunchParams } from '@telegram-apps/sdk';

const PlaceholderView = {
  template: '<van-empty description="Coming soon" />',
};

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
    component: PlaceholderView,
  },
  {
    path: '/proxy/:id',
    name: 'detail',
    component: PlaceholderView,
  },
  {
    path: '/create',
    name: 'create',
    component: PlaceholderView,
  },
  {
    path: '/create/progress',
    name: 'create-progress',
    component: PlaceholderView,
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
