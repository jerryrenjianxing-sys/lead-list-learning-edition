import assert from 'node:assert/strict'
import {
  buildCrawlerConfig,
  getCrawlerPlatforms,
  normalizeKeywords,
  parseTagInput,
  validateCrawlerTags,
} from '../src/lib/searchConfig.ts'
import { encodeFilePath } from '../src/lib/api.ts'

assert.equal(
  normalizeKeywords(' AI，智能体,  营销 ，,'),
  'AI,智能体,营销',
)
assert.equal(normalizeKeywords(' ， ,  '), '')
assert.deepEqual(parseTagInput(' 雨衣，雨具,雨衣 '), ['雨衣', '雨具'])
assert.equal(validateCrawlerTags([]), false)
assert.equal(validateCrawlerTags(['雨衣']), true)
assert.deepEqual(
  getCrawlerPlatforms('search', ['dy', 'xhs', 'ks']),
  ['dy', 'xhs', 'ks'],
)
assert.deepEqual(
  getCrawlerPlatforms('detail', ['dy', 'xhs', 'ks']),
  ['dy'],
)
assert.deepEqual(
  getCrawlerPlatforms('creator', ['xhs', 'ks']),
  ['xhs'],
)
assert.equal(
  encodeFilePath('xhs/json/search_#雨衣_50%.json'),
  'xhs/json/search_%23%E9%9B%A8%E8%A1%A3_50%25.json',
)

const shared = {
  platform: 'xhs',
  enableComments: true,
  enableSubComments: true,
  leadMode: true,
}

for (const [crawlerType, values, field] of [
  ['search', '雨衣，雨具', 'keywords'],
  ['detail', '作品链接1，作品ID2', 'specified_ids'],
  ['creator', '创作者链接1，创作者ID2', 'creator_ids'],
]) {
  const config = buildCrawlerConfig({
    ...shared,
    crawlerType,
    values,
  })
  assert.equal(config.crawler_type, crawlerType)
  assert.equal(config[field], values.replace('，', ','))
  assert.equal(config.max_notes_count, 0)
  assert.equal(config.max_comments_count, 0)
  assert.equal(config.lead_max_accounts, 0)
  assert.equal(config.enable_comments, true)
  assert.equal(config.enable_sub_comments, true)
  assert.equal(config.lead_mode, true)
  assert.equal(config.login_type, 'qrcode')
  assert.equal(config.headless, false)
  for (const otherField of ['keywords', 'specified_ids', 'creator_ids']) {
    if (otherField !== field) assert.equal(config[otherField], '')
  }
}

console.log('search config checks passed')
