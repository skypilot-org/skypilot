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
 * A plugin registers a wrapper via:
 *   api.registerComponent({
 *     id: 'my-provider',
 *     slot: 'app.providers',
 *     component: ({ children }) => <MyProvider>{children}</MyProvider>,
 *     order: 10,  // lower = outermost wrapper
 *   });
 *
 * @param {string} name - The slot name to look up registered wrappers
 * @param {React.ReactNode} children - The tree to wrap
 */
export function PluginWrapperSlot({ name, children }) {
  const wrappers = usePluginComponents(name);

  if (wrappers.length === 0) {
    return children;
  }

  // Nest wrappers: first registered (lowest order) is outermost
  let result = children;
  for (let i = wrappers.length - 1; i >= 0; i--) {
    const Wrapper = wrappers[i].component;
    result = <Wrapper key={wrappers[i].id}>{result}</Wrapper>;
  }
  return result;
}

export default PluginWrapperSlot;
