import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import { Layout } from '@/components/elements/layout';
import Link from 'next/link';
import Head from 'next/head';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { getConfig, updateConfig } from '@/data/connectors/workspaces';
import { ErrorDisplay } from '@/components/elements/ErrorDisplay';
import { CircularProgress } from '@mui/material';
import { SaveIcon, RotateCwIcon } from 'lucide-react';
import yaml from 'js-yaml';

export default function ConfigPage() {
  const router = useRouter();
  const [editableConfig, setEditableConfig] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [saveSuccess, setSaveSuccess] = useState(false);

  useEffect(() => {
    loadConfig();
  }, []);

  const loadConfig = async () => {
    setLoading(true);
    setError(null);
    try {
      const config = await getConfig();
      if (Object.keys(config).length === 0) {
        setEditableConfig(
          '# Empty SkyPilot config. Enter config in YAML format\n'
        );
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
    setSaveSuccess(false);
    try {
      const parsedConfig = yaml.load(editableConfig);
      await updateConfig(parsedConfig);
      setSaveSuccess(true);
      // Auto-hide success message after 3 seconds
      setTimeout(() => setSaveSuccess(false), 3000);
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

  return (
    <>
      <Head>
        <title>Edit SkyPilot Configuration | SkyPilot Dashboard</title>
      </Head>
      <Layout highlighted="workspaces">
        <div className="flex items-center justify-between mb-4 h-5">
          <div className="text-base flex items-center">
            <span className="text-sky-blue">SkyPilot Configuration</span>
          </div>

          <div className="text-sm flex items-center">
            {(loading || saving) && (
              <div className="flex items-center mr-4">
                <CircularProgress size={15} className="mt-0" />
                <span className="ml-2 text-gray-500">
                  {saving ? 'Applying...' : 'Loading...'}
                </span>
              </div>
            )}

            <div className="flex items-center space-x-4">
              <button
                onClick={loadConfig}
                disabled={loading || saving}
                className="text-sky-blue hover:text-sky-blue-bright font-medium inline-flex items-center"
              >
                <RotateCwIcon className="w-4 h-4 mr-1.5" />
                Refresh
              </button>
            </div>
          </div>
        </div>

        {/* Main Content */}
        <Card className="w-full">
          <CardHeader>
            <CardTitle className="text-base font-normal flex items-center justify-between">
              <span>Edit SkyPilot Configuration YAML</span>
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
              for details. Click{' '}
              <button
                onClick={loadConfig}
                disabled={loading || saving}
                className="text-blue-600 hover:underline underline-offset-2 disabled:text-gray-400 disabled:no-underline"
              >
                Refresh
              </button>{' '}
              to reset to the latest config on API server.
            </p>

            {/* Success Message */}
            {saveSuccess && (
              <div className="bg-green-50 border border-green-200 rounded p-4 mb-6">
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
                placeholder="Loading configuration..."
                disabled={loading || saving}
              />
            </div>

            <div className="flex justify-end space-x-3 pt-3">
              <Button
                variant="outline"
                onClick={handleCancel}
                disabled={saving}
              >
                Cancel
              </Button>
              <Button
                onClick={handleSave}
                disabled={loading || saving || !editableConfig.trim()}
                className="inline-flex items-center bg-blue-600 hover:bg-blue-700 text-white"
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
      </Layout>
    </>
  );
}
