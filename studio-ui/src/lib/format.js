// Human-friendly formatting.

export function duration(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '';
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${String(s % 60).padStart(2, '0')} s`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`;
}

export const percent = (fraction) => `${Math.round((fraction || 0) * 100)}%`;

export function signed(semitones) {
  const v = Number(semitones) || 0;
  return `${v > 0 ? '+' : v < 0 ? '−' : '±'}${Math.abs(v).toFixed(1)} st`;
}

export function pacingLabel(tempo) {
  if (!tempo) return '';
  const t = Number(tempo);
  const word = t <= 1.0001 ? 'Natural' : t < 1.25 ? 'Slightly faster' : t < 1.6 ? 'Fast' : 'Very fast';
  return `${t.toFixed(2)}× (${word})`;
}

export function timestamp(ms) {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}.${String(Math.floor((ms % 1000) / 100))}`;
}
