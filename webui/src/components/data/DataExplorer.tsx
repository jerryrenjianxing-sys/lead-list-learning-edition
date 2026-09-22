import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Download, FolderOpen, RefreshCw, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { dataApi } from '@/lib/api'
import { useCrawlerStore } from '@/store/crawlerStore'
import { FileCard } from './FileCard'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'

// 从文件名提取类别
function extractCategory(filename: string): string {
  // 文件名格式: search_comments_xxx, search_creators_xxx, search_videos_xxx 等
  const match = filename.match(/^(search_\w+?)_/)
  if (match) {
    return match[1]
  }
  // 其他格式尝试提取前缀
  const parts = filename.split('_')
  if (parts.length >= 2) {
    return `${parts[0]}_${parts[1]}`
  }
  return 'other'
}

// 类别显示名称
function getCategoryLabel(category: string): string {
  const labels: Record<string, string> = {
    'search_comments': 'Comments',
    'search_creators': 'Creators',
    'search_videos': 'Videos',
    'search_contents': 'Contents',
    'search_notes': 'Notes',
    'other': 'Other',
  }
  return labels[category] || category.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

export function DataExplorer() {
  const { t } = useTranslation('data')
  const [activeTab, setActiveTab] = useState<string>('all')
  const [selectedPaths, setSelectedPaths] = useState<string[]>([])
  const [isDeleting, setIsDeleting] = useState(false)
  const crawlerStatus = useCrawlerStore((state) => state.status)

  const { data, isLoading, refetch, isRefetching } = useQuery({
    queryKey: ['dataFiles'],
    queryFn: async () => {
      const { data } = await dataApi.getFiles()
      return data.files
    },
  })

  const files = data || []

  // 按类别分组文件
  const { categories, groupedFiles } = useMemo(() => {
    const grouped: Record<string, typeof files> = {}

    files.forEach(file => {
      const category = extractCategory(file.name)
      if (!grouped[category]) {
        grouped[category] = []
      }
      grouped[category].push(file)
    })

    // 按文件数量排序类别
    const sortedCategories = Object.keys(grouped).sort((a, b) =>
      grouped[b].length - grouped[a].length
    )

    return { categories: sortedCategories, groupedFiles: grouped }
  }, [files])

  // 当前显示的文件
  const displayFiles = activeTab === 'all' ? files : (groupedFiles[activeTab] || [])
  const displayPaths = displayFiles.map((file) => file.path)
  const allDisplayedSelected =
    displayPaths.length > 0 && displayPaths.every((path) => selectedPaths.includes(path))

  const downloadCurrentList = () => {
    const link = document.createElement('a')
    link.href = dataApi.getArchiveUrl(activeTab === 'all' ? undefined : activeTab)
    document.body.appendChild(link)
    link.click()
    link.remove()
  }

  const deleteSelected = async () => {
    if (
      !selectedPaths.length ||
      !window.confirm(`选中的 ${selectedPaths.length} 个文件将被永久删除且无法恢复。确定删除吗？`)
    ) return

    setIsDeleting(true)
    try {
      await dataApi.deleteFiles(selectedPaths)
      toast.success(`已删除 ${selectedPaths.length} 个文件`)
      setSelectedPaths([])
      await refetch()
    } catch {
      toast.error('删除失败。任务运行中不能删除文件，请稍后再试。')
    } finally {
      setIsDeleting(false)
    }
  }

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-mono font-bold text-cyber-neon-cyan glow-text-cyan tracking-wider">
            {t('explorer.title')}
          </h2>
          <Badge variant="default" className="font-mono">
            {t('explorer.records', { count: files.length })}
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          {files.length > 0 && (
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                setSelectedPaths((current) =>
                  allDisplayedSelected
                    ? current.filter((path) => !displayPaths.includes(path))
                    : [...new Set([...current, ...displayPaths])],
                )
              }
              className="font-mono"
            >
              {allDisplayedSelected ? '取消全选' : '全选当前列表'}
            </Button>
          )}
          {selectedPaths.length > 0 && (
            <Button
              variant="outline"
              size="sm"
              onClick={deleteSelected}
              disabled={isDeleting || crawlerStatus === 'running' || crawlerStatus === 'stopping'}
              className="font-mono text-red-500"
              title={crawlerStatus === 'running' || crawlerStatus === 'stopping' ? '任务运行中不能删除文件' : undefined}
            >
              <Trash2 className="w-4 h-4" />
              {isDeleting ? '删除中…' : `批量删除 (${selectedPaths.length})`}
            </Button>
          )}
          {files.length > 0 && (
            <Button
              variant="outline"
              size="sm"
              onClick={downloadCurrentList}
              className="font-mono"
            >
              <Download className="w-4 h-4" />
              {t('explorer.downloadCurrent')}
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={() => refetch()}
            disabled={isRefetching}
            className="font-mono"
          >
            <RefreshCw className={`w-4 h-4 ${isRefetching ? 'animate-spin' : ''}`} />
            {t('explorer.rescan')}
          </Button>
        </div>
      </div>

      <div className="mb-4 rounded-md border border-cyber-neon-cyan/30 bg-cyber-neon-cyan/5 px-3 py-2 text-sm text-cyber-text-secondary">
        请使用 1M 上下文模型进行分析（如 DeepSeek V4 Flash）。请将「打包下载当前列表」获得的数据包与
        <a
          href="/api/data/prompt"
          download="社交媒体B端采购潜客报告_轻量最终版.txt"
          className="mx-1 font-medium text-cyber-neon-cyan hover:underline"
        >
          下载 Prompt 整合包
        </a>
        完整发给大模型，并要求大模型严格按照提示词执行，必须全量分析所有数据。
      </div>

      {/* Category Tabs */}
      {files.length > 0 && categories.length > 1 && (
        <div className="flex items-center gap-2 mb-4 flex-wrap">
          <button
            onClick={() => setActiveTab('all')}
            className={`px-3 py-1.5 rounded-md text-xs font-mono transition-all ${
              activeTab === 'all'
                ? 'bg-cyber-neon-cyan text-black font-bold'
                : 'bg-cyber-bg-tertiary text-cyber-text-secondary hover:text-cyber-text-primary border border-cyber-border-subtle hover:border-cyber-neon-cyan/50'
            }`}
          >
            {t('explorer.allCategories')} ({files.length})
          </button>
          {categories.map(category => (
            <button
              key={category}
              onClick={() => setActiveTab(category)}
              className={`px-3 py-1.5 rounded-md text-xs font-mono transition-all ${
                activeTab === category
                  ? 'bg-cyber-neon-cyan text-black font-bold'
                  : 'bg-cyber-bg-tertiary text-cyber-text-secondary hover:text-cyber-text-primary border border-cyber-border-subtle hover:border-cyber-neon-cyan/50'
              }`}
            >
              {getCategoryLabel(category)} ({groupedFiles[category].length})
            </button>
          ))}
        </div>
      )}

      {/* Content */}
      {isLoading ? (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-cyber-text-muted font-mono animate-pulse">
            {t('explorer.loading')}
          </div>
        </div>
      ) : files.length === 0 ? (
        <div className="flex-1 flex flex-col items-center justify-center text-center">
          <div className="relative">
            <FolderOpen className="w-16 h-16 text-cyber-neon-cyan/30 mb-4" />
            <div className="absolute inset-0 blur-xl bg-cyber-neon-cyan/10" />
          </div>
          <h3 className="text-lg font-mono font-medium text-cyber-neon-cyan mb-2">
            {t('explorer.noData')}
          </h3>
          <p className="text-sm text-cyber-text-muted max-w-md font-mono">
            {t('explorer.noDataHint')}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {displayFiles.map((file) => (
            <FileCard
              key={file.path}
              file={file}
              selected={selectedPaths.includes(file.path)}
              onSelect={(selected) =>
                setSelectedPaths((current) =>
                  selected
                    ? [...new Set([...current, file.path])]
                    : current.filter((path) => path !== file.path),
                )
              }
            />
          ))}
        </div>
      )}
    </div>
  )
}
