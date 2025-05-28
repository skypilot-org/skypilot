import React, { useState, useEffect } from 'react';
import { ENDPOINT } from '@/data/connectors/constants';

export function VersionDisplay() {
  const [version, setVersion] = useState(null);

  useEffect(() => {
    fetch(`${ENDPOINT}/api/health`)
      .then((res) => res.json())
      .then((data) => {
        if (data.version) {
          setVersion(data.version);
        }
      })
      .catch((error) => {
        console.error('Error fetching version:', error);
      });
  }, []);

  if (!version) return null;

  return <div className="text-sm text-gray-500">Version: {version}</div>;
}
