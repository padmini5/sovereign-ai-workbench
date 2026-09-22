import { useCallback, useEffect, useState } from 'react';

/* Three product themes: dark (default), light, high-contrast.
   Persisted in localStorage; applied as data-theme on <html> so the
   all-CSS-variable stylesheet (workbench.css) and var()-based inline
   styles follow instantly. No backend involvement. */

export const THEMES = [
  { id: 'dark', label: 'Dark' },
  { id: 'light', label: 'Light' },
  { id: 'high-contrast', label: 'High Contrast' },
];

const KEY = 'sov_theme';
export const DEFAULT_THEME = 'dark';

export function themeIds() {
  return THEMES.map((t) => t.id);
}

export function readTheme() {
  try {
    const v = localStorage.getItem(KEY);
    if (themeIds().includes(v)) return v;
  } catch { /* storage unavailable — fall through to default */ }
  return DEFAULT_THEME;
}

export function applyTheme(id) {
  const v = themeIds().includes(id) ? id : DEFAULT_THEME;
  try {
    document.documentElement.dataset.theme = v;
  } catch { /* non-DOM environment (tests) — no-op */ }
  return v;
}

export function saveTheme(id) {
  const v = applyTheme(id);
  try {
    localStorage.setItem(KEY, v);
  } catch { /* private mode etc. — theme still applies for the session */ }
  return v;
}

/* Apply the stored theme as early as possible (call once at startup). */
export function initTheme() {
  return applyTheme(readTheme());
}

export function useTheme() {
  const [theme, setTheme] = useState(readTheme);
  useEffect(() => { applyTheme(theme); }, [theme]);
  const set = useCallback((id) => setTheme(saveTheme(id)), []);
  return [theme, set];
}
