import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import { retrieveLaunchParams } from '@telegram-apps/sdk';

type LaunchParams = {
  initDataRaw?: string;
  initData?: {
    authDate?: Date | number | string;
    user?: {
      id?: number;
    };
  };
};

function readLaunchParams(): LaunchParams | null {
  try {
    return retrieveLaunchParams(true) as LaunchParams;
  } catch {
    return null;
  }
}

export const useAuthStore = defineStore('auth', () => {
  const initDataRaw = ref('');
  const userId = ref<number | null>(null);
  const isValidated = ref(false);
  const retryCount = ref(0);

  const hasInitData = computed(() => initDataRaw.value.length > 0);

  function initialize(): boolean {
    const params = readLaunchParams();
    if (!params?.initDataRaw) {
      initDataRaw.value = '';
      userId.value = null;
      isValidated.value = false;
      return false;
    }

    initDataRaw.value = params.initDataRaw;
    userId.value = params.initData?.user?.id ?? null;
    isValidated.value = true;
    return true;
  }

  function refreshInitData(): boolean {
    retryCount.value += 1;
    return initialize();
  }

  function currentAuthDate(): Date | null {
    const params = readLaunchParams();
    const value = params?.initData?.authDate;
    if (value instanceof Date) return value;
    if (typeof value === 'number') return new Date(value * 1000);
    if (typeof value === 'string') return new Date(Number(value) * 1000);
    return null;
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
