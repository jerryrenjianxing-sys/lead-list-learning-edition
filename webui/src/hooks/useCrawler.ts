import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { crawlerApi, configApi } from '@/lib/api'
import { useCrawlerStore } from '@/store/crawlerStore'
import { LEAD_MODE_PLATFORMS, type CrawlerConfig } from '@/types/crawler'

export function useCrawlerStatus() {
  const setStatus = useCrawlerStore((state) => state.setStatus)
  const setRunningInfo = useCrawlerStore((state) => state.setRunningInfo)

  return useQuery({
    queryKey: ['crawlerStatus'],
    queryFn: async () => {
      const { data } = await crawlerApi.getStatus()
      setStatus(data.status)
      setRunningInfo(data.platform, data.crawler_type, data.started_at)
      return data
    },
    refetchInterval: 2000,
  })
}

const wait = (milliseconds: number) =>
  new Promise((resolve) => window.setTimeout(resolve, milliseconds))

export function useCrawlerQueue() {
  const queryClient = useQueryClient()
  const setStatus = useCrawlerStore((state) => state.setStatus)
  const clearLogs = useCrawlerStore((state) => state.clearLogs)
  const [isQueueRunning, setIsQueueRunning] = useState(false)
  const activeRun = useRef<{ cancelled: boolean } | null>(null)
  const currentTaskId = useRef<string | null>(null)

  const waitForTask = async (run: { cancelled: boolean }, taskId: string) => {
    while (!run.cancelled && activeRun.current === run) {
      await wait(1000)
      if (run.cancelled || activeRun.current !== run) return
      const { data } = await crawlerApi.getStatus(taskId)
      if (run.cancelled || activeRun.current !== run) return
      setStatus(data.status)
      if (data.status === 'error') {
        if (data.recovery_task_id === taskId) continue
        throw new Error(data.error_message || 'Crawler task failed')
      }
      if (data.recovery_task_id === taskId) continue
      if (data.status === 'idle') return
    }
  }

  const startQueue = async (platforms: string[], config: CrawlerConfig) => {
    if (activeRun.current || platforms.length === 0) return
    const run = { cancelled: false }
    activeRun.current = run
    setIsQueueRunning(true)
    clearLogs()

    const crawlJobs = platforms.map((platform) => ({
      ...config,
      platform,
      login_type: platforms.length > 1 ? 'qrcode' : config.login_type,
      lead_mode:
        config.lead_mode && LEAD_MODE_PLATFORMS.includes(platform),
    }))
    const loginJobs = platforms.length > 1
      ? platforms.map((platform) => ({
          ...config,
          platform,
          login_type: 'qrcode',
          crawler_type: 'login',
          enable_comments: false,
          enable_sub_comments: false,
          lead_mode: false,
        }))
      : []

    let activePlatform = ''
    try {
      for (const job of [...loginJobs, ...crawlJobs]) {
        if (run.cancelled || activeRun.current !== run) break
        activePlatform = job.platform
        setStatus('running')
        const taskId = window.crypto.randomUUID()
        currentTaskId.current = taskId
        await crawlerApi.start({ ...job, task_id: taskId })
        if (run.cancelled || activeRun.current !== run) {
          await crawlerApi.stop(taskId).catch(() => undefined)
          break
        }
        await waitForTask(run, taskId)
      }
      if (!run.cancelled && activeRun.current === run) {
        toast.success('All platform crawls completed')
      }
    } catch (error) {
      if (!run.cancelled && activeRun.current === run) {
        setStatus('error')
        const message = error instanceof Error ? error.message : 'Crawler task failed'
        toast.error(`${activePlatform}: ${message}`)
      }
    } finally {
      if (activeRun.current === run) {
        activeRun.current = null
        currentTaskId.current = null
        setIsQueueRunning(false)
        queryClient.invalidateQueries({ queryKey: ['crawlerStatus'] })
      }
    }
  }

  const stopQueue = async () => {
    const run = activeRun.current
    if (run) run.cancelled = true
    setStatus('stopping')
    try {
      const taskId =
        currentTaskId.current ?? (await crawlerApi.getStatus()).data.task_id
      if (taskId) await crawlerApi.stop(taskId)
      toast.success('Crawler stopped')
      setStatus('idle')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to stop crawler')
    } finally {
      if (!run && activeRun.current === null) {
        currentTaskId.current = null
        setIsQueueRunning(false)
        queryClient.invalidateQueries({ queryKey: ['crawlerStatus'] })
      }
    }
  }

  return { startQueue, stopQueue, isQueueRunning }
}

export function useCrawlerLogs() {
  const setLogs = useCrawlerStore((state) => state.setLogs)

  return useQuery({
    queryKey: ['crawlerLogs'],
    queryFn: async () => {
      const { data } = await crawlerApi.getLogs(500)
      setLogs(data.logs)
      return data.logs
    },
    refetchInterval: false, // Use WebSocket instead
  })
}

export function usePlatforms() {
  return useQuery({
    queryKey: ['platforms'],
    queryFn: async () => {
      const { data } = await configApi.getPlatforms()
      return data.platforms
    },
    staleTime: Infinity,
  })
}

export function useConfigOptions() {
  return useQuery({
    queryKey: ['configOptions'],
    queryFn: async () => {
      const { data } = await configApi.getOptions()
      return data
    },
    staleTime: Infinity,
  })
}
