import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import { QueryProvider } from '../../frontend/src/app/queryClient';
import { App } from './App';
import './index.css';

const rootElement = document.getElementById('root');

if (!rootElement) {
  throw new Error('Root element not found');
}

const appRoot = rootElement;

function showStartupError(error: unknown): void {
  const message =
    error instanceof Error
      ? `${error.name}: ${error.message}\n${error.stack ?? ''}`
      : String(error);
  const errorElement = document.createElement('pre');
  errorElement.id = 'startup-error';
  errorElement.style.cssText =
    'margin:24px;white-space:pre-wrap;color:#b91c1c;font:13px/1.5 monospace;';
  errorElement.textContent = `桌面客户端启动失败：\n${message}`;
  appRoot.replaceChildren(errorElement);
}

window.addEventListener('error', (event) => {
  showStartupError(event.error ?? event.message);
});
window.addEventListener('unhandledrejection', (event) => {
  showStartupError(event.reason);
});

appRoot.textContent = '正在启动 NL-RPA Desktop...';

try {
  createRoot(appRoot).render(
    <StrictMode>
      <BrowserRouter
        future={{
          v7_relativeSplatPath: true,
          v7_startTransition: true,
        }}
      >
        <QueryProvider>
          <App />
        </QueryProvider>
      </BrowserRouter>
    </StrictMode>,
  );
} catch (error) {
  showStartupError(error);
}
