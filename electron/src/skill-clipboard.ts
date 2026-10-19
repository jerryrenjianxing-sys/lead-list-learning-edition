import { clipboard, ClipboardItem } from 'electron';
import fs from 'node:fs/promises';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { SkillFileCopy } from './types';

export async function copySkillFile(data: string, options: unknown, load: () => Promise<string>): Promise<SkillFileCopy> {
  if (!options || typeof options !== 'object' || Array.isArray(options)
      || Object.keys(options).some(key => key !== 'name')) throw new Error('只接受 Skill 文件名。');
  const raw = (options as { name?: unknown }).name;
  const name = raw === undefined ? 'Media Deep Researcher.md' : raw;
  if (typeof name !== 'string' || name.length > 100 || !name.trim() || name !== name.trim()
      || /[<>:"/\\|?*\x00-\x1f]/.test(name) || !/\.md$/i.test(name) || !name.slice(0, -3).trim()
      || /^(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|$)/i.test(name)) {
    throw new Error('请使用有效的 .md 文件名，不含路径或 Windows 保留名称。');
  }
  const content = await load();
  if (!content.startsWith('# Media Deep Researcher') || !content.includes('```powershell')) {
    throw new Error('本机 Skill 文件尚未就绪，请重试。');
  }
  const parent = path.join(await fs.realpath(data), 'agent-share');
  await fs.mkdir(parent, { recursive: true });
  if ((await fs.lstat(parent)).isSymbolicLink()) throw new Error('Skill 分享目录不能是链接。');
  // Each copy remains valid after renaming or copying another entry, and after exit.
  const folder = path.join(parent, randomUUID());
  await fs.mkdir(folder, { mode: 0o700 });
  const file = path.join(folder, name);
  await fs.writeFile(file, content, { encoding: 'utf8', flag: 'wx', mode: 0o600 });
  await clipboard.write([new ClipboardItem({ 'text/uri-list': pathToFileURL(file).href })]);
  return { kind: 'file', name, size: Buffer.byteLength(content, 'utf8') };
}
