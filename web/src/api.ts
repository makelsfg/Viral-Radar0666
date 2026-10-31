import type {
  AnalysisSummary,
  EnvStatus,
  HotwordKeyword,
  HotwordRow,
  JobState,
  ProductDetail,
  ProductRow,
  ReportMeta,
  TaskInfo,
} from './types'

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init)
  if (!resp.ok) {
    let detail = `请求失败（${resp.status}）`
    try {
      const data = await resp.json()
      if (data?.detail) {
        detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
      }
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  return resp.json() as Promise<T>
}

export const api = {
  env: () => request<EnvStatus>('/api/env'),
  login: (profile?: string | null, site: 'taobao' | '1688' | 'pdd' = 'taobao') =>
    request<JobState>('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile: profile ?? null, site }),
    }),
  collect: (body: unknown) =>
    request<JobState>('/api/collect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  resume: (jobId: string) =>
    request<JobState>(`/api/jobs/${jobId}/resume`, { method: 'POST' }),
  continueJob: (jobId: string, platform?: string) =>
    request<JobState>(`/api/jobs/${jobId}/continue`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ platform: platform ?? null }),
    }),
  job: (jobId: string) => request<JobState>(`/api/jobs/${jobId}`),
  reportMeta: (batch: string) =>
    request<ReportMeta>(`/api/report/meta?batch=${encodeURIComponent(batch)}`),
  tasks: () => request<TaskInfo[]>('/api/tasks'),
  products: (taskId: string, batch: string, onlyDetail = true) =>
    request<ProductRow[]>(
      `/api/products?task_id=${encodeURIComponent(taskId)}&batch=${encodeURIComponent(batch)}` +
        `&only_detail=${onlyDetail ? 1 : 0}`,
    ),
  product: (itemId: string, platform = 'tmall') =>
    request<ProductDetail>(
      `/api/products/${encodeURIComponent(itemId)}?platform=${encodeURIComponent(platform)}`,
    ),
  deleteTask: (taskId: string) =>
    request<{ ok: boolean }>(`/api/tasks/${encodeURIComponent(taskId)}`, { method: 'DELETE' }),
  deleteBatch: (taskId: string, batch: string) =>
    request<{ ok: boolean }>(
      `/api/batches?task_id=${encodeURIComponent(taskId)}&batch=${encodeURIComponent(batch)}`,
      { method: 'DELETE' },
    ),
  analysisSummary: (taskId: string, batch: string) =>
    request<AnalysisSummary>(
      `/api/analysis/summary?task_id=${encodeURIComponent(taskId)}&batch=${encodeURIComponent(batch)}`,
    ),
  llmStatus: () =>
    request<{ configured: boolean; model: string | null }>('/api/analysis/llm-status'),
  analysisInsights: (taskId: string, batch: string) =>
    request<{ text: string; model: string }>('/api/analysis/insights', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: taskId, batch }),
    }),
  fetchHotwords: (keyword: string) =>
    request<{ keyword: string; words: string[] }>('/api/hotwords', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keyword }),
    }),
  hotwordHistory: (keyword?: string) =>
    request<HotwordRow[]>(
      `/api/hotwords${keyword ? `?keyword=${encodeURIComponent(keyword)}` : ''}`,
    ),
  hotwordKeywords: () => request<HotwordKeyword[]>('/api/hotwords/keywords'),
}