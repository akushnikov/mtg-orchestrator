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

  async function start(id: number): Promise<boolean> {
    const response = await api.patch(`/api/v1/instances/${id}/start`);
    if (!response.ok) return false;
    await fetchAll();
    return true;
  }

  async function stop(id: number): Promise<boolean> {
    const response = await api.patch(`/api/v1/instances/${id}/stop`);
    if (!response.ok) return false;
    await fetchAll();
    return true;
  }

  async function remove(id: number): Promise<boolean> {
    const response = await api.delete(`/api/v1/instances/${id}`);
    if (!response.ok) return false;
    instances.value = instances.value.filter((instance) => instance.id !== id);
    return true;
  }

  function findById(id: number): ProxyInstance | DefaultProxy | undefined {
    if (id === -1) return defaultProxy.value ?? undefined;
    return instances.value.find((instance) => instance.id === id);
  }

  return {
    instances,
    defaultProxy,
    loading,
    error,
    fetchAll,
    start,
    stop,
    remove,
    findById,
  };
});
