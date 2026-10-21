"""Tests for sky.provision.kubernetes.oci_nccl."""
from unittest import mock
from unittest.mock import patch

from sky.provision.kubernetes import oci_nccl
from sky.provision.kubernetes import utils as kubernetes_utils


def _eq(actual, expected):
    assert actual == expected, (actual, expected)


class _FakeApiException(Exception):

    def __init__(self, status):
        super().__init__(f'HTTP {status}')
        self.status = status


class TestGetEnvVars:
    """OCI network_tier: best NCCL env vars, selected per node shape."""

    # Master's profiles before shape selection, written out literally so the
    # table cannot be compared against itself.
    _MASTER_GB200 = {
        'NCCL_DEBUG': 'WARN',
        'NCCL_MNNVL_ENABLE': '1',
        'NCCL_CUMEM_ENABLE': '1',
        'NCCL_NET_PLUGIN': 'sys',
        'NCCL_IB_HCA': 'mlx5_0,mlx5_1,mlx5_3,mlx5_4',
        'NCCL_NVLS_ENABLE': '1',
        'NCCL_SOCKET_IFNAME': 'eth0',
    }
    _MASTER_GB300 = {
        'NCCL_DEBUG': 'WARN',
        'NCCL_MNNVL_ENABLE': '1',
        'NCCL_CUMEM_ENABLE': '1',
        'NCCL_NET_PLUGIN': 'none',
        'NCCL_IB_HCA': ('=mlx5_0,mlx5_1,mlx5_2,mlx5_3,mlx5_5,mlx5_6,mlx5_7,'
                        'mlx5_8'),
        'NCCL_NVLS_ENABLE': '1',
        'NCCL_SOCKET_IFNAME': 'eth0',
        'NCCL_NET_GDR_C2C': '1',
        'NCCL_NET_GDR_LEVEL': 'PHB',
        'NCCL_IB_GID_INDEX': '3',
        'NCCL_IB_TC': '41',
        'NCCL_IB_SL': '0',
        'NCCL_IB_TIMEOUT': '22',
        'NCCL_BUFFSIZE': '16777216',
        'NCCL_IB_QPS_PER_CONNECTION': '4',
        'NCCL_IB_SPLIT_DATA_ON_QPS': '0',
        'NCCL_DMABUF_ENABLE': '1',
    }
    _GENERIC = {
        'NCCL_IB_HCA': 'mlx5',
        'NCCL_IB_GID_INDEX': '3',
        'NCCL_IB_TC': '41',
        'NCCL_SOCKET_IFNAME': 'eth0',
        'UCX_TLS': 'tcp',
        'UCX_NET_DEVICES': 'eth0',
    }
    # Oracle's BM.GPU.GB200-v3.4 set, as its stack renders nccl.conf.
    _GB200_V3_CONF = '\n'.join([
        'NCCL_CUMEM_ENABLE=1',
        'NCCL_DEBUG=WARN',
        'NCCL_IB_GID_INDEX=3',
        'NCCL_IB_HCA==mlx5_0,mlx5_1,mlx5_2,mlx5_3,mlx5_5,mlx5_6,mlx5_7,mlx5_8',
        'NCCL_IB_QPS_PER_CONNECTION=1',
        'NCCL_IB_SL=0',
        'NCCL_IB_SPLIT_DATA_ON_QPS=0',
        'NCCL_IB_TC=41',
        'NCCL_IB_TIMEOUT=22',
        'NCCL_MNNVL_ENABLE=1',
        'NCCL_NET_GDR_C2C=1',
        'NCCL_NET_PLUGIN=none',
    ])

    @staticmethod
    def _nccl(env):
        return {k: v for k, v in env.items() if k.startswith('NCCL_')}

    def _env(self,
             shapes,
             configmaps=None,
             namespace='skypilot',
             pod_local_rdma=False):
        """Runs get_oci_nccl_env_vars against fake ConfigMaps.

        configmaps maps (namespace, name) to the nccl.conf text, or to an
        exception to raise for that read. Anything absent is a 404.
        """
        configmaps = configmaps or {}
        reads = []

        def read(name, namespace, **kwargs):
            # A hung API server must not hang the launch.
            assert kwargs.get('_request_timeout') is not None
            reads.append((namespace, name))
            entry = configmaps.get((namespace, name), _FakeApiException(404))
            if isinstance(entry, Exception):
                raise entry
            return mock.MagicMock(data={'nccl.conf': entry})

        core = mock.MagicMock()
        core.read_namespaced_config_map.side_effect = read
        with mock.patch.object(oci_nccl.kubernetes, 'core_api',
                               return_value=core), \
             mock.patch.object(oci_nccl.kubernetes, 'api_exception',
                               return_value=_FakeApiException):
            env = oci_nccl.get_env_vars('ctx',
                                        namespace,
                                        set(shapes),
                                        pod_local_rdma=pod_local_rdma)
        return env, reads

    def test_parse_keeps_exact_match_prefix(self):
        params = oci_nccl.parse_nccl_conf(self._GB200_V3_CONF)
        assert params['NCCL_IB_HCA'] == ('=mlx5_0,mlx5_1,mlx5_2,mlx5_3,'
                                         'mlx5_5,mlx5_6,mlx5_7,mlx5_8')
        assert len(params) == 12

    def test_parse_drops_comments_and_non_nccl_keys(self):
        """Only NCCL_/RCCL_ keys reach the pod: a ConfigMap in `default` must
        not be a way to set e.g. LD_PRELOAD in every SkyPilot pod."""
        params = oci_nccl.parse_nccl_conf('\n'.join([
            '# comment', '', '  NCCL_DEBUG = WARN  ', 'RCCL_X=1',
            'LD_PRELOAD=/x.so', 'PATH=/tmp', 'nccl_lower=1', 'NO_EQUALS_SIGN'
        ]))
        assert params == {'NCCL_DEBUG': 'WARN', 'RCCL_X': '1'}

    def test_configmap_name_matches_oracle(self):
        assert (oci_nccl.configmap_name('BM.GPU.GB200-v3.4') ==
                'oci-nccl-parameters-bm-gpu-gb200-v3-4')
        assert (oci_nccl.configmap_name('BM.GPU4.8') ==
                'oci-nccl-parameters-bm-gpu4-8')

    def test_configmap_replaces_profile(self):
        """An operator-edited ConfigMap wins over the built-in table, whole.

        E.g. a B300 cluster built with fewer NICs than the standard shape: the
        edited list must reach the pod, and table keys the edit dropped must
        not come back.
        """
        name = 'oci-nccl-parameters-bm-gpu-b300-8'
        conf = 'NCCL_IB_HCA==mlx5_0,mlx5_1\nNCCL_IB_GID_INDEX=3'
        env, _ = self._env({'BM.GPU.B300.8'}, {('default', name): conf})
        assert self._nccl(env) == {
            'NCCL_IB_HCA': '=mlx5_0,mlx5_1',
            'NCCL_IB_GID_INDEX': '3',
        }
        # UCX is not NCCL; the generic profile's UCX settings stay.
        assert env['UCX_TLS'] == 'tcp'
        assert env['UCX_NET_DEVICES'] == 'eth0'

    def test_grace_shapes_reproduce_master(self):
        """GB200.4 and GB300.4 still get exactly master's env, no UCX."""
        env, _ = self._env({'BM.GPU.GB200.4'})
        assert env == self._MASTER_GB200
        env, _ = self._env({'BM.GPU.GB300.4'})
        assert env == self._MASTER_GB300
        for shape in ('BM.GPU.GB200.4', 'BM.GPU.GB300.4'):
            env, _ = self._env({shape}, pod_local_rdma=True)
            assert env['NCCL_IB_HCA'] == 'mlx5', shape

    def test_unknown_or_missing_shape_is_master_generic(self):
        for shapes in (set(), {'BM.GPU.X.8'}):
            env, _ = self._env(shapes)
            assert env == self._GENERIC, shapes

    def test_built_in_table_for_customer_shapes(self):
        env, _ = self._env({'BM.GPU.B300.8'})
        # Checked against the 16 fabric NICs of a live BM.GPU.B300.8 node.
        assert env['NCCL_IB_HCA'] == (
            '=mlx5_0,mlx5_1,mlx5_7,mlx5_8,mlx5_9,mlx5_10,mlx5_11,mlx5_12,'
            'mlx5_13,mlx5_14,mlx5_16,mlx5_17,mlx5_18,mlx5_19,mlx5_20,mlx5_21')
        assert env['NCCL_IGNORE_CPU_AFFINITY'] == '1'
        env, _ = self._env({'BM.GPU.B200.8'})
        assert self._nccl(env) == {
            'NCCL_DEBUG': 'WARN',
            'NCCL_CUMEM_ENABLE': '0',
            'NCCL_IB_SPLIT_DATA_ON_QPS': '0',
            'NCCL_IB_GID_INDEX': '3',
            'NCCL_IB_HCA': '=mlx5_0,mlx5_3,mlx5_4,mlx5_5,mlx5_6,mlx5_9,mlx5_10,'
                           'mlx5_11',
            'NCCL_IB_TC': '41',
            'NCCL_IB_SL': '0',
            'NCCL_IB_TIMEOUT': '22',
            'NCCL_SOCKET_IFNAME': 'eth0',
            'NCCL_IGNORE_CPU_AFFINITY': '1',
        }
        assert env['UCX_NET_DEVICES'] == 'eth0'
        # A GB200-v3.4 node gets its own set, not the GB200.4 one.
        env, _ = self._env({'BM.GPU.GB200-v3.4'})
        assert self._nccl(env) == oci_nccl.parse_nccl_conf(self._GB200_V3_CONF)
        assert 'NCCL_NVLS_ENABLE' not in env
        # Oracle sets no socket interface for these shapes.
        for shape in ('BM.GPU.A100-v2.8', 'BM.GPU.GB200-v3.4'):
            env, _ = self._env({shape})
            assert 'NCCL_SOCKET_IFNAME' not in env, shape

    def test_customer_configmap_matches_table(self):
        # Rendered by Oracle's stack with `|-`: no trailing newline.
        conf = ('NCCL_CUMEM_ENABLE=1\nNCCL_DEBUG=WARN\n'
                'NCCL_IB_HCA=mlx5_0,mlx5_1,mlx5_3,mlx5_4\nNCCL_MNNVL_ENABLE=1\n'
                'NCCL_NET_PLUGIN=sys\nNCCL_NVLS_ENABLE=1\n'
                'NCCL_SOCKET_IFNAME=eth0')
        assert oci_nccl.parse_nccl_conf(conf) == self._MASTER_GB200

    def test_pod_namespace_before_default(self):
        name = 'oci-nccl-parameters-bm-gpu-b300-8'
        env, reads = self._env({'BM.GPU.B300.8'}, {
            ('skypilot', name): 'NCCL_IB_HCA=pod-ns',
            ('default', name): 'NCCL_IB_HCA=default-ns',
        })
        assert env['NCCL_IB_HCA'] == 'pod-ns'
        assert reads == [('skypilot', name)]
        env, _ = self._env({'BM.GPU.B300.8'}, {
            ('skypilot', name): _FakeApiException(403),
            ('default', name): 'NCCL_IB_HCA=default-ns',
        })
        assert env['NCCL_IB_HCA'] == 'default-ns'

    def test_read_failures_fall_back_to_table(self):
        """No ConfigMap readable is never a launch failure."""
        name = 'oci-nccl-parameters-bm-gpu-b200-8'
        table = self._env({'BM.GPU.B200.8'})[0]
        for error in (_FakeApiException(403), _FakeApiException(500),
                      RuntimeError('connection reset')):
            cms = {(ns, name): error for ns in ('skypilot', 'default')}
            env, _ = self._env({'BM.GPU.B200.8'}, cms)
            assert env == table, error

    def test_several_shapes(self):
        # GB200.4 and GB200-v2.4 share one Oracle set: unambiguous.
        env, _ = self._env({'BM.GPU.GB200.4', 'BM.GPU.GB200-v2.4'})
        assert self._nccl(env) == self._MASTER_GB200
        # Different sets: the env cannot depend on the scheduler's pick.
        env, _ = self._env({'BM.GPU.GB200.4', 'BM.GPU.GB200-v3.4'})
        assert env == self._nccl(self._GENERIC)
        # One shape without any set makes the whole request ambiguous.
        env, _ = self._env({'BM.GPU.GB200.4', 'BM.GPU.X.8'})
        assert env == self._GENERIC

    def test_grace_shapes_get_no_ucx(self):
        """UCX_TLS=tcp would push UCX users (e.g. NIXL) off RDMA and NVLink."""
        name = 'oci-nccl-parameters-bm-gpu-gb300-v2-4'
        cms = {('default', name): 'NCCL_MNNVL_ENABLE=1'}
        for shapes in (
            {'BM.GPU.GB200-v2.4'},
            {'BM.GPU.GB200-v3.4'},
            {'BM.GPU.GB300-v2.4'},  # Not in the table: ConfigMap only.
            {'BM.GPU.GB200.4', 'BM.GPU.GB200-v3.4'},  # Generic fallback.
        ):
            env, _ = self._env(shapes, cms)
            assert not [k for k in env if k.startswith('UCX_')], shapes
        # Any non-Grace shape in the pool keeps the generic profile's UCX.
        env, _ = self._env({'BM.GPU.GB200.4', 'BM.GPU.B200.8'})
        assert env == self._GENERIC

    def test_unlabeled_node_makes_the_shape_unknown(self):
        env, reads = self._env({'BM.GPU.GB200.4', oci_nccl.UNLABELED_SHAPE})
        assert env == self._GENERIC
        # The placeholder is never looked up as a ConfigMap name.
        assert {name for _, name in reads
               } == {'oci-nccl-parameters-bm-gpu-gb200-4'}

    def test_empty_configmap_is_ignored(self):
        name = 'oci-nccl-parameters-bm-gpu-b300-8'
        table = self._env({'BM.GPU.B300.8'})[0]
        for conf in ('', '# pending configuration', 'LD_PRELOAD=/x.so'):
            env, _ = self._env({'BM.GPU.B300.8'}, {('skypilot', name): conf})
            assert env == table, conf
        # An empty copy in the pod namespace defers to the one in `default`.
        env, _ = self._env({'BM.GPU.B300.8'}, {
            ('skypilot', name): '',
            ('default', name): 'NCCL_IB_HCA=default-ns',
        })
        assert env['NCCL_IB_HCA'] == 'default-ns'

    def test_sriov_widens_any_exact_list(self):
        name = 'oci-nccl-parameters-bm-gpu-gb200-v3-4'
        cms = {('default', name): self._GB200_V3_CONF}
        exact, _ = self._env({'BM.GPU.GB200-v3.4'}, cms)
        widened, _ = self._env({'BM.GPU.GB200-v3.4'}, cms, pod_local_rdma=True)
        assert widened['NCCL_IB_HCA'] == 'mlx5'
        assert {k: v for k, v in widened.items() if k != 'NCCL_IB_HCA'
               } == {k: v for k, v in exact.items() if k != 'NCCL_IB_HCA'}

    def test_gb300_gdr_level_overlay(self):
        """SkyPilot's PHB survives a ConfigMap, unless it sets the key."""
        name = 'oci-nccl-parameters-bm-gpu-gb300-4'
        conf = 'NCCL_NET_GDR_C2C=1\nNCCL_NET_PLUGIN=none'
        env, _ = self._env({'BM.GPU.GB300.4'}, {('default', name): conf})
        assert env['NCCL_NET_GDR_LEVEL'] == 'PHB'
        env, _ = self._env(
            {'BM.GPU.GB300.4'},
            {('default', name): conf + '\nNCCL_NET_GDR_LEVEL=SYS'})
        assert env['NCCL_NET_GDR_LEVEL'] == 'SYS'
        # Scoped to GB300: GB200-v3.4 also sets NET_GDR_C2C but gets no PHB.
        env, _ = self._env({'BM.GPU.GB200-v3.4'})
        assert 'NCCL_NET_GDR_LEVEL' not in env

    def test_non_oci_types_unchanged(self):
        net_type = kubernetes_utils.KubernetesHighPerformanceNetworkType
        coreweave = net_type.COREWEAVE
        assert coreweave.get_network_env_vars()['NCCL_IB_HCA'] == 'ibp'


class TestCandidateShapes:
    """The shapes are those of the nodes this pod may land on."""

    @staticmethod
    def _node(gpu, shape, **extra_labels):
        node = mock.MagicMock()
        node.metadata.labels = {'gpu': gpu, **extra_labels}
        if shape is not None:
            node.metadata.labels['node.kubernetes.io/instance-type'] = shape
        return node

    def _shapes(self, nodes, values=('B300',), node_selector=None):
        with patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                   return_value=nodes):
            return oci_nccl.candidate_shapes('ctx', 'gpu', list(values),
                                             node_selector)

    def test_only_nodes_matching_the_gpu(self):
        nodes = [
            self._node('B300', 'BM.GPU.B300.8'),
            self._node('GB200', 'BM.GPU.GB200.4'),
        ]
        _eq(self._shapes(nodes), {'BM.GPU.B300.8'})

    def test_pod_config_node_selector_narrows(self):
        nodes = [
            self._node('GB200', 'BM.GPU.GB200.4', pool='a'),
            self._node('GB200', 'BM.GPU.GB200-v3.4', pool='b'),
        ]
        _eq(self._shapes(nodes, ('GB200',)),
            {'BM.GPU.GB200.4', 'BM.GPU.GB200-v3.4'})
        _eq(
            self._shapes(
                nodes, ('GB200',),
                {'node.kubernetes.io/instance-type': 'BM.GPU.GB200-v3.4'}),
            {'BM.GPU.GB200-v3.4'})

    def test_unlabeled_eligible_node_is_kept(self):
        nodes = [
            self._node('B300', 'BM.GPU.B300.8'),
            self._node('B300', None),
        ]
        _eq(self._shapes(nodes), {'BM.GPU.B300.8', oci_nccl.UNLABELED_SHAPE})

    def test_cpu_only_request_has_no_shape(self):
        nodes = [self._node('B300', 'BM.GPU.B300.8')]
        with patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                   return_value=nodes):
            _eq(oci_nccl.candidate_shapes('ctx', None, None, None), set())
