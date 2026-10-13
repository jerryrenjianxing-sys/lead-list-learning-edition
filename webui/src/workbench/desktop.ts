export type UpdateState = {
  state: string;
  version?: string;
  message?: string;
  size?: number;
  progress?: number;
  notes?: string;
};

export type DesktopUpdates = {
  getUpdateStatus: () => Promise<UpdateState | null>;
  checkForUpdates: () => Promise<unknown>;
  downloadUpdate: () => Promise<unknown>;
  installUpdate: () => Promise<unknown>;
  onUpdateState: (handler: (state: UpdateState) => void) => () => void;
};

type WebViewBridge = {
  postMessage: (value: unknown) => void;
  addEventListener: (name: string, handler: (event: MessageEvent) => void) => void;
  removeEventListener: (name: string, handler: (event: MessageEvent) => void) => void;
};

export function desktopUpdates(): DesktopUpdates | undefined {
  const host = window as unknown as {
    mdrDesktop?: DesktopUpdates;
    chrome?: { webview?: WebViewBridge };
  };
  if (host.mdrDesktop) return host.mdrDesktop;
  const bridge = host.chrome?.webview;
  if (!bridge) return undefined;
  const send = async (action: string) => {
    bridge.postMessage({ type: "workbench-update", action });
  };
  return {
    getUpdateStatus: async () => { await send("status"); return null; },
    checkForUpdates: () => send("check"),
    downloadUpdate: () => send("download"),
    installUpdate: () => send("install"),
    onUpdateState: handler => {
      const receive = (event: MessageEvent) => {
        if (event.data?.type === "workbench-update" && event.data.value?.state) {
          handler(event.data.value);
        }
      };
      bridge.addEventListener("message", receive);
      return () => bridge.removeEventListener("message", receive);
    },
  };
}
