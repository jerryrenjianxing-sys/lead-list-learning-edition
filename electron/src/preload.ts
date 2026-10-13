import { contextBridge, ipcRenderer } from 'electron';
import type { DesktopBridge, UpdateState } from './types';
// No Electron event or caller-selected channel crosses into the page.
const bridge: DesktopBridge = {
  getUpdateStatus: () => ipcRenderer.invoke('mdr:update:status'),
  checkForUpdates: () => ipcRenderer.invoke('mdr:update:check'),
  downloadUpdate: () => ipcRenderer.invoke('mdr:update:download'),
  installUpdate: () => ipcRenderer.invoke('mdr:update:install'),
  onUpdateState(handler) {
    if (typeof handler !== 'function') throw new TypeError('Expected a state handler');
    const receive = (_event: unknown, state: UpdateState) => handler(state);
    ipcRenderer.on('mdr:update:state', receive);
    return () => ipcRenderer.removeListener('mdr:update:state', receive);
  }
};
// Sandboxed subframes receive no bridge.
if (process.isMainFrame) contextBridge.exposeInMainWorld('mdrDesktop', Object.freeze(bridge));
