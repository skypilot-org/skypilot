import React from 'react';
import { Tooltip } from '@nextui-org/tooltip';
import { formatDistance } from 'date-fns';

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
    return (
      <CustomTooltip
        content={formatDateTime(date)}
        className="capitalize text-sm text-muted-foreground"
      >
        {capitalizeFirstWord(formatDistance(date, now, { addSuffix: true }))}
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

export function formatDateTime(date) {
  const options = {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
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
