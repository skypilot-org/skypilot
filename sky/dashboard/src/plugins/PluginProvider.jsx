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

const PluginContext = createContext({
  topNavLinks: [],
  routes: [],
});

const initialState = {
  topNavLinks: [],
  routes: [],
};

const actions = {
  REGISTER_TOP_NAV_LINK: 'REGISTER_TOP_NAV_LINK',
  REGISTER_ROUTE: 'REGISTER_ROUTE',
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
    getContext() {
      return {
        basePath: BASE_PATH,
        apiEndpoint: ENDPOINT,
      };
    },
  };
}

export function PluginProvider({ children }) {
  const [state, dispatch] = useReducer(pluginReducer, initialState);

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
