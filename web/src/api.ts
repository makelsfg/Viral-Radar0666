import type {
  AnalysisSummary,
  BatchReport,
  EnvStatus,
  ExpertRun,
  ExpertSource,
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
  cancelJob: (jobId: string) =>
    request<{ ok: boolean; message: string }>(`/api/jobs/${jobId}/cancel`, { method: 'POST' }),
  discardJob: (jobId: string) =>
    request<{ ok: boolean; message: string }>(`/api/jobs/${jobId}/discard`, { method: 'POST' }),
  confirmJob: (jobId: string, keep: boolean) =>
    request<{ ok: boolean; message: string }>(`/api/jobs/${jobId}/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keep }),
    }),
  reportMeta: (batch: string) =>
    request<ReportMeta>(`/api/report/meta?batch=${encodeURIComponent(batch)}`),
  expertRuns: (limit = 20) => request<ExpertRun[]>(`/api/expert/runs?limit=${limit}`),
  expertRun: (id: string) =>
    request<ExpertRun>(`/api/expert/runs/${encodeURIComponent(id)}`),
  createExpertRun: (payload: {
    title: string
    keyword: string
    sources: ExpertSource[]
    max_rounds: number
    pass_score: number
  }) =>
    request<ExpertRun>('/api/expert/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  cancelExpertRun: (id: string) =>
    request<{ ok: boolean }>(`/api/expert/runs/${encodeURIComponent(id)}/cancel`, {
      method: 'POST',
    }),
  deleteExpertRun: (id: string) =>
    request<{ ok: boolean }>(`/api/expert/runs/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    }),
  tasks: () => request<TaskInfo[]>('/api/tasks'),
  products: (taskId: string | undefined, batch: string, onlyDetail = true) =>
    request<ProductRow[]>(
      `/api/products?batch=${encodeURIComponent(batch)}` +
        (taskId ? `&task_id=${encodeURIComponent(taskId)}` : '') +
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
    request<{ text: string; model: string; saved?: boolean }>('/api/analysis/insights', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: taskId, batch }),
    }),
  savedInsights: (taskId: string, batch: string) =>
    request<{ saved: boolean; text: string | null; model: string | null; created_at: string | null }>(
      `/api/analysis/insights/saved?task_id=${encodeURIComponent(taskId)}&batch=${encodeURIComponent(batch)}`,
    ),
  analysisReport: (batch: string) =>
    request<BatchReport>(`/api/analysis/report?batch=${encodeURIComponent(batch)}`),
}