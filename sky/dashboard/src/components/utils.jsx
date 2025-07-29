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
import { REFRESH_INTERVALS } from '@/lib/config';

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

  // Filter out unwanted lines
  const lines = str
    .split('\n')
    .filter(
      (line) =>
        !line.match(/<rich_.*?\[bold cyan\]/) &&
        !line.match(/<rich_.*>.*<\/rich_.*>/) &&
        !line.match(/├──/) &&
        !line.match(/└──/)
    );

  // Remove ANSI escape codes
  str = stripAnsiCodes(lines.join('\n'));

  // Process each line
  return str
    .split('\n')
    .map((line) => {
      // Match the format: "I 04-14 02:07:19 controller.py:59] DAG:"
      const standardMatch = line.match(
        /^([IWED])\s+(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+([^:]+:\d+\])(.*)/
      );

      if (standardMatch) {
        const [_, level, timestamp, location, message] = standardMatch;
        const logLevel =
          {
            I: 'INFO',
            W: 'WARNING',
            E: 'ERROR',
            D: 'DEBUG',
          }[level] || '';

        return `<span class="log-line ${logLevel}"><span class="level">${level}</span><span class="timestamp">${timestamp}</span><span class="location">${location}</span><span class="message">${message}</span></span>`;
      }

      // If it doesn't match the standard format, try to split on parentheses content
      const parts = line.match(/^(\([^)]+\))(.*)$/);
      if (parts) {
        const [_, prefix, rest] = parts;
        return `<span class="log-line"><span class="log-prefix">${prefix}</span><span class="log-rest">${rest}</span></span>`;
      }

      // If no patterns match, return the line as is
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
        dangerouslySetInnerHTML={{ __html: filteredLogs }}
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
