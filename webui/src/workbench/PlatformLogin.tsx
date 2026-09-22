import { useEffect, useRef, useState } from "react";
type Item = Record<string, any>;
const phases: Record<string, string> = {
  submitting: "正在提交登录请求…",
  queued: "登录已排队，前面的任务结束后会启动浏览器，请勿重复点击。",
  starting_browser: "正在启动登录浏览器，请稍候…",
  browser_ready: "浏览器已启动，正在检查已有登录信息…",
  opening_login: "浏览器已启动，正在准备登录页面，请稍候…",
  waiting_scan:
    "请在弹出的浏览器中扫码，并在手机上确认；软件确认后才算登录成功。",
  verifying: "正在确认登录状态，请稍候…",
  stopping: "正在取消登录，请稍候…",
  cancelled: "登录已取消，可以重新登录。",
  interrupted: "登录过程已中断，请重新登录。",
  failed: "验证未完成，请查看详情后重试。",
};
export function authLabel(p?: Item) {
  if (p?.last_check?.state === "network_error") return "连接异常";
  if (p?.last_check?.state === "challenge") return "需要验证";
  if (p?.last_check?.state === "unknown") return "待验证";
  if (p?.last_check?.state === "logged_out") return "需要重新登录";
  if (p?.login_state === "authenticated") return "已登录";
  if (p?.login_state === "logged_out") return "需要重新登录";
  if (p?.login_state === "unknown") return "验证未完成";
  return p?.saved_session ? "已有登录信息，待验证" : "尚未登录";
}
export default function PlatformLogin({
  platforms,
  pending,
  clock,
  start,
  details,
  stop,
  action,
  resume,
}: {
  platforms: Item[];
  pending: Record<string, string>;
  clock: number;
  start: (id: string) => void;
  details: (id: string) => void;
  stop: (id: string) => void;
  action: (id: string, action: string) => void;
  resume: (id: string) => void;
}) {
  const [forgetting, setForgetting] = useState<string | null>(null);
  const confirmRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (forgetting) confirmRef.current?.focus(); }, [forgetting]);
  return (
    <>
      <div className="wb-heading">
        <h1>平台登录</h1>
        <p>
          首次登录后自动保存。开始采集时会检查已有登录，仅在平台要求时提示你完成验证。
        </p>
      </div>
      <div className="wb-platform-grid">
        {platforms.map((p) => {
          const login = p.login;
          const working = Boolean(pending[p.id] || login?.active);
          const phase = pending[p.id] ? "submitting" : login?.phase;
          const message = pending[p.id]
            ? phases.submitting
            : p.session_error ||
              (p.last_check?.state === "network_error"
                ? "暂时无法连接平台，账号仍保留。网络恢复后可直接重试任务。"
                : "") ||
              login?.message ||
              (phase === "authenticated"
                ? p.session_status === "saved"
                  ? "登录已保存，后续任务会自动使用。"
                  : login.restored
                    ? "已恢复登录，无需再次扫码。"
                    : "已登录，登录信息已保存在本机。"
                : phases[phase]);
          const seconds = Math.max(
            0,
            Math.floor(
              (clock - Date.parse(pending[p.id] || login?.started || "")) /
                1000,
            ),
          );
          return (
            <section
              className="wb-card wb-login-card"
              key={p.id}
              id={`login-${p.id}`}
              tabIndex={-1}
            >
              <div className="wb-login-title">
                <img
                  width="36"
                  height="36"
                  src={`/platforms/${p.id}.svg`}
                  alt=""
                />
                <h2>{p.name}</h2>
                <span className={`wb-status state-${p.login_state}`}>
                  {authLabel(p)}
                </span>
              </div>
              {p.account?.name && <p>{p.account.name}</p>}
              {p.account?.name && p.session_status === "not_saved" && (
                <small>原登录档案已保留，验证后将创建加密备份。</small>
              )}
              <div
                className={`wb-login-feedback ${working ? "is-working" : ""}`}
                role="status"
                aria-live="polite"
              >
                {working && (
                  <span className="wb-login-spinner" aria-hidden="true" />
                )}
                <p>
                  {message ||
                    (p.saved_session
                      ? "已有登录信息，验证有效后即可使用，无需重复扫码。"
                      : "点击登录，等待浏览器弹出后完成扫码。")}
                </p>
              </div>
              {working && Number.isFinite(seconds) && (
                <small>已等待 {seconds} 秒 · 请勿重复点击</small>
              )}
              {p.verified_at && (
                <small>
                  最近验证：
                  {new Date(p.verified_at).toLocaleString("zh-CN", {
                    hour12: false,
                  })}{" "}
                  · 采集前会再次检查
                </small>
              )}
              {p.saved_at && (
                <small>
                  本机保存：
                  {new Date(p.saved_at).toLocaleString("zh-CN", {
                    hour12: false,
                  })}
                </small>
              )}
              <div className="wb-login-actions">
                <button
                  className="wb-primary"
                  disabled={working}
                  onClick={() =>
                    p.login_state === "authenticated" && !p.needs_user_action
                      ? action(p.id, "open")
                      : start(p.id)
                  }
                >
                  {working
                    ? "登录进行中…"
                    : p.last_check?.state === "logged_out"
                      ? "重新登录"
                    : p.needs_user_action
                      ? "完成验证"
                      : p.login_state === "authenticated"
                        ? "打开平台"
                        : ["failed", "cancelled", "interrupted"].includes(phase)
                          ? "重新登录"
                          : p.saved_session
                            ? "验证并登录"
                            : "打开登录"}
                </button>
                {login?.job_id && (
                  <button onClick={() => details(login.job_id)}>
                    查看详情
                  </button>
                )}
                {login?.active && login.task_state !== "stopping" && (
                  <button onClick={() => stop(login.job_id)}>取消</button>
                )}
              </div>
              <details>
                <summary>更多操作</summary>
                <div className="wb-login-actions">
                  <button disabled={working} onClick={() => action(p.id, "open")}>
                    打开平台浏览器
                  </button>
                  <button
                    disabled={working}
                    onClick={() => action(p.id, "check")}
                  >
                    {p.session_status === "failed" ? "重试保存" : "检查登录"}
                  </button>
                  <button
                    disabled={working}
                    onClick={() => setForgetting(p.id)}
                  >
                    忘记此账号
                  </button>
                </div>
              </details>
              {forgetting === p.id && (
                <div
                  role="alertdialog"
                  ref={confirmRef}
                  tabIndex={-1}
                  onKeyDown={(event) => { if (event.key === "Escape") setForgetting(null); }}
                  aria-label={`忘记${p.name}账号`}
                  className="wb-login-feedback"
                >
                  <p>
                    清除本机保存的登录信息，之后需要重新登录。采集数据和任务记录会保留。
                  </p>
                  <div className="wb-login-actions">
                    <button onClick={() => setForgetting(null)}>取消</button>
                    <button
                      onClick={() => {
                        setForgetting(null);
                        action(p.id, "forget");
                      }}
                    >
                      确认忘记
                    </button>
                  </div>
                </div>
              )}
              {p.account_confirmation_jobs?.map((id: string) => (
                <div key={id} className="wb-login-feedback">
                  <p>有任务等待确认账号。请确认当前账号适合继续这项任务。</p>
                  <button onClick={() => resume(id)}>
                    确认使用当前账号继续
                  </button>
                  <button onClick={() => details(id)}>查看任务</button>
                </div>
              ))}
              <small>
                搜索 · 指定内容 · 创作者 · 评论
                {p.enrich_profiles ? " · 公开资料补全" : ""}
              </small>
            </section>
          );
        })}
      </div>
    </>
  );
}
