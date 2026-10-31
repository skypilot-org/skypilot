# EFA detection fallback implementation plan

> **For implementation:** Use `superpowers:executing-plans` and test first.

**Goal:** On autoscaling AWS Kubernetes clusters, `network_tier: best` must request catalog-sized EFA when a currently running matching GPU node does not advertise an EFA resource.

**Architecture:** `_detect_network_type()` already scans nodes for a live EFA allocation and has a catalog fallback for cold AWS clusters. A matching non-EFA GPU node currently returns too early, bypassing that fallback. Continue the scan instead; live EFA remains authoritative, while the existing AWS/autoscaler/catalog gates govern the fallback.

**Tech Stack:** Python, pytest/unittest mocks.

### Task 1: Continue past non-EFA AWS GPU nodes

**Files:**
- Modify: `sky/clouds/kubernetes.py:1679-1698`
- Modify: `tests/unit_tests/test_sky/clouds/test_kubernetes.py:4085-4109`
- Modify: `tests/unit_tests/test_sky/clouds/test_kubernetes.py:4339-4351`

**Step 1: Write the failing tests**

Add a `TestDetectNetworkTypeEfaScaleFromZero` case with a matching AWS H100 GPU node that lacks `vpc.amazonaws.com/efa`, a configured autoscaler, and `derived_efa=32`; expect `AWS_EFA` with `{'efa_count': 32}`. Change the existing static-node test so its expected result is `NONE, None`, because an unschedulable static cluster cannot request EFA from a catalog.

**Step 2: Run the focused tests to verify they fail**

Run:
`uv run pytest tests/unit_tests/test_sky/clouds/test_kubernetes.py -k "aws_efa_detection_node_without_efa_resource or matching_gpu_without_efa_uses_catalog" -q`

Expected: the new autoscaling-node case fails because `_detect_network_type()` returns `AWS_EFA, None` before the catalog fallback.

**Step 3: Implement the smallest fix**

In `_detect_network_type()`, replace the post-EFA `return (network_type, metadata)` for a matching AWS GPU node with `continue`. Do not change the live `efa_count > 0` return or the AWS/autoscaler/catalog fallback conditions.

**Step 4: Run focused verification**

Run:
`uv run pytest tests/unit_tests/test_sky/clouds/test_kubernetes.py -k "aws_efa_detection_node_without_efa_resource or matching_gpu_without_efa_uses_catalog or warm_node_scan_wins" -q`

Expected: all selected tests pass; live EFA sizing still wins over the catalog.

**Step 5: Run the EFA regression suite**

Run:
`uv run pytest tests/unit_tests/test_sky/clouds/test_kubernetes.py -k "Efa or efa" -q`

**Step 6: Commit**

```bash
git add sky/clouds/kubernetes.py tests/unit_tests/test_sky/clouds/test_kubernetes.py
git commit -m "fix(kubernetes): fall back to catalog EFA sizing"
```
