# Modal Support Roadmap

## Summary

This PR should start with the smallest maintainable path for making Modal a
technically supported SkyPilot infrastructure target.

Recommended MVP: support a single-node Modal Sandbox as a SkyPilot cluster by
exposing SSH over a Modal unencrypted TCP tunnel, then reuse SkyPilot's existing
Ray/SSH launch, exec, status, and teardown flow. Modal-native execution should
be deferred unless the SSH tunnel proof fails.

References:

- Modal Sandboxes: https://modal.com/docs/reference/modal.Sandbox
- Modal tunnels: https://modal.com/docs/guide/tunnels
- Modal GPU resources: https://modal.com/docs/guide/gpu
- Modal region selection: https://modal.com/docs/guide/region-selection
- Modal pricing: https://modal.com/pricing

## Public Interface

- Add `modal` as a new infra/cloud name: `--infra modal`, `cloud: modal`, and
  `infra: modal`.
- Add optional install extra: `skypilot[modal]`.
- Do not import the Modal SDK eagerly during `import sky`.
- Do not add new SkyPilot SDK/CLI surface beyond existing cloud selection.
- Do not bump the SkyPilot API version for the MVP.
- Fail early for unsupported features using existing cloud feature gates:
  `stop`, `multi-node`, `spot_instance`, `clone_disk_from_cluster`, custom image
  IDs, storage mounting, host controllers, high-availability controllers,
  autostop/autodown, custom disk/network tiers, and local disk.

## Ranked Work

1. P0: prove feasibility before changing the PR.
   - Write a local throwaway script that creates a Modal Sandbox, installs and
     starts `sshd`, injects a SkyPilot public key, exposes
     `unencrypted_ports=[22]`, and successfully runs `ssh host -p port uptime`.
   - If this fails, stop the SSH/Ray MVP and switch to a larger Modal-native
     command runner/backend design.

2. P1: minimum technically supported cloud.
   - Add `sky.clouds.modal.Modal`, import/export it in `sky/clouds/__init__.py`,
     and add `modal` to `ALL_CLOUDS`.
   - Add `sky/catalog/modal_catalog.py` plus a small static catalog covering a
     CPU default and documented Modal GPU types/counts, with per-second prices
     converted to hourly prices.
   - Use a synthetic `auto` region as the default SkyPilot catalog region for
     Modal-managed placement; translate `auto` to `region=None` when calling
     Modal.
   - Also accept Modal's documented Sandbox container regions immediately:
     broad regions `us`, `eu`, `ap`, `uk`, `ca`, `me`, `sa`, `af`, `mx`; and
     narrow regions `us-east`, `us-central`, `us-south`, `us-west`, `eu-west`,
     `eu-north`, `eu-south`, `ap-northeast`, `ap-southeast`, `ap-south`,
     `ap-melbourne`, `jp`, `au`.
   - Price `auto` at base Modal pricing, explicit broad regions at Modal's
     documented 1.5x multiplier, and explicit narrow regions at Modal's
     documented 1.75x multiplier.
   - Only model Sandbox container `region`; do not add `routing_region`, which
     Modal documents as not applying to Sandboxes.
   - Add `sky/provision/modal/{__init__.py,config.py,instance.py,modal_utils.py}`
     implementing `bootstrap_instances`, `run_instances`,
     `terminate_instances`, `get_cluster_info`, `query_instances`, and
     `wait_instances`.
   - Add `sky/templates/modal-ray.yml.j2` and register it in
     `cloud_vm_ray_backend.py`.
   - In `run_instances`, create or reuse one Sandbox named from the SkyPilot
     cluster, start SSH, wait for readiness, read `sandbox.tunnels()[22]`, and
     return that as the head node endpoint.
   - Support only `sky launch`, `sky exec`, `sky status`, and `sky down` for one
     node.

3. P2: credentials and packaging.
   - Add `modal>=1.5.0; python_version >= "3.10"` to cloud extras.
   - Exclude Modal from `[all]` on Python 3.9, matching the existing dependency
     gating pattern for providers that require newer Python versions.
   - Implement `check_credentials()` using Modal config/token validation.
   - Support both `~/.modal.toml` and `MODAL_TOKEN_ID` /
     `MODAL_TOKEN_SECRET`.
   - Mount `~/.modal.toml` only when file-based credentials are in use.

4. P3: minimum tests maintainers will expect.
   - Unit test cloud registration, unsupported feature errors, catalog lookup,
     accelerator mapping, dependency gating, credential failure messages,
     status mapping, and idempotent termination.
   - Mock Modal SDK calls; do not require real Modal credentials in unit tests.
   - Add one Modal-specific smoke test marker that launches a cheap CPU or GPU
     task, runs `echo`, executes once, checks status, and tears down.
   - Run `bash format.sh --files` on touched Python files and targeted pytest.

5. P4: documentation for acceptance.
   - Add installation instructions for `uv pip install "skypilot[modal]"` and
     Modal credential setup.
   - Add one minimal YAML example using `resources.cloud: modal`.
   - Add one GPU example.
   - Document MVP limitations prominently: single-node only, Modal Sandbox
     timeout/lifetime behavior, no managed jobs or serve controllers, no object
     store mounting, and no stop/resume.

6. P5: low-risk v1 follow-ups.
   - Expand region handling if Modal exposes more granular region definitions
     or provider-specific placement controls.
   - Add Docker image support through Modal image construction.
   - Add `open_ports` / `query_ports` through Modal Sandbox tunnels.
   - Add better identity strings from Modal workspace/profile information.
   - Add a catalog fetcher so prices and GPU lists are not manually maintained.

7. P6: medium-risk feature expansion.
   - Map Modal Volumes and CloudBucketMounts into SkyPilot storage/volume
     concepts.
   - Map Modal Secrets from SkyPilot environment or secret conventions.
   - Approximate autostop with Modal Sandbox timeout controls where possible.
   - Add snapshot-based recovery for expired Sandboxes.

8. P7: hard features.
   - Multi-node clusters using Modal's multi-node or cluster primitives, with
     Ray networking tested across containers.
   - Managed jobs controller hosting on Modal.
   - SkyServe controller and replica support.
   - Region/capacity-aware failover, quota checks, and robust retry semantics.

9. P8: full support and hardening.
   - Old-client/new-server compatibility tests.
   - Remote API server credential injection.
   - Helm chart secret support.
   - Dashboard infra display.
   - Buildkite smoke coverage.
   - Concurrent launch/down race tests.
   - Leak detection for orphaned Sandboxes, apps, and tunnels.
   - Long-running timeout and recovery tests.

## Assumptions

- "Minimum supported" means real launch, exec, status, and down work, not merely
  parser/catalog recognition.
- The first implementation PR should be intentionally conservative and
  feature-gated.
- Modal-native execution is deferred unless the P0 SSH tunnel proof fails.
