"""A not-ready volume must fail a managed job, not send it round the retry loop.

Retrying cannot make a volume ready: either its storage is broken and an
operator has to fix it, or it is still provisioning and the job should be
resubmitted once it is. Either way the job is better off failing with a reason
than re-attempting the same launch until it hits the retry ceiling.
"""
from sky import exceptions
from sky.jobs import recovery_strategy


class TestVolumeNotReadyIsTerminal:

    def test_listed_as_a_precheck_failure(self):
        assert exceptions.VolumeNotReadyError in (
            recovery_strategy.PRECHECK_FAILURES)

    def test_caught_by_the_precheck_handler(self):
        """What the `except` clause actually evaluates."""
        assert isinstance(exceptions.VolumeNotReadyError('not ready'),
                          recovery_strategy.PRECHECK_FAILURES)

    def test_sits_with_the_other_storage_failures(self):
        """Storage problems were already terminal; a volume is the same kind of
        thing, so the two should not diverge."""
        assert exceptions.StorageError in recovery_strategy.PRECHECK_FAILURES
        assert exceptions.StorageSpecError in (
            recovery_strategy.PRECHECK_FAILURES)

    def test_resource_unavailability_is_not_a_precheck_failure(self):
        """That one has to keep retrying -- capacity comes and goes."""
        assert exceptions.ResourcesUnavailableError not in (
            recovery_strategy.PRECHECK_FAILURES)
