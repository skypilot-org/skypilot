import React from 'react';
import { CircularProgress } from '@mui/material';
import { CustomTooltip as Tooltip } from '@/components/utils';
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
      return 'bg-blue-100 text-sky-blue';
    case 'RUNNING':
    case 'IN_USE':
      return 'bg-green-50 text-green-700';
    case 'STOPPED':
      return 'bg-yellow-100 text-yellow-800';
    case 'TERMINATED':
      return 'bg-gray-100 text-gray-800';

    // Job specific statuses
    case 'PENDING':
      return 'bg-gray-100 text-gray-800';
    case 'SUCCEEDED':
      return 'bg-blue-50 text-blue-700';
    case 'FAILED':
      return 'bg-red-50 text-red-700';
    case 'CANCELLED':
      return 'bg-yellow-50 text-yellow-700';
    case 'RECOVERING':
      return 'bg-orange-50 text-orange-700';
    case 'SUBMITTED':
      return 'bg-indigo-50 text-indigo-700';
    case 'STARTING':
      return 'bg-cyan-50 text-cyan-700';
    case 'CANCELLING':
      return 'bg-yellow-50 text-yellow-700';
    case 'FAILED_SETUP':
      return 'bg-pink-50 text-pink-700';
    case 'FAILED_PRECHECKS':
      return 'bg-red-50 text-red-700';
    case 'FAILED_NO_RESOURCE':
      return 'bg-red-50 text-red-700';
    case 'FAILED_CONTROLLER':
      return 'bg-red-50 text-red-700';

    // Serve specific statuses - ReplicaStatus
    case 'READY':
      return 'bg-green-50 text-green-700';
    case 'PROVISIONING':
      return 'bg-blue-50 text-blue-700';
    case 'NOT_READY':
      return 'bg-yellow-50 text-yellow-700';
    case 'SHUTTING_DOWN':
      return 'bg-orange-50 text-orange-700';
    case 'FAILED_INITIAL_DELAY':
      return 'bg-red-50 text-red-700';
    case 'FAILED_PROBING':
      return 'bg-red-50 text-red-700';
    case 'FAILED_PROVISION':
      return 'bg-red-50 text-red-700';
    case 'FAILED_CLEANUP':
      return 'bg-red-50 text-red-700';
    case 'PREEMPTED':
      return 'bg-purple-50 text-purple-700';
    case 'UNKNOWN':
      return 'bg-gray-100 text-gray-800';

    // Serve specific statuses - ServiceStatus
    case 'CONTROLLER_INIT':
      return 'bg-blue-50 text-blue-700';
    case 'REPLICA_INIT':
      return 'bg-blue-50 text-blue-700';
    case 'CONTROLLER_FAILED':
      return 'bg-red-50 text-red-700';
    case 'NO_REPLICA':
      return 'bg-purple-50 text-purple-700';

    default:
      return 'bg-gray-100 text-gray-800';
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
    case 'TERMINATED':
    case 'FAILED':
    case 'CANCELLED':
      return <SquareIcon className="w-3 h-3 mr-1" />;
    case 'SUCCEEDED':
      return <TickIcon className="w-3 h-3 mr-1" />;
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
export const StatusBadge = ({ status }) => {
  return (
    <Tooltip content={status} className="text-muted-foreground text-sm">
      <span>{status2Badge(status)}</span>
    </Tooltip>
  );
};

export default StatusBadge;
