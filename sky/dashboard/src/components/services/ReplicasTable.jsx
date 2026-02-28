'use client';

import React from 'react';
import {
  Table,
  TableHeader,
  TableRow,
  TableHead,
  TableBody,
  TableCell,
} from '@/components/ui/table';
import { Card } from '@/components/ui/card';
import { StatusBadge } from '@/components/elements/StatusBadge';
import { formatDistance } from 'date-fns';

function formatLaunchedAt(timestamp) {
  if (!timestamp) return 'N/A';
  try {
    // Handle both Unix timestamps (seconds) and millisecond timestamps
    const date =
      timestamp > 1e12 ? new Date(timestamp) : new Date(timestamp * 1000);
    const now = new Date();
    const relativeTime = formatDistance(date, now, { addSuffix: true });

    // Shorten common patterns (e.g., "about 2 hours ago" -> "2h ago")
    return shortenRelativeTime(relativeTime);
  } catch {
    return 'Invalid Date';
  }
}

function shortenRelativeTime(timeString) {
  if (!timeString || typeof timeString !== 'string') return timeString;

  if (timeString === 'less than a minute ago') return 'Less than 1m ago';

  const aboutMatch = timeString.match(/^about\s+(\d+)\s+(\w+)\s+ago$/i);
  if (aboutMatch) {
    const num = aboutMatch[1];
    const unit = aboutMatch[2];
    const unitMap = {
      second: 's',
      seconds: 's',
      minute: 'm',
      minutes: 'm',
      hour: 'h',
      hours: 'h',
      day: 'd',
      days: 'd',
    };
    if (unitMap[unit]) return `${num}${unitMap[unit]} ago`;
  }

  const singleUnitMatch = timeString.match(/^a[n]?\s+(\w+)\s+ago$/i);
  if (singleUnitMatch) {
    const unit = singleUnitMatch[1];
    const unitMap = {
      second: 's',
      minute: 'm',
      hour: 'h',
      day: 'd',
      month: 'mo',
      year: 'yr',
    };
    if (unitMap[unit]) return `1${unitMap[unit]} ago`;
  }

  const multiUnitMatch = timeString.match(/^(\d+)\s+(\w+)\s+ago$/i);
  if (multiUnitMatch) {
    const num = multiUnitMatch[1];
    const unit = multiUnitMatch[2];
    const unitMap = {
      second: 's',
      seconds: 's',
      minute: 'm',
      minutes: 'm',
      hour: 'h',
      hours: 'h',
      day: 'd',
      days: 'd',
      month: 'mo',
      months: 'mo',
      year: 'yr',
      years: 'yr',
    };
    if (unitMap[unit]) return `${num}${unitMap[unit]} ago`;
  }

  return timeString;
}

export function ReplicasTable({ replicas }) {
  const replicaList = replicas || [];

  return (
    <div className="mb-6">
      <Card>
        <div className="flex items-center justify-between px-4 pt-4">
          <h3 className="text-lg font-semibold">Replicas</h3>
        </div>
        <div className="overflow-x-auto rounded-lg">
          <Table className="min-w-full">
            <TableHeader>
              <TableRow>
                <TableHead className="whitespace-nowrap">Replica ID</TableHead>
                <TableHead className="whitespace-nowrap">Name</TableHead>
                <TableHead className="whitespace-nowrap">Status</TableHead>
                <TableHead className="whitespace-nowrap">Version</TableHead>
                <TableHead className="whitespace-nowrap">Launched</TableHead>
                <TableHead className="whitespace-nowrap">Endpoint</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {replicaList.length > 0 ? (
                replicaList.map((replica) => (
                  <TableRow key={replica.replica_id}>
                    <TableCell className="font-medium">
                      {replica.replica_id}
                    </TableCell>
                    <TableCell>{replica.name || '-'}</TableCell>
                    <TableCell>
                      <StatusBadge status={replica.status} />
                    </TableCell>
                    <TableCell>
                      {replica.version != null ? replica.version : '-'}
                    </TableCell>
                    <TableCell>
                      {formatLaunchedAt(replica.launched_at)}
                    </TableCell>
                    <TableCell>
                      {replica.endpoint ? (
                        <span className="text-sm text-gray-600 truncate max-w-[200px]">
                          {replica.endpoint}
                        </span>
                      ) : (
                        <span className="text-gray-400">-</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))
              ) : (
                <TableRow>
                  <TableCell
                    colSpan={6}
                    className="text-center py-6 text-gray-500"
                  >
                    No replicas
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      </Card>
    </div>
  );
}
