import tailwindcss from '@tailwindcss/vite';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

// Dev proxy so the browser talks same-origin (no CORS): the SPA fetches
// `/api/v1/...` and Vite forwards to the control plane. Point QIITA_BASE at a
// local stack or the remote deploy.
const target = process.env.QIITA_BASE || 'https://qiita-miint.ucsd.edu';

export default defineConfig({
  plugins: [tailwindcss(), sveltekit()],
  server: {
    proxy: {
      '/api': { target, changeOrigin: true, secure: true }
    }
  }
});
