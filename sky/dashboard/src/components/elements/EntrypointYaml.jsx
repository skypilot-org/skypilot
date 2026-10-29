import React, { useState } from 'react';
import PropTypes from 'prop-types';
import {
  CheckIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  CopyIcon,
} from 'lucide-react';
import { CustomTooltip as Tooltip } from '@/components/utils';
import { YamlCodeBlock } from '@/components/ui/yaml-code-block';
import { formatYaml } from '@/lib/yamlUtils';

// Shared pieces of the "Entrypoint" + collapsible SkyPilot YAML section shown
// on the cluster, cluster job and managed job detail pages.

/**
 * Copies `text` to the clipboard and shows a check mark for two seconds.
 * `text` may be a function, evaluated on click, for content that is costly to
 * build. `compact` is the smaller, tooltip-less style of the managed job page.
 */
export function CopyButton({ text, title, compact = false }) {
  const [copied, setCopied] = useState(false);

  const onClick = async () => {
    try {
      await navigator.clipboard.writeText(
        typeof text === 'function' ? text() : text
      );
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy to clipboard:', err);
    }
  };

  if (compact) {
    return (
      <button
        onClick={onClick}
        title={title}
        className="ml-2 inline-flex items-center align-middle text-gray-400 hover:text-gray-700 focus:outline-none"
      >
        {copied ? (
          <CheckIcon className="w-3.5 h-3.5 text-green-600" />
        ) : (
          <CopyIcon className="w-3.5 h-3.5" />
        )}
      </button>
    );
  }
  return (
    <Tooltip
      content={copied ? 'Copied!' : title}
      className="text-muted-foreground"
    >
      <button
        onClick={onClick}
        title={title}
        className="flex items-center text-gray-500 hover:text-gray-700 transition-colors duration-200 p-1 ml-2"
      >
        {copied ? (
          <CheckIcon className="w-4 h-4 text-green-600" />
        ) : (
          <CopyIcon className="w-4 h-4" />
        )}
      </button>
    </Tooltip>
  );
}

CopyButton.propTypes = {
  text: PropTypes.oneOfType([PropTypes.string, PropTypes.func]).isRequired,
  title: PropTypes.string.isRequired,
  compact: PropTypes.bool,
};

/** The command a cluster or job was launched with, in a code box. */
export function EntrypointCommand({ command, className = '' }) {
  return (
    <div
      className={`bg-gray-50 border border-gray-200 rounded-md p-3 ${className}`}
    >
      <code className="text-sm text-gray-800 font-mono break-all">
        {command}
      </code>
    </div>
  );
}

EntrypointCommand.propTypes = {
  command: PropTypes.string.isRequired,
  className: PropTypes.string,
};

/**
 * A toggle that expands `children` (the YAML view), with a copy button.
 * `children` may be a function, so a costly view is only built when expanded.
 */
export function CollapsibleYaml({
  label = 'Show SkyPilot YAML',
  copyText,
  compact = false,
  children,
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div>
      <div className={`flex items-center ${compact ? '' : 'mb-2'}`}>
        <button
          onClick={() => setExpanded(!expanded)}
          aria-expanded={expanded}
          className={
            compact
              ? 'flex items-center gap-1 text-left focus:outline-none text-gray-900 hover:text-gray-700 transition-colors duration-200'
              : 'flex items-center text-left focus:outline-none text-gray-700 hover:text-gray-900 transition-colors duration-200'
          }
        >
          {expanded ? (
            <ChevronDownIcon
              className={compact ? 'w-4 h-4 text-gray-500' : 'w-4 h-4 mr-1'}
            />
          ) : (
            <ChevronRightIcon
              className={compact ? 'w-4 h-4 text-gray-500' : 'w-4 h-4 mr-1'}
            />
          )}
          <span className="text-base">{label}</span>
        </button>
        <CopyButton text={copyText} title="Copy YAML" compact={compact} />
      </div>
      {expanded && (typeof children === 'function' ? children() : children)}
    </div>
  );
}

CollapsibleYaml.propTypes = {
  label: PropTypes.string,
  copyText: PropTypes.oneOfType([PropTypes.string, PropTypes.func]).isRequired,
  compact: PropTypes.bool,
  children: PropTypes.oneOfType([PropTypes.node, PropTypes.func]),
};

/**
 * The "Entrypoint" heading, the command and the collapsible YAML, as on the
 * cluster and cluster job pages. Renders nothing when there is neither; the
 * YAML part is left out when `taskYaml` is empty or `{}`.
 */
export function EntrypointAndYaml({ entrypoint, taskYaml }) {
  const hasYaml = Boolean(taskYaml) && taskYaml.trim() !== '{}';
  if (!entrypoint && !hasYaml) {
    return null;
  }
  return (
    <div className="col-span-2">
      <div className="flex items-center">
        <div className="text-gray-600 font-medium text-base">Entrypoint</div>
        {entrypoint && <CopyButton text={entrypoint} title="Copy command" />}
      </div>
      <div className="space-y-4 mt-3">
        {entrypoint && <EntrypointCommand command={entrypoint} />}
        {hasYaml && (
          <CollapsibleYaml copyText={() => formatYaml(taskYaml)}>
            {() => <YamlCodeBlock value={formatYaml(taskYaml)} readOnly />}
          </CollapsibleYaml>
        )}
      </div>
    </div>
  );
}

EntrypointAndYaml.propTypes = {
  entrypoint: PropTypes.string,
  taskYaml: PropTypes.string,
};
