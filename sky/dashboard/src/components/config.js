'use client';

import React, { useState, useEffect, useRef } from 'react';
import { useRouter } from 'next/router';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { getConfig, updateConfig } from '@/data/connectors/workspaces';
import { ErrorDisplay } from '@/components/elements/ErrorDisplay';
import { CircularProgress } from '@mui/material';
import { SaveIcon } from 'lucide-react';
import yaml from 'js-yaml';
import { VersionDisplay } from '@/components/elements/version-display';
import { apiClient } from '@/data/connectors/client';
import { checkGrafanaAvailability, getGrafanaUrl } from '@/utils/grafana';

export function Config() {
  const router = useRouter();
  const [editableConfig, setEditableConfig] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [isGrafanaAvailable, setIsGrafanaAvailable] = useState(false);
  const successTimeoutRef = useRef(null);

  useEffect(() => {
    loadConfig();

    // Check Grafana availability
    const checkGrafana = async () => {
      const available = await checkGrafanaAvailability();
      setIsGrafanaAvailable(available);
    };

    if (typeof window !== 'undefined') {
      checkGrafana();
    }
  }, []);

  // Cleanup timeout on unmount
  useEffect(() => {
    return () => {
      if (successTimeoutRef.current) {
        clearTimeout(successTimeoutRef.current);
      }
    };
  }, []);

  const loadConfig = async () => {
    setLoading(true);
    setError(null);
    try {
      const config = await getConfig();
      if (Object.keys(config).length === 0) {
        setEditableConfig('');
      } else {
        setEditableConfig(yaml.dump(config, { indent: 2 }));
      }
    } catch (error) {
      console.error('Error loading config:', error);
      setError(error);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    // Clear any existing success timeout
    if (successTimeoutRef.current) {
      clearTimeout(successTimeoutRef.current);
      successTimeoutRef.current = null;
    }

    try {
      // Check user role first
      const response = await apiClient.get(`/users/role`);
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to get user role');
      }
      const data = await response.json();
      const currentUserRole = data.role;

      if (currentUserRole != 'admin') {
        setError(
          new Error(
            `${data.name} is logged in as non-admin and cannot edit config`
          )
        );
        setSaving(false);
        return;
      }

      let parsedConfig = yaml.load(editableConfig);

      // If the config is empty, only comments, or only whitespace,
      // treat it as an empty object.
      if (parsedConfig === null || parsedConfig === undefined) {
        parsedConfig = {};
      }

      // Client-side validation for the parsed configuration structure
      if (typeof parsedConfig !== 'object' || Array.isArray(parsedConfig)) {
        let validationMessage =
          'Invalid config structure: Configuration must be a mapping (key-value pairs) in YAML format.';
        if (Array.isArray(parsedConfig)) {
          validationMessage =
            'Invalid config structure: Configuration must be a mapping (key-value pairs) in YAML format.';
        } else {
          // This case would catch scalars if not for the null check above
          validationMessage =
            'Invalid config structure: Configuration must be a mapping (key-value pairs) in YAML format.';
        }
        setError(new Error(validationMessage));
        setSaving(false);
        return;
      }

      await updateConfig(parsedConfig);
      setSaveSuccess(true);
      // Auto-hide success message after 5 seconds
      successTimeoutRef.current = setTimeout(() => {
        setSaveSuccess(false);
        successTimeoutRef.current = null;
      }, 5000);
    } catch (error) {
      console.error('Error saving config:', error);
      setError(error);
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => {
    router.push('/workspaces');
  };

  const handleReset = () => {
    loadConfig();
  };

  const handleSuccessDismiss = () => {
    setSaveSuccess(false);
    if (successTimeoutRef.current) {
      clearTimeout(successTimeoutRef.current);
      successTimeoutRef.current = null;
    }
  };

  return (
    <>
      <div className="flex items-center justify-between mb-4 h-5">
        <div className="text-base flex items-center">
          <span className="text-sky-blue">SkyPilot API Server</span>
        </div>

        <div className="flex items-center">
          <div className="text-sm flex items-center">
            {(loading || saving) && (
              <div className="flex items-center mr-4">
                <CircularProgress size={15} className="mt-0" />
                <span className="ml-2 text-gray-500">
                  {saving ? 'Applying...' : 'Loading...'}
                </span>
              </div>
            )}
          </div>
          {isGrafanaAvailable && (
            <button
              onClick={() => {
                const grafanaUrl = getGrafanaUrl();
                const host = window.location.hostname;
                window.open(
                  `${grafanaUrl}/d/skypilot-apiserver-overview/skypilot-api-server?orgId=1&from=now-1h&to=now&timezone=browser&var-app=${host}`,
                  '_blank'
                );
              }}
              className="inline-flex items-center px-3 py-2 text-sm font-medium text-white bg-sky-blue-bright border border-transparent rounded-md shadow-sm hover:bg-sky-blue focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-sky-blue mr-4"
            >
              <svg
                className="w-4 h-4 mr-2"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
                xmlns="http://www.w3.org/2000/svg"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
                />
              </svg>
              View API Server Metrics
            </button>
          )}
          <VersionDisplay />
        </div>
      </div>

      {/* Main Content */}
      <Card className="w-full">
        <CardHeader>
          <CardTitle className="text-base font-normal flex items-center justify-between">
            <span>Edit SkyPilot API Server Configuration</span>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-gray-600 mb-3">
            Refer to the{' '}
            <a
              href="https://docs.skypilot.co/en/latest/reference/config.html"
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-600 hover:underline"
            >
              SkyPilot Docs
            </a>{' '}
            for details. The configuration should be in YAML format.
          </p>

          {/* Success Message */}
          {saveSuccess && (
            <div className="bg-green-50 border border-green-200 rounded p-4 mb-6">
              <div className="flex items-center justify-between">
                <div className="flex items-center">
                  <div className="flex-shrink-0">
                    <svg
                      className="h-5 w-5 text-green-400"
                      viewBox="0 0 20 20"
                      fill="currentColor"
                    >
                      <path
                        fillRule="evenodd"
                        d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z"
                        clipRule="evenodd"
                      />
                    </svg>
                  </div>
                  <div className="ml-3">
                    <p className="text-sm font-medium text-green-800">
                      Configuration saved successfully!
                    </p>
                  </div>
                </div>
                <div className="ml-auto pl-3">
                  <div className="-mx-1.5 -my-1.5">
                    <button
                      type="button"
                      onClick={handleSuccessDismiss}
                      className="inline-flex rounded-md bg-green-50 p-1.5 text-green-500 hover:bg-green-100 focus:outline-none focus:ring-2 focus:ring-green-600 focus:ring-offset-2 focus:ring-offset-green-50"
                    >
                      <span className="sr-only">Dismiss</span>
                      <svg
                        className="h-5 w-5"
                        viewBox="0 0 20 20"
                        fill="currentColor"
                      >
                        <path
                          fillRule="evenodd"
                          d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                          clipRule="evenodd"
                        />
                      </svg>
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Error Display */}
          {error && (
            <div className="mb-6">
              <ErrorDisplay
                error={error}
                title="Failed to apply new configuration"
                onDismiss={() => setError(null)}
              />
            </div>
          )}

          <div className="w-full">
            <textarea
              value={editableConfig}
              onChange={(e) => setEditableConfig(e.target.value)}
              className="w-full h-96 p-3 border border-gray-300 rounded font-mono text-sm resize-vertical focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder={
                loading
                  ? 'Loading configuration...'
                  : '# Enter SkyPilot configuration in YAML format\n# Example:\n# kubernetes:\n#   allowed_contexts: [default, my-context]'
              }
              disabled={loading || saving}
            />
          </div>

          <div className="flex justify-end space-x-3 pt-3">
            <Button variant="outline" onClick={handleCancel} disabled={saving}>
              Cancel
            </Button>
            <Button
              onClick={handleSave}
              disabled={loading || saving}
              className="inline-flex items-center bg-sky-600 hover:bg-sky-700 text-white"
            >
              {saving ? (
                <>
                  <CircularProgress size={16} className="mr-2" />
                  Applying...
                </>
              ) : (
                <>
                  <SaveIcon className="w-4 h-4 mr-1.5" />
                  Apply
                </>
              )}
            </Button>
          </div>
        </CardContent>
      </Card>
    </>
  );
}
