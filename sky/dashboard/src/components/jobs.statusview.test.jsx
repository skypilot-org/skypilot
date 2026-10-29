// `data/connectors/jobs` imports the plugin data-enhancement module, which
// imports back into it through the cache preloader. Loading that cycle from a
// test entry point hits the temporal-dead-zone error that already breaks
// `data/connectors/jobs.test.jsx`; the pure function under test needs none of
// it, so cut the edge here.
jest.mock('@/plugins/dataEnhancement', () => ({
  applyEnhancements: async (data) => data,
}));

import { deriveStatusView, statusGroups } from '@/components/jobs';

// The Managed Jobs page keeps its whole status UI -- the Active/All segments
// and the pill bar -- in one `status` query param. These cover the values a
// hand-edited or stale link can carry, because an unrecognised one used to
// leave `activeTab` pointing at a group that does not exist and crashed the
// page on render.
describe('deriveStatusView', () => {
  it('treats a missing status as "every status", with All highlighted', () => {
    expect(deriveStatusView('')).toEqual({
      statusGroupName: null,
      selectedStatuses: [],
      activeTab: 'all',
    });
    expect(deriveStatusView(undefined).activeTab).toBe('all');
  });

  it('recognises a group name and highlights that segment', () => {
    expect(deriveStatusView('active')).toEqual({
      statusGroupName: 'active',
      selectedStatuses: [],
      activeTab: 'active',
    });
    expect(deriveStatusView('finished').statusGroupName).toBe('finished');
  });

  it('reads a comma list as pills, with neither segment highlighted', () => {
    expect(deriveStatusView('RUNNING,SUCCEEDED')).toEqual({
      statusGroupName: null,
      selectedStatuses: ['RUNNING', 'SUCCEEDED'],
      activeTab: null,
    });
  });

  it('drops values that name no known status', () => {
    expect(deriveStatusView('RUNNINGG')).toEqual({
      statusGroupName: null,
      selectedStatuses: [],
      activeTab: 'all',
    });
    expect(deriveStatusView('RUNNING,RUNNINGG').selectedStatuses).toEqual([
      'RUNNING',
    ]);
  });

  it('never leaves activeTab naming a group that does not exist', () => {
    // `?status=,` is the minimal reproduction: truthy param, no usable
    // statuses. `statusGroups[activeTab]` must stay dereferenceable.
    for (const value of [',', ',,', ' ', 'ACTIVE', 'RUNNINGG']) {
      const { activeTab } = deriveStatusView(value);
      expect(activeTab === 'all' || statusGroups[activeTab]).toBeTruthy();
    }
  });
});
