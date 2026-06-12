import 'vant/lib/index.css';

import { mockTelegramEnv } from '@telegram-apps/sdk';
import { createPinia } from 'pinia';
import { createApp } from 'vue';

import App from './App.vue';
import router from './router';

if (import.meta.env.DEV && __DEV_MOCK_INIT_DATA__) {
  mockTelegramEnv({
    launchParams: {
      tgWebAppData: new URLSearchParams([
        ['user', JSON.stringify({ id: 12345, first_name: 'Dev' })],
        ['auth_date', String(Math.floor(Date.now() / 1000))],
        ['hash', 'dev-bypass'],
      ]),
      tgWebAppVersion: '8',
      tgWebAppPlatform: 'tdesktop',
      tgWebAppThemeParams: {
        bg_color: '#ffffff',
        text_color: '#202124',
        button_color: '#2481cc',
        button_text_color: '#ffffff',
      },
    },
    resetPostMessage: true,
  });
}

const app = createApp(App);

app.use(createPinia());
app.use(router);
app.mount('#app');
