import { fileURLToPath, URL } from 'node:url';

import vue from '@vitejs/plugin-vue';
import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      proxy: {
        '/api': 'http://localhost:8080',
        '/bot': 'http://localhost:8080',
      },
    },
    define: {
      // DEV ONLY: set VITE_DEV_MOCK_INIT_DATA=true in .env.local for local dev without Telegram.
      // NEVER set in docker-compose.yml build args.
      __DEV_MOCK_INIT_DATA__: JSON.stringify(env.VITE_DEV_MOCK_INIT_DATA === 'true'),
    },
  };
});
