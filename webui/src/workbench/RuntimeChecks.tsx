import { useEffect, useState } from "react";
import { api } from "./api";

type Check = { id: string; state: string; message: string; elapsed_seconds: number; local_state: string; network_state: string; steps: { id: string; name: string; state: string; message: string; elapsed_seconds: number }[] };
type Update = { state: string; version?: string; message?: string; size?: number; progress?: number; notes?: string };
type Bridge = { postMessage: (value: unknown) => void; addEventListener: (name: string, handler: (event: MessageEvent) => void) => void; removeEventListener: (name: string, handler: (event: MessageEvent) => void) => void };
const desktop = () => (window as unknown as { chrome?: { webview?: Bridge } }).chrome?.webview;
const labels: Record<string, string> = { pending: "待检查", running: "检查中", passed: "通过", failed: "未通过", limited: "平台限制", unconfirmed: "未能确认", skipped: "未检查", interrupted: "已中断", incomplete: "未完成", cancelling: "正在取消", cancelled: "已取消", timed_out: "已超时", completed: "检查结束" };
const releaseUrl = "https://github.com/jerryrenjianxing-sys/lead-list-learning-edition/releases";

export default function RuntimeChecks() {
  const [check, setCheck] = useState<Check | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [update, setUpdate] = useState<Update>({ state: "idle" });
  const active = check && ["running", "cancelling"].includes(check.state);
  useEffect(() => {
    let alive = true;
    const load = () => api<Check | null>("/diagnostics/self-tests/latest").then(r => { if (alive) setCheck(r); }).catch(e => { if (alive) setError(String(e)); });
    void load();
    const timer = setInterval(load, 2000);
    const bridge = desktop();
    const receive = (event: MessageEvent) => { if (event.data?.type === "workbench-update") setUpdate(event.data.value); };
    bridge?.addEventListener("message", receive);
    bridge?.postMessage({ type: "workbench-update", action: "status" });
    return () => { alive = false; clearInterval(timer); bridge?.removeEventListener("message", receive); };
  }, []);
  async function start(include_network: boolean) {
    setBusy(true); setError("");
    try { setCheck(await api("/diagnostics/self-tests", "POST", { include_network })); }
    catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  async function cancel() {
    if (!check) return;
    setBusy(true); setError("");
    try { setCheck(await api(`/diagnostics/self-tests/${check.id}/cancel`, "POST", {})); }
    catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  function updateAction(action: string) { desktop()?.postMessage({ type: "workbench-update", action }); }
  return <>
    <section className="wb-card wb-self-test">
      <h2>运行自检</h2>
      <p>无需登录账号。用独立临时数据检查软件，不产生正式任务或研究报告。</p>
      <div className="wb-check-actions">
        <button disabled={busy || !!active} onClick={() => void start(true)}>一键自检</button>
        <button disabled={busy || !!active} onClick={() => void start(false)}>仅检查本机</button>
        {active && <button disabled={busy || check.state === "cancelling"} onClick={() => void cancel()}>取消检查</button>}
      </div>
      {error && <p role="alert" className="wb-check-error">{error}</p>}
      {check && <>
        <p role="status" aria-live="polite">{labels[check.state]} · 已用 {Math.floor(check.elapsed_seconds)} 秒 · {check.message}</p>
        <p>本机：{labels[check.local_state]} · 联网：{labels[check.network_state]}</p>
        <ol className="wb-check-list">
          {check.steps.map(step => <li key={step.id}>
            <div><strong>{step.name}</strong><span className={`wb-check-state check-${step.state}`}>{labels[step.state]}</span></div>
            <p>{step.message || "等待前序检查"}{step.elapsed_seconds > 0 && ` · ${step.elapsed_seconds} 秒`}</p>
          </li>)}
        </ol>
      </>}
      <small>本机通过只代表本次基础检查通过。联网仅试采微博少量公开内容，不代表七个平台完整采集或分析质量已通过。</small>
    </section>
    <section className="wb-card">
      <h2>软件更新</h2>
      <p>版本与安装包由 GitHub 发布。当前使用预览通道，安装更新前会备份数据。</p>
      {desktop() ? <>
        <p role="status" aria-live="polite">{update.message || "可检查是否有新版本。"}</p>
        {update.version && <p>版本 {update.version}{update.size ? ` · ${(update.size / 1024 / 1024).toFixed(1)} MB` : ""}</p>}
        {update.notes && <details><summary>更新说明</summary><pre className="wb-update-notes">{update.notes}</pre></details>}
        {update.state === "downloading" && <progress aria-label="更新下载进度" max={100} value={update.progress || 0} />}
        <div className="wb-check-actions">
          <button disabled={["checking", "downloading", "installing"].includes(update.state)} onClick={() => updateAction("check")}>检查更新</button>
          {update.state === "available" && <button onClick={() => updateAction("download")}>下载更新</button>}
          {["downloaded", "waiting_idle"].includes(update.state) && <button onClick={() => updateAction("install")}>安装并重启</button>}
        </div>
      </> : <p>请在桌面软件中检查和安装更新，或从发行页下载完整安装包。</p>}
      <a href={releaseUrl} target="_blank" rel="noreferrer">打开 GitHub 发行页</a>
    </section>
  </>;
}
