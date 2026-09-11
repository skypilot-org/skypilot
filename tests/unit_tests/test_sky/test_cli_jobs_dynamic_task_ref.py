"""`sky jobs cancel 39-2` / `sky jobs logs 39-2`: a dynamic task of a job
group is addressed as <group id>-<task index> and resolved to its own job id
before the command runs."""
from unittest import mock

import click
import pytest

from sky.client.cli import command


class TestManagedJobRef:

    def test_plain_ids_pass_through(self):
        ref = command._ManagedJobRef()
        assert ref.convert('42', None, None) == 42
        assert ref.convert(42, None, None) == 42

    def test_dynamic_task_reference_parses_to_a_pair(self):
        assert command._ManagedJobRef().convert('39-2', None, None) == (39, 2)

    @pytest.mark.parametrize('bad', ['abc', '39-', '-2', '39-2-1', '3 9'])
    def test_anything_else_is_rejected(self, bad):
        with pytest.raises(click.BadParameter):
            command._ManagedJobRef().convert(bad, None, None)


class TestResolveManagedJobRefs:

    def _records(self):
        return [
            {
                'job_id': 39,
                'root_job_id': None,
                'dynamic_task_index': None
            },
            {
                'job_id': 42,
                'root_job_id': 39,
                'dynamic_task_index': 2
            },
            {
                'job_id': 43,
                'root_job_id': 39,
                'dynamic_task_index': 3
            },
        ]

    def test_plain_ids_need_no_lookup(self):
        with mock.patch.object(command.sdk, 'get') as get:
            assert command._resolve_managed_job_refs([7, 8]) == [7, 8]
            get.assert_not_called()

    def test_pairs_resolve_to_the_dynamic_task_s_job_id(self):
        with mock.patch.object(command.managed_jobs, 'queue_v2'), \
             mock.patch.object(command.sdk, 'get',
                               return_value=(self._records(), 3, {}, 3, [])):
            assert command._resolve_managed_job_refs([(39, 2), 7,
                                                      (39, 3)]) == [42, 7, 43]

    def test_unknown_pair_is_a_usage_error(self):
        with mock.patch.object(command.managed_jobs, 'queue_v2'), \
             mock.patch.object(command.sdk, 'get',
                               return_value=(self._records(), 3, {}, 3, [])):
            with pytest.raises(click.UsageError, match='No dynamic task 39-9'):
                command._resolve_managed_job_refs([(39, 9)])
