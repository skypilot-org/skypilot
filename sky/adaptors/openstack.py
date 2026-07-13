"""OpenStack SDK adaptor."""

import os
from typing import Any, Dict, Iterable, Optional, Tuple

from sky.adaptors import common

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for OpenStack. '
                         'Try running: pip install "skypilot[openstack]"')

openstack = common.LazyImport('openstack',
                              import_error_message=_IMPORT_ERROR_MESSAGE)

_REMOTE_CONFIG_DIR = '~/.config/openstack'
_CONFIG_FILENAMES = ('clouds.yaml', 'clouds.yml', 'clouds.json')
_SECURE_FILENAMES = ('secure.yaml', 'secure.yml', 'secure.json')
_VENDOR_FILENAMES = ('clouds-public.yaml', 'clouds-public.yml',
                     'clouds-public.json')
_CERTIFICATE_KEYS = frozenset(
    ('cacert', 'cert', 'key', 'client_cert', 'client_key'))


def get_connection(cloud: Optional[str], region: Optional[str] = None) -> Any:
    """Returns an SDK connection for a named clouds.yaml profile."""
    kwargs = {'cloud': cloud}
    if region is not None:
        kwargs['region_name'] = region
    return openstack.connect(**kwargs)


def _load_cloud_config(cloud: str,
                       region: Optional[str] = None) -> Tuple[Any, Any]:
    loader = openstack.config.OpenStackConfig()
    kwargs = {'cloud': cloud}
    if region is not None:
        kwargs['region_name'] = region
    return loader, loader.get_one(**kwargs)


def get_cloud_config(cloud: str, region: Optional[str] = None) -> Any:
    """Returns the SDK's merged config for a named cloud."""
    _, cloud_config = _load_cloud_config(cloud, region)
    return cloud_config


def _expand_existing_file(path: Any) -> Optional[str]:
    if not isinstance(path, str) or not path:
        return None
    expanded = os.path.abspath(os.path.expanduser(os.path.expandvars(path)))
    if os.path.isfile(expanded):
        return expanded
    return None


def _first_existing(paths: Iterable[Any]) -> Optional[str]:
    for path in paths:
        expanded = _expand_existing_file(path)
        if expanded is not None:
            return expanded
    return None


def _sibling_candidates(config_path: Optional[str],
                        filenames: Iterable[str]) -> Iterable[str]:
    if config_path is None:
        return ()
    directory = os.path.dirname(config_path)
    return (os.path.join(directory, filename) for filename in filenames)


def _loader_candidates(loader: Any, attribute: str) -> Iterable[Any]:
    candidates = getattr(loader, attribute, ())
    if isinstance(candidates, (list, tuple)):
        return candidates
    return ()


def _config_path(loader: Any) -> Optional[str]:
    return _first_existing(
        (getattr(loader, 'config_filename',
                 None), os.environ.get('OS_CLIENT_CONFIG_FILE'),
         *_loader_candidates(loader, '_config_files')))


def _secure_path(loader: Any, config_path: Optional[str]) -> Optional[str]:
    candidates = (
        getattr(loader, 'secure_config_filename', None),
        os.environ.get('OS_CLIENT_SECURE_FILE'),
        *_loader_candidates(loader, '_secure_files'),
        *_sibling_candidates(config_path, _SECURE_FILENAMES),
    )
    return _first_existing(candidates)


def _uses_vendor_profile(loader: Any, cloud: str) -> bool:
    raw_config = getattr(loader, 'cloud_config', {})
    if not isinstance(raw_config, dict):
        return False
    clouds = raw_config.get('clouds', {})
    if not isinstance(clouds, dict):
        return False
    cloud_config = clouds.get(cloud, {})
    return (isinstance(cloud_config, dict) and
            bool(cloud_config.get('profile') or cloud_config.get('cloud')))


def _vendor_path(loader: Any, cloud: str,
                 config_path: Optional[str]) -> Optional[str]:
    candidates = [
        getattr(loader, 'vendor_config_filename', None),
        *_sibling_candidates(config_path, _VENDOR_FILENAMES),
    ]
    if _uses_vendor_profile(loader, cloud):
        candidates.extend(_loader_candidates(loader, '_vendor_files'))
    return _first_existing(candidates)


def _referenced_certificate_paths(config: Any) -> Iterable[Tuple[str, str]]:
    if not isinstance(config, dict):
        return ()

    references = []
    for section in (config, config.get('auth', {})):
        if not isinstance(section, dict):
            continue
        for key in _CERTIFICATE_KEYS:
            original = section.get(key)
            if not isinstance(original, str):
                continue
            expanded = _expand_existing_file(original)
            if expanded is not None:
                references.append((original, expanded))
    verify = config.get('verify')
    if isinstance(verify, str):
        expanded_verify = _expand_existing_file(verify)
        if expanded_verify is not None:
            references.append((verify, expanded_verify))
    return references


def get_credential_file_mounts(cloud: str,
                               region: Optional[str] = None) -> Dict[str, str]:
    """Returns controller mounts needed by the selected cloud profile."""
    loader, cloud_config = _load_cloud_config(cloud, region)
    config_path = _config_path(loader)
    secure_path = _secure_path(loader, config_path)
    vendor_path = _vendor_path(loader, cloud, config_path)

    mounts = {}
    for remote_name, local_path in (
        ('clouds.yaml', config_path),
        ('secure.yaml', secure_path),
        ('clouds-public.yaml', vendor_path),
    ):
        if local_path is not None:
            mounts[os.path.join(_REMOTE_CONFIG_DIR, remote_name)] = local_path

    config = getattr(cloud_config, 'config', {})
    for remote_path, local_path in _referenced_certificate_paths(config):
        mounts[remote_path] = local_path
    return mounts
