"""Every carried cloud must have a branch in the auth-setup dispatch.

`sky/backends/backend_utils.py`'s `_add_auth_to_cluster_config` is an
`isinstance` chain ending in `assert False, cloud`. It has NO extension hook,
so a cloud that registers cleanly, passes `sky check`, resolves a ray template
and gets CHOSEN by the optimizer still dies at launch with a bare

    AssertionError: Spheron

naming only the cloud -- no file, no hint that an auth branch is what is
missing. That is precisely how this was found: on a live dev-usw2 launch, after
SkyPilot had already selected
`massed-compute_gpu_1x_pro_6000_blackwell_us-central-9` at $2.39/hr.

This is the FIFTH hardcoded per-cloud table a carried cloud has had to be
threaded through (after the catalog import path, ALL_CLOUDS, the cluster
config template map, and check_credentials). Each was invisible until the
previous one was fixed, because each is only reached once the earlier stage
succeeds. Registration tests cannot catch them -- only executing the stage can,
or a test like this one that reads the dispatch directly.
"""
import ast
import pathlib

import pytest

from sky import clouds

# The clouds this fork CARRIES on top of upstream. Upstream's own clouds are
# not asserted here: they are upstream's contract, not ours to guard.
CARRIED = ["Spheron", "Shadeform"]


def _auth_dispatch_clouds() -> set:
    """Names appearing in _add_auth_to_cluster_config's isinstance chain."""
    src = pathlib.Path(clouds.__file__).parent.parent / "backends" / "backend_utils.py"
    tree = ast.parse(src.read_text())
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_add_auth_to_cluster_config"
    )
    found = set()
    for node in ast.walk(fn):
        # clouds.X  ->  "X"
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "clouds":
                found.add(node.attr)
    return found


@pytest.mark.parametrize("cloud_name", CARRIED)
def test_carried_cloud_has_an_auth_branch(cloud_name):
    dispatched = _auth_dispatch_clouds()
    assert cloud_name in dispatched, (
        f"{cloud_name} has no branch in _add_auth_to_cluster_config's isinstance "
        f"chain, so every launch will die at `assert False, cloud` with a bare "
        f"AssertionError naming only the cloud. Add it to the "
        f"configure_ssh_info tuple (if the provisioner registers the key "
        f"itself) or give it a setup_<cloud>_authentication that ends with "
        f"`return configure_ssh_info(config)`. Currently dispatched: "
        f"{sorted(dispatched)}"
    )


@pytest.mark.parametrize("cloud_name", CARRIED)
def test_carried_cloud_is_registered(cloud_name):
    """Cheap companion: the cloud class must exist and be importable."""
    assert hasattr(clouds, cloud_name), f"clouds.{cloud_name} is not exported"
