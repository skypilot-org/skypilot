"""A remote jobs controller refuses a pool of another workspace when a job is
submitted, over gRPC and over the legacy SSH code path."""
import shlex
from unittest import mock

import grpc
import pytest

from sky import backends
from sky import exceptions
from sky.jobs import state as managed_job_state
from sky.schemas.generated import jobsv1_pb2
from sky.serve import serve_utils
from sky.skylet import job_lib
from sky.skylet import services


class _Aborted(Exception):
    """What a real gRPC context raises from `abort`."""


def _context():
    context = mock.Mock()
    context.abort.side_effect = _Aborted
    return context


def _request(pool='p'):
    return jobsv1_pb2.SetJobInfoWithoutJobIdRequest(
        name='j',
        workspace='ws-b',
        entrypoint='e',
        pool=pool,
        task_ids=[0],
        task_names=['t'],
        resources_str='r',
        metadata_jsons=['{}'],
        num_jobs=1,
        execution='parallel',
        is_primary_in_job_groups=[False])


@pytest.fixture
def inserts():
    with mock.patch.object(managed_job_state,
                           'set_job_info_without_job_id',
                           return_value=7) as insert, \
         mock.patch.object(managed_job_state, 'set_pending'):
        yield insert


class TestHandler:

    def _call(self, refusal, pool='p'):
        context = _context()
        with mock.patch.object(services.serve_utils, 'get_pool_refusal',
                               **refusal) as check:
            try:
                response = services.JobsServiceImpl().SetJobInfoWithoutJobId(
                    _request(pool), context)
            except _Aborted:
                response = None
        return response, context, check

    def test_usable_pool_is_inserted(self, inserts):
        response, context, check = self._call({'return_value': None})
        assert list(response.job_ids) == [7]
        context.abort.assert_not_called()
        check.assert_called_once_with('p', 'ws-b')
        inserts.assert_called_once()

    @pytest.mark.parametrize(('refusal', 'code', 'details'), [
        ((False, None), grpc.StatusCode.NOT_FOUND, "Pool 'p' not found."),
        ((True, 'ws-a'), grpc.StatusCode.FAILED_PRECONDITION, 'ws-a'),
    ])
    def test_refusal_keeps_its_status_code(self, inserts, refusal, code,
                                           details):
        _, context, _ = self._call({'return_value': refusal})
        context.abort.assert_called_once_with(code, details)
        inserts.assert_not_called()

    def test_failed_lookup_is_internal_not_unknown(self, inserts):
        # UNKNOWN would make the API server retry over SSH.
        _, context, _ = self._call({'side_effect': RuntimeError('db down')})
        context.abort.assert_called_once_with(grpc.StatusCode.INTERNAL,
                                              'db down')
        inserts.assert_not_called()

    def test_job_without_a_pool_is_not_checked(self, inserts):
        context = _context()
        request = _request()
        request.ClearField('pool')
        with mock.patch.object(services.serve_utils,
                               'get_pool_refusal') as check:
            services.JobsServiceImpl().SetJobInfoWithoutJobId(request, context)
        check.assert_not_called()
        inserts.assert_called_once()


def _run_codegen(capsys):
    """Runs the legacy submission code in this process; returns stdout."""
    command = job_lib.JobLibCodeGen.set_job_info_without_job_id(
        name='j',
        workspace='ws-b',
        entrypoint='e',
        pool='p',
        pool_hash=None,
        user_hash=None,
        task_ids=[0],
        task_names=['t'],
        resources_str='r',
        metadata_jsons=['{}'],
        is_primary_in_job_groups=[False],
        execution='parallel')
    args = shlex.split(command)
    code = args[args.index('-c') + 1]
    try:
        exec(code, {})  # pylint: disable=exec-used
    except SystemExit as e:
        assert e.code == 0
    return capsys.readouterr().out


def _submit_legacy(stdout):
    backend = mock.MagicMock(spec=backends.CloudVmRayBackend)
    backend.run_on_head.return_value = (0, stdout, '')
    handle = mock.Mock(is_grpc_enabled_with_flag=False)
    return backends.CloudVmRayBackend.set_job_info_without_job_id(
        backend,
        handle,
        name='j',
        workspace='ws-b',
        entrypoint='e',
        pool='p',
        pool_hash=None,
        user_hash=None,
        task_ids=[0],
        task_names=['t'],
        resources_str='r',
        metadata_jsons=['{}'],
        is_primary_in_job_groups=[False])


class TestLegacyPath:

    @pytest.mark.parametrize('refusal', [(False, None), (True, 'ws-a')])
    def test_refusal_reaches_the_api_server(self, capsys, inserts, refusal):
        with mock.patch.object(serve_utils,
                               'get_pool_refusal',
                               return_value=refusal) as check:
            stdout = _run_codegen(capsys)
        check.assert_called_once_with('p', 'ws-b')
        inserts.assert_not_called()
        with pytest.raises(exceptions.PoolRefusedError) as info:
            _submit_legacy(stdout)
        assert (info.value.exists, info.value.workspace) == refusal

    def test_usable_pool_is_inserted(self, capsys, inserts):
        with mock.patch.object(serve_utils,
                               'get_pool_refusal',
                               return_value=None):
            stdout = _run_codegen(capsys)
        inserts.assert_called_once()
        assert _submit_legacy(stdout) == [7]

    def test_controller_without_the_check_inserts(self, capsys, inserts,
                                                  monkeypatch):
        monkeypatch.delattr(serve_utils, 'get_pool_refusal')
        stdout = _run_codegen(capsys)
        inserts.assert_called_once()
        assert _submit_legacy(stdout) == [7]


class _RpcError(grpc.RpcError):

    def __init__(self, code, details):
        super().__init__()
        self._code = code
        self._details = details

    def code(self):
        return self._code

    def details(self):
        return self._details


class TestGrpcPath:

    def _submit(self, error):
        backend = mock.MagicMock(spec=backends.CloudVmRayBackend)
        handle = mock.Mock(is_grpc_enabled_with_flag=True)
        with mock.patch('sky.backends.backend_utils.invoke_skylet_with_retries',
                        side_effect=error):
            return backends.CloudVmRayBackend.set_job_info_without_job_id(
                backend,
                handle,
                name='j',
                workspace='ws-b',
                entrypoint='e',
                pool='p',
                pool_hash=None,
                user_hash=None,
                task_ids=[0],
                task_names=['t'],
                resources_str='r',
                metadata_jsons=['{}'],
                is_primary_in_job_groups=[False])

    @pytest.mark.parametrize(('code', 'details', 'refusal'), [
        (grpc.StatusCode.NOT_FOUND, "Pool 'p' not found.", (False, None)),
        (grpc.StatusCode.FAILED_PRECONDITION, 'ws-a', (True, 'ws-a')),
    ])
    def test_refusal_codes_map_to_the_refusal(self, code, details, refusal):
        with pytest.raises(exceptions.PoolRefusedError) as info:
            self._submit(_RpcError(code, details))
        assert (info.value.exists, info.value.workspace) == refusal

    def test_other_codes_pass_through(self):
        error = _RpcError(grpc.StatusCode.PERMISSION_DENIED, 'x')
        with pytest.raises(grpc.RpcError) as info:
            self._submit(error)
        assert info.value is error
