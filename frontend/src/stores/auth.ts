import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import { retrieveRawInitData } from '@telegram-apps/sdk';

type ParsedInitData = {
  raw: string;
  userId: number | null;
  authDate: Date | null;
};

// SDK v3: retrieveRawInitData() returns the raw init data query string (the
// exact value the backend validates). retrieveLaunchParams() does NOT expose
// initDataRaw/initData — it uses tgWebApp* keys — so we read the raw string and
// parse user.id / auth_date out of it ourselves (version-proof).
function readInitData(): ParsedInitData | null {
  let raw: string | undefined;
  try {
    raw = retrieveRawInitData();
  } catch {
    return null;
  }
  if (!raw) {
    return null;
  }

  const params = new URLSearchParams(raw);

  let userId: number | null = null;
  try {
    const user = JSON.parse(params.get('user') ?? '{}') as { id?: number };
    userId = typeof user.id === 'number' ? user.id : null;
  } catch {
    userId = null;
  }

  const authDateSec = Number(params.get('auth_date'));
  const authDate =
    Number.isFinite(authDateSec) && authDateSec > 0 ? new Date(authDateSec * 1000) : null;

  return { raw, userId, authDate };
}

export const useAuthStore = defineStore('auth', () => {
  const initDataRaw = ref('');
  const userId = ref<number | null>(null);
  const isValidated = ref(false);
  const retryCount = ref(0);

  const hasInitData = computed(() => initDataRaw.value.length > 0);

  function initialize(): boolean {
    const data = readInitData();
    if (!data) {
      initDataRaw.value = '';
      userId.value = null;
      isValidated.value = false;
      return false;
    }

    initDataRaw.value = data.raw;
    userId.value = data.userId;
    isValidated.value = true;
    return true;
  }

  function refreshInitData(): boolean {
    retryCount.value += 1;
    return initialize();
  }

  function currentAuthDate(): Date | null {
    return readInitData()?.authDate ?? null;
  }

  return {
    initDataRaw,
    userId,
    isValidated,
    retryCount,
    hasInitData,
    initialize,
    refreshInitData,
    currentAuthDate,
  };
});
