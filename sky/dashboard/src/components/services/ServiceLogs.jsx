'use client';

import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { RotateCwIcon } from 'lucide-react';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useLogStreamer } from '@/hooks/useLogStreamer';
import { streamServiceLogs } from '@/data/connectors/services';

// Log targets for service logs
const LOG_TARGETS = [
  { value: 'CONTROLLER', label: 'Controller' },
  { value: 'LOAD_BALANCER', label: 'Load Balancer' },
  { value: 'REPLICA', label: 'Replica' },
];

/**
 * Service Logs component with target selector and optional replica picker.
 *
 * Uses the shared useLogStreamer hook to stream logs from the SkyPilot API
 * server for a given service, target (controller / load balancer / replica),
 * and optional replica ID.
 *
 * @param {Object} props
 * @param {string} props.serviceName - The name of the service
 * @param {Array} props.replicas - Array of replica_info objects
 * @param {number} props.refreshTrigger - Increment to trigger log refresh
 */
export function ServiceLogs({
  serviceName,
  replicas = [],
  refreshTrigger = 0,
}) {
  const [target, setTarget] = useState('CONTROLLER');
  const [replicaId, setReplicaId] = useState(null);
  const [logsRefreshToken, setLogsRefreshToken] = useState(0);
  const logsEndRef = useRef(null);
  const logsContainerRef = useRef(null);

  // When target changes to REPLICA, default to the first replica if available
  useEffect(() => {
    if (target === 'REPLICA' && replicas.length > 0 && replicaId === null) {
      setReplicaId(replicas[0].replica_id);
    }
  }, [target, replicas, replicaId]);

  // Reset replica selection when switching away from REPLICA target
  useEffect(() => {
    if (target !== 'REPLICA') {
      setReplicaId(null);
    }
  }, [target]);

  const streamArgs = useMemo(
    () => ({
      serviceName,
      target,
      replicaId: target === 'REPLICA' ? replicaId : null,
    }),
    [serviceName, target, replicaId]
  );

  const handleLogsError = useCallback((error) => {
    console.error('Error streaming service logs:', error);
  }, []);

  const {
    lines: logLines,
    isLoading,
    hasReceivedFirstChunk,
  } = useLogStreamer({
    streamFn: streamServiceLogs,
    streamArgs,
    enabled: Boolean(serviceName) &&
      (target !== 'REPLICA' || replicaId !== null),
    refreshTrigger: logsRefreshToken + refreshTrigger,
    onError: handleLogsError,
  });

  // Auto-scroll to bottom on new log lines
  useEffect(() => {
    if (logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logLines]);

  const handleRefreshLogs = () => {
    setLogsRefreshToken((token) => token + 1);
  };

  return (
    <div className="mt-6">
      <div className="rounded-lg border bg-card text-card-foreground shadow-sm">
        <div className="flex items-center justify-between px-4 pt-4">
          <h3 className="text-lg font-semibold">Service Logs</h3>
          <button
            onClick={handleRefreshLogs}
            className="p-1.5 rounded-md text-gray-500 hover:text-gray-700 hover:bg-gray-100 transition-colors"
            aria-label="Refresh logs"
          >
            <RotateCwIcon
              className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`}
            />
          </button>
        </div>
        <div className="p-5">
          {/* Target and Replica Selectors */}
          <div className="mb-4 flex flex-wrap gap-4 items-center">
            <div className="flex items-center gap-2">
              <label className="text-sm font-medium text-gray-700 whitespace-nowrap">
                Target:
              </label>
              <Select value={target} onValueChange={setTarget}>
                <SelectTrigger className="w-[180px]">
                  <SelectValue placeholder="Select target" />
                </SelectTrigger>
                <SelectContent>
                  {LOG_TARGETS.map((t) => (
                    <SelectItem key={t.value} value={t.value}>
                      {t.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {target === 'REPLICA' && (
              <div className="flex items-center gap-2">
                <label className="text-sm font-medium text-gray-700 whitespace-nowrap">
                  Replica:
                </label>
                <Select
                  value={replicaId !== null ? String(replicaId) : ''}
                  onValueChange={(val) => setReplicaId(Number(val))}
                >
                  <SelectTrigger className="w-[140px]">
                    <SelectValue placeholder="Select replica" />
                  </SelectTrigger>
                  <SelectContent>
                    {replicas.map((r) => (
                      <SelectItem
                        key={r.replica_id}
                        value={String(r.replica_id)}
                      >
                        Replica {r.replica_id}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
          </div>

          {/* Log Display */}
          <div
            ref={logsContainerRef}
            className="bg-gray-900 text-green-400 rounded-md p-4 font-mono text-xs overflow-auto max-h-[600px]"
          >
            {isLoading && !hasReceivedFirstChunk && (
              <div className="text-gray-500">Loading logs...</div>
            )}
            {!isLoading && logLines.length === 0 && (
              <div className="text-gray-500">No logs available.</div>
            )}
            {logLines.map((line, idx) => (
              <div key={idx} className="whitespace-pre-wrap break-all">
                {line}
              </div>
            ))}
            <div ref={logsEndRef} />
          </div>
        </div>
      </div>
    </div>
  );
}

export default ServiceLogs;
