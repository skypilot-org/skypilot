import React, { useState, useEffect } from 'react';
import { Tooltip } from '@nextui-org/tooltip';
import { formatDistance } from 'date-fns';
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/ui/select';
import { REFRESH_INTERVALS, UI_CONFIG } from '@/lib/config';
import Link from 'next/link';

// Refresh interval in milliseconds
export const REFRESH_INTERVAL = REFRESH_INTERVALS.REFRESH_INTERVAL;

function capitalizeFirstWord(text) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function relativeTime(date) {
  if (!date) {
    return 'N/A';
  }

  const now = new Date();
  const differenceInDays = (now - date) / (1000 * 3600 * 24);
  if (Math.abs(differenceInDays) < 7) {
    const originalTimeString = formatDistance(date, now, { addSuffix: true });
    const shortenedTimeString = shortenTimeString(originalTimeString);

    return (
      <CustomTooltip
        content={formatDateTime(date)}
        className="capitalize text-sm text-muted-foreground"
      >
        {shortenedTimeString}
      </CustomTooltip>
    );
  } else {
    return (
      <CustomTooltip
        content={capitalizeFirstWord(formatDateTime(date))}
        className="text-sm text-muted-foreground"
      >
        {capitalizeFirstWord(formatDate(date))}
      </CustomTooltip>
    );
  }
}

// Helper function to shorten time strings
function shortenTimeString(timeString) {
  if (!timeString || typeof timeString !== 'string') {
    return timeString;
  }

  // Handle "just now" case
  if (timeString === 'just now') {
    return 'now';
  }

  // Handle "less than a minute ago" case
  if (timeString.toLowerCase() === 'less than a minute ago') {
    return 'Less than 1m ago';
  }

  // Handle "about X unit(s) ago" e.g. "about 1 hour ago" -> "1h ago"
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
      month: 'mo',
      months: 'mo',
      year: 'yr',
      years: 'yr',
    };
    if (unitMap[unit]) {
      return `${num}${unitMap[unit]} ago`;
    }
  }

  // Handle "a minute ago", "an hour ago", etc.
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
    if (unitMap[unit]) {
      return `1${unitMap[unit]} ago`;
    }
  }

  // Handle "X units ago"
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
    if (unitMap[unit]) {
      return `${num}${unitMap[unit]} ago`;
    }
  }

  // Fallback to original string if no patterns match
  return timeString;
}

export function formatDateTime(date) {
  const options = {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
    hour12: false, // Use 24-hour format
    timeZoneName: 'short',
  };
  return date.toLocaleString('en-CA', options).replace(',', '');
}

export function formatDate(date) {
  const options = {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  };
  return date.toLocaleString('en-CA', options).replace(',', '');
}

const DEFAULT_TOOLTIP_PROPS = {
  placement: 'bottom',
  color: 'default',
};

export const CustomTooltip = ({ children, ...props }) => {
  const content = props.content;
  props.content = undefined;
  return (
    <Tooltip
      {...DEFAULT_TOOLTIP_PROPS}
      {...props}
      content={
        <span className="left-full w-max px-2 py-1 text-sm text-gray-100 bg-gray-500 text-sm capitalize rounded">
          {content}
        </span>
      }
    >
      {children}
    </Tooltip>
  );
};

export const NonCapitalizedTooltip = ({ children, ...props }) => {
  const content = props.content;
  props.content = undefined;
  return (
    <Tooltip
      {...DEFAULT_TOOLTIP_PROPS}
      {...props}
      content={
        <span className="left-full w-max px-2 py-1 text-sm text-gray-100 bg-gray-500 text-sm rounded">
          {content}
        </span>
      }
    >
      {children}
    </Tooltip>
  );
};

// Format duration from seconds to a readable format
export function formatDuration(durationInSeconds) {
  if (!durationInSeconds && durationInSeconds !== 0) return '-';

  // Convert to a whole number if it's a float
  durationInSeconds = Math.floor(durationInSeconds);

  const units = [
    { value: 31536000, label: 'y' }, // years (365 days)
    { value: 2592000, label: 'mo' }, // months (30 days)
    { value: 86400, label: 'd' }, // days
    { value: 3600, label: 'h' }, // hours
    { value: 60, label: 'm' }, // minutes
    { value: 1, label: 's' }, // seconds
  ];

  let remaining = durationInSeconds;
  let result = '';
  let count = 0;

  for (const unit of units) {
    if (remaining >= unit.value && count < 2) {
      const value = Math.floor(remaining / unit.value);
      result += `${value}${unit.label} `;
      remaining %= unit.value;
      count++;
    }
  }

  return result.trim() || '0s';
}

export function formatLogs(str) {
  if (!str) return '';

  // Remove ANSI escape codes
  const cleaned = stripAnsiCodes(str);

  // Split into lines and format each one
  return cleaned
    .split('\n')
    .filter((line) => {
      // Filter out empty lines and rich terminal formatting artifacts
      return (
        line.trim() !== '' && // remove empty
        !line.match(/<rich_.*?\[bold cyan\]/) &&
        !line.match(/<rich_.*>.*<\/rich_.*>/) &&
        !line.match(/├──/) &&
        !line.match(/└──/)
      );
    })
    .map((line) => {
      // Wrap each line in log formatting
      return `<span class="log-line"><span class="message">${line}</span></span>`;
    })
    .join('\n');
}

export const logStyles = `
  .logs-container {
    background-color: #f7f7f7;
    padding: 16px;
    max-height: calc(100vh - 300px);
    overflow-y: auto;
    overflow-x: hidden;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
    line-height: 1.5;
    border-radius: 6px;
    min-height: fit-content;
  }

  .log-line {
    display: block;
    white-space: pre-wrap;
    margin: 2px 0;
  }

  .log-line .level {
    display: inline;
    width: 1ch;
    margin-right: 1ch;
    font-weight: bold;
  }

  .log-line.INFO .level {
    color: #2563eb;
  }

  .log-line.WARNING .level {
    color: #d97706;
  }

  .log-line.ERROR .level {
    color: #dc2626;
  }

  .log-line.DEBUG .level {
    color: #6b7280;
  }

  .log-line .timestamp {
    color: #059669;
    margin-right: 1ch;
    white-space: nowrap;
  }

  .log-line .location {
    color: #6366f1;
    margin-right: 1ch;
    white-space: nowrap;
  }

  .log-line .message {
    color: #111827;
    word-break: break-word;
    white-space: pre-wrap;
  }

  .log-line .log-prefix {
    color: #6366f1;
    font-weight: 500;
  }

  .log-line .log-rest {
    color: #111827;
    word-break: break-word;
    white-space: pre-wrap;
  }
`;

export function stripAnsiCodes(str) {
  return str.replace(/\x1b\[([0-9]{1,2}(;[0-9]{1,2})?)?[mGKH]/g, '');
}

function extractNodeTypes(logs) {
  const nodePattern = /\((head|worker\d+),/g; // Matches 'head' or 'worker' followed by any number
  const nodeTypes = new Set();

  let match;
  while ((match = nodePattern.exec(logs)) !== null) {
    nodeTypes.add(match[1]); // Add the node type to the set
  }

  const sortedNodeTypes = Array.from(nodeTypes).sort((a, b) => {
    if (a === 'head') return -1;
    if (b === 'head') return 1;
    return a.localeCompare(b, undefined, {
      numeric: true,
      sensitivity: 'base',
    });
  });

  return sortedNodeTypes; // Return sorted array
}

export function LogFilter({ logs, controller = false }) {
  const [selectedNode, setSelectedNode] = useState('all');
  const [filteredLogs, setFilteredLogs] = useState(logs);
  const [nodeTypes, setNodeTypes] = useState([]);

  useEffect(() => {
    setNodeTypes(extractNodeTypes(logs));
  }, [logs]);

  useEffect(() => {
    if (selectedNode === 'all') {
      setFilteredLogs(logs);
    } else {
      const filtered = logs
        .split('\n')
        .filter((line) => line.includes(`(${selectedNode},`));
      setFilteredLogs(filtered.join('\n'));
    }
  }, [selectedNode, logs]);

  return (
    <div>
      <style>{logStyles}</style>
      {!controller && (
        <div style={{ marginBottom: '1rem' }}>
          <Select
            onValueChange={(value) => setSelectedNode(value)}
            value={selectedNode}
          >
            <SelectTrigger
              aria-label="Node"
              className="focus:ring-0 focus:ring-offset-0"
            >
              <SelectValue placeholder="Select Node" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Nodes</SelectItem>
              {nodeTypes.map((node) => (
                <SelectItem key={node} value={node}>
                  {node}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
      <div
        className="logs-container"
        dangerouslySetInnerHTML={{ __html: formatLogs(filteredLogs) }}
      />
    </div>
  );
}

// New component for timestamps with dotted underlines and local timezone tooltips
export function TimestampWithTooltip({ date }) {
  if (!date) {
    return 'N/A';
  }

  const now = new Date();
  const differenceInDays = (now - date) / (1000 * 3600 * 24);

  // Format the full timestamp in '2025-06-13, 03:53:33 PM PDT' format
  const dateStr =
    date.getFullYear() +
    '-' +
    String(date.getMonth() + 1).padStart(2, '0') +
    '-' +
    String(date.getDate()).padStart(2, '0');
  const timeStr = date.toLocaleString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
    hour12: true,
    timeZoneName: 'short',
  });
  const fullLocalTimestamp = dateStr + ' ' + timeStr;

  let displayText;
  // Always show relative time with shortened format
  const originalTimeString = formatDistance(date, now, { addSuffix: true });
  displayText = shortenTimeString(originalTimeString);

  return (
    <CustomTooltip
      content={fullLocalTimestamp}
      className="text-sm text-muted-foreground"
    >
      <span className="border-b border-dotted border-gray-400 cursor-help">
        {displayText}
      </span>
    </CustomTooltip>
  );
}

// Helper function to format timestamp in full format without underline (for detail pages)
export function formatFullTimestamp(date) {
  if (!date) {
    return 'N/A';
  }

  const dateStr =
    date.getFullYear() +
    '-' +
    String(date.getMonth() + 1).padStart(2, '0') +
    '-' +
    String(date.getDate()).padStart(2, '0');
  const timeStr = date.toLocaleString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
    hour12: true,
    timeZoneName: 'short',
  });
  return dateStr + ' ' + timeStr;
}

// Shared badge components for pools

export const getJobStatusCounts = (poolData) => {
  if (!poolData || !poolData.jobCounts) return {};
  return poolData.jobCounts;
};

export const getInfraSummary = (replicaInfo) => {
  if (!replicaInfo || replicaInfo.length === 0) return {};

  const readyWorkers = replicaInfo.filter(
    (worker) => worker.status === 'READY'
  );

  const cloudData = {};
  readyWorkers.forEach((worker) => {
    try {
      // Handle undefined/null/empty cloud values
      const hasCloud =
        worker.cloud &&
        worker.cloud.trim() !== '' &&
        worker.cloud !== 'undefined';
      const hasRegion =
        worker.region &&
        worker.region !== 'undefined' &&
        worker.region !== null &&
        worker.region.trim() !== '';

      // Skip if both cloud and region are missing
      if (!hasCloud && !hasRegion) {
        return;
      }

      const cloud = hasCloud ? worker.cloud : 'Unknown';
      const region = hasRegion ? worker.region : null;

      if (!cloudData[cloud]) {
        cloudData[cloud] = {
          count: 0,
          regions: new Set(),
        };
      }

      cloudData[cloud].count += 1;
      if (region) {
        cloudData[cloud].regions.add(region);
      }
    } catch (error) {
      // Handle errors gracefully
      if (!cloudData['Unknown']) {
        cloudData['Unknown'] = {
          count: 0,
          regions: new Set(),
        };
      }
      cloudData['Unknown'].count += 1;
    }
  });

  // Convert to the expected format: "Cloud (X regions) Total" or "Kubernetes (X contexts) Total"
  const infraCounts = {};
  Object.entries(cloudData).forEach(([cloud, data]) => {
    const regionCount = data.regions.size;

    // Use 'context' for Kubernetes, 'region' for other clouds
    const isKubernetes =
      cloud.toLowerCase().includes('kubernetes') ||
      cloud.toLowerCase().includes('k8s');
    const locationTerm = isKubernetes ? 'context' : 'region';
    const locationText =
      regionCount === 1
        ? `1 ${locationTerm}`
        : `${regionCount} ${locationTerm}s`;

    const key = regionCount > 0 ? `${cloud} (${locationText})` : cloud;
    infraCounts[key] = data.count;
  });

  return infraCounts;
};

export const JobStatusBadges = ({ jobCounts, getStatusStyle }) => {
  if (!jobCounts || Object.keys(jobCounts).length === 0) {
    return <span className="text-gray-1000">No active jobs</span>;
  }

  return (
    <div className="flex flex-wrap gap-1">
      {Object.entries(jobCounts).map(([status, count]) => {
        const style = getStatusStyle(status);
        return (
          <span
            key={status}
            className={`px-2 py-1 rounded-full flex items-center space-x-2 text-xs font-medium ${style}`}
          >
            <span>{status}</span>
            <span className="text-xs bg-white/50 px-1.5 py-0.5 rounded">
              {count}
            </span>
          </span>
        );
      })}
    </div>
  );
};

export const InfraBadges = ({ replicaInfo }) => {
  const infraCounts = getInfraSummary(replicaInfo);

  if (Object.keys(infraCounts).length === 0) {
    return <span className="text-gray-500 text-sm">-</span>;
  }

  const NAME_TRUNCATE_LENGTH = UI_CONFIG.NAME_TRUNCATE_LENGTH;

  const truncateCloudRegion = (cloudWithRegion) => {
    // Check if there's a region part in parentheses
    const parenIndex = cloudWithRegion.indexOf('(');
    if (parenIndex === -1) {
      // No region part, return as is
      return cloudWithRegion;
    }

    const cloudName = cloudWithRegion.substring(0, parenIndex).trim();
    const regionPart = cloudWithRegion.substring(
      parenIndex + 1,
      cloudWithRegion.length - 1
    );

    // Only truncate the region part if it's longer than the truncate length
    if (regionPart.length <= NAME_TRUNCATE_LENGTH) {
      return cloudWithRegion;
    }

    // Truncate only the region part
    const truncatedRegion = `${regionPart.substring(0, Math.floor((NAME_TRUNCATE_LENGTH - 3) / 2))}...${regionPart.substring(regionPart.length - Math.ceil((NAME_TRUNCATE_LENGTH - 3) / 2))}`;

    return `${cloudName} (${truncatedRegion})`;
  };

  return (
    <div className="flex flex-wrap gap-1">
      {Object.entries(infraCounts).map(([cloudWithRegion, count]) => {
        const displayText = truncateCloudRegion(cloudWithRegion);
        const shouldTruncate = displayText !== cloudWithRegion;

        return (
          <span
            key={cloudWithRegion}
            className="px-2 py-1 rounded-full flex items-center space-x-2 text-xs font-medium bg-blue-50 text-blue-700"
          >
            {shouldTruncate ? (
              <NonCapitalizedTooltip
                content={cloudWithRegion}
                className="text-sm text-muted-foreground"
              >
                <span>{displayText}</span>
              </NonCapitalizedTooltip>
            ) : (
              <span>{cloudWithRegion}</span>
            )}
            <span className="text-xs bg-white/50 px-1.5 py-0.5 rounded">
              {count}
            </span>
          </span>
        );
      })}
    </div>
  );
};

// Common function for rendering pool links with hash comparison
export const renderPoolLink = (poolName, poolHash, poolsData) => {
  if (!poolName) return '-';

  // Check if pool hash matches to determine if we should link
  const matchingPool = poolsData.find(
    (pool) => pool.name === poolName && pool.hash === poolHash
  );

  if (matchingPool && poolHash) {
    // Running pool - show green circle indicator
    return (
      <div className="flex items-center space-x-2">
        <NonCapitalizedTooltip content="This pool is running" placement="top">
          <div className="w-2 h-2 bg-green-700 rounded-full"></div>
        </NonCapitalizedTooltip>
        <Link
          href={`/jobs/pools/${poolName}`}
          className="text-gray-700 hover:text-blue-600 hover:underline"
        >
          {poolName}
        </Link>
      </div>
    );
  }
  // Pool exists but no matching hash found - likely terminated
  const shortHash = poolHash ? poolHash.substring(0, 4) : '';
  return (
    <div className="flex items-center space-x-2">
      <NonCapitalizedTooltip content="This pool is terminated" placement="top">
        <div className="w-2 h-2 bg-gray-800"></div>
      </NonCapitalizedTooltip>
      <span>
        {poolName} ({shortHash})
      </span>
    </div>
  );
};
