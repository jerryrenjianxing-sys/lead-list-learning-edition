import path from 'node:path';
import crypto from 'node:crypto';
import fs from 'node:fs';
import { Asset, Instance, PACK_ID, PRODUCT } from './types';

export function localOrigin(value: string): string | undefined {
  try {
    const url = new URL(value);
    if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) || url.username || url.password) return;
    return url.origin;
  } catch { return; }
}
export function trustedSource(source: string, base: string): boolean {
  const origin = localOrigin(base);
  return !!origin && localOrigin(source) === origin;
}
export function externalUrl(value: string): boolean {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password; } catch { return false; }
}
export function samePath(a: string, b: string): boolean {
  const normalize = (value: string) => path.resolve(value).replace(/[\\/]+$/, '').toLowerCase();
  return normalize(a) === normalize(b);
}
export function validateDiscovery(value: unknown): Instance {
  const i = value as Instance;
  if (!i || i.product !== PRODUCT || !localOrigin(i.base_url) || new URL(i.base_url).pathname !== '/' || !i.token || typeof i.token !== 'string' || typeof i.version !== 'string' || !Number.isInteger(i.pid) || i.pid <= 0 || typeof i.data_directory !== 'string' || i.api_version !== 1) throw new Error('无效的本机后台发现文件。');
  return i;
}
export function compatibleInstance(i: Instance, version: string, data: string): boolean { return i.version === version && samePath(i.data_directory, data); }
export function newerVersion(candidate: string, current: string): boolean {
  const parse = (v: string) => /^(\d+)\.(\d+)\.(\d+)$/.exec(v)?.slice(1).map(Number);
  const a = parse(candidate), b = parse(current);
  if (!a || !b) return false;
  for (let n = 0; n < 3; n++) if (a[n] !== b[n]) return a[n] > b[n];
  return false;
}
export function acceptableAsset(asset: Asset, current: string): boolean {
  return asset?.PackageId === PACK_ID && asset.Type === 'Full' && newerVersion(asset.Version, current) && Number.isSafeInteger(asset.Size) && asset.Size > 0 && /^[a-fA-F0-9]{64}$/.test(asset.SHA256) && /^[A-Za-z0-9._-]+-full\.nupkg$/.test(asset.FileName) && !asset.FileName.includes('..');
}
export async function verifyPackage(file: string, asset: Asset): Promise<void> {
  const stat = await fs.promises.stat(file);
  if (stat.size !== asset.Size) throw new Error('更新文件大小不匹配。');
  const sha = crypto.createHash('sha256');
  for await (const chunk of fs.createReadStream(file)) sha.update(chunk);
  if (sha.digest('hex').toLowerCase() !== asset.SHA256.toLowerCase()) throw new Error('更新文件校验失败。');
}
export function automaticCheckDue(value: string | undefined, now = Date.now()): boolean {
  const timestamp = value ? Date.parse(value.trim()) : NaN;
  return !Number.isFinite(timestamp) || now - timestamp >= 24 * 60 * 60 * 1000;
}
