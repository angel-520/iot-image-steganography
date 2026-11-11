import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true, proxy: { '/api': { target: 'http://127.0.0.1:8000', ws: true } } },
  preview: { port: 5173 },
  build: {
    chunkSizeWarningLimit: 1500,
    rollupOptions: {output: {manualChunks: {charts:['echarts/core','echarts/charts','echarts/components','echarts/renderers']}}},
  },
});
