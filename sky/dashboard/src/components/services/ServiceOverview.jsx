'use client';

import React, { useState } from 'react';
import { Card } from '@/components/ui/card';
import { StatusBadge } from '@/components/elements/StatusBadge';
import { CopyIcon, CheckIcon } from 'lucide-react';

function formatUptime(uptimeTimestamp) {
  if (!uptimeTimestamp || uptimeTimestamp <= 0) return '-';
  // uptime is an absolute Unix timestamp (seconds since epoch)
  const durationSecs = Math.floor(Date.now() / 1000 - uptimeTimestamp);
  if (durationSecs <= 0) return '-';
  const days = Math.floor(durationSecs / 86400);
  const hours = Math.floor((durationSecs % 86400) / 3600);
  const mins = Math.floor((durationSecs % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h ${mins}m`;
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

export function ServiceOverview({ serviceData }) {
  const [copiedEndpoint, setCopiedEndpoint] = useState(false);

  if (!serviceData) return null;

  const replicas = serviceData.replica_info || [];
  const readyCount = replicas.filter((r) => r.status === 'READY').length;
  const totalCount = replicas.length;

  const copyEndpoint = async () => {
    if (!serviceData.endpoint) return;
    try {
      await navigator.clipboard.writeText(serviceData.endpoint);
      setCopiedEndpoint(true);
      setTimeout(() => setCopiedEndpoint(false), 2000);
    } catch (err) {
      console.error('Failed to copy endpoint:', err);
    }
  };

  return (
    <div className="mb-6">
      <Card>
        <div className="flex items-center justify-between px-4 pt-4">
          <h3 className="text-lg font-semibold">Details</h3>
        </div>
        <div className="p-4">
          <div className="grid grid-cols-2 gap-6">
            {/* Name */}
            <div>
              <div className="text-gray-600 font-medium text-base">Name</div>
              <div className="text-base mt-1">
                {serviceData.name || 'N/A'}
              </div>
            </div>

            {/* Status */}
            <div>
              <div className="text-gray-600 font-medium text-base">Status</div>
              <div className="text-base mt-1">
                <StatusBadge status={serviceData.status} />
              </div>
            </div>

            {/* Resources */}
            <div>
              <div className="text-gray-600 font-medium text-base">
                Resources
              </div>
              <div className="text-base mt-1">
                {serviceData.requested_resources_str || '-'}
              </div>
            </div>

            {/* Endpoint */}
            <div>
              <div className="text-gray-600 font-medium text-base">
                Endpoint
              </div>
              <div className="text-base mt-1">
                {serviceData.endpoint ? (
                  <div className="flex items-center gap-1">
                    <span className="text-sm text-gray-600 truncate max-w-[300px]">
                      {serviceData.endpoint}
                    </span>
                    <button
                      onClick={copyEndpoint}
                      className="text-gray-400 hover:text-gray-600 p-0.5"
                      title="Copy endpoint"
                    >
                      {copiedEndpoint ? (
                        <CheckIcon className="w-3.5 h-3.5 text-green-500" />
                      ) : (
                        <CopyIcon className="w-3.5 h-3.5" />
                      )}
                    </button>
                    {serviceData.service_type && (
                      <span className={`ml-1 px-1.5 py-0.5 text-xs font-medium rounded ${
                        serviceData.service_type === 'LoadBalancer'
                          ? 'bg-green-100 text-green-700'
                          : serviceData.service_type === 'NodePort'
                          ? 'bg-yellow-100 text-yellow-700'
                          : 'bg-gray-100 text-gray-500'
                      }`}>
                        {serviceData.service_type === 'ClusterIP' ? 'ClusterIP (internal)' : serviceData.service_type}
                      </span>
                    )}
                  </div>
                ) : serviceData.service_type === 'LoadBalancer' ? (
                  <span className="text-amber-600 text-sm">Pending external IP...</span>
                ) : (
                  <span className="text-gray-400">-</span>
                )}
              </div>
            </div>

            {/* Uptime */}
            <div>
              <div className="text-gray-600 font-medium text-base">Uptime</div>
              <div className="text-base mt-1">
                {formatUptime(serviceData.uptime)}
              </div>
            </div>

            {/* Replicas */}
            <div>
              <div className="text-gray-600 font-medium text-base">
                Replicas
              </div>
              <div className="text-base mt-1">
                {readyCount}/{totalCount} ready
              </div>
            </div>

            {/* Autoscaling Policy */}
            <div>
              <div className="text-gray-600 font-medium text-base">
                Autoscaling Policy
              </div>
              <div className="text-base mt-1">
                {serviceData.policy || '-'}
              </div>
            </div>

            {/* Load Balancing Policy */}
            <div>
              <div className="text-gray-600 font-medium text-base">
                Load Balancing Policy
              </div>
              <div className="text-base mt-1">
                {serviceData.load_balancing_policy || '-'}
              </div>
            </div>

            {/* TLS */}
            <div>
              <div className="text-gray-600 font-medium text-base">TLS</div>
              <div className="text-base mt-1">
                {serviceData.tls_encrypted ? (
                  <span className="inline-flex items-center px-2 py-1 rounded-full text-sm bg-green-50 text-green-700">
                    Yes
                  </span>
                ) : (
                  <span className="inline-flex items-center px-2 py-1 rounded-full text-sm bg-gray-100 text-gray-800">
                    No
                  </span>
                )}
              </div>
            </div>

            {/* Controller Job ID */}
            <div>
              <div className="text-gray-600 font-medium text-base">
                Controller Job ID
              </div>
              <div className="text-base mt-1">
                {serviceData.controller_job_id != null
                  ? serviceData.controller_job_id
                  : '-'}
              </div>
            </div>
          </div>
        </div>
      </Card>
    </div>
  );
}
