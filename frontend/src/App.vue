<script setup lang="ts">
import { onMounted } from 'vue';
import { RouterView } from 'vue-router';
import { expandViewport, mountViewport } from '@telegram-apps/sdk';

import { useThemeStore } from './stores/theme';

const themeStore = useThemeStore();

onMounted(() => {
  themeStore.init();
  mountViewport.ifAvailable();
  expandViewport.ifAvailable();
});
</script>

<template>
  <van-config-provider :theme-vars="themeStore.themeVars">
    <main class="app-root">
      <RouterView />
    </main>
  </van-config-provider>
</template>

<style>
html,
body,
#app {
  min-height: 100%;
  margin: 0;
  background: var(--van-background);
  color: var(--van-text-color);
  font-family: var(--van-base-font);
}

.app-root {
  min-height: 100vh;
  padding-bottom: env(safe-area-inset-bottom);
  background: var(--van-background);
}

a {
  color: var(--van-primary-color);
}
</style>
