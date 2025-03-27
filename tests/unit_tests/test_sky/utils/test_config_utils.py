import pytest

from sky.utils import config_utils


def test_nested_config(monkeypatch) -> None:
    """Test that the nested config works."""
    config = config_utils.Config()
    config.set_nested(('aws', 'ssh_proxy_command'), 'value')
    assert config == {'aws': {'ssh_proxy_command': 'value'}}

    assert config.get_nested(('admin_policy',), 'default') == 'default'
    config.set_nested(('aws', 'use_internal_ips'), True)
    assert config == {
        'aws': {
            'ssh_proxy_command': 'value',
            'use_internal_ips': True
        }
    }


def test_merge_k8s_configs_with_container_resources():
    """Test merging Kubernetes configs with container resource specifications."""
    base_config = {
        'containers': [{
            'resources': {
                'limits': {
                    'cpu': '1',
                    'memory': '1Gi'
                },
                'requests': {
                    'cpu': '0.5'
                }
            }
        }]
    }
    override_config = {
        'containers': [{
            'resources': {
                'limits': {
                    'memory': '2Gi'
                },
                'requests': {
                    'memory': '1Gi'
                }
            }
        }]
    }

    config_utils.merge_k8s_configs(base_config, override_config)
    container = base_config['containers'][0]
    assert container['resources']['limits'] == {'cpu': '1', 'memory': '2Gi'}
    assert container['resources']['requests'] == {'cpu': '0.5', 'memory': '1Gi'}


def test_merge_k8s_configs_with_deeper_override():
    base_config = {
        'containers': [{
            'resources': {
                'limits': {
                    'cpu': '1',
                    'memory': '1Gi'
                },
            }
        }]
    }
    override_config = {
        'containers': [{
            'resources': {
                'limits': {
                    'memory': '2Gi'
                },
                'requests': {
                    'memory': '1Gi'
                }
            }
        }]
    }

    config_utils.merge_k8s_configs(base_config, override_config)
    container = base_config['containers'][0]
    assert container['resources']['limits'] == {'cpu': '1', 'memory': '2Gi'}
    assert container['resources']['requests'] == {'memory': '1Gi'}


def test_config_nested_empty_intermediate():
    """Test setting nested config with empty intermediate dictionaries."""
    config = config_utils.Config()

    # Set deeply nested value with no existing intermediate dicts
    config.set_nested(('a', 'b', 'c', 'd'), 'value')
    assert config.get_nested(('a', 'b', 'c', 'd'), None) == 'value'

    # Verify intermediate dictionaries were created
    assert isinstance(config['a'], dict)
    assert isinstance(config['a']['b'], dict)
    assert isinstance(config['a']['b']['c'], dict)


def test_config_get_nested_with_override():
    """Test getting nested config with overrides."""
    config = config_utils.Config({'a': {'b': {'c': 1}}})

    # Test simple override
    value = config.get_nested(('a', 'b', 'c'),
                              default_value=None,
                              override_configs={'a': {
                                  'b': {
                                      'c': 2
                                  }
                              }})
    assert value == 2

    # Test override with allowed keys
    value = config.get_nested(('a', 'b', 'c'),
                              default_value=None,
                              override_configs={'a': {
                                  'b': {
                                      'c': 3
                                  }
                              }},
                              allowed_override_keys=[('a', 'b', 'c')])
    assert value == 3

    # Test override with disallowed keys
    with pytest.raises(ValueError):
        config.get_nested(('a', 'b', 'c'),
                          default_value=None,
                          override_configs={'a': {
                              'b': {
                                  'c': 4
                              }
                          }},
                          disallowed_override_keys=[('a', 'b', 'c')])


def test_merge_k8s_configs_with_image_pull_secrets():
    """Test merging Kubernetes configs with imagePullSecrets."""
    base_config = {'imagePullSecrets': [{'name': 'secret1'}]}
    override_config = {
        'imagePullSecrets': [{
            'name': 'secret2',
            'namespace': 'test'
        }]
    }

    config_utils.merge_k8s_configs(base_config, override_config)
    assert len(base_config['imagePullSecrets']) == 1
    assert base_config['imagePullSecrets'][0]['name'] == 'secret2'
    assert base_config['imagePullSecrets'][0]['namespace'] == 'test'


def test_config_override_with_allowed_keys():
    """Test config override with allowed keys restrictions."""
    base_config = config_utils.Config({
        'aws': {
            'vpc_name': 'default-vpc',
            'security_group': 'default-sg'
        },
        'gcp': {
            'project_id': 'default-project'
        }
    })

    override_config = {
        'aws': {
            'vpc_name': 'custom-vpc'
        },
        'gcp': {
            'project_id': 'custom-project'  # This should fail
        }
    }

    # Only allow aws.vpc_name to be overridden
    allowed_keys = [('aws', 'vpc_name')]

    # We raise error whenever the override key is not in the allowed keys.
    with pytest.raises(ValueError, match='not in allowed override keys:'):
        base_config.get_nested(('aws', 'vpc_name'),
                               default_value=None,
                               override_configs=override_config,
                               allowed_override_keys=allowed_keys)

    # Should raise error when trying to override disallowed key
    with pytest.raises(ValueError, match='not in allowed override keys:'):
        base_config.get_nested(('gcp', 'project_id'),
                               default_value=None,
                               override_configs=override_config,
                               allowed_override_keys=allowed_keys)

    allowed_keys = [('aws', 'vpc_name'), ('gcp', 'project_id')]
    value = base_config.get_nested(('aws', 'vpc_name'),
                                   default_value=None,
                                   override_configs=override_config,
                                   allowed_override_keys=allowed_keys)
    assert value == 'custom-vpc'

    value = base_config.get_nested(('gcp', 'project_id'),
                                   default_value=None,
                                   override_configs=override_config,
                                   allowed_override_keys=allowed_keys)
    assert value == 'custom-project'

    override_config = {
        'aws': {
            'vpc_name': 'custom-vpc',
            'security_group': 'custom-sg'
        }
    }
    with pytest.raises(ValueError, match='not in allowed override keys:'):
        base_config.get_nested(('aws', 'vpc_name'),
                               default_value=None,
                               override_configs=override_config,
                               allowed_override_keys=allowed_keys)

    allowed_keys = [('aws', 'vpc_name'), ('aws', 'security_group')]
    value = base_config.get_nested(('aws', 'security_group'),
                                   default_value=None,
                                   override_configs=override_config,
                                   allowed_override_keys=allowed_keys)
    assert value == 'custom-sg'

    allowed_keys = [('aws',)]
    value = base_config.get_nested(('aws', 'vpc_name'),
                                   default_value=None,
                                   override_configs=override_config,
                                   allowed_override_keys=allowed_keys)
    assert value == 'custom-vpc'


def test_k8s_config_merge_with_multiple_volumes():
    """Test merging Kubernetes configs with multiple volume configurations."""
    base_config = {
        'volumes': [{
            'name': 'vol1',
            'hostPath': '/path1'
        }, {
            'name': 'vol2',
            'hostPath': '/path2'
        }],
        'volumeMounts': [{
            'name': 'vol1',
            'mountPath': '/mnt1'
        }, {
            'name': 'vol2',
            'mountPath': '/mnt2'
        }]
    }

    override_config = {
        'volumes': [
            {
                'name': 'vol1',
                'hostPath': '/new-path1'
            },  # Should update existing
            {
                'name': 'vol3',
                'hostPath': '/path3'
            }  # Should append
        ],
        'volumeMounts': [
            {
                'name': 'vol1',
                'mountPath': '/new-mnt1'
            },  # Should update existing
            {
                'name': 'vol3',
                'mountPath': '/mnt3'
            }  # Should append
        ]
    }

    config_utils.merge_k8s_configs(base_config, override_config)

    # Check volumes
    assert len(base_config['volumes']) == 3
    vol1 = next(v for v in base_config['volumes'] if v['name'] == 'vol1')
    assert vol1['hostPath'] == '/new-path1'
    vol3 = next(v for v in base_config['volumes'] if v['name'] == 'vol3')
    assert vol3['hostPath'] == '/path3'

    # Check volumeMounts
    assert len(base_config['volumeMounts']) == 3
    mount1 = next(m for m in base_config['volumeMounts'] if m['name'] == 'vol1')
    assert mount1['mountPath'] == '/new-mnt1'
    mount3 = next(m for m in base_config['volumeMounts'] if m['name'] == 'vol3')
    assert mount3['mountPath'] == '/mnt3'


def test_nested_config_override_precedence():
    """Test that config overrides follow correct precedence rules."""
    base_config = config_utils.Config({
        'kubernetes': {
            'pod_config': {
                'metadata': {
                    'labels': {
                        'env': 'prod',
                        'team': 'ml'
                    }
                },
                'spec': {
                    'containers': [{
                        'resources': {
                            'limits': {
                                'cpu': '1',
                                'memory': '1Gi'
                            }
                        }
                    }]
                }
            }
        }
    })

    override_config = {
        'kubernetes': {
            'pod_config': {
                'metadata': {
                    'labels': {
                        'env': 'dev',  # Should override
                        'project': 'skypilot'  # Should add
                    }
                },
                'spec': {
                    'containers': [{
                        'resources': {
                            'limits': {
                                'memory': '2Gi'  # Should override
                            }
                        }
                    }]
                }
            }
        }
    }

    # Get nested value with override
    result = base_config.get_nested(('kubernetes', 'pod_config'),
                                    default_value=None,
                                    override_configs=override_config)

    # Check that labels were properly merged
    assert result['metadata']['labels'] == {
        'env': 'dev',
        'team': 'ml',
        'project': 'skypilot'
    }

    # Check that container resources were properly merged
    container = result['spec']['containers'][0]
    assert container['resources']['limits'] == {'cpu': '1', 'memory': '2Gi'}


def test_nested_config_override_with_nonexistent_key():
    """Test that config override with nonexistent key in base config."""
    base_config = config_utils.Config({})
    override_config = {
        'kubernetes': {
            'pod_config': {
                'metadata': {
                    'labels': {
                        'env': 'dev',
                        'project': 'skypilot'
                    }
                }
            }
        }
    }
    result = base_config.get_nested(('kubernetes', 'pod_config'),
                                    default_value=None,
                                    override_configs=override_config)
    assert result == override_config['kubernetes']['pod_config']
