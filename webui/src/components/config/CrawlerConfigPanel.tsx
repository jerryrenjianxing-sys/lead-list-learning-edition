import type { ComponentType, ReactNode, KeyboardEvent } from 'react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Database, Globe, MessageSquare, Play, Square, X } from 'lucide-react'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import { Checkbox } from '@/components/ui/checkbox'
import { Button } from '@/components/ui/button'
import { useCrawlerStore } from '@/store/crawlerStore'
import { usePlatforms, useConfigOptions, useCrawlerQueue } from '@/hooks/useCrawler'
import { LEAD_MODE_PLATFORMS } from '@/types/crawler'
import { ParsedIdList } from './ParsedIdList'

type SectionProps = {
  title: string
  description: string
  icon: ComponentType<{ className?: string }>
  children: ReactNode
  className?: string
}

function Section({ title, description, icon: Icon, children, className = '' }: SectionProps) {
  return (
    <section className={`rounded-lg glass-panel float-panel overflow-hidden ${className}`}>
      <header className="px-4 py-3 border-b border-cyber-border-subtle/50 flex items-center gap-3 bg-cyber-bg-tertiary/30">
        <div className="h-8 w-8 rounded-md bg-cyber-bg-tertiary border border-cyber-border-subtle flex items-center justify-center flex-shrink-0">
          <Icon className="h-4 w-4 text-cyber-neon-cyan" />
        </div>
        <div className="min-w-0">
          <div className="text-xs font-mono font-semibold text-cyber-text-primary tracking-wide">
            {title}
          </div>
          <div className="text-[10px] text-cyber-text-muted leading-snug truncate">
            {description}
          </div>
        </div>
      </header>
      <div className="p-4 space-y-4">
        {children}
      </div>
    </section>
  )
}

type FieldProps = {
  label: string
  hint?: string
  children: ReactNode
}

function Field({ label, hint, children }: FieldProps) {
  return (
    <div className="space-y-2">
      <div className="space-y-0.5">
        <Label className="text-xs text-cyber-text-secondary font-mono">
          {label}
        </Label>
        {hint ? (
          <p className="text-[10px] text-cyber-text-muted leading-snug">
            {hint}
          </p>
        ) : null}
      </div>
      {children}
    </div>
  )
}

type KeywordInputProps = {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  disabled?: boolean
}

function KeywordInput({ value, onChange, placeholder, disabled }: KeywordInputProps) {
  const [inputValue, setInputValue] = useState('')

  // 将逗号分隔的字符串转换为数组
  const keywords = value ? value.split(',').map((k) => k.trim()).filter(Boolean) : []

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      const trimmed = inputValue.trim()
      if (trimmed && !keywords.includes(trimmed)) {
        const newKeywords = [...keywords, trimmed]
        onChange(newKeywords.join(','))
        setInputValue('')
      }
    }
  }

  const removeKeyword = (keywordToRemove: string) => {
    const newKeywords = keywords.filter((k) => k !== keywordToRemove)
    onChange(newKeywords.join(','))
  }

  return (
    <div className="space-y-2">
      <Input
        value={inputValue}
        onChange={(e) => setInputValue(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        disabled={disabled}
        className="h-9 text-xs"
      />
      {keywords.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {keywords.map((keyword) => (
            <span
              key={keyword}
              className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-cyber-neon-cyan/10 border border-cyber-neon-cyan/30 text-cyber-neon-cyan text-xs font-mono"
            >
              {keyword}
              {!disabled && (
                <button
                  type="button"
                  onClick={() => removeKeyword(keyword)}
                  className="hover:text-cyber-neon-pink transition-colors"
                >
                  <X className="w-3 h-3" />
                </button>
              )}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

export function CrawlerConfigPanel() {
  const { t } = useTranslation('config')
  const config = useCrawlerStore((state) => state.config)
  const updateConfig = useCrawlerStore((state) => state.updateConfig)
  const status = useCrawlerStore((state) => state.status)

  const { data: platforms } = usePlatforms()
  const { data: options } = useConfigOptions()
  const { startQueue, stopQueue, isQueueRunning } = useCrawlerQueue()
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>([
    config.platform,
  ])

  const isDisabled =
    isQueueRunning || status === 'running' || status === 'stopping'
  const isRunning = isQueueRunning || status === 'running'
  const isBusy = status === 'stopping'
  const leadModeAvailable = (
    config.crawler_type === 'search' ? selectedPlatforms : [config.platform]
  ).some((platform) => LEAD_MODE_PLATFORMS.includes(platform))
  const leadModeEnabled = config.lead_mode && leadModeAvailable

  const handleStart = () => {
    const platformsToRun =
      config.crawler_type === 'search' ? selectedPlatforms : [config.platform]
    void startQueue(platformsToRun, {
      ...config,
      login_type: 'qrcode',
      headless: false,
    })
  }

  const handleStop = () => {
    void stopQueue()
  }

  const togglePlatform = (platform: string, checked: boolean) => {
    const next = checked
      ? [...selectedPlatforms.filter((value) => value !== platform), platform]
      : selectedPlatforms.filter((value) => value !== platform)
    if (next.length === 0) return
    setSelectedPlatforms(next)
    updateConfig({
      platform: next[0],
      ...(next.length > 1 ? { login_type: 'qrcode' } : {}),
    })
  }

  return (
    <div className="space-y-4 animate-slide-up">
      {/* Row 1: Two Config Columns */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Column 1: Target & Mode Section */}
        <Section
          title={t('section.targetMatrix.title')}
          description={t('section.targetMatrix.description')}
          icon={Globe}
        >
          <Field label={t('field.platform')}>
            {config.crawler_type === 'search' ? (
              <div className="grid grid-cols-2 gap-2">
                {platforms?.map((platform) => {
                  const checked = selectedPlatforms.includes(platform.value)
                  return (
                    <div
                      key={platform.value}
                      className="flex items-center gap-2 rounded-md border border-cyber-border-subtle bg-cyber-bg-tertiary/30 p-2 text-xs font-mono"
                    >
                      <Checkbox
                        id={`platform-${platform.value}`}
                        checked={checked}
                        onCheckedChange={(value) =>
                          togglePlatform(platform.value, value === true)
                        }
                        disabled={isDisabled || (checked && selectedPlatforms.length === 1)}
                      />
                      <Label
                        htmlFor={`platform-${platform.value}`}
                        className="cursor-pointer text-xs font-mono"
                      >
                        {platform.label}
                      </Label>
                    </div>
                  )
                })}
              </div>
            ) : (
              <Select
                value={config.platform}
                onValueChange={(value) => {
                  updateConfig({ platform: value })
                  setSelectedPlatforms([value])
                }}
                disabled={isDisabled}
              >
                <SelectTrigger className="h-9 text-xs">
                  <SelectValue placeholder={t('field.platformPlaceholder')} />
                </SelectTrigger>
                <SelectContent>
                  {platforms?.map((platform) => (
                    <SelectItem key={platform.value} value={platform.value}>
                      {platform.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label={t('field.crawlType')}>
              <Select
                value={config.crawler_type}
                onValueChange={(value) => updateConfig({ crawler_type: value })}
                disabled={isDisabled}
              >
                <SelectTrigger className="h-9 text-xs">
                  <SelectValue placeholder={t('field.crawlTypePlaceholder')} />
                </SelectTrigger>
                <SelectContent>
                  {options?.crawler_types.map((type) => (
                    <SelectItem key={type.value} value={type.value}>
                      {type.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>

            <Field label={t('field.startPage')}>
              <Input
                type="number"
                min={1}
                value={config.start_page}
                onChange={(e) => updateConfig({ start_page: parseInt(e.target.value) || 1 })}
                disabled={isDisabled}
                className="h-9 text-xs"
              />
            </Field>
          </div>

          {/* 根据爬虫类型显示不同的输入框 */}
          {config.crawler_type === 'search' && (
            <Field label={t('field.keywords')} hint={t('field.keywordsHint')}>
              <KeywordInput
                placeholder={t('field.keywordsPlaceholder')}
                value={config.keywords}
                onChange={(keywords) => updateConfig({ keywords })}
                disabled={isDisabled}
              />
            </Field>
          )}

          {config.crawler_type === 'detail' && (
            <Field label={t('field.specifiedIds')} hint={t('field.specifiedIdsHint')}>
              <textarea
                value={config.specified_ids}
                onChange={(e) => updateConfig({ specified_ids: e.target.value })}
                disabled={isDisabled}
                placeholder={t(`field.specifiedIdsPlaceholder.${config.platform}`, t('field.specifiedIdsPlaceholder.default'))}
                className="min-h-[60px] w-full rounded-md border border-cyber-border-DEFAULT bg-cyber-bg-tertiary px-3 py-2 text-xs font-mono text-cyber-text-primary placeholder:text-cyber-text-muted focus-visible:outline-none focus-visible:border-cyber-neon-cyan/50 focus-visible:shadow-cyber-soft disabled:cursor-not-allowed disabled:opacity-50 transition-all resize-none"
              />
              <ParsedIdList
                value={config.specified_ids}
                platform={config.platform}
                type="detail"
                disabled={isDisabled}
              />
              {config.platform === 'xhs' && (
                <div className="mt-2 rounded-lg border border-cyber-neon-orange/30 bg-cyber-neon-orange/5 p-2 text-[10px] leading-snug text-cyber-neon-orange font-mono">
                  {t('warning.xhsToken')}
                </div>
              )}
            </Field>
          )}

          {config.crawler_type === 'creator' && (
            <Field label={t('field.creatorIds')} hint={t('field.creatorIdsHint')}>
              <textarea
                value={config.creator_ids}
                onChange={(e) => updateConfig({ creator_ids: e.target.value })}
                disabled={isDisabled}
                placeholder={t(`field.creatorIdsPlaceholder.${config.platform}`, t('field.creatorIdsPlaceholder.default'))}
                className="min-h-[60px] w-full rounded-md border border-cyber-border-DEFAULT bg-cyber-bg-tertiary px-3 py-2 text-xs font-mono text-cyber-text-primary placeholder:text-cyber-text-muted focus-visible:outline-none focus-visible:border-cyber-neon-cyan/50 focus-visible:shadow-cyber-soft disabled:cursor-not-allowed disabled:opacity-50 transition-all resize-none"
              />
              <ParsedIdList
                value={config.creator_ids}
                platform={config.platform}
                type="creator"
                disabled={isDisabled}
              />
              {config.platform === 'xhs' && (
                <div className="mt-2 rounded-lg border border-cyber-neon-orange/30 bg-cyber-neon-orange/5 p-2 text-[10px] leading-snug text-cyber-neon-orange font-mono">
                  {t('warning.xhsToken')}
                </div>
              )}
            </Field>
          )}
        </Section>

        {/* Column 2: Output & Runtime Section */}
        <Section
          title={t('section.outputConfig.title')}
          description={t('section.outputConfig.description')}
          icon={Database}
        >
          <Field label={t('field.saveFormat')}>
            <Select
              value={config.save_option}
              onValueChange={(value) => updateConfig({ save_option: value })}
              disabled={isDisabled}
            >
              <SelectTrigger className="h-9 text-xs">
                <SelectValue placeholder={t('field.saveFormatPlaceholder')} />
              </SelectTrigger>
              <SelectContent>
                {options?.save_options.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <div className="space-y-2">
            <div className="flex items-center gap-3 rounded-lg border border-cyber-border-subtle bg-cyber-bg-tertiary/30 p-2.5 hover:border-cyber-border-DEFAULT transition-colors">
              <Checkbox
                checked={config.enable_comments}
                onCheckedChange={(checked) => {
                  const isChecked = checked === true
                  updateConfig({
                    enable_comments: isChecked,
                    enable_sub_comments: isChecked ? config.enable_sub_comments : false,
                  })
                }}
                disabled={isDisabled}
              />
              <div className="flex items-center gap-2">
                <MessageSquare className="h-3.5 w-3.5 text-cyber-text-secondary" />
                <p className="text-xs font-mono text-cyber-text-primary">{t('field.commentExtraction')}</p>
              </div>
            </div>

            <div className="flex items-center gap-3 rounded-lg border border-cyber-border-subtle bg-cyber-bg-tertiary/30 p-2.5 hover:border-cyber-border-DEFAULT transition-colors">
              <Checkbox
                checked={config.enable_sub_comments}
                onCheckedChange={(checked) => updateConfig({ enable_sub_comments: checked === true })}
                disabled={isDisabled || !config.enable_comments}
              />
              <p className="text-xs font-mono text-cyber-text-primary">{t('field.subComments')}</p>
            </div>

            <div className="flex items-center gap-3 rounded-lg border border-cyber-border-subtle bg-cyber-bg-tertiary/30 p-2.5 hover:border-cyber-border-DEFAULT transition-colors">
              <Checkbox
                checked={leadModeEnabled}
                onCheckedChange={(checked) => updateConfig({ lead_mode: checked === true })}
                disabled={isDisabled || !leadModeAvailable}
              />
              <div className="min-w-0 flex-1">
                <p className="text-xs font-mono text-cyber-text-primary">
                  {t('field.commenterAccounts')}
                </p>
                <p className={`text-[10px] leading-snug ${
                  leadModeAvailable ? 'text-cyber-text-muted' : 'text-cyber-neon-orange'
                }`}>
                  {leadModeAvailable
                    ? t('field.commenterAccountsHint')
                    : t('field.commenterAccountsUnavailableHint')}
                </p>
              </div>
            </div>

          </div>
        </Section>
      </div>

      {/* Row 2: Start/Stop Button - Full Width */}
      <div className="w-full">
        {isRunning ? (
          <Button
            onClick={handleStop}
            disabled={isBusy}
            className="w-full h-12 bg-cyber-neon-pink text-white font-mono font-bold text-sm tracking-wider hover:bg-cyber-neon-pink/90 hover:shadow-glow-pink-sm transition-all"
          >
            <Square className="w-4 h-4" />
            {isBusy ? t('button.stopping') : t('button.terminate')}
          </Button>
        ) : (
          <Button
            onClick={handleStart}
            disabled={isBusy}
            className="w-full h-12 bg-cyber-neon-cyan text-cyber-bg-primary font-mono font-bold text-sm tracking-wider hover:bg-cyber-neon-cyan/90 hover:shadow-glow-cyan-sm transition-all"
          >
            <Play className="w-4 h-4" />
            {t('button.initiateScan')}
          </Button>
        )}
      </div>
    </div>
  )
}
