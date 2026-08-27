"""Deployment-specific policy for a pod that has not started yet.

SkyPilot recognises a fixed set of pending reasons that can legitimately
persist for a long time (pulling a large image, an external volume
provisioner, a running init container) and waits them out; everything else is
failed once it has held the same reason for long enough. That set is
necessarily incomplete: a deployment with its own CSI driver, admission
controller or image registry knows about slow steps SkyPilot does not, and can
often tell that a wait will never succeed long before any deadline expires.

A registered policy says so per pod, with ``WAIT`` (legitimately open-ended:
the no-progress deadline is suppressed and the launch may park, releasing its
executor worker) or ``FAIL`` (provisioning fails now, with the policy's
message). None means no opinion, leaving the built-in classification in
charge.

Example usage in a plugin:
    from sky.utils.plugin_extensions import PodStartPolicy
    from sky.utils.plugin_extensions import PodStartVerdict

    def my_policy(pod, reason, context, cluster_name):
        if reason == 'MyProvisionerWorking':
            return PodStartVerdict.WAIT, None
        if my_probe_says_hopeless(pod, context):
            return PodStartVerdict.FAIL, 'Storage class X is not available'
        return None

    PodStartPolicy.register(my_policy)

Example usage in core SkyPilot:
    verdict = PodStartPolicy.get(pod, reason, context, cluster_name)

Two properties a policy must have:

  * **Cheap, or self-throttled.** It is consulted for every pod that is not
    running yet, on every poll -- as often as once a second. Probe on a timer
    of your own rather than on every call.
  * **Park-transparent.** A launch that parks re-runs from the beginning when
    it resumes, so a ``FAIL`` verdict has to be reachable again on that
    re-run: derive it from cluster state, not from a timer living in the
    process that first saw it.
"""
import enum
from typing import Any, Optional, Protocol, Tuple

from sky import sky_logging

logger = sky_logging.init_logger(__name__)


class PodStartVerdict(enum.Enum):
    """What a policy says about a pod that has not started yet."""
    # Legitimately open-ended: wait it out, and park rather than hold a worker.
    WAIT = 'wait'
    # Will never succeed: fail provisioning now instead of waiting.
    FAIL = 'fail'


class PodStartPolicyFunc(Protocol):
    """Protocol for a pod-start policy function.

    Returns a ``(verdict, message)`` pair, or None for no opinion. ``message``
    is the provisioning error for ``FAIL``; it is ignored for ``WAIT``.
    """

    def __call__(
        self,
        pod: Any,
        reason: Optional[str],
        context: Optional[str],
        cluster_name: str,
    ) -> Optional[Tuple[PodStartVerdict, Optional[str]]]:
        ...


class PodStartPolicy:
    """Singleton class for the pod-start policy extension point.

    Plugins register their policy during their install() phase; core SkyPilot
    consults it through get(), which returns None whenever there is no policy
    or the policy has no opinion.
    """

    _provider_func: Optional[PodStartPolicyFunc] = None

    @classmethod
    def register(cls, provider: PodStartPolicyFunc) -> None:
        """Register a pod-start policy function.

        Only one policy can be registered at a time.

        Args:
            provider: Function returning a ``(PodStartVerdict, message)`` pair
                for a pod that has not started yet, or None for no opinion.
        """
        cls._provider_func = provider
        logger.debug('Registered pod-start policy')

    @classmethod
    def is_registered(cls) -> bool:
        """Check if a pod-start policy is registered."""
        return cls._provider_func is not None

    @classmethod
    def get(
        cls,
        pod: Any,
        reason: Optional[str],
        context: Optional[str],
        cluster_name: str,
    ) -> Optional[Tuple[PodStartVerdict, Optional[str]]]:
        """Ask the registered policy about a pod that has not started yet.

        Args:
            pod: The pod, as returned by the Kubernetes API.
            reason: The pending reason SkyPilot derived, or None if it could
                not derive one.
            context: Kubernetes context the pod lives in.
            cluster_name: SkyPilot cluster the pod belongs to.

        Returns:
            The policy's ``(verdict, message)``, or None when no policy is
            registered, it has no opinion, or it failed.

        A policy that raises or answers malformedly is treated as having no
        opinion rather than failing the launch: this runs inside the
        provisioning wait, where an escaping exception would abort a launch
        that is merely slow.
        """
        if cls._provider_func is None:
            return None
        try:
            # pylint: disable=not-callable
            verdict = cls._provider_func(pod, reason, context, cluster_name)
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'Pod-start policy failed: {e}')
            return None
        if verdict is None:
            return None
        if not isinstance(verdict, tuple) or len(verdict) != 2:
            logger.debug(f'Pod-start policy returned {verdict!r}, expected a '
                         '(PodStartVerdict, message) pair; ignoring.')
            return None
        decision, message = verdict
        if not isinstance(decision, PodStartVerdict):
            logger.debug(f'Pod-start policy returned verdict {decision!r}, '
                         'expected a PodStartVerdict; ignoring.')
            return None
        return decision, message
