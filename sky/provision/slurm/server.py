"""Remote Slurm cluster registration API endpoints.

CRUD over ``~/.slurm/config`` (an OpenSSH ``ssh_config``-format file) and the
per-cluster identity / ``known_hosts`` files on the API server. Mirrors the
structure of ``sky/ssh_node_pools/server.py``: synchronous handlers that call
the registration core directly (no long-running work, so no ``executor``).

This is a control-plane surface with no per-caller scoping on the management
files it writes, so the whole router is blocklisted for the ``user`` role in
``sky/users/rbac.py``.
"""
from typing import Any, Dict, List

import fastapi

from sky.provision.slurm import registration
from sky.utils import common_utils

router = fastapi.APIRouter()


@router.get('')
def get_slurm_clusters() -> Dict[str, Dict[str, Any]]:
    """List registered Slurm clusters (names + non-secret detail)."""
    try:
        return registration.list_clusters()
    except Exception as e:  # pylint: disable=broad-except
        raise fastapi.HTTPException(status_code=500,
                                    detail='Failed to list Slurm clusters: '
                                    f'{common_utils.format_exception(e)}')


@router.post('')
def register_slurm_cluster(cluster: Dict[str, Any]) -> Dict[str, str]:
    """Upsert a Slurm cluster.

    Body fields: ``name``, ``host``, ``user``, ``identity_file`` (private key
    contents), and optionally ``port`` (default 22), ``host_key``
    (known_hosts contents, enables host-key pinning), ``proxy_jump``, and
    ``identities_only`` (default True).
    """
    try:
        registration.register_cluster(
            name=cluster['name'],
            host=cluster['host'],
            user=cluster['user'],
            identity_file=cluster['identity_file'],
            port=int(cluster.get('port', 22)),
            host_key=cluster.get('host_key'),
            proxy_jump=cluster.get('proxy_jump'),
            identities_only=cluster.get('identities_only', True),
        )
        return {'status': 'success'}
    except KeyError as e:
        raise fastapi.HTTPException(status_code=400,
                                    detail=f'Missing required field: {e}')
    except ValueError as e:
        raise fastapi.HTTPException(status_code=400,
                                    detail=common_utils.format_exception(e))
    except Exception as e:  # pylint: disable=broad-except
        raise fastapi.HTTPException(status_code=500,
                                    detail='Failed to register Slurm cluster: '
                                    f'{common_utils.format_exception(e)}')


@router.delete('/{name}')
def delete_slurm_cluster(name: str) -> Dict[str, str]:
    """Remove a Slurm cluster and its identity / known_hosts files."""
    try:
        if registration.delete_cluster(name):
            return {'status': 'success'}
        raise fastapi.HTTPException(status_code=404,
                                    detail=f'Slurm cluster `{name}` not found')
    except fastapi.HTTPException:
        raise
    except ValueError as e:
        raise fastapi.HTTPException(status_code=400,
                                    detail=common_utils.format_exception(e))
    except Exception as e:  # pylint: disable=broad-except
        raise fastapi.HTTPException(status_code=500,
                                    detail='Failed to delete Slurm cluster: '
                                    f'{common_utils.format_exception(e)}')


# Kept for symmetry with ssh_node_pools; unused today but re-exported so the
# router module is the single import surface for the registration API.
__all__: List[str] = ['router']
