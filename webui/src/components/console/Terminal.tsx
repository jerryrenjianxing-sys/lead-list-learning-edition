import { useEffect, useRef } from 'react'
import { Database, RefreshCw, Trash2, X } from 'lucide-react'
import { TerminalLine } from './TerminalLine'
import { useCrawlerStore } from '@/store/crawlerStore'
import { DataExplorerDialog } from '@/components/data/DataExplorerDialog'

type TerminalProps = {
  onClose?: () => void
}

export function Terminal({ onClose }: TerminalProps) {
  const logs = useCrawlerStore((state) => state.logs)
  const clearLogs = useCrawlerStore((state) => state.clearLogs)
  const restoreLogs = useCrawlerStore((state) => state.restoreLogs)
  const clearedAfterLogId = useCrawlerStore(
    (state) => state.clearedAfterLogId,
  )
  const status = useCrawlerStore((state) => state.status)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [logs])

  return (
    <div className="terminal-panel">
      <header className="terminal-header">
        <div className="terminal-title">
          <span className="terminal-lights" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          <span>运行终端</span>
          <small>{logs.length} 条日志</small>
        </div>
        <div className="terminal-actions">
          <DataExplorerDialog variant="terminal" label="数据管理">
            <Database size={15} />
          </DataExplorerDialog>
          {clearedAfterLogId !== null && (
            <button type="button" onClick={restoreLogs} title="恢复日志">
              <RefreshCw size={16} />
              <span className="sr-only">恢复日志</span>
            </button>
          )}
          <button
            type="button"
            onClick={clearLogs}
            disabled={logs.length === 0}
            title="清除日志"
          >
            <Trash2 size={16} />
            <span className="sr-only">清除日志</span>
          </button>
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              data-autofocus
              title="关闭终端"
            >
              <X size={18} />
              <span className="sr-only">关闭终端</span>
            </button>
          )}
        </div>
      </header>

      <div ref={scrollRef} className="terminal-log terminal-scroll">
        {logs.length === 0 ? (
          <div className="terminal-empty">
            <span>潜客名单 / RUN CONSOLE</span>
            <p>等待任务启动。运行日志会在这里持续更新。</p>
          </div>
        ) : (
          logs.map((log) => <TerminalLine key={log.id} log={log} />)
        )}
        {status === 'running' && (
          <div className="terminal-cursor">
            <span>crawler@local:~$</span>
            <i />
          </div>
        )}
      </div>

      <footer className="terminal-footer">
        <span className={`terminal-status-dot status-${status}`} />
        {status === 'running'
          ? '正在运行'
          : status === 'stopping'
            ? '正在停止'
            : status === 'error'
              ? '运行异常'
              : '等待任务'}
      </footer>
    </div>
  )
}
