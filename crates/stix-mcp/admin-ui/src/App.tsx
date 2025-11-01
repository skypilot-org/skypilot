import { useState } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { apiClient } from './lib/api';
import Layout from './components/Layout';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import Agents from './pages/Agents';
import Locks from './pages/Locks';
import Logs from './pages/Logs';
import WorkTargets from './pages/WorkTargets';
import Configuration from './pages/Configuration';

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(() => {
    return !!apiClient.getAdminToken();
  });

  const handleLogin = () => {
    setIsAuthenticated(true);
  };

  const handleLogout = () => {
    apiClient.clearAdminToken();
    setIsAuthenticated(false);
  };

  if (!isAuthenticated) {
    return <Login onLogin={handleLogin} />;
  }

  return (
    <Layout onLogout={handleLogout}>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/agents" element={<Agents />} />
        <Route path="/locks" element={<Locks />} />
        <Route path="/logs" element={<Logs />} />
        <Route path="/work-targets" element={<WorkTargets />} />
        <Route path="/config" element={<Configuration />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}

export default App;
