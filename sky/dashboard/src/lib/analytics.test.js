/**
 * Tests for PostHog product analytics wrapper (analytics.js).
 */

// Mock posthog-js before importing analytics module.
jest.mock('posthog-js', () => ({
  init: jest.fn(),
  identify: jest.fn(),
  register: jest.fn(),
  capture: jest.fn(),
}));

// Use a fresh module for each test so _initialized resets.
let analytics;
let posthog;

beforeEach(() => {
  jest.resetModules();
  jest.resetAllMocks();

  // Re-require after module reset to get fresh _initialized state.
  posthog = require('posthog-js');
  analytics = require('./analytics');
});

describe('initPostHog', () => {
  test('initializes posthog with correct config', () => {
    analytics.initPostHog();

    expect(posthog.init).toHaveBeenCalledTimes(1);
    expect(posthog.init).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({
        api_host: 'https://us.i.posthog.com',
        autocapture: true,
        capture_pageview: false,
        capture_pageleave: true,
        persistence: 'localStorage',
      })
    );
  });

  test('only initializes once on multiple calls', () => {
    analytics.initPostHog();
    analytics.initPostHog();
    analytics.initPostHog();

    expect(posthog.init).toHaveBeenCalledTimes(1);
  });
});

describe('isEnabled', () => {
  test('returns false before init', () => {
    expect(analytics.isEnabled()).toBe(false);
  });

  test('returns true after init', () => {
    analytics.initPostHog();
    expect(analytics.isEnabled()).toBe(true);
  });
});

describe('identifyUser', () => {
  test('calls posthog.identify with correct args', () => {
    analytics.initPostHog();
    analytics.identifyUser('hash123', 'alice');

    expect(posthog.identify).toHaveBeenCalledWith('hash123', {
      username: 'alice',
      source: 'dashboard',
    });
  });

  test('passes extra properties', () => {
    analytics.initPostHog();
    analytics.identifyUser('hash123', 'alice', { role: 'admin' });

    expect(posthog.identify).toHaveBeenCalledWith('hash123', {
      username: 'alice',
      source: 'dashboard',
      role: 'admin',
    });
  });

  test('is a no-op before init', () => {
    analytics.identifyUser('hash123', 'alice');
    expect(posthog.identify).not.toHaveBeenCalled();
  });
});

describe('registerDeployment', () => {
  test('calls posthog.register with source', () => {
    analytics.initPostHog();
    analytics.registerDeployment({ sky_version: '1.0' });

    expect(posthog.register).toHaveBeenCalledWith({
      source: 'dashboard',
      sky_version: '1.0',
    });
  });
});

describe('trackPageView', () => {
  test('captures $pageview event with path', () => {
    analytics.initPostHog();
    analytics.trackPageView('/clusters');

    expect(posthog.capture).toHaveBeenCalledWith(
      '$pageview',
      expect.objectContaining({
        path: '/clusters',
      })
    );
  });
});

describe('domain-specific tracking', () => {
  beforeEach(() => {
    analytics.initPostHog();
  });

  test('trackClusterAction captures cluster_action', () => {
    analytics.trackClusterAction('ssh', { cluster: 'mycluster' });

    expect(posthog.capture).toHaveBeenCalledWith('cluster_action', {
      source: 'dashboard',
      action: 'ssh',
      cluster: 'mycluster',
    });
  });

  test('trackJobAction captures job_action', () => {
    analytics.trackJobAction('view_logs', { jobId: '42' });

    expect(posthog.capture).toHaveBeenCalledWith('job_action', {
      source: 'dashboard',
      action: 'view_logs',
      jobId: '42',
    });
  });

  test('trackWorkspaceAction captures workspace_action', () => {
    analytics.trackWorkspaceAction('create');

    expect(posthog.capture).toHaveBeenCalledWith('workspace_action', {
      source: 'dashboard',
      action: 'create',
    });
  });

  test('trackRecipeAction captures recipe_action', () => {
    analytics.trackRecipeAction('view', { recipe: 'llama' });

    expect(posthog.capture).toHaveBeenCalledWith('recipe_action', {
      source: 'dashboard',
      action: 'view',
      recipe: 'llama',
    });
  });

  test('trackInfraAction captures infra_action', () => {
    analytics.trackInfraAction('refresh');

    expect(posthog.capture).toHaveBeenCalledWith('infra_action', {
      source: 'dashboard',
      action: 'refresh',
    });
  });

  test('trackFilterUsed captures filter_used with filter_type', () => {
    analytics.trackFilterUsed('cluster', { property: 'status' });

    expect(posthog.capture).toHaveBeenCalledWith('filter_used', {
      source: 'dashboard',
      filter_type: 'cluster',
      property: 'status',
    });
  });

  test('trackPluginPageView captures plugin_page_view', () => {
    analytics.trackPluginPageView('gpu_healer', '/health');

    expect(posthog.capture).toHaveBeenCalledWith('plugin_page_view', {
      source: 'dashboard',
      plugin: 'gpu_healer',
      path: '/health',
    });
  });
});

describe('no-ops before init', () => {
  test('all track functions are no-ops before init', () => {
    analytics.trackPageView('/clusters');
    analytics.trackEvent('test_event');
    analytics.trackClusterAction('ssh');
    analytics.trackJobAction('view_logs');
    analytics.trackWorkspaceAction('create');
    analytics.trackRecipeAction('view');
    analytics.trackInfraAction('refresh');
    analytics.trackFilterUsed('cluster');
    analytics.trackPluginPageView('test', '/path');
    analytics.registerDeployment({ version: '1.0' });

    expect(posthog.capture).not.toHaveBeenCalled();
    expect(posthog.register).not.toHaveBeenCalled();
  });
});
