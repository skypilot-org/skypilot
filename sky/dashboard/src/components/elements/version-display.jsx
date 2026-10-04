import React, { useState, useEffect, createContext, useContext } from 'react';
import { ArrowUpCircle, Bell } from 'lucide-react';
import { NonCapitalizedTooltip } from '@/components/utils';
import { apiClient } from '@/data/connectors/client';

const VersionContext = createContext({
  version: null,
  latestVersion: null,
  commit: null,
  plugins: [],
});

export function VersionProvider({ children }) {
  const [version, setVersion] = useState(null);
  const [latestVersion, setLatestVersion] = useState(null);
  const [commit, setCommit] = useState(null);
  const [plugins, setPlugins] = useState([]);

  const getVersionAndPlugins = async () => {
    // Concurrently fetch health and plugins data
    const [healthResponse, pluginsResponse] = await Promise.all([
      apiClient.get('/api/health'),
      apiClient.get('/api/plugins'),
    ]);

    // Process health data
    if (healthResponse.ok) {
      const healthData = await healthResponse.json();
      if (healthData.version) {
        setVersion(healthData.version);
      }
      if (healthData.commit) {
        setCommit(healthData.commit);
      }
      if (healthData.latest_version) {
        setLatestVersion(healthData.latest_version);
      }
    } else {
      console.error(
        `API request /api/health failed with status ${healthResponse.status}`
      );
    }

    // Process plugins data
    if (pluginsResponse.ok) {
      const pluginsData = await pluginsResponse.json();
      if (pluginsData.plugins && pluginsData.plugins.length > 0) {
        setPlugins(pluginsData.plugins);
      }
    } else {
      console.error(
        `API request /api/plugins failed with status ${pluginsResponse.status}`
      );
    }
  };

  useEffect(() => {
    getVersionAndPlugins();
  }, []);

  return (
    <VersionContext.Provider
      value={{ version, latestVersion, commit, plugins }}
    >
      {children}
    </VersionContext.Provider>
  );
}

export function useVersionInfo() {
  return useContext(VersionContext);
}

/**
 * What the version tooltip shows once it is open: the core version/commit and
 * one line per plugin that has not opted out of the display.
 *
 * Exported separately from the tooltip that hosts it because the hosting
 * tooltip only mounts its content while open, and this is the part with the
 * behaviour worth asserting on.
 */
export function VersionTooltipContent({
  version,
  latestVersion,
  commit,
  plugins,
  showUpdateInfo = true,
  showCommit = true,
}) {
  // Everything below keys off what is actually shown, not off the raw list: a
  // plugin that opted out of the display should not make the core commit call
  // itself "Core", and should not suppress the "not available" fallback.
  const visiblePlugins = plugins.filter(
    (plugin) => !plugin.hidden_from_display
  );

  return (
    <div className="flex flex-col gap-0.5">
      {showUpdateInfo && latestVersion && (
        <div className="mb-1">
          <div className="font-bold">Update Available</div>
          <div>Current version: {version}</div>
          <div>New version available: {latestVersion}</div>
        </div>
      )}
      {showCommit && commit && (
        <div>
          {visiblePlugins.length > 0 ? 'Core commit' : 'Commit'}: {commit}
        </div>
      )}
      {visiblePlugins.map((plugin, index) => {
        const pluginName = plugin.name || 'Unknown Plugin';
        const parts = [];
        if (plugin.version) parts.push(plugin.version);
        if (showCommit && plugin.commit) parts.push(plugin.commit);
        return parts.length > 0 ? (
          <div key={index}>
            {pluginName}: {parts.join(' - ')}
          </div>
        ) : null;
      })}
      {!commit &&
        visiblePlugins.length === 0 &&
        (!latestVersion || !showUpdateInfo) && (
          <div>Version information not available</div>
        )}
    </div>
  );
}

export function VersionTooltip({
  children,
  version,
  latestVersion,
  commit,
  plugins,
  showUpdateInfo = true,
  showCommit = true,
}) {
  return (
    <NonCapitalizedTooltip
      content={
        <VersionTooltipContent
          version={version}
          latestVersion={latestVersion}
          commit={commit}
          plugins={plugins}
          showUpdateInfo={showUpdateInfo}
          showCommit={showCommit}
        />
      }
      className="text-sm text-muted-foreground"
    >
      {children}
    </NonCapitalizedTooltip>
  );
}

export function UpgradeHint() {
  const { version, latestVersion, commit, plugins } = useVersionInfo();

  if (!version || !latestVersion) return null;

  return (
    <VersionTooltip
      version={version}
      latestVersion={latestVersion}
      commit={commit}
      plugins={plugins}
      showCommit={false}
    >
      <div className="inline-flex items-center justify-center transition-colors duration-150 cursor-help">
        <div className="p-2 rounded-full text-gray-600 hover:bg-gray-100 hover:text-blue-600">
          <Bell className="w-5 h-5" />
        </div>
      </div>
    </VersionTooltip>
  );
}

export function NewVersionAvailable() {
  const { latestVersion } = useVersionInfo();

  if (!latestVersion) return null;

  return (
    <div className="flex items-center mr-4 text-amber-600 animate-pulse">
      <ArrowUpCircle className="w-4 h-4 mr-1.5" />
      <span className="text-sm font-medium">
        New version available: {latestVersion}
      </span>
    </div>
  );
}

export function VersionDisplay() {
  const { version, latestVersion, commit, plugins } = useVersionInfo();

  if (!version) return null;

  return (
    <VersionTooltip
      version={version}
      latestVersion={latestVersion}
      commit={commit}
      plugins={plugins}
      showUpdateInfo={false}
    >
      <div className="inline-flex items-center justify-center transition-colors duration-150 cursor-help">
        <div className="text-sm text-gray-500 border-b border-dotted border-gray-400 hover:text-blue-600 hover:border-blue-600">
          Version: {version}
        </div>
      </div>
    </VersionTooltip>
  );
}
