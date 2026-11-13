import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  base: './',
  plugins: [react()],
  resolve: {
    dedupe: [
      'react',
      'react-dom',
      'react-router',
      'react-router-dom',
    ],
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    fs: {
      allow: ['..'],
    },
  },
  build: {
    emptyOutDir: true,
    outDir: 'dist',
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
  test: {
    environment: 'node',
    include: [
      '../tests/test_sidecar.test.ts',
      '../tests/desktop-routes.test.ts',
      '../tests/installed-apps.test.ts',
      '../tests/installed-app-mcp.test.ts',
    ],
  },
});
