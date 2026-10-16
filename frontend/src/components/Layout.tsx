import clsx from "clsx";
import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { useMeta } from "../api/hooks";
import { longDate, seasonName } from "../lib/format";

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/simulator", label: "Season simulator" },
  { to: "/match", label: "Match lab" },
  { to: "/models", label: "Model evaluation" },
  { to: "/teams", label: "Team ratings" },
  { to: "/method", label: "Methodology" },
];

type Theme = "light" | "dark" | "system";

function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      const saved = localStorage.getItem("theme");
      return saved === "light" || saved === "dark" ? saved : "system";
    } catch {
      return "system";
    }
  });
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") delete root.dataset.theme;
    else root.dataset.theme = theme;
    try {
      if (theme === "system") localStorage.removeItem("theme");
      else localStorage.setItem("theme", theme);
    } catch {
      // Private mode or blocked storage: the theme still applies for this visit.
    }
  }, [theme]);
  return [theme, setTheme];
}

export function Layout() {
  const [theme, setTheme] = useTheme();
  const meta = useMeta();
  const [open, setOpen] = useState(false);

  return (
    <div className="min-h-screen bg-page">
      <header className="sticky top-0 z-20 border-b border-line bg-page/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-4 px-4 sm:px-6">
          <NavLink to="/" className="flex items-center gap-2 font-semibold text-ink">
            <img src="/favicon.svg" alt="" className="h-6 w-6" />
            <span>EPL Forecast</span>
          </NavLink>
          <nav aria-label="Main" className="hidden flex-1 items-center gap-1 lg:flex">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  clsx(
                    "rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors",
                    isActive ? "bg-raised text-ink" : "text-ink-2 hover:text-ink",
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <select
              aria-label="Colour theme"
              value={theme}
              onChange={(e) => setTheme(e.target.value as Theme)}
              className="h-8 rounded-md border border-line bg-surface px-2 text-[12px] text-ink-2"
            >
              <option value="system">System</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
            <button
              type="button"
              className="rounded-md border border-line px-2.5 py-1 text-[13px] text-ink lg:hidden"
              aria-expanded={open}
              aria-controls="mobile-nav"
              onClick={() => setOpen((v) => !v)}
            >
              Menu
            </button>
          </div>
        </div>
        {open && (
          <nav id="mobile-nav" aria-label="Main" className="border-t border-line px-4 py-2 lg:hidden">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                onClick={() => setOpen(false)}
                className={({ isActive }) =>
                  clsx("block rounded-md px-3 py-2 text-[14px]", isActive ? "bg-raised text-ink" : "text-ink-2")
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        )}
      </header>

      {meta.data?.dataset.synthetic && (
        <div role="note" className="border-b border-line bg-raised">
          <p className="mx-auto max-w-7xl px-4 py-2 text-[13px] text-ink-2 sm:px-6">
            <strong className="font-semibold text-ink">Demo data.</strong> {meta.data.dataset.label}: fictional clubs
            simulated by this project, so results measure the software and models, not real-world accuracy.
          </p>
        </div>
      )}

      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
        <Outlet />
      </main>

      <footer className="mx-auto max-w-7xl px-4 pb-10 text-[12px] text-muted sm:px-6">
        {meta.data && (
          <p>
            {seasonName(meta.data.current_season)} · model {meta.data.production_model}{" "}
            <code className="text-ink-2">{meta.data.model_version ?? "not trained"}</code>
            {meta.data.last_result && <> · results through {longDate(meta.data.last_result)}</>} ·{" "}
            <a className="underline hover:text-ink" href="/api/v1/docs">
              API docs
            </a>
          </p>
        )}
        {meta.data && (
          <p className="mt-1">
            Data: {meta.data.dataset.label}. {meta.data.dataset.license} Forecasts are probabilities, not betting
            advice.
          </p>
        )}
      </footer>
    </div>
  );
}
