// The PAT the SPA sends as `Authorization: Bearer …`. Kept in localStorage so a
// refresh survives; exposed as a rune so components react when it changes.
// (SPA only — this module never runs under SSR, so `localStorage` is safe.)
const KEY = 'qiita_token';

function createAuth() {
  let token = $state(typeof localStorage !== 'undefined' ? (localStorage.getItem(KEY) ?? '') : '');
  return {
    get token() {
      return token;
    },
    get isSet() {
      return token.length > 0;
    },
    set(t: string) {
      token = t.trim();
      localStorage.setItem(KEY, token);
    },
    clear() {
      token = '';
      localStorage.removeItem(KEY);
    }
  };
}

export const auth = createAuth();
