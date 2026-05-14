'use client';

import React from 'react';
import { usePluginComponents } from './PluginProvider';

/**
 * PluginPageSlot renders the **first** plugin component registered for `name`
 * instead of the fallback. If no plugin is registered, renders `fallback`.
 *
 * Unlike PluginSlot (which wraps results in a <div> and renders all registered
 * components), this helper has page-replacement semantics: exactly one of
 * (plugin component | fallback) renders, with no wrapper element. The first
 * registration wins; we log a dev warning when more than one plugin tries to
 * own the same page slot.
 *
 * @param {string} name - The slot name (e.g., 'infra.page')
 * @param {React.ReactNode} fallback - Rendered when no plugin is registered
 */
export function PluginPageSlot({ name, fallback = null }) {
  const components = usePluginComponents(name);

  if (components.length === 0) {
    return fallback;
  }

  if (components.length > 1 && process.env.NODE_ENV !== 'production') {
    // eslint-disable-next-line no-console
    console.warn(
      `[PluginPageSlot] Multiple plugins registered for slot "${name}"; rendering the first one and ignoring the rest:`,
      components.map((c) => c.id)
    );
  }

  const Component = components[0].component;
  return <Component />;
}

export default PluginPageSlot;
