import { Navigate, Route, Routes, useParams } from 'react-router-dom';

import { Layout } from './components/Layout';
import { lazyNamed, LazyPage } from './lib/lazy-page';
import { LoginPage } from './pages/LoginPage';

const DashboardPage = lazyNamed(
  () => import('./pages/DashboardPage'),
  'DashboardPage',
);
const WorkflowsPage = lazyNamed(
  () => import('./pages/WorkflowsPage'),
  'WorkflowsPage',
);
const WorkflowEditor = lazyNamed(
  () => import('./pages/WorkflowEditor'),
  'WorkflowEditor',
);
const ExecutionsPage = lazyNamed(
  () => import('./pages/ExecutionsPage'),
  'ExecutionsPage',
);
const ExecutionDetail = lazyNamed(
  () => import('./pages/ExecutionDetail'),
  'ExecutionDetail',
);
const PendingPage = lazyNamed(
  () => import('./pages/PendingPage'),
  'PendingPage',
);
const ConnectionsPage = lazyNamed(
  () => import('./pages/ConnectionsPage'),
  'ConnectionsPage',
);
const SettingsPage = lazyNamed(
  () => import('./pages/SettingsPage'),
  'SettingsPage',
);
const AdvancedToolsPage = lazyNamed(
  () => import('./pages/AdvancedToolsPage'),
  'AdvancedToolsPage',
);
const ChatPage = lazyNamed(
  () => import('./pages/ChatPage'),
  'ChatPage',
);
const AppsPage = lazyNamed(
  () => import('./pages/AppsPage'),
  'AppsPage',
);

function LegacyWorkflowRedirect() {
  const { id } = useParams();
  return (
    <Navigate to={`/workflows/${String(id)}/studio`} replace />
  );
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<Layout />}>
        <Route
          index
          element={<LazyPage><DashboardPage /></LazyPage>}
        />
        <Route
          path="/workflows"
          element={<LazyPage><WorkflowsPage /></LazyPage>}
        />
        <Route path="/workflows/:id" element={<LegacyWorkflowRedirect />} />
        <Route
          path="/workflows/:id/studio"
          element={<LazyPage><WorkflowEditor /></LazyPage>}
        />
        <Route
          path="/executions"
          element={<LazyPage><ExecutionsPage /></LazyPage>}
        />
        <Route
          path="/executions/:id"
          element={<LazyPage><ExecutionDetail /></LazyPage>}
        />
        <Route
          path="/pending"
          element={<LazyPage><PendingPage /></LazyPage>}
        />
        <Route
          path="/connections"
          element={<LazyPage><ConnectionsPage /></LazyPage>}
        />
        <Route
          path="/settings"
          element={<LazyPage><SettingsPage /></LazyPage>}
        />
        <Route
          path="/advanced/tools"
          element={<LazyPage><AdvancedToolsPage /></LazyPage>}
        />
        <Route
          path="/advanced/chat"
          element={<LazyPage><ChatPage /></LazyPage>}
        />
        <Route path="/chat" element={<Navigate to="/advanced/chat" replace />} />
        <Route
          path="/apps"
          element={<LazyPage><AppsPage /></LazyPage>}
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

export default App;
