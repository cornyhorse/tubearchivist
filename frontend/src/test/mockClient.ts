import { vi } from 'vitest';

/** every network call of api/ux/client as a mock; notBuilt and toRuleBody stay real */
export const clientMock = {
  getQueueSummary: vi.fn(),
  listJobs: vi.fn(),
  createJob: vi.fn(),
  jobAction: vi.fn(),
  listLanes: vi.fn(),
  updateLane: vi.fn(),
  listScheduled: vi.fn(),
  updateScheduled: vi.fn(),
  runScheduled: vi.fn(),
  listFound: vi.fn(),
  foundAction: vi.fn(),
  previewAdd: vi.fn(),
  addUrls: vi.fn(),
  listRules: vi.fn(),
  listRulePresets: vi.fn(),
  getRule: vi.fn(),
  saveRule: vi.fn(),
  deleteRule: vi.fn(),
  previewRule: vi.fn(),
  getEffectiveRule: vi.fn(),
  listVideoOverrides: vi.fn(),
  setVideoOverride: vi.fn(),
  clearVideoOverride: vi.fn(),
  listSchemes: vi.fn(),
  previewScheme: vi.fn(),
  setScheme: vi.fn(),
  getVideoNumbering: vi.fn(),
  setVideoNumbering: vi.fn(),
  clearVideoNumbering: vi.fn(),
  getMediaServer: vi.fn(),
  saveMediaServer: vi.fn(),
  testMediaServer: vi.fn(),
  listLibraries: vi.fn(),
  notifyMediaServer: vi.fn(),
  refreshMediaServer: vi.fn(),
};

/** reset, and make every call answer 501 unless a test says otherwise */
export const resetClientMock = () => {
  for (const fn of Object.values(clientMock)) {
    fn.mockReset();
    fn.mockResolvedValue({ status: 501, error: { error: 'not built yet' } });
  }
};
