'use client';

import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import { getClusters } from '@/data/connectors/clusters';
import { Card, CardContent, CardHeader, CardTitle, CardFooter } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { CircularProgress } from '@mui/material';
import Link from 'next/link';

export function Workspaces() {
  const [workspaceData, setWorkspaceData] = useState([]);
  const [loading, setLoading] = useState(true);

  const router = useRouter();

  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      try {
        const clusters = await getClusters();
        const counts = {};
        clusters.forEach(cluster => {
          const workspaceName = cluster.workspace || 'default';
          counts[workspaceName] = (counts[workspaceName] || 0) + 1;
        });
        const data = Object.entries(counts).map(([name, clusterCount]) => ({
          name,
          clusterCount,
        })).sort((a, b) => a.name.localeCompare(b.name)); // Sort by workspace name
        setWorkspaceData(data);
      } catch (error) {
        console.error('Error fetching workspace data:', error);
        // Optionally set an error state here to display to the user
      }
      setLoading(false);
    };
    fetchData();
  }, []);

  if (loading) {
    return (
      <div className="flex justify-center items-center h-64">
        <CircularProgress />
        <span className="ml-2 text-gray-500">Loading workspaces...</span>
      </div>
    );
  }

  if (workspaceData.length === 0) {
    return (
      <div className="text-center py-10">
        <p className="text-lg text-gray-600">No workspaces found.</p>
        <p className="text-sm text-gray-500 mt-2">
          Create a cluster to see its workspace here.
        </p>
      </div>
    );
  }

  const handleEditWorkspace = (workspaceName) => {
    // Navigate to the dynamic workspace page
    router.push(`/workspaces/${encodeURIComponent(workspaceName)}`);
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-4 h-5">
        <div className="text-base flex items-center">
          <span className="text-sky-blue leading-none">Workspaces</span>
        </div>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {workspaceData.map(ws => (
          <Card key={ws.name}>
            <CardHeader>
              <CardTitle className="text-lg font-semibold">{ws.name}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-gray-600">
                Clusters: <span className="font-medium">{ws.clusterCount}</span>
              </p>
            </CardContent>
            <CardFooter className="flex justify-end">
              <Button 
                variant="outline" 
                size="sm"
                onClick={() => handleEditWorkspace(ws.name)}
              >
                Details
              </Button>
            </CardFooter>
          </Card>
        ))}
      </div>
    </div>
  );
} 
