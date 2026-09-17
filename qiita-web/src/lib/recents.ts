// Client-side "recently opened studies" — the API has no list-all-studies
// endpoint, so this is how the landing shows anything at all. Stored in
// localStorage, newest first, capped.
export type RecentStudy = {
  idx: number;
  accessible: boolean;
  biosampleCount: number | null;
  lastOpened: number;
};

const KEY = 'qiita_recent_studies';
const CAP = 30;

export function getRecents(): RecentStudy[] {
  if (typeof localStorage === 'undefined') return [];
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? '[]');
  } catch {
    return [];
  }
}

export function addRecent(r: RecentStudy): void {
  const list = getRecents().filter((x) => x.idx !== r.idx);
  list.unshift(r);
  localStorage.setItem(KEY, JSON.stringify(list.slice(0, CAP)));
}

export function removeRecent(idx: number): void {
  localStorage.setItem(KEY, JSON.stringify(getRecents().filter((x) => x.idx !== idx)));
}
