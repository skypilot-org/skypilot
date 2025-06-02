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
        if (data.version) {
          setVersion(data.version);
        }
        if (data.commit) {
          setCommit(data.commit);
        }
      })
      .catch((error) => {
        console.error(
          'Error fetching API server version and commit hash:',
          error
        );
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
