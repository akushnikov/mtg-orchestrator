import { fileURLToPath, URL } from 'node:url';

import { VantResolver } from '@vant/auto-import-resolver';
import vue from '@vitejs/plugin-vue';
import Components from 'unplugin-vue-components/vite';
import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');

  return {
    plugins: [
      vue(),
      // On-demand Vant: auto-imports the <van-*> components used in templates
      // plus their per-component styles, so only what's used is bundled (no full
      // vant/lib/index.css). Function APIs (showToast/showDialog) stay as explicit
      // imports; their styles are pulled in main.ts.
      Components({
        resolvers: [VantResolver()],
        dts: 'src/components.d.ts',
      }),
    ],
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
      // DEBUG ONLY: build with VITE_DISABLE_DECOY=true to bypass the decoy/maskirovka
      // redirect and inspect the Mini App in a plain browser. Defaults false →
      // a normal production build keeps the decoy guard (bypass is dead-code-eliminated).
      __DISABLE_DECOY__: JSON.stringify(env.VITE_DISABLE_DECOY === 'true'),
    },
  };
});
