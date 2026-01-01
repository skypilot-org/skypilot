'use client';

import React from 'react';
import { usePluginComponents } from './PluginProvider';

/**
 * PluginSlot renders plugin-registered components for a given slot name.
 *
 * @param {string} name - The slot name (e.g., 'clusters.detail.events')
 * @param {object} context - Context data passed to each plugin component as props
 * @param {React.ReactNode} fallback - Optional fallback to render when no plugins are registered
 * @param {string} wrapperClassName - Optional CSS class for the wrapper div
 */
export function PluginSlot({
  name,
  context = {},
  fallback = null,
  wrapperClassName = '',
}) {
  const components = usePluginComponents(name);

  if (components.length === 0) {
    return fallback;
  }

  return (
    <div className={wrapperClassName || undefined}>
      {components.map((config) => {
        const Component = config.component;
        return <Component key={config.id} {...context} />;
      })}
    </div>
  );
}

export default PluginSlot;
