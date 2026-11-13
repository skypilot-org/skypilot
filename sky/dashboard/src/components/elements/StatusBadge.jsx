import React from 'react';
import { CircularProgress } from '@mui/material';
import { NonCapitalizedTooltip as Tooltip } from '@/components/utils';
import {
  FilledCircleIcon,
  SquareIcon,
  PauseIcon,
  CircleIcon,
  TickIcon,
} from '@/components/elements/icons';

// Shared badge style definition
const badgeClasses = 'inline-flex items-center px-2 py-1 rounded-full text-sm';

// Mapping of status to colors for consistency
export const getStatusStyle = (status) => {
  switch (status) {
    // Cluster specific statuses
    case 'LAUNCHING':
      return 'bg-blue-tint-strong text-brand';
    case 'UNHEALTHY':
      return 'bg-red-tint text-red-fg-strong';
    case 'RUNNING':
    case 'IN_USE':
      return 'bg-green-tint text-green-fg-strong';
    case 'STOPPED':
      return 'bg-yellow-tint-strong text-yellow-fg-stronger';
    case 'AUTOSTOPPING':
      return 'bg-purple-tint-strong text-purple-fg-stronger';
    case 'TERMINATED':
      return 'bg-surface-muted text-content-heavy';

    // Job specific statuses
    case 'PENDING':
      return 'bg-surface-muted text-content-heavy';
    case 'SUCCEEDED':
      return 'bg-blue-tint text-blue-fg-strong';
    case 'FAILED':
      return 'bg-red-tint text-red-fg-strong';
    case 'CANCELLED':
      return 'bg-yellow-tint text-yellow-fg-strong';
    case 'RECOVERING':
      return 'bg-orange-tint text-orange-fg-strong';
    case 'WINDING_DOWN':
      return 'bg-purple-tint text-purple-fg-strong';
    case 'SUBMITTED':
      return 'bg-indigo-tint text-indigo-fg-strong';
    case 'STARTING':
      return 'bg-cyan-tint text-cyan-fg-strong';
    case 'CANCELLING':
      return 'bg-yellow-tint text-yellow-fg-strong';
    case 'FAILED_SETUP':
      return 'bg-pink-tint text-pink-fg-strong';
    case 'FAILED_PRECHECKS':
      return 'bg-red-tint text-red-fg-strong';
    case 'FAILED_NO_RESOURCE':
      return 'bg-red-tint text-red-fg-strong';
    case 'FAILED_CONTROLLER':
      return 'bg-red-tint text-red-fg-strong';

    // Volume statuses - 'READY' and 'NOT_READY' are
    // handled below with Serve statuses

    // Serve specific statuses - ReplicaStatus
    case 'READY':
      return 'bg-green-tint text-green-fg-strong';
    case 'PROVISIONING':
      return 'bg-blue-tint text-blue-fg-strong';
    case 'NOT_READY':
      return 'bg-yellow-tint text-yellow-fg-strong';
    case 'SHUTTING_DOWN':
      return 'bg-orange-tint text-orange-fg-strong';
    case 'FAILED_INITIAL_DELAY':
      return 'bg-red-tint text-red-fg-strong';
    case 'FAILED_PROBING':
      return 'bg-red-tint text-red-fg-strong';
    case 'FAILED_PROVISION':
      return 'bg-red-tint text-red-fg-strong';
    case 'FAILED_CLEANUP':
      return 'bg-red-tint text-red-fg-strong';
    case 'PREEMPTED':
      return 'bg-purple-tint text-purple-fg-strong';
    case 'UNKNOWN':
      return 'bg-surface-muted text-content-heavy';

    // Serve specific statuses - ServiceStatus
    case 'CONTROLLER_INIT':
      return 'bg-blue-tint text-blue-fg-strong';
    case 'REPLICA_INIT':
      return 'bg-blue-tint text-blue-fg-strong';
    case 'CONTROLLER_FAILED':
      return 'bg-red-tint text-red-fg-strong';
    case 'NO_REPLICA':
      return 'bg-purple-tint text-purple-fg-strong';

    default:
      return 'bg-surface-muted text-content-heavy';
  }
};

// Get appropriate icon based on status
export const getStatusIcon = (status) => {
  switch (status) {
    case 'LAUNCHING':
    case 'STARTING':
      return <CircularProgress size={12} className="w-3 h-3 mr-1" />;
    case 'RUNNING':
    case 'IN_USE':
      return <FilledCircleIcon className="w-3 h-3 mr-1" />;
    case 'STOPPED':
      return <PauseIcon className="w-3 h-3 mr-1" />;
    case 'AUTOSTOPPING':
      return <CircularProgress size={12} className="w-3 h-3 mr-1" />;
    case 'TERMINATED':
    case 'FAILED':
    case 'CANCELLED':
      return <SquareIcon className="w-3 h-3 mr-1" />;
    case 'SUCCEEDED':
      return <TickIcon className="w-3 h-3 mr-1" />;
    case 'UNHEALTHY':
      return <FilledCircleIcon className="w-3 h-3 mr-1" />;
    case 'PENDING':
    case 'RECOVERING':
    case 'SUBMITTED':
    case 'CANCELLING':
    case 'FAILED_SETUP':
    case 'FAILED_PRECHECKS':
    case 'FAILED_NO_RESOURCE':
    case 'FAILED_CONTROLLER':
    case 'READY':
    case 'NOT_READY':
    case 'CONTROLLER_INIT':
    case 'REPLICA_INIT':
    case 'NO_REPLICA':
      return <CircleIcon className="w-3 h-3 mr-1" />;
    case 'WINDING_DOWN':
    case 'PROVISIONING':
    case 'SHUTTING_DOWN':
      return <CircularProgress size={12} className="w-3 h-3 mr-1" />;
    case 'FAILED_INITIAL_DELAY':
    case 'FAILED_PROBING':
    case 'FAILED_PROVISION':
    case 'FAILED_CLEANUP':
    case 'CONTROLLER_FAILED':
    case 'UNKNOWN':
      return <SquareIcon className="w-3 h-3 mr-1" />;
    case 'PREEMPTED':
      return <PauseIcon className="w-3 h-3 mr-1" />;
    default:
      return <FilledCircleIcon className="w-3 h-3 mr-1" />;
  }
};

// Unified status to badge renderer
export const status2Badge = (status) => {
  const statusStyle = getStatusStyle(status);
  const statusIcon = getStatusIcon(status);

  return (
    <span className={`${badgeClasses} ${statusStyle}`}>
      {statusIcon}
      {status}
    </span>
  );
};

// Reusable StatusBadge component with tooltip
export const StatusBadge = ({ status, statusTooltip }) => {
  const tooltipContent = statusTooltip || status;

  return (
    <Tooltip content={tooltipContent}>
      <span>{status2Badge(status)}</span>
    </Tooltip>
  );
};

export default StatusBadge;
