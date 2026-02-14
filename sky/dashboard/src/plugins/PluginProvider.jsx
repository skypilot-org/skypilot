'use client';

import React, {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useReducer,
} from 'react';
import { BASE_PATH, ENDPOINT } from '@/data/connectors/constants';
import { apiClient } from '@/data/connectors/client';
import dashboardCache from '@/lib/cache';
import { checkGrafanaAvailability, getGrafanaUrl } from '@/utils/grafana';

const PluginContext = createContext({
  topNavLinks: [],
  routes: [],
  components: {},
  dataEnhancements: {},
  tableColumns: {},
  dataProviders: {},
});

const initialState = {
  topNavLinks: [],
  routes: [],
  components: {}, // Map of slot name → array of component configs
  dataEnhancements: {}, // Map of dataSource → array of enhancements
  tableColumns: {}, // Map of table name → array of column configs
  dataProviders: {}, // Map of provider id → provider config (with useHook)
};

const actions = {
  REGISTER_TOP_NAV_LINK: 'REGISTER_TOP_NAV_LINK',
  REGISTER_ROUTE: 'REGISTER_ROUTE',
  REGISTER_COMPONENT: 'REGISTER_COMPONENT',
  REGISTER_DATA_ENHANCEMENT: 'REGISTER_DATA_ENHANCEMENT',
  REGISTER_TABLE_COLUMN: 'REGISTER_TABLE_COLUMN',
  REGISTER_DATA_PROVIDER: 'REGISTER_DATA_PROVIDER',
};

function pluginReducer(state, action) {
  switch (action.type) {
    case actions.REGISTER_TOP_NAV_LINK:
      return {
        ...state,
        topNavLinks: upsertById(state.topNavLinks, action.payload),
      };
    case actions.REGISTER_ROUTE:
      return {
        ...state,
        routes: upsertById(state.routes, action.payload),
      };
    case actions.REGISTER_COMPONENT: {
      const { slot } = action.payload;
      const existing = state.components[slot] || [];
      const updated = upsertById(existing, action.payload);
      // Sort by order (lower order = renders first)
      updated.sort((a, b) => (a.order || 100) - (b.order || 100));
      return {
        ...state,
        components: {
          ...state.components,
          [slot]: updated,
        },
      };
    }
    case actions.REGISTER_DATA_ENHANCEMENT: {
      const { dataSource } = action.payload;
      const existing = state.dataEnhancements[dataSource] || [];
      const updated = upsertById(existing, action.payload);
      // Sort by priority, then by dependencies
      updated.sort((a, b) => {
        const aPriority = a.priority ?? 100;
        const bPriority = b.priority ?? 100;
        if (aPriority !== bPriority) {
          return aPriority - bPriority;
        }
        // If b depends on a, a should run first
        if (b.dependencies?.includes(a.id)) {
          return -1;
        }
        if (a.dependencies?.includes(b.id)) {
          return 1;
        }
        return 0;
      });
      return {
        ...state,
        dataEnhancements: {
          ...state.dataEnhancements,
          [dataSource]: updated,
        },
      };
    }
    case actions.REGISTER_TABLE_COLUMN: {
      const { table } = action.payload;
      const existing = state.tableColumns[table] || [];
      const updated = upsertById(existing, action.payload);
      // Sort by order (lower order = earlier position)
      updated.sort((a, b) => {
        const aOrder = a.header?.order ?? 100;
        const bOrder = b.header?.order ?? 100;
        return aOrder - bOrder;
      });
      return {
        ...state,
        tableColumns: {
          ...state.tableColumns,
          [table]: updated,
        },
      };
    }
    case actions.REGISTER_DATA_PROVIDER:
      return {
        ...state,
        dataProviders: {
          ...state.dataProviders,
          [action.payload.id]: action.payload,
        },
      };
    default:
      return state;
  }
}

function upsertById(collection, item) {
  const index = collection.findIndex((entry) => entry.id === item.id);
  if (index === -1) {
    return [...collection, item];
  }
  const next = [...collection];
  next[index] = item;
  return next;
}

const pluginScriptPromises = new Map();

function resolveScriptUrl(jsPath) {
  if (!jsPath || typeof jsPath !== 'string') {
    return null;
  }
  if (/^https?:\/\//.test(jsPath)) {
    return jsPath;
  }
  if (typeof window === 'undefined') {
    return jsPath;
  }
  try {
    return new URL(jsPath, window.location.origin).toString();
  } catch (error) {
    console.warn(
      '[SkyDashboardPlugin] Failed to resolve plugin script path:',
      jsPath,
      error
    );
    return null;
  }
}

function loadPluginScript(jsPath, requiresEarlyInit = false) {
  if (typeof window === 'undefined') {
    return null;
  }
  const resolved = resolveScriptUrl(jsPath);
  if (!resolved) {
    return null;
  }
  if (pluginScriptPromises.has(resolved)) {
    return pluginScriptPromises.get(resolved);
  }

  console.log('Loading plugin script:', resolved);
  const promise = new Promise((resolve) => {
    const script = document.createElement('script');
    script.type = 'text/javascript';
    script.async = true;
    script.src = resolved;
    if (requiresEarlyInit) script.dataset.requiresEarlyInit = 'true';
    script.onload = () => resolve();
    script.onerror = (error) => {
      console.warn(
        '[SkyDashboardPlugin] Failed to load plugin script:',
        resolved,
        error
      );
      resolve();
    };
    document.head.appendChild(script);
  });

  pluginScriptPromises.set(resolved, promise);
  return promise;
}

async function fetchPluginManifest() {
  try {
    const response = await apiClient.get(`/api/plugins`);
    if (!response.ok) {
      console.warn(
        '[SkyDashboardPlugin] Failed to fetch plugin manifest:',
        response.status,
        response.statusText
      );
      return [];
    }
    const payload = await response.json();
    if (!payload || !Array.isArray(payload.plugins)) {
      return [];
    }
    console.log('Plugin manifest:', payload.plugins);
    return payload.plugins;
  } catch (error) {
    console.warn('[SkyDashboardPlugin] Error fetching plugin manifest:', error);
    return [];
  }
}

function extractJsPath(pluginDescriptor) {
  if (!pluginDescriptor || typeof pluginDescriptor !== 'object') {
    return null;
  }
  if (pluginDescriptor.js_extension_path) {
    console.log(
      'Extracting JS extension path:',
      pluginDescriptor.js_extension_path
    );
    return pluginDescriptor.js_extension_path;
  }
  return null;
}

function normalizeNavLink(link) {
  if (!link || !link.id || !link.label || !link.href) {
    console.warn(
      '[SkyDashboardPlugin] Invalid top nav link registration:',
      link
    );
    return null;
  }

  const normalized = {
    id: String(link.id),
    label: String(link.label),
    href: String(link.href),
    order: Number.isFinite(link.order) ? link.order : 0,
    group: link.group ? String(link.group) : null,
    target: link.target === '_blank' ? '_blank' : '_self',
    rel:
      link.rel ??
      (link.target === '_blank' || /^https?:\/\//.test(String(link.href))
        ? 'noopener noreferrer'
        : undefined),
    external:
      link.external ??
      (/^(https?:)?\/\//.test(String(link.href)) || link.target === '_blank'),
    badge: typeof link.badge === 'string' ? link.badge : null,
    icon:
      typeof link.icon === 'string' || React.isValidElement(link.icon)
        ? link.icon
        : null,
    description:
      typeof link.description === 'string' ? link.description : undefined,
  };

  return normalized;
}

function normalizeRoute(route) {
  if (
    !route ||
    typeof route !== 'object' ||
    !route.id ||
    !route.path ||
    typeof route.mount !== 'function'
  ) {
    console.warn('[SkyDashboardPlugin] Invalid route registration:', route);
    return null;
  }

  const normalizedPath = String(route.path);
  const pathname = normalizedPath.startsWith('/')
    ? normalizedPath
    : `/${normalizedPath}`;

  return {
    id: String(route.id),
    path: pathname,
    title: typeof route.title === 'string' ? route.title : undefined,
    description:
      typeof route.description === 'string' ? route.description : undefined,
    mount: route.mount,
    unmount: typeof route.unmount === 'function' ? route.unmount : undefined,
    context:
      route.context && typeof route.context === 'object'
        ? route.context
        : undefined,
  };
}

function normalizeComponent(config) {
  if (
    !config ||
    typeof config !== 'object' ||
    !config.id ||
    !config.slot ||
    typeof config.component !== 'function'
  ) {
    console.warn(
      '[SkyDashboardPlugin] Invalid component registration:',
      config
    );
    return null;
  }

  return {
    id: String(config.id),
    slot: String(config.slot),
    component: config.component,
    order: Number.isFinite(config.order) ? config.order : 100,
    conditions:
      config.conditions && typeof config.conditions === 'object'
        ? {
            pages: Array.isArray(config.conditions.pages)
              ? config.conditions.pages.map(String)
              : undefined,
          }
        : undefined,
  };
}

function normalizeDataEnhancement(config) {
  if (
    !config ||
    typeof config !== 'object' ||
    !config.id ||
    !config.dataSource ||
    typeof config.enhance !== 'function'
  ) {
    console.warn(
      '[SkyDashboardPlugin] Invalid data enhancement registration:',
      config
    );
    return null;
  }

  return {
    id: String(config.id),
    dataSource: String(config.dataSource),
    enhance: config.enhance,
    priority: Number.isFinite(config.priority) ? config.priority : 100,
    dependencies: Array.isArray(config.dependencies)
      ? config.dependencies.map(String)
      : undefined,
    fields: Array.isArray(config.fields)
      ? config.fields.map(String)
      : undefined,
  };
}

function normalizeTableColumn(config) {
  if (
    !config ||
    typeof config !== 'object' ||
    !config.id ||
    !config.table ||
    !config.header ||
    typeof config.header !== 'object' ||
    !config.header.label ||
    !config.cell ||
    typeof config.cell !== 'object' ||
    typeof config.cell.render !== 'function'
  ) {
    console.warn(
      '[SkyDashboardPlugin] Invalid table column registration:',
      config
    );
    return null;
  }

  return {
    id: String(config.id),
    table: String(config.table),
    header: {
      label: String(config.header.label),
      sortKey: config.header.sortKey
        ? String(config.header.sortKey)
        : undefined,
      className: config.header.className
        ? String(config.header.className)
        : undefined,
      order: Number.isFinite(config.header.order) ? config.header.order : 100,
    },
    cell: {
      render: config.cell.render,
      className: config.cell.className
        ? String(config.cell.className)
        : undefined,
    },
    conditions:
      config.conditions && typeof config.conditions === 'object'
        ? {
            showWhen:
              typeof config.conditions.showWhen === 'function'
                ? config.conditions.showWhen
                : undefined,
          }
        : undefined,
  };
}

/**
 * Normalizes a URL by stripping credentials and ensuring it's safe for history API.
 * This prevents SecurityError when the current URL has credentials but the target URL doesn't.
 * Relative URLs are returned as-is since they're safe for history API.
 * @param {string} url - The URL to normalize
 * @returns {string} Normalized URL without credentials, or the original URL if it's relative or invalid
 */
function normalizeUrlForHistory(url) {
  if (!url || typeof url !== 'string') {
    return url;
  }

  // If it's a relative URL (starts with / or is a path), keep it relative
  // Relative URLs are safe for history API and don't need normalization
  if (
    url.startsWith('/') ||
    (!url.startsWith('http://') && !url.startsWith('https://'))
  ) {
    return url;
  }

  try {
    // Parse the absolute URL
    const urlObj = new URL(url);

    // Strip credentials from the URL
    urlObj.username = '';
    urlObj.password = '';

    // Return the normalized URL
    return urlObj.toString();
  } catch (error) {
    // If URL parsing fails, return the original URL
    console.warn('[SkyDashboardPlugin] Failed to normalize URL:', url, error);
    return url;
  }
}

/**
 * Intercepts history.pushState and history.replaceState to normalize URLs.
 * This prevents SecurityError when URLs contain credentials.
 */
function interceptHistoryApi() {
  if (typeof window === 'undefined' || !window.history) {
    return;
  }

  // Store original methods
  const originalPushState = window.history.pushState;
  const originalReplaceState = window.history.replaceState;

  // Override pushState
  window.history.pushState = function (state, title, url) {
    let normalizedUrl = url;
    if (url && typeof url === 'string') {
      normalizedUrl = normalizeUrlForHistory(url);
    }
    try {
      return originalPushState.call(this, state, title, normalizedUrl);
    } catch (error) {
      // If pushState still fails (e.g., due to origin mismatch), try with a relative URL
      if (
        error.name === 'SecurityError' &&
        normalizedUrl &&
        typeof normalizedUrl === 'string'
      ) {
        try {
          const urlObj = new URL(normalizedUrl, window.location.href);
          const relativeUrl = urlObj.pathname + urlObj.search + urlObj.hash;
          return originalPushState.call(this, state, title, relativeUrl);
        } catch {
          // If that also fails, rethrow the original error
          throw error;
        }
      }
      throw error;
    }
  };

  // Override replaceState
  window.history.replaceState = function (state, title, url) {
    let normalizedUrl = url;
    if (url && typeof url === 'string') {
      normalizedUrl = normalizeUrlForHistory(url);
    }
    try {
      return originalReplaceState.call(this, state, title, normalizedUrl);
    } catch (error) {
      // If replaceState still fails (e.g., due to origin mismatch), try with a relative URL
      if (
        error.name === 'SecurityError' &&
        normalizedUrl &&
        typeof normalizedUrl === 'string'
      ) {
        try {
          const urlObj = new URL(normalizedUrl, window.location.href);
          const relativeUrl = urlObj.pathname + urlObj.search + urlObj.hash;
          return originalReplaceState.call(this, state, title, relativeUrl);
        } catch {
          // If that also fails, rethrow the original error
          throw error;
        }
      }
      throw error;
    }
  };
}

function createPluginApi(dispatch) {
  return {
    registerTopNavLink(link) {
      const normalized = normalizeNavLink(link);
      if (!normalized) {
        return null;
      }
      dispatch({
        type: actions.REGISTER_TOP_NAV_LINK,
        payload: normalized,
      });
      return normalized.id;
    },
    registerRoute(route) {
      const normalized = normalizeRoute(route);
      if (!normalized) {
        return null;
      }
      dispatch({
        type: actions.REGISTER_ROUTE,
        payload: normalized,
      });
      return normalized.id;
    },
    registerComponent(config) {
      const normalized = normalizeComponent(config);
      if (!normalized) {
        return null;
      }
      dispatch({
        type: actions.REGISTER_COMPONENT,
        payload: normalized,
      });
      return normalized.id;
    },
    registerDataEnhancement(config) {
      const normalized = normalizeDataEnhancement(config);
      if (!normalized) {
        return null;
      }
      // Validate field conflicts with existing enhancements
      const existingEnhancements = getDataEnhancements(normalized.dataSource);
      if (normalized.fields && normalized.fields.length > 0) {
        const conflicts = [];
        existingEnhancements.forEach((existing) => {
          if (existing.fields && existing.fields.length > 0) {
            const overlap = normalized.fields.filter((f) =>
              existing.fields.includes(f)
            );
            if (overlap.length > 0) {
              conflicts.push({
                plugin: existing.id,
                fields: overlap,
              });
            }
          }
        });
        if (conflicts.length > 0) {
          console.warn(
            `[SkyDashboardPlugin] Field conflicts detected for ${normalized.id}:`,
            conflicts
          );
        }
      }
      dispatch({
        type: actions.REGISTER_DATA_ENHANCEMENT,
        payload: normalized,
      });
      return normalized.id;
    },
    registerTableColumn(config) {
      const normalized = normalizeTableColumn(config);
      if (!normalized) {
        return null;
      }
      dispatch({
        type: actions.REGISTER_TABLE_COLUMN,
        payload: normalized,
      });
      return normalized.id;
    },
    getContext() {
      return {
        basePath: BASE_PATH,
        apiEndpoint: ENDPOINT,
        dashboardCache: dashboardCache,
        grafanaUtils: {
          checkGrafanaAvailability,
          getGrafanaUrl,
        },
        // Provide URL normalization utility for plugins
        normalizeUrl: normalizeUrlForHistory,
      };
    },
    getComponents() {
      // Lazy import to avoid circular dependencies.
      // This dynamically provides all components from the ui directory.
      // eslint-disable-next-line no-undef
      return require('@/components/ui');
    },
    registerDataProvider(config) {
      if (!config?.id) {
        console.warn(
          '[SkyDashboardPlugin] Invalid data provider: missing id',
          config
        );
        return null;
      }
      const normalized = {
        id: String(config.id),
        name: config.name || config.id,
        useHook: config.useHook,
      };
      dispatch({
        type: actions.REGISTER_DATA_PROVIDER,
        payload: normalized,
      });
      console.log('[SkyDashboardPlugin] Registered data provider:', config.id);
      return config.id;
    },
  };
}

export function PluginProvider({ children }) {
  const [state, dispatch] = useReducer(pluginReducer, initialState);

  // Expose state reference for getDataEnhancements to access outside React context
  useEffect(() => {
    if (typeof window !== 'undefined') {
      window.__pluginStateRef = { current: state };
      return () => {
        if (window.__pluginStateRef) {
          delete window.__pluginStateRef;
        }
      };
    }
  }, [state]);

  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }

    // Intercept history API to normalize URLs and prevent SecurityError
    // when URLs contain credentials
    interceptHistoryApi();

    let cancelled = false;
    const api = createPluginApi(dispatch);
    window.SkyDashboardPluginAPI = api;
    window.dispatchEvent(
      new CustomEvent('skydashboard:plugins-ready', { detail: api })
    );
    const bootstrapPlugins = async () => {
      const manifest = await fetchPluginManifest();
      if (cancelled) {
        return;
      }
      manifest.forEach((pluginDescriptor) => {
        const jsPath = extractJsPath(pluginDescriptor);
        if (jsPath && !cancelled) {
          const requiresEarlyInit =
            pluginDescriptor.requires_early_init === true;
          loadPluginScript(jsPath, requiresEarlyInit);
        }
      });
    };
    void bootstrapPlugins();

    return () => {
      cancelled = true;
      if (window.SkyDashboardPluginAPI === api) {
        delete window.SkyDashboardPluginAPI;
      }
    };
  }, [dispatch]);

  const value = useMemo(() => state, [state]);

  return (
    <PluginContext.Provider value={value}>{children}</PluginContext.Provider>
  );
}

export function usePluginState() {
  return useContext(PluginContext);
}

export function useTopNavLinks() {
  const { topNavLinks } = usePluginState();
  return useMemo(
    () =>
      [...topNavLinks].sort((a, b) => {
        return a.order - b.order;
      }),
    [topNavLinks]
  );
}

export function useGroupedNavLinks() {
  const { topNavLinks } = usePluginState();

  return useMemo(() => {
    const sorted = [...topNavLinks].sort((a, b) => a.order - b.order);

    // Separate links with and without group
    const ungrouped = sorted.filter((link) => !link.group);
    const grouped = sorted.filter((link) => link.group);

    // Categorize by group
    const groups = grouped.reduce((acc, link) => {
      const groupName = link.group;
      if (!acc[groupName]) {
        acc[groupName] = [];
      }
      acc[groupName].push(link);
      return acc;
    }, {});

    return { ungrouped, groups };
  }, [topNavLinks]);
}

export function usePluginRoutes() {
  const { routes } = usePluginState();
  return routes;
}

export function usePluginRoute(pathname) {
  const routes = usePluginRoutes();
  return useMemo(() => {
    if (!pathname) {
      return null;
    }
    return routes.find((route) => route.path === pathname) || null;
  }, [pathname, routes]);
}

export function usePluginComponents(slot) {
  const { components } = usePluginState();
  return useMemo(() => {
    if (!slot) {
      return [];
    }
    const slotComponents = components[slot] || [];
    // Filter by conditions if needed (e.g., page-specific components)
    if (typeof window === 'undefined') {
      return slotComponents;
    }
    const currentPath = window.location.pathname;
    return slotComponents.filter((config) => {
      if (config.conditions?.pages) {
        return config.conditions.pages.some((page) =>
          currentPath.includes(page)
        );
      }
      return true;
    });
  }, [slot, components]);
}

/**
 * Get data enhancements for a specific data source
 * @param {string} dataSource - The data source name (e.g., 'jobs', 'clusters')
 * @returns {Array} Array of enhancement configurations
 */
export function getDataEnhancements(dataSource) {
  // This function needs access to the current state
  // Since it's called from outside React context, we need to access it differently
  // For now, we'll use a module-level state reference
  if (typeof window !== 'undefined' && window.__pluginStateRef) {
    const state = window.__pluginStateRef.current;
    return state?.dataEnhancements?.[dataSource] || [];
  }
  return [];
}

/**
 * Hook to get table columns for a specific table
 * @param {string} tableName - The table name (e.g., 'clusters', 'jobs')
 * @param {object} context - Optional context for filtering columns
 * @returns {Array} Array of column configurations sorted by order
 */
export function useTableColumns(tableName, context = {}) {
  const { tableColumns } = usePluginState();
  return useMemo(() => {
    if (!tableName) {
      return [];
    }
    const columns = tableColumns[tableName] || [];
    // Filter by conditions if provided
    return columns.filter((column) => {
      if (column.conditions?.showWhen) {
        return column.conditions.showWhen(context);
      }
      return true;
    });
  }, [tableName, tableColumns, context]);
}

export function useDataProvider(id) {
  const { dataProviders } = usePluginState();
  return dataProviders[id] || null;
}

/**
 * Hook to merge base columns with plugin columns, automatically handling replacements.
 * Plugin columns with the same ID as base columns will replace the base columns.
 *
 * @param {string} tableName - The table name (e.g., 'clusters', 'jobs')
 * @param {Array} baseColumns - Array of base column definitions
 * @param {object} context - Optional context for filtering columns and conditional display
 * @param {function} transformPluginColumn - Optional function to transform plugin columns into the format expected by the table
 * @returns {Array} Merged and filtered columns, sorted by order
 */
export function useMergedTableColumns(
  tableName,
  baseColumns = [],
  context = {},
  transformPluginColumn = null
) {
  const pluginColumns = useTableColumns(tableName, context);

  return useMemo(() => {
    // Transform plugin columns if a transform function is provided
    const pluginColumnDefs = transformPluginColumn
      ? pluginColumns.map((col) => transformPluginColumn(col))
      : pluginColumns.map((col) => ({
          id: col.id,
          order: col.header.order,
          isPlugin: true,
          pluginColumn: col,
        }));

    // Create a set of plugin column IDs to identify replacements
    const pluginColumnIds = new Set(pluginColumnDefs.map((col) => col.id));

    // Merge base and plugin columns, sort by order
    const allColumns = [...baseColumns, ...pluginColumnDefs].sort(
      (a, b) => a.order - b.order
    );

    // Filter columns:
    // 1. Remove base columns that have a plugin replacement (same ID)
    // 2. Handle conditional columns based on context
    const visibleColumns = allColumns.filter((col) => {
      // Filter out base columns that have a plugin replacement
      if (!col.isPlugin && pluginColumnIds.has(col.id)) {
        return false;
      }

      // Handle conditional columns
      if (col.conditional) {
        // Allow context to provide a function to check conditional columns
        if (
          context.shouldShowColumn &&
          typeof context.shouldShowColumn === 'function'
        ) {
          return context.shouldShowColumn(col.id);
        }
        // Default: don't show conditional columns unless explicitly enabled
        return false;
      }

      return true;
    });

    return visibleColumns;
  }, [baseColumns, pluginColumns, transformPluginColumn, context]);
}
