<script setup lang="ts">
import { onMounted } from 'vue';
import { useRouter } from 'vue-router';

import { useAuthStore } from '../stores/auth';
import { useProxyStore } from '../stores/proxy';

const router = useRouter();
const auth = useAuthStore();
const proxy = useProxyStore();

function isAuthDateStale(): boolean {
  const authDate = auth.currentAuthDate();
  if (!authDate) return false;
  return Date.now() - authDate.getTime() > 300_000;
}

onMounted(async () => {
  // DEBUG (build-time): skip the decoy/reopen redirects so the screens render in
  // a plain browser. API calls will still 403 without real initData.
  if (!auth.initialize() && !__DISABLE_DECOY__) {
    await router.replace({ name: 'decoy' });
    return;
  }

  const responses = await proxy.fetchAll();
  if (!__DISABLE_DECOY__ && responses.some((response) => response.status === 403)) {
    await router.replace({ name: isAuthDateStale() ? 'reopen' : 'decoy' });
    return;
  }

  await router.replace({ name: 'list' });
});
</script>

<template>
  <section>
    <van-nav-bar title="MTG Proxies" />
    <div class="boot-content">
      <van-skeleton title :row="4" />
    </div>
  </section>
</template>

<style scoped>
.boot-content {
  padding: 16px;
}
</style>
