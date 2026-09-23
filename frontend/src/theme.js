/* Light-only product theme: the app always boots light (initTheme below
   forces + persists 'light'). The palette/theme ids below remain only so
   the persistence helpers and the CSS contrast blocks keep their contract.
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

/* Apply the light theme at startup (call once at startup): the product is
   light-only — always force light regardless of any stale stored value. */
export function initTheme() {
  return saveTheme('light');
}
