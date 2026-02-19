/**
 * GPU name canonicalization utilities.
 *
 * Mirrors the logic from SkyPilot's Python backend
 * (sky.provision.kubernetes.utils.GFDLabelFormatter) so the dashboard
 * displays clean, consistent GPU names regardless of the raw label value
 * reported by different Kubernetes GPU discovery plugins.
 */

/**
 * Canonical GPU model names, ordered so that more-specific names are checked
 * before less-specific ones (e.g. H100-80GB before H100).
 * Keep in sync with sky/provision/kubernetes/constants.py.
 */
export const CANONICAL_GPU_NAMES = [
  'GB300',
  'GB200',
  'B300',
  'B200',
  'B100',
  'GH200',
  'H200',
  'H100-MEGA',
  'H100',
  'A100',
  'A10G',
  'A10',
  'A16',
  'A30',
  'A40',
  'RTX6000-Ada',
  'L40S',
  'L40',
  'L4',
  'A6000',
  'A5000',
  'A4000',
  'V100',
  'P100',
  'P40',
  'P4000',
  'P4',
  'T4g',
  'T4',
  'K80',
  'M60',
];

/**
 * Canonicalize a raw GPU model string to a clean display name.
 *
 * @param {string} rawName - The raw GPU name (e.g. "NVIDIA A100-SXM4-80GB")
 * @returns {string} A canonical display name (e.g. "A100-80GB")
 */
export function canonicalizeGpuName(rawName) {
  if (!rawName) return 'Unknown';
  const value = rawName.trim();
  if (!value) return 'Unknown';

  for (const canonical of CANONICAL_GPU_NAMES) {
    // Word-boundary matching to prevent substring matches (e.g. L4 vs L40)
    const re = new RegExp(
      `\\b${canonical.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`,
      'i'
    );
    if (re.test(value)) {
      return canonical;
    }
  }

  // Fallback: clean up the name
  return (
    value
      .toUpperCase()
      .replace(/^NVIDIA[\s-]*/i, '')
      .replace(/^GEFORCE[\s-]*/i, '')
      .replace(/RTX[\s-]/i, 'RTX')
      .replace(/-SXM[\d]*/, '')
      .replace(/-PCIE/, '')
      .trim() || 'Unknown'
  );
}
