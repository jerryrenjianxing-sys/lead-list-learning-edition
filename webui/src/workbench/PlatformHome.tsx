"use client";
/* eslint-disable @next/next/no-img-element */
import { useEffect, useRef, useState } from "react";

import { loadSkillMarkdown, fetchLocalApi } from "./home-loader";
import ThemeToggle from "./ThemeToggle";
const API = "";
import "./skill-home.css";

const agents = [
  ["claude-code", "Claude Code"],
  ["cursor", "Cursor"],
  ["codex", "Codex"],
  ["copilot", "GitHub Copilot"],
  ["windsurf", "Windsurf"],
  ["gemini", "Gemini"],
  ["cline", "Cline"],
  ["amp", "Amp"],
  ["antigravity", "Antigravity"],
  ["openclaw", "OpenClaw"],
  ["droid", "Droid"],
  ["goose", "Goose"],
  ["kilo", "Kilo"],
  ["kiro-cli", "Kiro CLI"],
  ["nous-research", "Hermes"],
  ["opencode", "OpenCode"],
  ["roo", "Roo"],
  ["trae", "Trae"],
  ["vscode", "VS Code"],
  ["zed", "Zed"],
];
type ActionState = "idle" | "loading" | "done" | "error";

function Icon({
  kind,
}: {
  kind: "copy" | "grid" | "pause" | "play";
}) {
  const paths = {
    copy: (
      <>
        <rect x="8" y="8" width="12" height="13" rx="2" />
        <path d="M15 8V3H3v13h5" />
      </>
    ),
    grid: (
      <>
        <rect x="3" y="3" width="7" height="7" rx="1" />
        <rect x="14" y="3" width="7" height="7" rx="1" />
        <rect x="3" y="14" width="7" height="7" rx="1" />
        <rect x="14" y="14" width="7" height="7" rx="1" />
      </>
    ),
    pause: <path d="M8 5v14M16 5v14" />,
    play: <path d="m8 4 12 8-12 8Z" />,
  };
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[kind]}
    </svg>
  );
}

export default function PlatformHome({
  legacySession = false,
}: {
  legacySession?: boolean;
}) {
  const [copy, setCopy] = useState<ActionState>("idle");
  const [manual, setManual] = useState(""),
    [paused, setPaused] = useState(false);
  const copying = useRef(false),
    dialog = useRef<HTMLDialogElement>(null),
    manualText = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (manual) {
      dialog.current?.showModal();
      manualText.current?.focus();
      manualText.current?.select();
    }
  }, [manual]);
  async function copySkill() {
    if (copying.current) return;
    copying.current = true;
    setCopy("loading");
    try {
      const markdown = await loadSkillMarkdown(() =>
        fetchLocalApi(
          `${API}/api/platform-skill?format=connect`,
          { cache: "no-store" },
          15000,
        ),
      );
      try {
        await navigator.clipboard.writeText(markdown);
        setCopy("done");
      } catch {
        setCopy("idle");
        setManual(markdown);
      }
    } catch {
      setCopy("error");
    } finally {
      copying.current = false;
    }
  }
  return (
    <main className="skill-home" id="main-content">
      <header className="skill-header">
        <a href="/" className="skill-brand" aria-label="Media Deep Researcher 首页">
          <img src="/brand/media-deep-researcher.png" alt="" width="32" height="32" />
          <span>Media Deep Researcher</span>
        </a>
        <span className="skill-header-caption">你的 Agent，你的工作流。</span>
        <ThemeToggle />
      </header>
      <section className="skill-hero" aria-labelledby="skill-title">
        <p className="skill-eyebrow">
          <span />
          为你的 Agent，接入行动力
        </p>
        <h1 id="skill-title">
          <span className="skill-title-line">Media</span>{" "}
          <span className="skill-title-line">
            Deep Researcher<span className="skill-title-dot">.</span>
          </span>
        </h1>
        <h2>
          一份 Skill，<span>采集数据，自由分析。</span>
        </h2>
        <p className="skill-description">
          把采集、分析与成果，交给你熟悉的 Agent。
          <br />
          复制 Skill，粘贴给 Agent 即可开始。
        </p>
        <div className="skill-actions">
          <a className="skill-button skill-button-primary" href="#manage">
            <Icon kind="grid" />
            任务台
          </a>
          <button
            className="skill-button skill-button-secondary"
            type="button"
            disabled={copy === "loading"}
            onClick={() => void copySkill()}
          >
            <Icon kind="copy" />
            {copy === "loading"
              ? "正在读取…"
              : copy === "done"
                ? "已复制"
                : "复制 Skill"}
          </button>
        </div>
        <p className="skill-format">
          复制接入 · 本地运行 · 使用自己的 Agent
        </p>
        <div className="skill-feedback" aria-live="polite" aria-atomic="true">
          {copy === "error" ? (
            <p className="skill-error">
              读取失败，请重试复制，或到任务台检查本机服务。
            </p>
          ) : copy === "done" ? (
            <p>Skill 已复制，粘贴给你的 Agent 即可。</p>
          ) : null}
        </div>
        {legacySession ? (
          <p className="skill-legacy">
            旧会话数据仍保留；此入口已改为外部 Skill，不会删除历史记录。
          </p>
        ) : null}
      </section>
      <section
        className={`skill-agents ${paused ? "is-paused" : ""}`}
        aria-labelledby="skill-agents-heading"
      >
        <div className="skill-agents-heading">
          <h2 id="skill-agents-heading">与你熟悉的 Agent 协作</h2>
          <button
            className="skill-marquee-toggle"
            type="button"
            onClick={() => setPaused(!paused)}
            aria-label={paused ? "继续图标滚动" : "暂停图标滚动"}
            aria-pressed={paused}
          >
            <Icon kind={paused ? "play" : "pause"} />
          </button>
        </div>
        <div className="skill-marquee">
          <div className="skill-marquee-track">
            {[0, 1].map((group) => (
              <ul
                className="skill-agent-group"
                key={group}
                aria-hidden={group === 1 ? true : undefined}
              >
                {agents.map(([id, name]) => (
                  <li key={id}>
                    <img
                      src={`/agents/${id}.svg`}
                      alt=""
                      width="28"
                      height="28"
                    />
                    <span>{name}</span>
                  </li>
                ))}
              </ul>
            ))}
          </div>
        </div>
        <p className="skill-agents-note">
          适用于支持 Skill、文件与本地命令的 Agent；具体接入能力以所用工具为准。
        </p>
      </section>
      <footer className="skill-footer">
        <span>本地运行 · 自由连接</span>
      </footer>
      <dialog
        ref={dialog}
        className="skill-copy-dialog"
        onClose={() => setManual("")}
      >
        <h2>手动复制 Skill</h2>
        <p>
          浏览器未允许直接复制。下方已选中 Skill，请按 Ctrl+C（Mac 为
          ⌘C）。
        </p>
        <textarea
          ref={manualText}
          aria-label="Skill"
          readOnly
          value={manual}
        />
        <button
          type="button"
          className="skill-button skill-button-primary"
          onClick={() => dialog.current?.close()}
        >
          完成
        </button>
      </dialog>
    </main>
  );
}
