"use client";
/* eslint-disable @next/next/no-img-element */
import { useRef, useState } from "react";
import ThemeToggle from "./ThemeToggle";
import { desktopSkillCopy } from "./desktop";
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
  const [feedback, setFeedback] = useState("");
  const [paused, setPaused] = useState(false);
  const copying = useRef(false);
  async function copySkill() {
    if (copying.current) return;
    copying.current = true;
    setCopy("loading");
    setFeedback("");
    try {
      const copyFile = desktopSkillCopy();
      if (!copyFile) {
        setCopy("error");
        setFeedback("请在新版桌面软件中复制");
        return;
      }
      await copyFile({ name: "Media Deep Researcher.md" });
      setCopy("done");
      setFeedback("已复制 MD 文件");
    } catch (error) {
      setCopy("error");
      setFeedback(error instanceof Error ? error.message.replace(/^Error invoking remote method '[^']+': (?:Error: )?/, "") : "复制失败，请重试");
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
              {feedback || "复制失败，请重试，或到任务台检查本机服务。"}
            </p>
          ) : copy === "done" ? (
            <p>{feedback}</p>
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
    </main>
  );
}
