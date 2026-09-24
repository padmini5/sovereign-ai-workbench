/* Navigation model — UI VISIBILITY ONLY. The backend independently enforces
   every endpoint (require_perm / require_role); hiding a link is a courtesy,
   not a control. */

export const NAV = [
  { id: 'dashboard', labelKey: 'dashboard', ico: '◧', show: () => true },
  { id: 'employees', labelKey: 'employees', ico: '⛉', perm: 'USER_READ' },
  { id: 'profile', labelKey: 'myProfile', ico: '⛇', show: () => true },
  { id: 'attendance', labelKey: 'attendance', ico: '◷', perm: 'ATTENDANCE_READ' },
  { id: 'schedule', labelKey: 'schedule', ico: '◔', perm: 'WORK_READ' },
  { id: 'worksheets', labelKey: 'worksheets', ico: '◫', perm: 'WORKSHEET_READ' },
  { id: 'spreadsheets', labelKey: 'spreadsheets', ico: '∑', perm: 'ANALYTICS_READ' },
  { id: 'performance', labelKey: 'performance', ico: '★', perm: 'WORK_READ' },
  { id: 'assistant', labelKey: 'assistant', ico: '✦', perm: 'AI_CHAT' },
  { id: 'documents', labelKey: 'documents', ico: '▤', perm: 'DOCUMENT_READ' },
  { id: 'images', labelKey: 'images', ico: '▣', perm: 'DOCUMENT_READ' },
  { id: 'voice', labelKey: 'voice', ico: '◉', perm: 'AI_CHAT' },
  { id: 'agents', labelKey: 'agents', ico: '⌬', perm: 'AI_AGENT_USE' },
  { id: 'reports', labelKey: 'reports', ico: '▦', perm: 'REPORT_READ' },
  { id: 'audit', labelKey: 'audit', ico: '☰', perm: 'AUDIT_READ' },
  { id: 'admin', labelKey: 'admin', ico: '⛨', roles: ['ADMIN', 'security_admin'] },
  { id: 'system', labelKey: 'systemCaps', ico: '◈', roles: ['ADMIN', 'security_admin'] },
  { id: 'settings', labelKey: 'settings', ico: '⚒', show: () => true },
];

export function canSee(page, user) {
  if (!user) return false;
  if (page.roles) return page.roles.includes(user.role);
  if (page.perm) return (user.permissions || []).includes(page.perm);
  return page.show ? !!page.show(user) : true;
}

export const visibleNav = (user) => NAV.filter((p) => canSee(p, user));
