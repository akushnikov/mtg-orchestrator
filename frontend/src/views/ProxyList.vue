<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import {
  hideBackButton,
  mountMainButton,
  offMainButtonClick,
  onMainButtonClick,
  setMainButtonParams,
} from '@telegram-apps/sdk';

import { useProxyStore, type ProxyInstance } from '../stores/proxy';

const router = useRouter();
const proxy = useProxyStore();
const refreshing = ref(false);

const sortedInstances = computed(() =>
  [...proxy.instances].sort((a, b) => {
    if (a.status !== b.status) {
      if (a.status === 'running') return -1;
      if (b.status === 'running') return 1;
    }
    return a.domain.localeCompare(b.domain);
  }),
);

function statusType(status: string) {
  if (status === 'running') return 'success';
  if (status === 'stopped') return 'default';
  return 'warning';
}

function statusLabel(status: string) {
  if (status === 'running') return 'Running';
  if (status === 'stopped') return 'Stopped';
  if (status === 'creating') return 'Creating...';
  return status;
}

async function refresh() {
  refreshing.value = true;
  await proxy.fetchAll();
  refreshing.value = false;
}

function openCreate() {
  router.push({ name: 'create' });
}

function openDetail(instance: ProxyInstance | { id: number }) {
  router.push({ name: 'detail', params: { id: instance.id } });
}

onMounted(async () => {
  hideBackButton.ifAvailable();
  mountMainButton.ifAvailable();
  setMainButtonParams.ifAvailable({
    isVisible: true,
    isEnabled: true,
    text: 'Add proxy',
  });
  onMainButtonClick(openCreate);
  if (!proxy.defaultProxy && proxy.instances.length === 0) {
    await proxy.fetchAll();
  }
});

onUnmounted(() => {
  offMainButtonClick(openCreate);
});
</script>

<template>
  <section>
    <van-nav-bar title="MTG Proxies">
      <template #right>
        <button class="icon-button" aria-label="Add proxy" title="Add proxy" @click="openCreate">
          <van-icon name="add-o" />
        </button>
      </template>
    </van-nav-bar>

    <van-pull-refresh v-model="refreshing" @refresh="refresh">
      <div class="list-body">
        <van-cell
          v-if="proxy.defaultProxy"
          title="mtg-default"
          :label="proxy.defaultProxy.domain"
          clickable
          @click="openDetail(proxy.defaultProxy)"
        >
          <template #icon>
            <van-icon class="cell-icon" name="shield-o" />
          </template>
          <template #right-icon>
            <van-tag type="primary">Default</van-tag>
          </template>
        </van-cell>

        <van-list :finished="true">
          <van-cell
            v-for="instance in sortedInstances"
            :key="instance.id"
            :title="instance.domain"
            is-link
            @click="openDetail(instance)"
          >
            <template #label>
              <van-tag :type="statusType(instance.status)">{{ statusLabel(instance.status) }}</van-tag>
            </template>
          </van-cell>
        </van-list>

        <van-empty v-if="sortedInstances.length === 0" description="No proxies yet">
          <van-button type="primary" round @click="openCreate">Add your first proxy</van-button>
        </van-empty>
      </div>
    </van-pull-refresh>
  </section>
</template>

<style scoped>
.list-body {
  min-height: calc(100vh - 46px);
  padding: 16px;
}

.icon-button {
  display: inline-grid;
  min-width: 44px;
  min-height: 44px;
  place-items: center;
  border: 0;
  background: transparent;
  color: var(--van-primary-color);
  font: inherit;
}

.cell-icon {
  margin-right: 8px;
  color: var(--van-primary-color);
}
</style>
