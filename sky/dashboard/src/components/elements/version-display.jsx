import React, { useState, useEffect } from 'react';
import { NonCapitalizedTooltip } from '@/components/utils';
import { apiClient } from '@/data/connectors/client';

export function VersionDisplay() {
  const [version, setVersion] = useState(null);
  const [commit, setCommit] = useState(null);

  const getVersion = async () => {
    const data = await apiClient.get('/api/health');
    if (!data.ok) {
      console.error(
        `API request /api/health failed with status ${data.status}`
      );
      return;
    }
    const healthData = await data.json();
    if (healthData.version) {
      setVersion(healthData.version);
    }
    if (healthData.commit) {
      setCommit(healthData.commit);
    }
  };

  useEffect(() => {
    getVersion();
  }, []);

  if (!version) return null;

  // Create tooltip text
  const tooltipText = commit
    ? `Commit: ${commit}`
    : 'Commit information not available';

  return (
    <NonCapitalizedTooltip
      content={tooltipText}
      className="text-sm text-muted-foreground"
    >
      <div className="text-sm text-gray-500 cursor-help border-b border-dotted border-gray-400 inline-block">
        Version: {version}
      </div>
    </NonCapitalizedTooltip>
  );
}
