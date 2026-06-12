<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import {
  mountBackButton,
  mountMainButton,
  offBackButtonClick,
  offMainButtonClick,
  onBackButtonClick,
  onMainButtonClick,
  setMainButtonParams,
  showBackButton,
} from '@telegram-apps/sdk';

const router = useRouter();
const route = useRoute();
const domain = ref(String(route.query.domain || ''));
const submitted = ref(false);
const domainPattern = /^(?!-)[a-z0-9-]{1,63}(?:\.[a-z0-9-]{1,63})+(?<!-)$/i;

const errorMessage = computed(() => {
  if (!submitted.value || domainPattern.test(domain.value.trim())) return '';
  return 'Enter a valid domain (e.g. vk.com)';
});

function goBack() {
  router.push({ name: 'list' });
}

function submit() {
  submitted.value = true;
  const value = domain.value.trim().toLowerCase();
  if (!domainPattern.test(value)) return;
  router.push({ name: 'create-progress', query: { domain: value } });
}

onMounted(() => {
  mountBackButton.ifAvailable();
  showBackButton.ifAvailable();
  onBackButtonClick(goBack);
  mountMainButton.ifAvailable();
  setMainButtonParams.ifAvailable({
    isVisible: true,
    isEnabled: true,
    text: 'Validate & create',
  });
  onMainButtonClick(submit);
});

onUnmounted(() => {
  offBackButtonClick(goBack);
  offMainButtonClick(submit);
});
</script>

<template>
  <section>
    <van-nav-bar title="Add proxy" left-arrow @click-left="goBack" />
    <div class="form-body">
      <van-field
        v-model="domain"
        label="Masquerade domain"
        placeholder="e.g. vk.com"
        type="text"
        autocomplete="off"
        autocorrect="off"
        autocapitalize="none"
        :error-message="errorMessage"
      />
      <van-button type="primary" round block size="large" @click="submit">
        Validate & create
      </van-button>
    </div>
  </section>
</template>

<style scoped>
.form-body {
  display: grid;
  gap: 16px;
  padding: 16px;
}
</style>
