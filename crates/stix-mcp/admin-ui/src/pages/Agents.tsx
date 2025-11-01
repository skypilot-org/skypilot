import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient, Agent } from '../lib/api';
import { Plus, Trash2, RefreshCw, Copy, Check, Shield, Clock } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';

const AVAILABLE_PERMISSIONS = [
  'ReadFiles',
  'WriteFiles',
  'LockFiles',
  'UpdateProgress',
  'CreateTasks',
  'SearchCodebase',
  'ViewLogs',
];

export default function Agents() {
  const queryClient = useQueryClient();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [copiedToken, setCopiedToken] = useState<string | null>(null);
  const [newAgentToken, setNewAgentToken] = useState<string | null>(null);

  const { data: agents, isLoading } = useQuery<Agent[]>({
    queryKey: ['agents'],
    queryFn: () => apiClient.listAgents(),
  });

  const createMutation = useMutation({
    mutationFn: (data: { name: string; permissions: string[] }) =>
      apiClient.createAgent(data.name, data.permissions),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['agents'] });
      setNewAgentToken(data.token);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (agentId: string) => apiClient.deleteAgent(agentId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agents'] });
    },
  });

  const regenerateMutation = useMutation({
    mutationFn: (agentId: string) => apiClient.regenerateToken(agentId),
    onSuccess: (data) => {
      setNewAgentToken(data.token);
      queryClient.invalidateQueries({ queryKey: ['agents'] });
    },
  });

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedToken(text);
    setTimeout(() => setCopiedToken(null), 2000);
  };

  const handleCreateAgent = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const formData = new FormData(e.currentTarget);
    const name = formData.get('name') as string;
    const permissions = formData.getAll('permissions') as string[];
    
    createMutation.mutate({ name, permissions });
    e.currentTarget.reset();
  };

  if (isLoading) {
    return <div className="text-slate-400">Loading agents...</div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-white mb-2">Agents & API Keys</h1>
          <p className="text-slate-400">Manage KI-Agents and their API tokens</p>
        </div>
        <button
          onClick={() => setShowCreateModal(true)}
          className="flex items-center px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors"
        >
          <Plus className="w-5 h-5 mr-2" />
          Create Agent
        </button>
      </div>

      {/* Token Display Modal */}
      {newAgentToken && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-slate-800 rounded-lg p-6 max-w-lg w-full border border-slate-700">
            <h2 className="text-xl font-bold text-white mb-4">Agent Created Successfully!</h2>
            <p className="text-slate-400 mb-4">
              Save this token securely. You won't be able to see it again!
            </p>
            <div className="bg-slate-900 p-4 rounded-lg border border-slate-700 mb-4">
              <code className="text-green-400 break-all">{newAgentToken}</code>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => copyToClipboard(newAgentToken)}
                className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors"
              >
                {copiedToken === newAgentToken ? (
                  <>
                    <Check className="w-4 h-4 inline mr-2" />
                    Copied!
                  </>
                ) : (
                  <>
                    <Copy className="w-4 h-4 inline mr-2" />
                    Copy Token
                  </>
                )}
              </button>
              <button
                onClick={() => setNewAgentToken(null)}
                className="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-white rounded-lg transition-colors"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Create Agent Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-slate-800 rounded-lg p-6 max-w-lg w-full border border-slate-700">
            <h2 className="text-xl font-bold text-white mb-4">Create New Agent</h2>
            <form onSubmit={handleCreateAgent} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-2">
                  Agent Name
                </label>
                <input
                  name="name"
                  type="text"
                  required
                  placeholder="e.g., Agent-A, PythonBot"
                  className="w-full px-4 py-2 bg-slate-900 border border-slate-600 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-300 mb-2">
                  Permissions
                </label>
                <div className="space-y-2 max-h-48 overflow-y-auto">
                  {AVAILABLE_PERMISSIONS.map((permission) => (
                    <label
                      key={permission}
                      className="flex items-center p-2 hover:bg-slate-700 rounded cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        name="permissions"
                        value={permission}
                        className="mr-3 w-4 h-4"
                      />
                      <span className="text-slate-300">{permission}</span>
                    </label>
                  ))}
                </div>
              </div>

              <div className="flex gap-2 pt-4">
                <button
                  type="submit"
                  disabled={createMutation.isPending}
                  className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-slate-700 text-white rounded-lg transition-colors"
                >
                  {createMutation.isPending ? 'Creating...' : 'Create Agent'}
                </button>
                <button
                  type="button"
                  onClick={() => setShowCreateModal(false)}
                  className="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-white rounded-lg transition-colors"
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Agents List */}
      <div className="grid gap-4">
        {agents?.map((agent) => (
          <div
            key={agent.id}
            className="bg-slate-800 rounded-lg p-6 border border-slate-700"
          >
            <div className="flex items-start justify-between mb-4">
              <div>
                <h3 className="text-xl font-bold text-white">{agent.name}</h3>
                <p className="text-sm text-slate-400 font-mono mt-1">{agent.id}</p>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => regenerateMutation.mutate(agent.id)}
                  disabled={regenerateMutation.isPending}
                  className="p-2 bg-yellow-600 hover:bg-yellow-700 disabled:bg-slate-700 text-white rounded-lg transition-colors"
                  title="Regenerate Token"
                >
                  <RefreshCw className="w-4 h-4" />
                </button>
                <button
                  onClick={() => {
                    if (confirm(`Delete agent ${agent.name}?`)) {
                      deleteMutation.mutate(agent.id);
                    }
                  }}
                  disabled={deleteMutation.isPending}
                  className="p-2 bg-red-600 hover:bg-red-700 disabled:bg-slate-700 text-white rounded-lg transition-colors"
                  title="Delete Agent"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4 mb-4">
              <div className="flex items-center text-sm">
                <Clock className="w-4 h-4 text-slate-400 mr-2" />
                <span className="text-slate-400">Created:</span>
                <span className="text-slate-300 ml-2">
                  {formatDistanceToNow(new Date(agent.created_at), { addSuffix: true })}
                </span>
              </div>
              <div className="flex items-center text-sm">
                <Clock className="w-4 h-4 text-slate-400 mr-2" />
                <span className="text-slate-400">Last Active:</span>
                <span className="text-slate-300 ml-2">
                  {formatDistanceToNow(new Date(agent.last_active), { addSuffix: true })}
                </span>
              </div>
            </div>

            <div>
              <div className="flex items-center mb-2">
                <Shield className="w-4 h-4 text-slate-400 mr-2" />
                <span className="text-sm font-medium text-slate-300">Permissions:</span>
              </div>
              <div className="flex flex-wrap gap-2">
                {agent.permissions.map((permission) => (
                  <span
                    key={permission}
                    className="px-3 py-1 bg-blue-900/50 text-blue-300 text-xs rounded-full border border-blue-700"
                  >
                    {permission}
                  </span>
                ))}
              </div>
            </div>
          </div>
        ))}

        {(!agents || agents.length === 0) && (
          <div className="text-center py-12 bg-slate-800 rounded-lg border border-slate-700">
            <p className="text-slate-400">No agents created yet</p>
            <button
              onClick={() => setShowCreateModal(true)}
              className="mt-4 text-blue-400 hover:text-blue-300"
            >
              Create your first agent
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
