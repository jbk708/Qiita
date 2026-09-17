import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/**
 * Static SPA: no Node server in prod. `fallback: index.html` makes every route
 * resolve client-side, so nginx can serve the built bundle as flat files and
 * keep `/api/v1/*` proxied to the control plane (see docs in README.md).
 * @type {import('@sveltejs/kit').Config}
 */
export default {
  preprocess: vitePreprocess(),
  kit: {
    adapter: adapter({ fallback: 'index.html' })
  }
};
