import { Settings } from 'lucide-react';
import { Navigate, Route, Routes } from 'react-router-dom';

import { api } from '../../frontend/src/api/axios';
import { Layout } from '../../frontend/src/components/Layout';
import { AppsPage } from '../../frontend/src/pages/AppsPage';
import { AdvancedToolsPage } from '../../frontend/src/pages/AdvancedToolsPage';
import { ChatPage } from '../../frontend/src/pages/ChatPage';
import { ConnectionsPage } from '../../frontend/src/pages/ConnectionsPage';
import { DashboardPage } from '../../frontend/src/pages/DashboardPage';
import { ExecutionDetail } from '../../frontend/src/pages/ExecutionDetail';
import { ExecutionsPage } from '../../frontend/src/pages/ExecutionsPage';
import { LoginPage } from '../../frontend/src/pages/LoginPage';
import { PendingPage } from '../../frontend/src/pages/PendingPage';
import { SettingsPage as ConnectorSettingsPage } from '../../frontend/src/pages/SettingsPage';
import { WorkflowEditor } from '../../frontend/src/pages/WorkflowEditor';
import { WorkflowsPage } from '../../frontend/src/pages/WorkflowsPage';
import { getBackendUrl } from './lib/backend';
import { SettingsPage as DesktopSettingsPage } from './pages/SettingsPage';

api.defaults.baseURL = getBackendUrl();

const desktopNavigationItems = [
  {
    label: '设置',
    to: '/desktop-settings',
    icon: Settings,
  },
];

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <Layout
            additionalNavigationItems={desktopNavigationItems}
          />
        }
      >
        <Route index element={<DashboardPage />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/apps" element={<AppsPage />} />
        <Route path="/workflows" element={<WorkflowsPage />} />
        <Route path="/workflows/:id" element={<WorkflowEditor />} />
        <Route path="/executions" element={<ExecutionsPage />} />
        <Route path="/executions/:id" element={<ExecutionDetail />} />
        <Route path="/pending" element={<PendingPage />} />
        <Route
          path="/connections"
          element={<ConnectionsPage />}
        />
        <Route
          path="/advanced/tools"
          element={<AdvancedToolsPage />}
        />
        <Route
          path="/settings"
          element={<ConnectorSettingsPage />}
        />
        <Route
          path="/desktop-settings"
          element={<DesktopSettingsPage />}
        />
        <Route path="*" element={<Navigate to="/chat" replace />} />
      </Route>
    </Routes>
  );
}

export default App;
