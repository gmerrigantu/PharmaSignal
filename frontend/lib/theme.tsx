"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

export type Theme = "light" | "dark";

type ThemeContextValue = {
  theme: Theme;
  toggle: () => void;
  setTheme: (t: Theme) => void;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

const STORAGE_KEY = "pharmasignal-theme";

/**
 * Inline script (injected in <head>) that resolves the theme before first paint to
 * avoid a flash of the wrong color scheme. Reads localStorage, falls back to the OS
 * preference, and sets data-theme on <html>.
 */
export const themeNoFlashScript = `(function(){try{var t=localStorage.getItem('${STORAGE_KEY}');if(!t){t=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';}document.documentElement.setAttribute('data-theme',t);}catch(e){}})();`;

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>("light");

  // Sync React state with whatever the no-flash script already applied.
  useEffect(() => {
    const current = (document.documentElement.getAttribute("data-theme") as Theme) || "light";
    setThemeState(current);
  }, []);

  const setTheme = useCallback((t: Theme) => {
    setThemeState(t);
    document.documentElement.setAttribute("data-theme", t);
    try {
      localStorage.setItem(STORAGE_KEY, t);
    } catch {
      /* ignore */
    }
  }, []);

  const toggle = useCallback(() => {
    setTheme(theme === "dark" ? "light" : "dark");
  }, [theme, setTheme]);

  const value = useMemo(() => ({ theme, toggle, setTheme }), [theme, toggle, setTheme]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}

/** Concrete chart colors per theme (recharts needs literal color strings, not CSS vars). */
export type ChartPalette = {
  grid: string;
  axis: string;
  accent: string;
  blue: string;
  gold: string;
  high: string;
  moderate: string;
  low: string;
  flagged: string;
  muted: string;
  tooltipBg: string;
};

const LIGHT_PALETTE: ChartPalette = {
  grid: "#e7ebf0",
  axis: "#8a94a6",
  accent: "#0d9488",
  blue: "#3b6fb0",
  gold: "#b7791f",
  high: "#d4456a",
  moderate: "#c8881a",
  low: "#94a3b8",
  flagged: "#0d9488",
  muted: "#cbd5e1",
  tooltipBg: "#ffffff",
};

const DARK_PALETTE: ChartPalette = {
  grid: "#222b38",
  axis: "#6b7686",
  accent: "#2dd4bf",
  blue: "#5b91d6",
  gold: "#d4a23a",
  high: "#f0668c",
  moderate: "#e0a23a",
  low: "#64748b",
  flagged: "#2dd4bf",
  muted: "#3a4556",
  tooltipBg: "#141a22",
};

export function useChartPalette(): ChartPalette {
  const { theme } = useTheme();
  return theme === "dark" ? DARK_PALETTE : LIGHT_PALETTE;
}
