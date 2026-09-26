import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  // `@klinecharts/pro` declares `klinecharts` as a peerDependency and uses a
  // bare `import 'klinecharts'` in its ESM build. Workspace installs hoist
  // klinecharts to the root node_modules; without explicit hints rollup
  // fails to resolve it from the frontend subpackage during `vite build`.
  optimizeDeps: {
    include: ['klinecharts'],
  },
  build: {
    commonjsOptions: {
      include: [/@klinecharts/, /node_modules/],
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:4000',
        changeOrigin: true,
      },
    },
  },
});
