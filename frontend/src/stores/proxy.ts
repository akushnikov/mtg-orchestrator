import { defineStore } from 'pinia';
import { ref } from 'vue';

import { api } from '../lib/api';

export interface ProxyInstance {
  id: number;
  domain: string;
  slug: string;
  port: number;
  status: string;
  tg_url: string;
  created_at: string;
}

export interface DefaultProxy {
  id: -1;
  domain: string;
  tg_url: string;
  read_only: true;
}

export const useProxyStore = defineStore('proxy', () => {
  const instances = ref<ProxyInstance[]>([]);
  const defaultProxy = ref<DefaultProxy | null>(null);
  const loading = ref(false);
  const error = ref('');

  async function fetchAll(): Promise<Response[]> {
    loading.value = true;
    error.value = '';

    try {
      const responses = await Promise.all([
        api.get('/api/v1/instances/'),
        api.get('/api/v1/instances/default'),
      ]);

      if (responses.some((response) => response.status === 403)) {
        return responses;
      }

      const [instancesResponse, defaultResponse] = responses;
      if (!instancesResponse.ok || !defaultResponse.ok) {
        error.value = 'Failed to load proxies';
        return responses;
      }

      instances.value = await instancesResponse.json();
      defaultProxy.value = await defaultResponse.json();
      return responses;
    } finally {
      loading.value = false;
    }
  }

  return {
    instances,
    defaultProxy,
    loading,
    error,
    fetchAll,
  };
});
