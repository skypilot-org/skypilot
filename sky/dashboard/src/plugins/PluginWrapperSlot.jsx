'use client';

import React from 'react';
import { usePluginComponents } from './PluginProvider';

/**
 * PluginWrapperSlot lets plugins wrap the component tree with providers.
 *
 * Unlike PluginSlot (which renders components as siblings), this component
 * nests registered plugin components as wrappers around {children}. Each
 * wrapper receives {children} as a prop and must render them.
 *
 * Usage in _app.js:
 *   <PluginWrapperSlot name="app.providers">
 *     <Layout><Component /></Layout>
 *   </PluginWrapperSlot>
 *
 * Per-instance context (e.g. row id) can be passed via the `context` prop;
 * each wrapper receives those keys as props alongside `children`. A
 * `fallback` element renders when no plugins are registered (defaults to
 * `children`).
 *
 * A plugin registers a wrapper via:
 *   api.registerComponent({
 *     id: 'my-provider',
 *     slot: 'app.providers',
 *     component: ({ children }) => <MyProvider>{children}</MyProvider>,
 *     order: 10,  // lower = outermost wrapper
 *   });
 *
 * @param {string} name - The slot name to look up registered wrappers
 * @param {object} context - Optional per-instance props passed to each wrapper
 * @param {React.ReactNode} fallback - Optional element rendered when no
 *   wrapper plugins are registered (defaults to children)
 * @param {React.ReactNode} children - The tree to wrap
 */
export function PluginWrapperSlot({ name, context = {}, fallback, children }) {
  const wrappers = usePluginComponents(name);

  if (wrappers.length === 0) {
    return fallback !== undefined ? fallback : children;
  }

  // Nest wrappers: first registered (lowest order) is outermost.
  // Each wrapper receives context spread as props, in addition to children.
  let result = children;
  for (let i = wrappers.length - 1; i >= 0; i--) {
    const Wrapper = wrappers[i].component;
    result = (
      <Wrapper key={wrappers[i].id} {...context}>
        {result}
      </Wrapper>
    );
  }
  return result;
}

export default PluginWrapperSlot;
