import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient, WorkTarget } from '../lib/api';
import { Plus, Trash2, Edit, Target } from 'lucide-react';

export default function WorkTargets() {
  const queryClient = useQueryClient();
  const [showCreateModal, setShowCreateModal] = useState(false);

  const { data: targets, isLoading } = useQuery<WorkTarget[]>({
    queryKey: ['work-targets'],
    queryFn: () => apiClient.listWorkTargets(),
  });

  const createMutation = useMutation({
    mutationFn: (data: Partial<WorkTarget>) => apiClient.createWorkTarget(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['work-targets'] });
      setShowCreateModal(false);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => apiClient.deleteWorkTarget(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['work-targets'] });
    },
  });

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const formData = new FormData(e.currentTarget);
    createMutation.mutate({
      title: formData.get('title') as string,
      description: formData.get('description') as string,
      priority: formData.get('priority') as any,
      assigned_to: formData.get('assigned_to') as string || null,
    });
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-white mb-2">Work Targets</h1>
          <p className="text-slate-400">Define tasks and goals for AI agents</p>
        </div>
        <button
          onClick={() => setShowCreateModal(true)}
          className="flex items-center px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg"
        >
          <Plus className="w-5 h-5 mr-2" />
          Create Target
        </button>
      </div>

      {showCreateModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-slate-800 rounded-lg p-6 max-w-lg w-full border border-slate-700">
            <h2 className="text-xl font-bold text-white mb-4">Create Work Target</h2>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-2">Title</label>
                <input name="title" required className="w-full px-4 py-2 bg-slate-900 border border-slate-600 rounded-lg text-white" />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-2">Description</label>
                <textarea name="description" required rows={4} className="w-full px-4 py-2 bg-slate-900 border border-slate-600 rounded-lg text-white" />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-2">Priority</label>
                <select name="priority" className="w-full px-4 py-2 bg-slate-900 border border-slate-600 rounded-lg text-white">
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-2">Assign to</label>
                <input name="assigned_to" placeholder="Agent name (optional)" className="w-full px-4 py-2 bg-slate-900 border border-slate-600 rounded-lg text-white" />
              </div>
              <div className="flex gap-2">
                <button type="submit" className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg">
                  Create
                </button>
                <button type="button" onClick={() => setShowCreateModal(false)} className="px-4 py-2 bg-slate-700 text-white rounded-lg">
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      <div className="grid gap-4">
        {targets?.map((target) => (
          <div key={target.id} className="bg-slate-800 rounded-lg p-6 border border-slate-700">
            <div className="flex justify-between items-start">
              <div className="flex-1">
                <h3 className="text-xl font-bold text-white mb-2">{target.title}</h3>
                <p className="text-slate-400 mb-4">{target.description}</p>
                <div className="flex gap-2">
                  <span className={`px-3 py-1 rounded-full text-xs ${
                    target.priority === 'high' ? 'bg-red-900/50 text-red-300' :
                    target.priority === 'medium' ? 'bg-yellow-900/50 text-yellow-300' :
                    'bg-green-900/50 text-green-300'
                  }`}>
                    {target.priority}
                  </span>
                  {target.assigned_to && (
                    <span className="px-3 py-1 bg-blue-900/50 text-blue-300 rounded-full text-xs">
                      {target.assigned_to}
                    </span>
                  )}
                </div>
              </div>
              <button
                onClick={() => deleteMutation.mutate(target.id)}
                className="p-2 bg-red-600 hover:bg-red-700 text-white rounded-lg"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          </div>
        ))}
        {(!targets || targets.length === 0) && (
          <div className="text-center py-12 bg-slate-800 rounded-lg border border-slate-700">
            <Target className="w-12 h-12 text-slate-600 mx-auto mb-4" />
            <p className="text-slate-400">No work targets defined</p>
          </div>
        )}
      </div>
    </div>
  );
}
