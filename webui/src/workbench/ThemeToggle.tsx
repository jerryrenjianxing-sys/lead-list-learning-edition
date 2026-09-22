import { useEffect, useRef, useState } from "react";
import { Moon, Sun } from "lucide-react";
import { gsap } from "gsap";

type Theme = "light" | "dark";
const currentTheme = (): Theme =>
  document.documentElement.dataset.theme === "dark" ? "dark" : "light";

function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem("mediaworkbench-theme", theme);
  } catch {
    // The current window can still switch themes when storage is unavailable.
  }
  window.dispatchEvent(new Event("mediaworkbench-theme-change"));
}

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(currentTheme);
  const button = useRef<HTMLButtonElement>(null);
  const layer = useRef<HTMLDivElement>(null);
  const icon = useRef<HTMLSpanElement>(null);
  const pending = useRef<Theme | null>(null);

  useEffect(() => {
    const overlay = layer.current;
    const symbol = icon.current;
    const sync = () => setTheme(currentTheme());
    const observer = new MutationObserver(sync);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => {
      observer.disconnect();
      gsap.killTweensOf([overlay, symbol]);
      // Navigation during a transition retains the user's last selection.
      if (pending.current) applyTheme(pending.current);
      pending.current = null;
    };
  }, []);

  function toggleTheme() {
    const next: Theme = (pending.current ?? currentTheme()) === "dark" ? "light" : "dark";
    const overlay = layer.current;
    const symbol = icon.current;
    gsap.killTweensOf([overlay, symbol]);
    pending.current = next;

    const finish = () => {
      applyTheme(next);
      pending.current = null;
      setTheme(next);
    };
    if (
      !overlay ||
      !button.current ||
      window.matchMedia("(prefers-reduced-motion: reduce)").matches ||
      next === currentTheme()
    ) {
      if (overlay) gsap.set(overlay, { display: "none", clearProps: "clipPath,opacity" });
      if (symbol) gsap.set(symbol, { clearProps: "transform" });
      finish();
      return;
    }

    // Restore MediaFlow's GSAP circular wipe, centered on the theme button.
    const bounds = button.current.getBoundingClientRect();
    const x = bounds.left + bounds.width / 2;
    const y = bounds.top + bounds.height / 2;
    const radius = Math.ceil(Math.hypot(
      Math.max(x, window.innerWidth - x),
      Math.max(y, window.innerHeight - y),
    ));
    overlay.dataset.nextTheme = next;
    gsap.set(overlay, {
      display: "block",
      opacity: 1,
      clipPath: `circle(0px at ${x}px ${y}px)`,
    });
    gsap.to(overlay, {
      clipPath: `circle(${radius}px at ${x}px ${y}px)`,
      duration: 0.48,
      ease: "power3.inOut",
      onComplete: () => {
        finish();
        gsap.to(overlay, {
          opacity: 0,
          duration: 0.16,
          ease: "power1.out",
          onComplete: () => { gsap.set(overlay, { display: "none" }); },
        });
      },
    });
    if (symbol) gsap.fromTo(symbol,
      { rotation: next === "dark" ? -90 : 90, scale: 0.8 },
      { rotation: 0, scale: 1, duration: 0.55, ease: "power2.out", clearProps: "transform" },
    );
  }

  const label = theme === "dark" ? "切换到浅色主题" : "切换到深色主题";
  return (
    <>
      <button ref={button} type="button" className="skill-theme"
        aria-label={label} title={label} onClick={toggleTheme}>
        <span ref={icon} className="skill-theme-icon" aria-hidden="true">
          {theme === "dark" ? <Sun size={19} strokeWidth={1.75} /> : <Moon size={19} strokeWidth={1.75} />}
        </span>
      </button>
      <div ref={layer} className="skill-theme-transition" aria-hidden="true" />
    </>
  );
}
