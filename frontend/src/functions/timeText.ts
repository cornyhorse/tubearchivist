/** "just now", "5 min ago", "3 h ago", "yesterday", "4 days ago", then a date */
export const ago = (when: string | number | Date | null | undefined, now = Date.now()): string => {
  if (when === null || when === undefined) return '';
  const time = new Date(when).getTime();
  const seconds = Math.round((now - time) / 1000);
  if (seconds < 0) return inFuture(time, now);
  if (seconds < 60) return 'just now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days === 1) return 'yesterday';
  if (days < 7) return `${days} days ago`;
  return new Date(time).toLocaleDateString();
};

/** "in 5 min", "in 3 h", "tomorrow 03:00" */
export const inFuture = (time: number, now = Date.now()): string => {
  const minutes = Math.round((time - now) / 60000);
  if (minutes < 1) return 'any moment';
  if (minutes < 60) return `in ${minutes} min`;
  const date = new Date(time);
  const clock = `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
  const today = new Date(now);
  const sameDay = date.toDateString() === today.toDateString();
  if (sameDay) return `today ${clock}`;
  const tomorrow = new Date(now + 86400000);
  if (date.toDateString() === tomorrow.toDateString()) return `tomorrow ${clock}`;
  return `${date.toLocaleDateString()} ${clock}`;
};

/** 95 -> "1 min 35 s", 3720 -> "1 h 2 min" */
export const duration = (seconds: number | null | undefined): string => {
  if (seconds === null || seconds === undefined) return '';
  const s = Math.round(seconds);
  if (s < 60) return `${s} s`;
  const minutes = Math.floor(s / 60);
  if (minutes < 60) return s % 60 ? `${minutes} min ${s % 60} s` : `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`;
};
