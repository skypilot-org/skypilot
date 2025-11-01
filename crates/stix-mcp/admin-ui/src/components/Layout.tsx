import { ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';
import {
  LayoutDashboard,
  Users,
  Lock,
  FileText,
  Target,
  Settings,
  LogOut,
  Activity,
} from 'lucide-react';

interface LayoutProps {
  children: ReactNode;
  onLogout: () => void;
}

export default function Layout({ children, onLogout }: LayoutProps) {
  const location = useLocation();

  const navigation = [
    { name: 'Dashboard', href: '/', icon: LayoutDashboard },
    { name: 'Agents & API Keys', href: '/agents', icon: Users },
    { name: 'File Locks', href: '/locks', icon: Lock },
    { name: 'Activity Logs', href: '/logs', icon: FileText },
    { name: 'Work Targets', href: '/work-targets', icon: Target },
    { name: 'Configuration', href: '/config', icon: Settings },
  ];

  return (
    <div className="min-h-screen bg-slate-900 text-slate-100">
      {/* Sidebar */}
      <div className="fixed inset-y-0 left-0 w-64 bg-slate-800 border-r border-slate-700">
        <div className="flex flex-col h-full">
          {/* Logo */}
          <div className="flex items-center h-16 px-6 border-b border-slate-700">
            <Activity className="w-8 h-8 text-blue-500 mr-3" />
            <div>
              <h1 className="text-xl font-bold text-white">STIX MCP</h1>
              <p className="text-xs text-slate-400">Admin Dashboard</p>
            </div>
          </div>

          {/* Navigation */}
          <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
            {navigation.map((item) => {
              const Icon = item.icon;
              const isActive = location.pathname === item.href;
              return (
                <Link
                  key={item.name}
                  to={item.href}
                  className={`flex items-center px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                    isActive
                      ? 'bg-blue-600 text-white'
                      : 'text-slate-300 hover:bg-slate-700 hover:text-white'
                  }`}
                >
                  <Icon className="w-5 h-5 mr-3" />
                  {item.name}
                </Link>
              );
            })}
          </nav>

          {/* Logout */}
          <div className="p-3 border-t border-slate-700">
            <button
              onClick={onLogout}
              className="flex items-center w-full px-3 py-2 text-sm font-medium text-slate-300 hover:bg-slate-700 hover:text-white rounded-lg transition-colors"
            >
              <LogOut className="w-5 h-5 mr-3" />
              Logout
            </button>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="pl-64">
        <main className="min-h-screen p-8">
          {children}
        </main>
      </div>
    </div>
  );
}
