import { useAuthStore } from '../stores/auth';

const BASE_URL = '';

type JsonBody = Record<string, unknown> | unknown[] | undefined;

async function request(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
  const auth = useAuthStore();
  const headers = new Headers(init.headers);

  if (auth.initDataRaw) {
    headers.set('Authorization', `tma ${auth.initDataRaw}`);
  }

  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers,
  });

  if (response.status === 403 && retry && auth.refreshInitData()) {
    return request(path, init, false);
  }

  return response;
}

function withJson(method: string, path: string, body?: JsonBody): Promise<Response> {
  return request(path, {
    method,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export const api = {
  get: (path: string) => request(path),
  post: (path: string, body?: JsonBody) => withJson('POST', path, body),
  patch: (path: string, body?: JsonBody) => withJson('PATCH', path, body),
  delete: (path: string) => request(path, { method: 'DELETE' }),
};
