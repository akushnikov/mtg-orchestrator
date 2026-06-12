/// <reference types="vite/client" />

declare const __DEV_MOCK_INIT_DATA__: boolean;

interface ImportMetaEnv {
  readonly VITE_DEV_MOCK_INIT_DATA?: string;
  readonly VITE_BOT_USERNAME?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
