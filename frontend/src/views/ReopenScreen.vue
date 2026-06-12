<script setup lang="ts">
function openTelegram() {
  const username = import.meta.env.VITE_BOT_USERNAME || '';
  window.location.href = username ? `https://t.me/${username}` : 'https://t.me/';
}
</script>

<template>
  <section class="reopen-screen">
    <van-empty image="network" description="Open the Mini App from the Telegram bot to continue.">
      <template #default>
        <h1>Session expired</h1>
        <van-button type="primary" round block @click="openTelegram">Open in Telegram</van-button>
      </template>
    </van-empty>
  </section>
</template>

<style scoped>
.reopen-screen {
  display: grid;
  min-height: 100vh;
  align-items: center;
  padding: 24px 16px;
}

h1 {
  margin: 0 0 16px;
  font-size: 16px;
  font-weight: 600;
  line-height: 22px;
  text-align: center;
}
</style>
