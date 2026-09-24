/* Dynamic date label for the Dashboard header — always computed from the
   current clock at render time, never a hardcoded or historical date. */

export function todayLabel(d = new Date()) {
  try {
    return d.toLocaleDateString(undefined, {
      weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
    });
  } catch {
    return d.toDateString();
  }
}
