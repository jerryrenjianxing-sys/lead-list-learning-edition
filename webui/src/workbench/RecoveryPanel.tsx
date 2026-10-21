import { useEffect, useState } from "react";
import { api } from "./api";

type Attempt = { attempt: number; state: string; origin: string; started?: string; finished?: string };
type Fact = { seq: number; created: string; source?: string; capability?: string; kind?: string; message: string };
type Scope = { cursor: number; capability: string; scope: string; outcome: string; reason: string; counts: { inserted: number; updated: number; unchanged: number } };
const outcomes: Record<string, string> = {
  normal_end: "已到平台终页", quantity_reached: "达到设置数量", no_results: "正常无结果",
  abnormal_stop: "异常停止，范围未完成", not_executed: "未执行", user_stopped: "用户停止", running: "执行中",
};
export default function RecoveryPanel({ jobId,revision }: { jobId: string; revision: string }) {
  const [attempts, setAttempts] = useState<Attempt[]>([]);
  const [attempt, setAttempt] = useState(0);
  const [events, setEvents] = useState<Fact[]>([]);
  const [scopes, setScopes] = useState<Scope[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [scopeNext, setScopeNext] = useState<number | null>(null);
  const [error, setError] = useState("");
  async function load(page = 0, append = false) {
    const result = await api(`/jobs/${jobId}/events?attempt=${attempt}&after=${page}&limit=100`);
    setEvents(old => append ? [...old, ...result.items] : result.items);
    setNext(result.next_after);
  }
  async function loadScopes(page = 0, append = false) {
    const result = await api(`/jobs/${jobId}/scopes?attempt=${attempt}&after=${page}&limit=50`);
    setScopes(old => append ? [...old, ...result.items] : result.items);
    setScopeNext(result.next_after);
  }
  useEffect(() => {
    let active = true;
    setError("");
    api(`/jobs/${jobId}/attempts`).then(result => {
      if (!active) return;
      setAttempts(result.items);
      setAttempt(previous => previous || result.items.at(-1)?.attempt || 0);
    }).catch(e => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, [jobId,revision]);
  useEffect(() => {
    if (!attempt) return;
    let active = true;
    Promise.all([api(`/jobs/${jobId}/events?attempt=${attempt}&limit=100`), api(`/jobs/${jobId}/scopes?attempt=${attempt}&limit=50`)]).then(([timeline, ranges]) => {
      if (!active) return;
      setEvents(timeline.items); setNext(timeline.next_after);
      setScopes(ranges.items); setScopeNext(ranges.next_after);
    }).catch(e => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, [jobId, attempt,revision]);
  return <div className="wb-recovery">
    <h3>轮次与范围</h3>
    {error && <p role="alert">读取历史失败：{error}</p>}
    <label>运行轮次 <select value={attempt} onChange={e => setAttempt(Number(e.target.value))}>
      {!attempts.length && <option value={0}>尚未运行</option>}
      {attempts.map(item => <option key={item.attempt} value={item.attempt}>第 {item.attempt} 轮{item.origin === "legacy_snapshot" ? "（旧版快照，历史不完整）" : ""}</option>)}
    </select></label>
    <a className="wb-button" href={`/api/v1/jobs/${jobId}/diagnostics`} download>导出脱敏诊断</a>
    {scopes.map(scope => <p key={scope.cursor}>
      <strong>{scope.capability}</strong> · {scope.scope}：{outcomes[scope.outcome] || scope.outcome}
      <small>原因 {scope.reason} · 新增 {scope.counts.inserted} / 更新 {scope.counts.updated} / 重复 {scope.counts.unchanged}</small>
    </p>)}
    {scopeNext && <button onClick={() => void loadScopes(scopeNext, true).catch(e => setError(String(e)))}>更多范围</button>}
    <h3>本轮时间线</h3>
    <button onClick={() => void Promise.all([load(),loadScopes()]).catch(e => setError(String(e)))}>刷新本轮</button>
    <pre className="wb-log">{events.map(event => `${new Date(event.created).toLocaleString("zh-CN")} [${event.source || "旧记录"}${event.capability ? "/"+event.capability : ""}] ${event.message}`).join("\n") || "本轮暂无事件"}</pre>
    {next && <button onClick={() => void load(next, true).catch(e => setError(String(e)))}>更晚的事件</button>}
  </div>;
}
