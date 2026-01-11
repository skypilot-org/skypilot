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
});

const initialState = {
  topNavLinks: [],
  routes: [],
  components: {}, // Map of slot name → array of component configs
  dataFetchers: {}, // Map of resource type → fetcher object
  features: {}, // Map of feature name → feature config
  dataProviders: {}, // Map of provider id → provider config
};

const actions = {
  REGISTER_TOP_NAV_LINK: 'REGISTER_TOP_NAV_LINK',
  REGISTER_ROUTE: 'REGISTER_ROUTE',
  REGISTER_COMPONENT: 'REGISTER_COMPONENT',
  REGISTER_DATA_FETCHER: 'REGISTER_DATA_FETCHER',
  REGISTER_FEATURE: 'REGISTER_FEATURE',
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
    case actions.REGISTER_DATA_FETCHER: {
      const { resourceType, fetcher } = action.payload;
      return {
        ...state,
        dataFetchers: {
          ...state.dataFetchers,
          [resourceType]: fetcher,
        },
      };
    }
    case actions.REGISTER_FEATURE: {
      const { name, config } = action.payload;
      return {
        ...state,
        features: {
          ...state.features,
          [name]: config,
        },
      };
    }
    case actions.REGISTER_DATA_PROVIDER: {
      const { id } = action.payload;
      return {
        ...state,
        dataProviders: {
          ...state.dataProviders,
          [id]: action.payload,
        },
      };
    }
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

function loadPluginScript(jsPath) {
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
    icon: typeof link.icon === 'string' ? link.icon : null,
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
    getContext() {
      return {
        basePath: BASE_PATH,
        apiEndpoint: ENDPOINT,
        dashboardCache: dashboardCache,
        grafanaUtils: {
          checkGrafanaAvailability,
          getGrafanaUrl,
        },
      };
    },
    registerDataFetcher(resourceType, fetcher) {
      if (!resourceType || typeof resourceType !== 'string') {
        console.warn(
          '[SkyDashboardPlugin] Invalid data fetcher registration: missing resourceType'
        );
        return null;
      }
      if (!fetcher || typeof fetcher !== 'object') {
        console.warn(
          '[SkyDashboardPlugin] Invalid data fetcher registration: fetcher must be an object'
        );
        return null;
      }
      dispatch({
        type: actions.REGISTER_DATA_FETCHER,
        payload: { resourceType, fetcher },
      });
      console.log('[SkyDashboardPlugin] Registered data fetcher:', resourceType);
      return resourceType;
    },
    getDefaultFetcher(resourceType) {
      // Return null - plugins can use this as fallback
      // In the future, this could return the default fetcher for clusters/jobs
      return null;
    },
    registerFeature(name, config) {
      if (!name || typeof name !== 'string') {
        console.warn(
          '[SkyDashboardPlugin] Invalid feature registration: missing name'
        );
        return null;
      }
      dispatch({
        type: actions.REGISTER_FEATURE,
        payload: { name, config: config || {} },
      });
      console.log('[SkyDashboardPlugin] Registered feature:', name);
      return name;
    },
    registerDataProvider(providerConfig) {
      if (!providerConfig || typeof providerConfig !== 'object' || !providerConfig.id) {
        console.warn(
          '[SkyDashboardPlugin] Invalid data provider registration:',
          providerConfig
        );
        return null;
      }
      dispatch({
        type: actions.REGISTER_DATA_PROVIDER,
        payload: providerConfig,
      });
      console.log('[SkyDashboardPlugin] Registered data provider:', providerConfig.id);
      return providerConfig.id;
    },
  };
}

export function PluginProvider({ children }) {
  const [state, dispatch] = useReducer(pluginReducer, initialState);

  // Expose plugin state on window for non-React code (e.g., data connectors)
  useEffect(() => {
    if (typeof window !== 'undefined') {
      window.__SKYDASHBOARD_PLUGIN_STATE__ = state;
    }
    return () => {
      if (typeof window !== 'undefined' && window.__SKYDASHBOARD_PLUGIN_STATE__ === state) {
        delete window.__SKYDASHBOARD_PLUGIN_STATE__;
      }
    };
  }, [state]);

  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }

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
      manifest
        .map((pluginDescriptor) => extractJsPath(pluginDescriptor))
        .filter(Boolean)
        .forEach((jsPath) => {
          if (!cancelled) {
            loadPluginScript(jsPath);
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
  }, []);

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

export function useDataFetcher(resourceType) {
  const { dataFetchers } = usePluginState();
  return useMemo(() => {
    if (!resourceType) {
      return null;
    }
    return dataFetchers[resourceType] || null;
  }, [resourceType, dataFetchers]);
}

export function useFeature(featureName) {
  const { features } = usePluginState();
  return useMemo(() => {
    if (!featureName) {
      return null;
    }
    return features[featureName] || null;
  }, [featureName, features]);
}

export function useDataProvider(providerId) {
  const { dataProviders } = usePluginState();
  return useMemo(() => {
    if (!providerId) {
      return null;
    }
    return dataProviders[providerId] || null;
  }, [providerId, dataProviders]);
}

export function useAllDataProviders() {
  const { dataProviders } = usePluginState();
  return dataProviders;
}
