import type { CrawlerConfig, CrawlerType } from '@/types/crawler'

type CrawlerConfigInput = {
  platform: string
  crawlerType: CrawlerType
  values: string
  enableComments: boolean
  enableSubComments: boolean
  leadMode: boolean
}

export function normalizeKeywords(value: string): string {
  return value
    .split(/[,，]/)
    .map((keyword) => keyword.trim())
    .filter(Boolean)
    .join(',')
}

export function parseTagInput(value: string): string[] {
  return [...new Set(normalizeKeywords(value).split(',').filter(Boolean))]
}

export function validateCrawlerTags(tags: string[]): boolean {
  return tags.some((tag) => tag.trim())
}

export function getCrawlerPlatforms(
  crawlerType: CrawlerType,
  selectedPlatforms: string[],
): string[] {
  return crawlerType === 'search'
    ? selectedPlatforms
    : selectedPlatforms.slice(0, 1)
}

export function buildCrawlerConfig({
  platform,
  crawlerType,
  values,
  enableComments,
  enableSubComments,
  leadMode,
}: CrawlerConfigInput): CrawlerConfig {
  const normalized = normalizeKeywords(values)
  return {
    platform,
    crawler_type: crawlerType,
    keywords: crawlerType === 'search' ? normalized : '',
    specified_ids: crawlerType === 'detail' ? normalized : '',
    creator_ids: crawlerType === 'creator' ? normalized : '',
    start_page: 1,
    save_option: 'json',
    login_type: 'qrcode',
    headless: false,
    cookies: '',
    enable_comments: enableComments,
    enable_sub_comments: enableSubComments,
    lead_mode: leadMode,
    max_notes_count: 0,
    max_comments_count: 0,
    lead_max_accounts: 0,
  }
}
