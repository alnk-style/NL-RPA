import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('react-flow-renderer') || id.includes('/d3-')) {
            return 'flow-vendor';
          }

          if (
            id.includes('react-markdown') ||
            id.includes('rehype-') ||
            id.includes('remark-') ||
            id.includes('highlight.js') ||
            id.includes('micromark') ||
            id.includes('mdast') ||
            id.includes('hast')
          ) {
            return 'markdown-vendor';
          }

          if (
            id.includes('/react/') ||
            id.includes('/react-dom/') ||
            id.includes('/react-router') ||
            id.includes('/scheduler/')
          ) {
            return 'react-vendor';
          }

          if (
            id.includes('/axios/') ||
            id.includes('/zustand/') ||
            id.includes('/lucide-react/') ||
            id.includes('/clsx/') ||
            id.includes('/tailwind-merge/')
          ) {
            return 'app-vendor';
          }
        },
      },
    },
  },
});
