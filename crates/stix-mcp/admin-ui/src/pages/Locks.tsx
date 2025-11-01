import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient, FileLock } from '../lib/api';
import { Lock, Unlock, Clock, User } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';

export default function Locks() {
  const queryClient = useQueryClient();

  const { data: locks, isLoading } = useQuery<FileLock[]>({
    queryKey: ['locks'],
    queryFn: () => apiClient.listLocks(),
    refetchInterval: 5000,
  });

  const unlockMutation = useMutation({
    mutationFn: (filePath: string) => apiClient.forceUnlock(filePath),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['locks'] });
    },
  });

  if (isLoading) {
    return <div className="text-slate-400">Loading locks...</div>;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-white mb-2">File Locks</h1>
        <p className="text-slate-400">Manage active file locks across all agents</p>
      </div>

      <div className="grid gap-4">
        {locks?.map((lock, index) => (
          <div
            key={index}
            className="bg-slate-800 rounded-lg p-6 border border-slate-700"
          >
            <div className="flex items-start justify-between">
              <div className="flex-1">
                <div className="flex items-center mb-2">
                  <Lock className="w-5 h-5 text-yellow-500 mr-2" />
                  <h3 className="text-lg font-semibold text-white">{lock.file_path}</h3>
                </div>
                
                <div className="space-y-2">
                  <div className="flex items-center text-sm">
                    <User className="w-4 h-4 text-slate-400 mr-2" />
                    <span className="text-slate-400">Owner:</span>
                    <span className="text-slate-300 ml-2">{lock.owner_name}</span>
                    <span className="text-slate-500 ml-2 font-mono text-xs">({lock.owner_id})</span>
                  </div>
                  
                  <div className="flex items-center text-sm">
                    <Clock className="w-4 h-4 text-slate-400 mr-2" />
                    <span className="text-slate-400">Locked:</span>
                    <span className="text-slate-300 ml-2">
                      {formatDistanceToNow(new Date(lock.locked_at), { addSuffix: true })}
                    </span>
                  </div>
                  
                  {lock.expires_at && (
                    <div className="flex items-center text-sm">
                      <Clock className="w-4 h-4 text-slate-400 mr-2" />
                      <span className="text-slate-400">Expires:</span>
                      <span className="text-slate-300 ml-2">
                        {formatDistanceToNow(new Date(lock.expires_at), { addSuffix: true })}
                      </span>
                    </div>
                  )}
                </div>
              </div>
              
              <button
                onClick={() => {
                  if (confirm(`Force unlock ${lock.file_path}?`)) {
                    unlockMutation.mutate(lock.file_path);
                  }
                }}
                disabled={unlockMutation.isPending}
                className="ml-4 flex items-center px-4 py-2 bg-red-600 hover:bg-red-700 disabled:bg-slate-700 text-white rounded-lg transition-colors"
              >
                <Unlock className="w-4 h-4 mr-2" />
                Force Unlock
              </button>
            </div>
          </div>
        ))}

        {(!locks || locks.length === 0) && (
          <div className="text-center py-12 bg-slate-800 rounded-lg border border-slate-700">
            <Lock className="w-12 h-12 text-slate-600 mx-auto mb-4" />
            <p className="text-slate-400">No active file locks</p>
          </div>
        )}
      </div>
    </div>
  );
}
