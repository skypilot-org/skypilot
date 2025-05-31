import React, { useState, useEffect } from 'react';
import { ENDPOINT } from '@/data/connectors/constants';
import { NonCapitalizedTooltip } from '@/components/utils';

export function VersionDisplay() {
  const [version, setVersion] = useState(null);
  const [commit, setCommit] = useState(null);

  useEffect(() => {
    fetch(`${ENDPOINT}/api/health`)
      .then((res) => res.json())
      .then((data) => {
        console.log('Health API response:', data); // Debug logging
        if (data.version) {
          setVersion(data.version);
        }
        if (data.commit) {
          setCommit(data.commit);
          console.log('Commit found:', data.commit); // Debug logging
        } else {
          console.log('No commit found in response'); // Debug logging
        }
      })
      .catch((error) => {
        console.error('Error fetching version:', error);
      });
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
