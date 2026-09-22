import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import PlatformLogin, { authLabel } from "./PlatformLogin";
import "./workbench.css";
type Item = Record<string, any>;
const platforms = [
  ["xhs", "小红书"],
  ["dy", "抖音"],
  ["ks", "快手"],
  ["bili", "哔哩哔哩"],
  ["wb", "微博"],
  ["tieba", "贴吧"],
  ["zhihu", "知乎"],
];
const tabs = [
  ["overview", "概览"],
  ["jobs", "采集任务"],
  ["data", "数据集"],
  ["analyses", "分析成果"],
  ["platforms", "平台登录"],
  ["settings", "设置"],
];
const labels: Record<string, string> = {
  queued: "排队中",
  running: "正在采集",
  logging_in: "正在登录",
  stopping: "正在停止",
  completed: "已完成",
  cancelled: "已停止",
  interrupted: "已中断",
  failed: "失败",
  partial: "部分完成",
  needs_login: "需要登录",
  waiting_agent: "等待 Agent 分析",
};
const name = (id: string) => platforms.find((p) => p[0] === id)?.[1] || id;
const date = (s: string) =>
  new Date(s).toLocaleString("zh-CN", { hour12: false });
function Status({ value }: { value: string }) {
  return (
    <span className={`wb-status state-${value}`}>{labels[value] || value}</span>
  );
}
export default function Workbench() {
  const [tab, T] = useState("overview"),
    [error, E] = useState(""),
    [busy, B] = useState(false),
    [notice, N] = useState("");
  const [jobs, J] = useState<Item[]>([]),
    [datasets, D] = useState<Item[]>([]),
    [analyses, A] = useState<Item[]>([]),
    [artifacts, F] = useState<Item[]>([]),
    [statuses, P] = useState<Item[]>([]);
  const [archivedCount, AC] = useState(0);
  const [selected, S] = useState<string[]>([]),
    [mode, M] = useState("search"),
    [input, I] = useState(""),
    [maxNotes, MN] = useState(0),
    [maxComments, MC] = useState(0),
    [options, O] = useState({
      enable_comments: true,
      enable_sub_comments: false,
      enrich_profiles: false,
    });
  const [jobId, JI] = useState(""),
    [events, EV] = useState<Item[]>([]),
    [qr, QR] = useState(false);
  const [datasetId, DI] = useState(""),
    [records, R] = useState<Item[]>([]),
    [next, NX] = useState<number | null>(null),
    [query, Q] = useState("");
  const [title, AT] = useState(""),
    [goal, G] = useState(""),
    [schema, SC] = useState(""),
    [analysisId, AI] = useState(""),
    [results, AR] = useState<Item[]>([]),
    [nextResult, NR] = useState<number | null>(null);
  const [settings, SET] = useState<Item>({}),
    [diagnostics, DX] = useState<Item>({}),
    [browserPath, BP] = useState(""),
    [legacyPath, LP] = useState("");
  const previousAuth = useRef<Set<string>>(new Set());
  const submitting = useRef(new Set<string>());
  const [pendingLogin, PL] = useState<Record<string, string>>({});
  const [clock, CLOCK] = useState(Date.now());
  function applyPlatforms(items: Item[]) {
    const authenticated = new Set<string>(
      items
        .filter((p) => p.can_select ?? p.login_state === "authenticated")
        .map((p) => p.id),
    );
    const newly = [...authenticated].filter(
      (id) => !previousAuth.current.has(id),
    );
    previousAuth.current = authenticated;
    S((old) => [
      ...new Set([...old.filter((id) => authenticated.has(id)), ...newly]),
    ]);
    P(items);
  }
  function goLogin(id: string) {
    T("platforms");
    requestAnimationFrame(() => {
      const card = document.getElementById(`login-${id}`);
      card?.scrollIntoView({ block: "center" });
      card?.focus({ preventScroll: true });
    });
  }
  async function startLogin(id: string, action = "login") {
    if (
      submitting.current.has(id) ||
      statuses.find((p) => p.id === id)?.login?.active
    )
      return;
    submitting.current.add(id);
    PL((old) => ({ ...old, [id]: new Date().toISOString() }));
    E("");
    N("");
    try {
      await api(
        action === "login" ? "/jobs" : `/platforms/${id}/session/${action}`,
        "POST",
        action === "login" ? { platform: id, crawler_type: "login" } : {},
        15000,
      );
      const p = await api("/platforms", "GET", undefined, 15000);
      applyPlatforms(p.items);
    } catch {
      // A timed-out response may already have created a task. Reconcile before retrying.
      try {
        const p = await api("/platforms", "GET", undefined, 15000);
        applyPlatforms(p.items);
        if (!p.items.find((p: Item) => p.id === id)?.login?.active)
          E(
            "暂未确认登录任务状态，请查看平台提示后重试；重复提交会复用已有流程。",
          );
      } catch {
        E(
          "与本机服务的连接暂时中断，无法确认登录是否启动；连接恢复后会自动显示进度。",
        );
      }
    } finally {
      submitting.current.delete(id);
      PL((old) => {
        const copy = { ...old };
        delete copy[id];
        return copy;
      });
    }
  }
  async function refresh() {
    const [j, d, a, f, p] = await Promise.all([
      api("/jobs"),
      api("/datasets"),
      api("/analyses"),
      api("/artifacts"),
      api("/platforms"),
    ]);
    J(j.items);
    AC(j.archived_count || 0);
    D(d.items);
    A(a.items);
    F(f.items);
    applyPlatforms(p.items);
  }
  useEffect(() => {
    void refresh().catch((e) => E(String(e)));
    const timer = setInterval(
      () => void refresh().catch((e) => E(String(e))),
      3000,
    );
    const clockTimer = setInterval(() => CLOCK(Date.now()), 1000);
    return () => {
      clearInterval(timer);
      clearInterval(clockTimer);
    };
  }, []);
  useEffect(() => {
    if (!jobId) return;
    let active = true;
    const load = () =>
      api(`/jobs/${jobId}/events?limit=2000`)
        .then((r) => {
          if (active) EV(r.items);
        })
        .catch((e) => E(String(e)));
    void load();
    const timer = setInterval(load, 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [jobId]);
  async function perform(action: () => Promise<unknown>, message = "") {
    B(true);
    E("");
    N("");
    try {
      await action();
      await refresh();
      if (message) N(message);
    } catch (e) {
      E(e instanceof Error ? e.message : String(e));
    } finally {
      B(false);
    }
  }
  async function clearHistory(ids?: string[]) {
    const r = await api("/jobs/clear-history", "POST", ids ? { ids } : {});
    if (!ids || ids.includes(jobId)) {
      JI("");
      EV([]);
      QR(false);
    }
    N(`已清理 ${r.count} 条任务记录，数据、成果和登录信息仍保留。`);
  }
  async function restoreHistory() {
    const r = await api("/jobs/restore-history", "POST", {});
    N(`已恢复 ${r.count} 条任务记录。`);
  }
  async function createJobs() {
    if (!selected.length) throw Error("请选择至少一个平台");
    for (const platform of selected)
      await api("/jobs", "POST", {
        platform,
        crawler_type: mode,
        keywords: mode === "search" ? input : "",
        specified_ids: mode === "detail" ? input : "",
        creator_ids: mode === "creator" ? input : "",
        max_notes_count: maxNotes,
        max_comments_count: maxComments,
        ...options,
        enrich_profiles:
          options.enrich_profiles && ["xhs", "dy", "ks"].includes(platform),
      });
    T("jobs");
  }
  async function showData(id: string, after = 0) {
    const r = await api(
      `/datasets/${id}/records?after=${after}&q=${encodeURIComponent(query)}`,
    );
    DI(id);
    R((old) => (after ? [...old, ...r.items] : r.items));
    NX(r.next_after);
    T("data");
  }
  async function showAnalysis(id: string, offset = 0) {
    const r = await api(`/analyses/${id}/results?offset=${offset}`);
    AI(id);
    AR((old) => (offset ? [...old, ...r.items] : r.items));
    NR(r.next_offset);
    T("analyses");
  }
  async function showSettings() {
    const [s, d] = await Promise.all([api("/settings"), api("/diagnostics")]);
    SET(s);
    BP(s.browser_path || "");
    DX(d);
    T("settings");
  }
  async function demo() {
    const r = await api("/demo", "POST", {});
    await showData(r.id);
  }
  async function upload(file: File, path = "/imports/file") {
    const form = new FormData();
    form.append("file", file);
    const r = await api(path, "POST", form);
    if (path === "/imports/file") await showData(r.id);
  }
  const current = analyses.find((a) => a.id === analysisId);
  const active = jobs.filter((j) =>
    ["running", "queued", "stopping"].includes(j.state),
  );
  return (
    <div className="wb-app">
      <aside className="wb-sidebar">
        <a href="#" className="wb-brand">
          <img src="/brand/media-deep-researcher.png" alt="" />
          <span className="wb-brand-name">
            <span>Media</span>
            <span>Deep Researcher</span>
          </span>
        </a>
        <p className="wb-caption">本地数据 · 自由分析</p>
        <nav>
          {tabs.map(([id, title]) => (
            <button
              key={id}
              className={tab === id ? "active" : ""}
              onClick={() =>
                id === "settings" ? void perform(showSettings) : T(id)
              }
            >
              {title}
            </button>
          ))}
        </nav>
        <a className="wb-skill-link" href="#">
          ← 返回 Skill 首页
        </a>
        <small>Media Deep Researcher · 0.1.1</small>
      </aside>
      <main className="wb-main">
        <header className="wb-top">
          <span>工作空间 / {tabs.find((t) => t[0] === tab)?.[1]}</span>
          <span className="wb-live">● 本机服务</span>
        </header>
        {error && (
          <div className="wb-alert" role="alert">
            {error}
            <button onClick={() => E("")} aria-label="关闭错误">
              ×
            </button>
          </div>
        )}
        {notice && (
          <div className="wb-notice" role="status">
            {notice}
          </div>
        )}
        {tab === "overview" && (
          <>
            <div className="wb-heading">
              <p className="wb-eyebrow">YOUR LOCAL WORKSPACE</p>
              <h1>让数据，连接你的想法。</h1>
              <p>
                采集由软件执行，分析由你熟悉的 Agent
                完成，进度和成果保存在这里。
              </p>
            </div>
            <div className="wb-metrics">
              {[
                [active.length, "进行中的任务"],
                [
                  datasets.reduce((n, d) => n + d.record_count, 0),
                  "已保存的数据",
                ],
                [analyses.length, "分析任务"],
                [artifacts.length, "成果文件"],
              ].map(([n, title]) => (
                <section className="wb-card" key={title}>
                  <strong>{n.toLocaleString()}</strong>
                  <span>{title}</span>
                </section>
              ))}
            </div>
            <div className="wb-columns">
              <section className="wb-card">
                <h2>从一份数据开始</h2>
                <p>
                  选择平台发起采集，或导入已有文件。你的 Agent
                  可以直接连接并操作这些任务。
                </p>
                <button className="wb-primary" onClick={() => T("jobs")}>
                  创建采集任务
                </button>
                <button onClick={() => T("data")}>导入数据</button>
              </section>
              <section className="wb-card">
                <h2>先体验完整流程</h2>
                <p>
                  使用城市通勤体验的合成数据，无需社媒登录，即可自定义分析目标。
                </p>
                <button
                  disabled={busy}
                  onClick={() =>
                    void perform(
                      demo,
                      "演示数据已导入，可以让 Agent 开始分析。",
                    )
                  }
                >
                  加载演示数据
                </button>
              </section>
            </div>
            <section className="wb-card">
              <h2>最近任务</h2>
              {jobs.slice(0, 5).map((j) => (
                <button
                  className="wb-row"
                  key={j.id}
                  onClick={() => {
                    JI(j.id);
                    T("jobs");
                  }}
                >
                  <span>
                    {name(j.platform)} ·{" "}
                    {j.options.keywords ||
                      (
                        {
                          login: "登录验证",
                          search: "关键词搜索",
                          detail: "指定内容",
                          creator: "创作者主页",
                        } as Record<string, string>
                      )[j.options.crawler_type] ||
                      j.options.crawler_type}
                  </span>
                  <Status
                    value={
                      j.state === "running" &&
                      j.options.crawler_type === "login"
                        ? "logging_in"
                        : j.state
                    }
                  />
                </button>
              ))}
              {!jobs.length && (
                <p className="wb-empty">
                  还没有采集任务，也可以从演示数据开始。
                </p>
              )}
            </section>
          </>
        )}
        {tab === "jobs" && (
          <>
            <div className="wb-heading">
              <h1>采集任务</h1>
              <p>任务按顺序在后台执行。窗口进入托盘后，采集会继续。</p>
            </div>
            <form
              className="wb-card"
              onSubmit={(e) => {
                e.preventDefault();
                void perform(() => createJobs(), "任务已加入后台队列。");
              }}
            >
              <div className="wb-platform-select">
                {platforms.map(([id, title]) => {
                  const p = statuses.find((p) => p.id === id);
                  const ready =
                    p?.can_select ?? p?.login_state === "authenticated";
                  return (
                    <div className="wb-platform-choice" key={id}>
                      <label>
                        <input
                          type="checkbox"
                          checked={selected.includes(id)}
                          disabled={!ready}
                          onChange={(e) =>
                            S((old) =>
                              e.target.checked
                                ? [...old, id]
                                : old.filter((x) => x !== id),
                            )
                          }
                        />
                        <img src={`/platforms/${id}.svg`} alt="" />
                        {title}
                      </label>
                      <small>{authLabel(p)}</small>
                      {!ready && (
                        <button
                          type="button"
                          className="wb-link"
                          onClick={() => goLogin(id)}
                        >
                          去登录
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
              <div className="wb-form-row">
                <label>
                  采集方式
                  <select value={mode} onChange={(e) => M(e.target.value)}>
                    <option value="search">关键词搜索</option>
                    <option value="detail">指定内容</option>
                    <option value="creator">创作者主页</option>
                  </select>
                </label>
                <label className="wb-grow">
                  {mode === "search"
                    ? "关键词（多个用逗号分隔）"
                    : mode === "detail"
                      ? "内容链接或编号"
                      : "创作者链接或编号"}
                  <input
                    required
                    value={input}
                    onChange={(e) => I(e.target.value)}
                    placeholder="输入你希望采集的内容"
                  />
                </label>
              </div>
              <div className="wb-form-row">
                <label>
                  内容数量
                  <input
                    type="number"
                    min="0"
                    value={maxNotes}
                    onChange={(e) => MN(Number(e.target.value))}
                  />
                </label>
                <label>
                  每条内容的评论数
                  <input
                    type="number"
                    min="0"
                    value={maxComments}
                    onChange={(e) => MC(Number(e.target.value))}
                  />
                </label>
                <p>0 表示不指定数量上限。实际结果取决于平台可获取的数据。</p>
              </div>
              <div className="wb-checks">
                {[
                  ["enable_comments", "采集评论"],
                  ["enable_sub_comments", "包含回复"],
                  ["enrich_profiles", "补充公开资料（小红书 / 抖音 / 快手）"],
                ].map(([key, title]) => (
                  <label key={key}>
                    <input
                      type="checkbox"
                      checked={options[key as keyof typeof options]}
                      onChange={(e) =>
                        O({ ...options, [key]: e.target.checked })
                      }
                    />
                    {title}
                  </label>
                ))}
              </div>
              <button
                className="wb-primary"
                disabled={busy || !selected.length}
              >
                {busy ? "正在提交…" : "加入采集队列"}
              </button>
            </form>
            <section className="wb-card">
              <div className="wb-history-toolbar">
                <h2>任务记录</h2>
                <button
                  disabled={
                    busy ||
                    !jobs.some(
                      (j) =>
                        !["queued", "running", "stopping"].includes(j.state),
                    )
                  }
                  onClick={() => void perform(() => clearHistory())}
                >
                  清理已结束任务
                </button>
                {archivedCount > 0 && (
                  <button
                    disabled={busy}
                    onClick={() => void perform(restoreHistory)}
                  >
                    恢复已清理记录（{archivedCount}）
                  </button>
                )}
              </div>
              <p className="wb-option-help">
                清理只移除列表记录，保留采集数据、分析成果和登录信息；运行中及排队中的任务不会清理。
              </p>
              {jobs.map((j) => (
                <div className="wb-row" key={j.id}>
                  <button
                    className="wb-link wb-grow"
                    onClick={() => {
                      JI(j.id);
                      QR(false);
                    }}
                  >
                    <strong>
                      {name(j.platform)} ·{" "}
                      {j.options.keywords ||
                        (
                          {
                            login: "登录验证",
                            search: "关键词搜索",
                            detail: "指定内容",
                            creator: "创作者主页",
                          } as Record<string, string>
                        )[j.options.crawler_type] ||
                        j.options.crawler_type}
                    </strong>
                    <small>
                      {date(j.created)} · 第 {j.attempt} 次运行
                    </small>
                  </button>
                  <Status
                    value={
                      j.state === "running" &&
                      j.options.crawler_type === "login"
                        ? "logging_in"
                        : j.state
                    }
                  />
                  {["queued", "running"].includes(j.state) ? (
                    <button
                      disabled={busy}
                      onClick={() =>
                        void perform(() =>
                          api(`/jobs/${j.id}/stop`, "POST", {}),
                        )
                      }
                    >
                      停止
                    </button>
                  ) : (
                    !["completed", "stopping"].includes(j.state) && (
                      <button
                        disabled={busy}
                        onClick={() =>
                          void perform(() =>
                            api(`/jobs/${j.id}/resume`, "POST", {}),
                          )
                        }
                      >
                        恢复
                      </button>
                    )
                  )}
                  {!["queued", "running", "stopping"].includes(j.state) && (
                    <button
                      disabled={busy}
                      onClick={() => void perform(() => clearHistory([j.id]))}
                    >
                      清理
                    </button>
                  )}
                  <button
                    disabled={!j.dataset_id}
                    onClick={() => void perform(() => showData(j.dataset_id))}
                  >
                    数据
                  </button>
                </div>
              ))}
              {!jobs.length && <p className="wb-empty">暂无任务</p>}
            </section>
            {jobId && (
              <section className="wb-card">
                <h2>
                  任务详情 <small>{jobId}</small>
                </h2>
                <p>
                  {jobs.find((j) => j.id === jobId)?.error ||
                    "运行日志与已保存进度"}
                </p>
                <button onClick={() => QR(!qr)}>查看登录二维码</button>
                {qr && (
                  <div className="wb-qr">
                    <img
                      src={`/api/v1/jobs/${jobId}/qrcode`}
                      alt="尚未生成二维码时，请查看任务浏览器或日志"
                    />
                  </div>
                )}
                <pre className="wb-log">
                  {events
                    .map((e) => `${date(e.created)}  ${e.message}`)
                    .join("\n") || "等待任务日志…"}
                </pre>
              </section>
            )}
          </>
        )}
        {tab === "data" && (
          <>
            <div className="wb-heading">
              <h1>数据集</h1>
              <p>采集和导入的数据保存在本机，分析保留创建时的数据快照。</p>
            </div>
            <div className="wb-toolbar">
              <label className="wb-upload">
                导入 JSON / JSONL / CSV / Excel
                <input
                  type="file"
                  accept=".json,.jsonl,.csv,.xlsx"
                  disabled={busy}
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) void perform(() => upload(f));
                    e.target.value = "";
                  }}
                />
              </label>
              <button disabled={busy} onClick={() => void perform(demo)}>
                导入演示数据
              </button>
            </div>
            <section className="wb-card">
              {datasets.map((d) => (
                <button
                  className={`wb-row ${d.id === datasetId ? "selected" : ""}`}
                  key={d.id}
                  onClick={() => void perform(() => showData(d.id))}
                >
                  <span className="wb-grow">
                    <strong>{d.name}</strong>
                    <small>{date(d.created)}</small>
                  </span>
                  <span>{d.record_count.toLocaleString()} 条</span>
                  <span>查看 →</span>
                </button>
              ))}
              {!datasets.length && (
                <p className="wb-empty">导入数据，或先创建采集任务。</p>
              )}
            </section>
            {datasetId && (
              <>
                <section className="wb-card">
                  <div className="wb-section-title">
                    <h2>数据预览</h2>
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        void perform(() => showData(datasetId));
                      }}
                    >
                      <input
                        aria-label="筛选数据内容"
                        placeholder="搜索内容"
                        value={query}
                        onChange={(e) => Q(e.target.value)}
                      />
                      <button>筛选</button>
                    </form>
                  </div>
                  <div className="wb-table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>来源</th>
                          <th>类型</th>
                          <th>内容</th>
                        </tr>
                      </thead>
                      <tbody>
                        {records.map((r) => (
                          <tr key={r.id}>
                            <td>
                              {name(r.platform)}
                              <small>{r.id}</small>
                            </td>
                            <td>{r.kind}</td>
                            <td>
                              <pre>{JSON.stringify(r.payload, null, 2)}</pre>
                              {/^https?:\/\//i.test(r.source_url) && (
                                <a
                                  href={r.source_url}
                                  target="_blank"
                                  rel="noreferrer"
                                >
                                  查看原文
                                </a>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {next !== null && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        void perform(() => showData(datasetId, next))
                      }
                    >
                      加载更多
                    </button>
                  )}
                </section>
                <form
                  className="wb-card"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void perform(async () => {
                      const r = await api("/analyses", "POST", {
                        dataset_id: datasetId,
                        name: title,
                        goal,
                        ...(schema.trim()
                          ? { result_schema: JSON.parse(schema) }
                          : {}),
                      });
                      await showAnalysis(r.id);
                    }, "任务已保存，让你的 Agent 连接后即可继续。");
                  }}
                >
                  <h2>定义一个分析任务</h2>
                  <label>
                    任务名称
                    <input
                      required
                      value={title}
                      onChange={(e) => AT(e.target.value)}
                      placeholder="由你决定分析什么"
                    />
                  </label>
                  <label>
                    分析目标
                    <textarea
                      required
                      value={goal}
                      onChange={(e) => G(e.target.value)}
                      placeholder="描述你关心的问题、范围与成果"
                    />
                  </label>
                  <details>
                    <summary>可选：自定义结果结构</summary>
                    <textarea
                      value={schema}
                      onChange={(e) => SC(e.target.value)}
                      placeholder="JSON Schema；留空时由 Agent 自由组织结果"
                    />
                  </details>
                  <button className="wb-primary" disabled={busy}>
                    保存分析任务
                  </button>
                </form>
              </>
            )}
          </>
        )}
        {tab === "analyses" && (
          <>
            <div className="wb-heading">
              <h1>分析成果</h1>
              <p>你与 Agent 的分析进度、证据和文件，都保存在这里。</p>
            </div>
            <section className="wb-card">
              {analyses.map((a) => (
                <button
                  className="wb-row"
                  key={a.id}
                  onClick={() => void perform(() => showAnalysis(a.id))}
                >
                  <span className="wb-grow">
                    <strong>{a.name}</strong>
                    <small>{a.goal}</small>
                  </span>
                  <span>
                    {a.coverage.processed} / {a.coverage.total} 已处理
                  </span>
                  <Status value={a.state} />
                </button>
              ))}
              {!analyses.length && (
                <p className="wb-empty">
                  选择数据集创建分析，或让 Agent 直接创建。
                </p>
              )}
            </section>
            {current && (
              <section className="wb-card">
                <h2>{current.name}</h2>
                <p>{current.goal}</p>
                <p>
                  已处理 {current.coverage.processed} · 跳过{" "}
                  {current.coverage.skipped} · 失败 {current.coverage.failed} ·
                  待处理 {current.coverage.pending}
                </p>
                <small>任务编号：{current.id}</small>
                <div className="wb-toolbar">
                  {["xlsx", "docx", "csv", "json", "md"].map((format) => (
                    <button
                      key={format}
                      disabled={busy}
                      onClick={() =>
                        void perform(async () => {
                          const f = await api(
                            `/analyses/${analysisId}/export`,
                            "POST",
                            { format },
                          );
                          const a = document.createElement("a");
                          a.href = f.download_url;
                          a.download = f.name;
                          a.click();
                        }, "成果文件已生成。")
                      }
                    >
                      导出 {format.toUpperCase()}
                    </button>
                  ))}
                  <label className="wb-upload">
                    添加附件
                    <input
                      type="file"
                      disabled={busy}
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        if (f)
                          void perform(() =>
                            upload(f, `/analyses/${analysisId}/attachments`),
                          );
                        e.target.value = "";
                      }}
                    />
                  </label>
                </div>
                {results.map((r) => (
                  <article className="wb-result" key={r.id}>
                    <pre>{JSON.stringify(r.payload, null, 2)}</pre>
                    <small>
                      证据：{r.evidence_ids.join("，") || "未引用原始记录"}
                    </small>
                  </article>
                ))}
                {!results.length && (
                  <p className="wb-empty">
                    等待 Agent 提交结果。离开此页不会丢失任务。
                  </p>
                )}
                {nextResult !== null && (
                  <button
                    onClick={() =>
                      void perform(() => showAnalysis(analysisId, nextResult))
                    }
                  >
                    加载更多结果
                  </button>
                )}
              </section>
            )}
            <section className="wb-card">
              <h2>成果文件</h2>
              {artifacts.map((f) => (
                <a
                  className="wb-row"
                  key={f.id}
                  href={f.download_url}
                  download={f.name}
                >
                  <span>{f.name}</span>
                  <small>
                    {(f.size / 1024).toFixed(1)} KB · {date(f.created)}
                  </small>
                  <span>下载 ↓</span>
                </a>
              ))}
              {!artifacts.length && <p className="wb-empty">暂无文件</p>}
            </section>
          </>
        )}
        {tab === "platforms" && (
          <PlatformLogin
            platforms={statuses}
            pending={pendingLogin}
            clock={clock}
            start={(id) => void startLogin(id)}
            action={(id, action) =>
              action === "forget"
                ? void perform(
                    () => api(`/platforms/${id}/session/forget`, "POST", {}),
                    "已忘记此账号，采集数据仍保留。",
                  )
                : void startLogin(id, action)
            }
            resume={(id) =>
              void perform(
                () => api(`/jobs/${id}/resume`, "POST", {}),
                "已确认使用当前账号继续任务。",
              )
            }
            details={(id) => {
              JI(id);
              QR(false);
              T("jobs");
            }}
            stop={(id) =>
              void perform(() => api(`/jobs/${id}/stop`, "POST", {}))
            }
          />
        )}
        {tab === "settings" && (
          <>
            <div className="wb-heading">
              <h1>设置</h1>
              <p>软件运行环境与用户数据分开保存。</p>
            </div>
            <section className="wb-card">
              <h2>数据与浏览器</h2>
              <label>
                数据目录
                <input readOnly value={settings.data_directory || ""} />
              </label>
              <label>
                浏览器路径（可留空）
                <input
                  value={browserPath}
                  onChange={(e) => BP(e.target.value)}
                  placeholder="自动查找 Chrome / Edge，必要时使用内置浏览器"
                />
              </label>
              <button
                disabled={busy}
                onClick={() =>
                  void perform(
                    () =>
                      api("/settings", "PUT", { browser_path: browserPath }),
                    "设置已保存。",
                  )
                }
              >
                保存设置
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  void perform(async () => {
                    const r = await api("/backup", "POST", {});
                    N(`备份已保存：${r.path}`);
                  })
                }
              >
                备份数据库
              </button>
            </section>
            <section className="wb-card">
              <h2>导入旧版数据</h2>
              <p>读取旧数据目录中的 JSON、JSONL、CSV 和 Excel，保留原文件。</p>
              <input
                value={legacyPath}
                onChange={(e) => LP(e.target.value)}
                placeholder="旧版 data 目录的完整路径"
              />
              <button
                disabled={busy || !legacyPath}
                onClick={() =>
                  void perform(async () => {
                    const r = await api("/imports/legacy", "POST", {
                      path: legacyPath,
                    });
                    await showData(r.id);
                    if (r.errors.length)
                      E(
                        `${r.errors.length} 个文件未导入：${JSON.stringify(r.errors)}`,
                      );
                  })
                }
              >
                导入旧数据
              </button>
            </section>
            <section className="wb-card">
              <h2>运行检查</h2>
              <p>
                后台队列：{diagnostics.queue_alive ? "正常" : "未运行"} · Python{" "}
                {diagnostics.python} · 软件 {diagnostics.version}
              </p>
              <button onClick={() => void perform(showSettings)}>
                重新检查
              </button>
            </section>
          </>
        )}
      </main>
    </div>
  );
}
