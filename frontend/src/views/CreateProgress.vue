<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { showDialog, showSuccessToast } from 'vant';
import { setMainButtonParams } from '@telegram-apps/sdk';

import { useAuthStore } from '../stores/auth';
import { useProxyStore } from '../stores/proxy';

type StageKey = 'validating' | 'creating_container' | 'rendering_nginx' | 'reloading_nginx' | 'done';
type StageStatus = 'pending' | 'active' | 'done' | 'error';

interface ProgressEvent {
  stage: StageKey;
  status: 'in_progress' | 'done' | 'error';
  detail?: string;
  tg_url?: string;
}

const stages: Array<{ key: StageKey; label: string }> = [
  { key: 'validating', label: 'Validating domain TLS' },
  { key: 'creating_container', label: 'Starting container' },
  { key: 'rendering_nginx', label: 'Updating routing' },
  { key: 'reloading_nginx', label: 'Reloading proxy' },
  { key: 'done', label: 'Proxy ready' },
];

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const proxy = useProxyStore();
const controller = new AbortController();
const stageState = ref<Record<StageKey, StageStatus>>({
  validating: 'active',
  creating_container: 'pending',
  rendering_nginx: 'pending',
  reloading_nginx: 'pending',
  done: 'pending',
});
const failedStage = ref<StageKey | null>(null);
const completed = ref(false);
const domain = computed(() => String(route.query.domain || ''));

function setStage(event: ProgressEvent) {
  if (event.status === 'error') {
    stageState.value[event.stage] = 'error';
    failedStage.value = event.stage;
    return;
  }
  if (event.status === 'done') {
    stageState.value[event.stage] = 'done';
  } else {
    stageState.value[event.stage] = 'active';
  }
}

function parseSse(buffer: string): ProgressEvent[] {
  return buffer
    .split('\n\n')
    .map((chunk) => chunk.split('\n').find((line) => line.startsWith('data: '))?.slice(6))
    .filter((data): data is string => Boolean(data))
    .map((data) => JSON.parse(data) as ProgressEvent);
}

async function handleError(event: ProgressEvent) {
  controller.abort();
  const title = event.detail === 'duplicate' ? 'Already exists' : event.stage === 'validating' ? 'Domain not reachable' : 'Creation failed';
  const message =
    event.detail === 'duplicate'
      ? `A proxy for ${domain.value} already exists.`
      : event.stage === 'validating'
        ? `The server could not verify TLS on ${domain.value}. Check the domain and try again.`
        : `Server error during ${event.stage}. The proxy was not created.`;
  await showDialog({ title, message, confirmButtonText: 'Go back' });
  await router.replace({ name: 'create', query: { domain: domain.value } });
}

async function handleDone() {
  completed.value = true;
  controller.abort();
  await proxy.fetchAll();
  window.setTimeout(() => {
    showSuccessToast('Proxy created');
    router.replace({ name: 'list' });
  }, 800);
}

async function openStream() {
  if (!domain.value) {
    await router.replace({ name: 'create' });
    return;
  }

  // EventSource cannot send POST bodies or Authorization headers, so this uses fetch-based SSE.
  const response = await fetch('/api/v1/instances/create/stream', {
    method: 'POST',
    body: JSON.stringify({ domain: domain.value }),
    headers: {
      'Content-Type': 'application/json',
      Authorization: `tma ${auth.initDataRaw}`,
    },
    signal: controller.signal,
  });

  if (!response.ok || !response.body) {
    await handleError({ stage: 'creating_container', status: 'error' });
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const boundary = buffer.lastIndexOf('\n\n');
    if (boundary === -1) continue;
    const ready = buffer.slice(0, boundary + 2);
    buffer = buffer.slice(boundary + 2);
    for (const event of parseSse(ready)) {
      setStage(event);
      if (event.status === 'error') {
        await handleError(event);
        return;
      }
      if (event.stage === 'done') {
        await handleDone();
        return;
      }
    }
  }
}

function cancel() {
  controller.abort();
  router.replace({ name: 'create', query: { domain: domain.value } });
}

onMounted(() => {
  setMainButtonParams.ifAvailable({ isVisible: false });
  void openStream();
});

onUnmounted(() => {
  controller.abort();
});
</script>

<template>
  <section>
    <van-nav-bar :title="completed ? 'Done!' : 'Creating proxy...'" />
    <div class="progress-body">
      <van-steps direction="vertical" :active="stages.findIndex((stage) => stageState[stage.key] === 'active')">
        <van-step v-for="stage in stages" :key="stage.key">
          <template #active-icon>
            <van-loading v-if="stageState[stage.key] === 'active'" size="16px" />
          </template>
          <template #finish-icon>
            <van-icon v-if="stageState[stage.key] === 'error'" name="close" color="var(--van-danger-color)" />
            <van-icon v-else name="success" color="var(--van-success-color)" />
          </template>
          <span :class="{ failed: failedStage === stage.key }">{{ stage.label }}</span>
        </van-step>
      </van-steps>

      <van-button v-if="!completed" type="default" plain round block @click="cancel">Cancel</van-button>
    </div>
  </section>
</template>

<style scoped>
.progress-body {
  display: grid;
  gap: 24px;
  padding: 16px;
}

.failed {
  color: var(--van-danger-color);
}
</style>
