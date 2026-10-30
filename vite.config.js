import {defineConfig} from 'vite';
import {resolve} from 'node:path';

export default defineConfig({
  base: '/',
  build: {
    rollupOptions: {
      input: {
        main: resolve(import.meta.dirname, 'index.html'),
        splats: resolve(import.meta.dirname, 'splats.html'),
        reconstruction: resolve(import.meta.dirname, 'reconstruction.html'),
      },
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {target: 'http://127.0.0.1:8767', changeOrigin: true},
      '/ws': {target: 'ws://127.0.0.1:8767', ws: true},
      '/assets': {target: 'http://127.0.0.1:8767', changeOrigin: true},
      '/reconstructions': {target: 'http://127.0.0.1:8767', changeOrigin: true}
    }
  },
  define: {CESIUM_BASE_URL: JSON.stringify('/cesiumStatic/')}
});
