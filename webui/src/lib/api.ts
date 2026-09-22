import axios from 'axios'
import type { CrawlerConfig as BaseCrawlerConfig } from '@/types/crawler'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Types
export interface CrawlerConfig extends BaseCrawlerConfig {
  task_id?: string
}

export interface CrawlerStatus {
  status: 'idle' | 'running' | 'stopping' | 'error'
  task_id: string | null
  platform: string | null
  crawler_type: string | null
  started_at: string | null
  error_message: string | null
  recovery_type: 'xhs_verification' | 'xhs_resume' | 'profile_resume' | null
  recovery_task_id: string | null
  recovery_platform: 'dy' | 'ks' | 'xhs' | null
  recovery_reason: 'risk_control' | 'login_expired' | 'browser_closed' | 'network_error' | null
}

export interface CrawlerStartResponse {
  status: string
  message: string
  task_id: string
}

export interface LogEntry {
  id: number
  timestamp: string
  level: 'info' | 'warning' | 'error' | 'success' | 'debug'
  message: string
}

export interface DataFile {
  name: string
  path: string
  size: number
  modified_at: number
  record_count: number | null
  type: string
}

export interface FilePreviewResponse {
  data: Record<string, unknown>[]
  total: number
  columns?: string[]
}

export interface Platform {
  value: string
  label: string
  icon: string
}

export interface ConfigOption {
  value: string
  label: string
}

export const encodeFilePath = (path: string) =>
  path.split('/').map(encodeURIComponent).join('/')

// API functions
export const crawlerApi = {
  start: (config: CrawlerConfig) =>
    api.post<CrawlerStartResponse>('/crawler/start', config),
  stop: (taskId: string) => api.post('/crawler/stop', { task_id: taskId }),
  recoverXhs: (taskId: string) =>
    api.post(`/crawler/recover-xhs/${encodeURIComponent(taskId)}`),
  recoverDouyinProfiles: (taskId: string) =>
    api.post(`/crawler/recover-douyin-profiles/${encodeURIComponent(taskId)}`),
  recoverProfiles: (taskId: string) =>
    api.post(`/crawler/recover-profiles/${encodeURIComponent(taskId)}`),
  getStatus: (taskId?: string) =>
    api.get<CrawlerStatus>('/crawler/status', {
      params: taskId ? { task_id: taskId } : undefined,
    }),
  getLogs: (limit = 100) => api.get<{ logs: LogEntry[] }>('/crawler/logs', { params: { limit } }),
}

export const dataApi = {
  getFiles: (platform?: string, fileType?: string) =>
    api.get<{ files: DataFile[] }>('/data/files', { params: { platform, file_type: fileType } }),
  getFileContent: (path: string, limit = 100) =>
    api.get<FilePreviewResponse>('/data/files/' + encodeFilePath(path), { params: { preview: true, limit } }),
  getStats: () => api.get('/data/stats'),
  getDownloadUrl: (path: string) => `/api/data/download/${encodeFilePath(path)}`,
  getArchiveUrl: (category?: string) =>
    `/api/data/archive${category ? `?category=${encodeURIComponent(category)}` : ''}`,
  deleteFiles: (paths: string[]) => api.delete<{ deleted: number }>('/data/files', { data: { paths } }),
}

export const configApi = {
  getPlatforms: () => api.get<{ platforms: Platform[] }>('/config/platforms'),
  getOptions: () =>
    api.get<{
      login_types: ConfigOption[]
      crawler_types: ConfigOption[]
      save_options: ConfigOption[]
    }>('/config/options'),
}

export interface EnvCheckResult {
  success: boolean
  message: string
  output?: string
  error?: string
}

export const envApi = {
  check: () => api.get<EnvCheckResult>('/env/check'),
}

export default api
