export const PRODUCT = 'MediaWorkbench';
export const PACK_ID = 'MediaWorkbench.Desktop';
export const DISPLAY_NAME = 'Media Deep Researcher';
export const CHANNEL = 'win-preview';
export const REPOSITORY = 'https://github.com/jerryrenjianxing-sys/lead-list-learning-edition';
export interface UpdateState { state: string; message?: string; progress?: number; version?: string; size?: number; notes?: string }
export interface Instance { product: string; version: string; api_version: number; base_url: string; token: string; pid: number; data_directory: string }
export interface Paths { root: string; python: string; data: string; instance: string; version: string }
export interface Asset { PackageId: string; Version: string; Type: string; FileName: string; Size: number; SHA256: string; NotesMarkdown?: string }
export interface SkillFileCopy { kind: 'file'; name: string; size: number }
export interface DesktopBridge {
  copySkillFile(options: { name?: string }): Promise<SkillFileCopy>;
  getUpdateStatus(): Promise<UpdateState>;
  checkForUpdates(): Promise<UpdateState>;
  downloadUpdate(): Promise<UpdateState>;
  installUpdate(): Promise<UpdateState>;
  onUpdateState(handler: (state: UpdateState) => void): () => void;
}
