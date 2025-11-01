import { useQuery } from '@tanstack/react-query';
import { apiClient, Stats } from '../lib/api';
import { Users, Lock, FileText, Activity, TrendingUp } from 'lucide-react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';

export default function Dashboard() {
  const { data: stats, isLoading } = useQuery<Stats>({
    queryKey: ['stats'],
    queryFn: () => apiClient.getStats(),
    refetchInterval: 5000,
  });

  const statCards = [
    {
      title: 'Total Agents',
      value: stats?.total_agents || 0,
      icon: Users,
      color: 'bg-blue-500',
    },
    {
      title: 'Active Agents',
      value: stats?.active_agents || 0,
      icon: Activity,
      color: 'bg-green-500',
    },
    {
      title: 'Active Locks',
      value: stats?.total_locks || 0,
      icon: Lock,
      color: 'bg-yellow-500',
    },
    {
      title: 'Requests Today',
      value: stats?.requests_today || 0,
      icon: TrendingUp,
      color: 'bg-purple-500',
    },
  ];

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-slate-400">Loading dashboard...</div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-white mb-2">Dashboard</h1>
        <p className="text-slate-400">Overview of your STIX MCP server</p>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        {statCards.map((stat) => {
          const Icon = stat.icon;
          return (
            <div
              key={stat.title}
              className="bg-slate-800 rounded-lg p-6 border border-slate-700"
            >
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-slate-400 text-sm font-medium">{stat.title}</p>
                  <p className="text-3xl font-bold text-white mt-2">{stat.value}</p>
                </div>
                <div className={`${stat.color} p-3 rounded-lg`}>
                  <Icon className="w-6 h-6 text-white" />
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Activity Chart */}
      <div className="bg-slate-800 rounded-lg p-6 border border-slate-700">
        <h2 className="text-xl font-bold text-white mb-4">Activity Overview</h2>
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={[
                { time: '00:00', requests: 12 },
                { time: '04:00', requests: 8 },
                { time: '08:00', requests: 45 },
                { time: '12:00', requests: 78 },
                { time: '16:00', requests: 62 },
                { time: '20:00', requests: 34 },
              ]}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
              <XAxis dataKey="time" stroke="#94a3b8" />
              <YAxis stroke="#94a3b8" />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#1e293b',
                  border: '1px solid #334155',
                  borderRadius: '0.5rem',
                }}
              />
              <Line
                type="monotone"
                dataKey="requests"
                stroke="#3b82f6"
                strokeWidth={2}
                dot={{ fill: '#3b82f6' }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Recent Activity */}
      <div className="bg-slate-800 rounded-lg p-6 border border-slate-700">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-bold text-white">Recent Activity</h2>
          <FileText className="w-5 h-5 text-slate-400" />
        </div>
        <div className="space-y-3">
          <div className="flex items-center justify-between py-2 border-b border-slate-700">
            <div>
              <p className="text-white font-medium">Agent-A locked file</p>
              <p className="text-sm text-slate-400">src/main.rs</p>
            </div>
            <span className="text-xs text-slate-400">2 mins ago</span>
          </div>
          <div className="flex items-center justify-between py-2 border-b border-slate-700">
            <div>
              <p className="text-white font-medium">Agent-B updated progress</p>
              <p className="text-sm text-slate-400">Database Integration</p>
            </div>
            <span className="text-xs text-slate-400">5 mins ago</span>
          </div>
          <div className="flex items-center justify-between py-2">
            <div>
              <p className="text-white font-medium">New task created</p>
              <p className="text-sm text-slate-400">Implement API endpoint</p>
            </div>
            <span className="text-xs text-slate-400">12 mins ago</span>
          </div>
        </div>
      </div>
    </div>
  );
}
