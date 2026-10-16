import React, { useEffect, useState } from 'react';
import { InfoIcon } from 'lucide-react';
import { getAllowedNodesConfig } from '@/data/connectors/infra';
import { NonCapitalizedTooltip } from '@/components/utils';

/**
 * Resolve whether a K8s context's node list is filtered by an `allowed_nodes`
 * config. Shared by the hint banner and the row badge. Fails closed: any error
 * (or a skipped, non-K8s context) yields `false` so callers never render a
 * misleading indicator.
 */
function useAllowedNodesConfigured(context, skip) {
  const [configured, setConfigured] = useState(false);

  useEffect(() => {
    if (skip) {
      setConfigured(false);
      return undefined;
    }
    // Reset before the in-flight fetch so switching contexts fails closed
    // rather than flashing the previous context's filtered/unfiltered state.
    setConfigured(false);
    let cancelled = false;
    getAllowedNodesConfig(context)
      .then((data) => {
        if (!cancelled) setConfigured(!!data?.configured);
      })
      .catch(() => {
        if (!cancelled) setConfigured(false);
      });
    return () => {
      cancelled = true;
    };
  }, [context, skip]);

  return configured;
}

/**
 * Hint banner shown on the infra context-detail page when the context's node
 * list is filtered by an `allowed_nodes` config. Without this, an admin who
 * expects to see more nodes than are listed may mistake the filtered view for
 * a bug.
 *
 * `allowed_nodes` is a Kubernetes-only concept, so this renders nothing for
 * Slurm contexts or SSH node pools (`ssh-<pool>`).
 */
export function AllowedNodesHint({ contextName, isSlurm = false }) {
  const skip = isSlurm || !contextName || contextName.startsWith('ssh-');
  const configured = useAllowedNodesConfigured(contextName, skip);

  if (skip || !configured) {
    return null;
  }

  return (
    <div
      role="note"
      className="flex items-start gap-2 mb-4 px-3 py-2.5 rounded-md border border-blue-200 bg-blue-50 text-sm leading-normal text-blue-900"
    >
      <InfoIcon className="w-4 h-4 mt-0.5 flex-shrink-0" />
      <span>
        This context has{' '}
        <code className="px-1 py-0.5 rounded bg-blue-100 text-xs font-mono">
          allowed_nodes
        </code>{' '}
        configured, so only matching nodes are shown here — other nodes in this
        context are hidden by configuration
      </span>
    </div>
  );
}

/**
 * Compact badge shown next to a K8s context's name in the main infra-page
 * Kubernetes table when that context's node list is filtered by an
 * `allowed_nodes` config. Surfaces the same information as `AllowedNodesHint`
 * without needing to click into the context, so the Nodes count isn't
 * mistaken for the full cluster. `kind` is 'k8s' | 'ssh' | 'slurm' and `id` is
 * the context name.
 */
export function AllowedNodesRowBadge({ id, kind }) {
  const skip = kind !== 'k8s' || !id;
  const configured = useAllowedNodesConfigured(id, skip);

  if (skip || !configured) {
    return null;
  }

  return (
    <NonCapitalizedTooltip
      content="Node list filtered by allowed_nodes — not all nodes in this context are shown"
      placement="top"
    >
      <span
        role="img"
        aria-label="allowed_nodes filter active"
        className="inline-flex items-center flex-shrink-0 text-blue-600"
      >
        <InfoIcon className="w-3.5 h-3.5" />
      </span>
    </NonCapitalizedTooltip>
  );
}

export default AllowedNodesHint;
