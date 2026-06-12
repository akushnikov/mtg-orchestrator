<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { showConfirmDialog, showFailToast, showSuccessToast } from 'vant';
import {
  hideBackButton,
  mountBackButton,
  offBackButtonClick,
  onBackButtonClick,
  setMainButtonParams,
  showBackButton,
} from '@telegram-apps/sdk';

import { useProxyStore, type DefaultProxy, type ProxyInstance } from '../stores/proxy';

const route = useRoute();
const router = useRouter();
const proxy = useProxyStore();
const loading = ref(false);

const id = computed(() => Number(route.params.id));
const instance = computed(() => proxy.findById(id.value));
const isDefault = computed(() => id.value === -1);

function statusType(status?: string) {
  if (status === 'running') return 'success';
  if (status === 'stopped') return 'default';
  return 'warning';
}

function statusLabel(status?: string) {
  if (status === 'running') return 'Running';
  if (status === 'stopped') return 'Stopped';
  if (status === 'creating') return 'Creating...';
  return status || 'Default';
}

function goBack() {
  router.push({ name: 'list' });
}

async function runStart(item: ProxyInstance) {
  loading.value = true;
  const ok = await proxy.start(item.id);
  loading.value = false;
  ok ? showSuccessToast('Proxy started') : showFailToast('Start failed - try again');
}

async function runStop(item: ProxyInstance) {
  try {
    // Vant 4 replacement for Dialog.confirm.
    await showConfirmDialog({
      title: 'Stop proxy',
      message: `Stop proxy for ${item.domain}?`,
      confirmButtonText: 'Stop proxy',
      cancelButtonText: 'Cancel',
    });
  } catch {
    return;
  }
  loading.value = true;
  const ok = await proxy.stop(item.id);
  loading.value = false;
  ok ? showSuccessToast('Proxy stopped') : showFailToast('Stop failed - try again');
}

async function runDelete(item: ProxyInstance) {
  try {
    // Vant 4 replacement for Dialog.confirm.
    await showConfirmDialog({
      title: 'Delete proxy',
      message: `Delete proxy for ${item.domain}? This cannot be undone.`,
      confirmButtonText: 'Delete proxy',
      confirmButtonColor: 'var(--van-danger-color)',
      cancelButtonText: 'Cancel',
    });
  } catch {
    return;
  }

  loading.value = true;
  const ok = await proxy.remove(item.id);
  loading.value = false;
  if (ok) {
    showSuccessToast('Proxy deleted');
    await router.push({ name: 'list' });
  } else {
    showFailToast('Delete failed - try again');
  }
}

onMounted(async () => {
  setMainButtonParams.ifAvailable({ isVisible: false });
  mountBackButton.ifAvailable();
  showBackButton.ifAvailable();
  onBackButtonClick(goBack);
  if (!instance.value) {
    await proxy.fetchAll();
  }
});

onUnmounted(() => {
  offBackButtonClick(goBack);
  hideBackButton.ifAvailable();
});
</script>

<template>
  <section>
    <van-nav-bar :title="instance?.domain || 'Proxy'" left-arrow @click-left="goBack" />

    <van-empty v-if="!instance" description="Proxy not found" />

    <template v-else>
      <van-cell-group inset>
        <a v-if="instance.tg_url" :href="instance.tg_url" class="proxy-link">
          <van-cell title="Proxy link" value="Tap to connect" clickable>
            <template #icon>
              <van-icon class="cell-icon" name="link-o" />
            </template>
          </van-cell>
        </a>
        <van-cell title="Domain" :value="instance.domain" />
        <van-cell v-if="!isDefault" title="Status">
          <template #value>
            <van-tag :type="statusType((instance as ProxyInstance).status)">
              {{ statusLabel((instance as ProxyInstance).status) }}
            </van-tag>
          </template>
        </van-cell>
        <van-cell v-if="!isDefault" title="Internal port" :value="(instance as ProxyInstance).port" />
        <van-cell v-if="isDefault" title="Read-only" value="Default proxy cannot be modified" />
      </van-cell-group>

      <div v-if="!isDefault" class="actions">
        <van-button
          v-if="(instance as ProxyInstance).status === 'stopped'"
          type="primary"
          plain
          round
          block
          size="large"
          :loading="loading"
          @click="runStart(instance as ProxyInstance)"
        >
          Start proxy
        </van-button>
        <van-button
          v-if="(instance as ProxyInstance).status === 'running'"
          type="default"
          round
          block
          size="large"
          :loading="loading"
          @click="runStop(instance as ProxyInstance)"
        >
          Stop proxy
        </van-button>
        <van-button
          type="danger"
          plain
          round
          block
          size="large"
          :loading="loading"
          @click="runDelete(instance as ProxyInstance)"
        >
          Delete proxy
        </van-button>
      </div>
    </template>
  </section>
</template>

<style scoped>
.proxy-link {
  color: inherit;
  text-decoration: none;
}

.cell-icon {
  margin-right: 8px;
  color: var(--van-primary-color);
}

.actions {
  display: grid;
  gap: 16px;
  padding: 24px 16px;
}
</style>
