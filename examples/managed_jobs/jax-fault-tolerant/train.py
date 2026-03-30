"""Fault-tolerant distributed JAX training example.

Implements the JAX fault tolerance pattern from
https://docs.jax.dev/en/latest/fault_tolerance.html

Uses live_devices() context manager to detect available devices each step,
allgather to find recovering processes, and send/recv to sync state from
process 0 to recovered workers. Training continues with whatever devices
are alive, automatically integrating recovered workers.

Usage with SkyPilot:
  # Set jobs.use_v1: true in ~/.sky/config.yaml
  sky jobs launch examples/managed_jobs/jax-fault-tolerant/job.yaml -y

  # Kill workers to test recovery:
  kubectl delete pod <job-pod>-{3..5} --force --grace-period=0
"""
import os

os.environ['XLA_FLAGS'] = ' '.join([
    '--xla_gpu_nccl_terminate_on_error=false',
    '--xla_gpu_nccl_async_execution=true',
    '--xla_gpu_nccl_blocking_communicators=false',
])
os.environ['XLA_PYTHON_CLIENT_ABORT_COLLECTIVES_ON_FAILURE'] = '1'
os.environ['XLA_PYTHON_CLIENT_USE_TFRT_GPU_CLIENT'] = '1'

import json
import subprocess
import time

import jax
import jax.numpy as jnp
from jax.experimental import shard_map
from jax.experimental.multihost_utils import live_devices
import numpy as np

NUM_EPOCHS = int(os.environ.get("NUM_EPOCHS", 50))
STEPS_PER_EPOCH = int(os.environ.get("STEPS_PER_EPOCH", 20))
LEARNING_RATE = 1e-4
DEVICE_BATCH_SIZE = 32
HIDDEN_DIM = 128
HEARTBEAT_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Distributed primitives (from JAX fault tolerance docs)
# ---------------------------------------------------------------------------

def allgather(x: float, devices: list[jax.Device]) -> list[float]:
    """Performs an AllGather of a scalar across the provided devices."""
    n = len(devices)
    mesh = jax.make_mesh((n,), ("i",), devices=devices)
    spec = jax.sharding.PartitionSpec('i')
    p = lambda x: jax.lax.all_gather(x, "i", tiled=True)
    f = jax.shard_map(p, mesh=mesh, in_specs=spec, out_specs=spec)
    return jax.block_until_ready(
        f(np.array([x] * len(devices)))
    ).addressable_shards[0].data


def replicated(x: jax.Array, devices: list[jax.Device]):
    """Return x replicated across the provided devices."""
    mesh = jax.make_mesh((len(devices),), ("i",), devices=devices)
    spec = jax.sharding.PartitionSpec(None)
    sharding = jax.sharding.NamedSharding(mesh, spec)
    shards = [jax.device_put(x, d) for d in jax.local_devices()]
    return jax.make_array_from_single_device_arrays(
        x.shape, sharding, shards
    )


def sharded(x: jax.Array, devices: list[jax.Device]):
    """Return x sharded across the provided devices."""
    n = len(devices)
    mesh = jax.make_mesh((n,), ("i",), devices=devices)
    spec = jax.sharding.PartitionSpec("i")
    sharding = jax.sharding.NamedSharding(mesh, spec)
    m = sharding.addressable_devices_indices_map(x.shape)
    shards = [jax.device_put(x[m[d]], d) for d in jax.local_devices()]
    return jax.make_array_from_single_device_arrays(x.shape, sharding, shards)


def send(x: jax.Array, from_device: jax.Device, to_device: jax.Device):
    """Send x from from_device to to_device (pairs with recv)."""
    devices = [from_device, to_device]
    psum = lambda x: jax.lax.psum(x, "i")
    mesh = jax.make_mesh((2,), ("i",), devices=devices)
    spec = jax.sharding.PartitionSpec(None)
    x = replicated(x, [from_device, to_device])
    shard_map.shard_map(psum, mesh=mesh, in_specs=spec, out_specs=spec)(x)


def recv(x: jax.Array, from_device: jax.Device, to_device: jax.Device):
    """Receive x from a matching send."""
    assert isinstance(x, jax.Array)
    to_device = jax.local_devices()[0]
    devices = [from_device, to_device]
    psum = lambda x: jax.lax.psum(x, "i")
    mesh = jax.make_mesh((2,), ("i",), devices=devices)
    spec = jax.sharding.PartitionSpec(None)
    x = jnp.zeros_like(x)
    x = replicated(x, [from_device, to_device])
    return shard_map.shard_map(
        psum, mesh=mesh, in_specs=spec, out_specs=spec
    )(x)


# ---------------------------------------------------------------------------
# Model: simple 2-layer MLP for regression
# ---------------------------------------------------------------------------

def init_params(key):
    k1, k2 = jax.random.split(key)
    return {
        "w1": jax.random.normal(k1, (HIDDEN_DIM, HIDDEN_DIM)) * 0.01,
        "w2": jax.random.normal(k2, (HIDDEN_DIM, 1)) * 0.01,
    }


def predict(params, x):
    h = jnp.maximum(x @ params["w1"], 0)
    return h @ params["w2"]


def loss_fn(params, x, y):
    return jnp.mean((predict(params, x) - y) ** 2)


loss_and_grad = jax.jit(jax.value_and_grad(loss_fn))


# ---------------------------------------------------------------------------
# Data generation
# ---------------------------------------------------------------------------

def generate_dataset(key, n_samples=10000):
    """Generate synthetic regression data."""
    k1, k2 = jax.random.split(key)
    X = jax.random.normal(k1, (n_samples, HIDDEN_DIM))
    # Target: linear combination of first 10 features + noise
    true_w = jnp.zeros(HIDDEN_DIM).at[:10].set(1.0)
    Y = (X @ true_w + 0.1 * jax.random.normal(k2, (n_samples,)))[:, None]
    return X, Y


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------

def get_coordinator_address():
    """Get coordinator address from SkyPilot node discovery."""
    try:
        result = subprocess.run(
            ["skypilot_nodes"], capture_output=True, text=True, timeout=5
        )
        data = json.loads(result.stdout)
        nodes = data.get("ready", [])
        # Rank 0 node is the coordinator
        for node in nodes:
            if node.get("rank") == 0:
                return f"{node['ip']}:9876"
    except Exception:
        pass
    return "localhost:9876"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    rank = int(os.environ.get("SKYPILOT_NODE_RANK", 0))
    num_nodes = int(os.environ.get("SKYPILOT_NUM_NODES", 1))

    print(f"=== JAX Fault-Tolerant Training ===")
    print(f"Process {rank}/{num_nodes}")

    # Initialize distributed JAX with fault tolerance
    jax.config.update("jax_enable_recoverability", True)

    coordinator_address = get_coordinator_address()
    print(f"Coordinator: {coordinator_address}")

    jax.distributed.initialize(
        coordinator_address=coordinator_address,
        process_id=rank,
        num_processes=num_nodes,
        local_device_ids=[0],
        heartbeat_timeout_seconds=HEARTBEAT_TIMEOUT,
    )
    print(f"Devices: {jax.devices()}")
    print(f"Local devices: {jax.local_devices()}")

    # Initialize model and data
    keys = iter(jax.random.split(jax.random.key(seed=42), num=3))
    weights = init_params(next(keys))
    X, Y = generate_dataset(next(keys))

    step = 0
    total_steps = NUM_EPOCHS * STEPS_PER_EPOCH

    while step < total_steps:
        epoch = step // STEPS_PER_EPOCH
        epoch_step = step % STEPS_PER_EPOCH

        try:
            with live_devices(jax.devices()) as devices:
                n_devices = len(devices)

                if epoch_step == 0:
                    print(f"\n{'='*60}")
                    print(f"  EPOCH {epoch + 1}/{NUM_EPOCHS}  "
                          f"({n_devices} live devices)")
                    print(f"{'='*60}")

                # --- Recovery: sync recovering processes with process 0 ---
                steps = allgather(step, devices)
                recovering = [
                    d for d, s in zip(devices, steps) if s != steps[0]
                ]
                if recovering:
                    print(f"  Recovering {len(recovering)} device(s)...")
                    for d in recovering:
                        if jax.process_index() == 0:
                            for key in weights:
                                send(weights[key], jax.devices()[0], d)
                            send(jnp.array([step]), jax.devices()[0], d)
                        elif d.process_index == jax.process_index():
                            for key in weights:
                                weights[key] = recv(
                                    weights[key], jax.devices()[0], d
                                )
                            step = int(
                                recv(
                                    jnp.array([step]), jax.devices()[0], d
                                )[0]
                            )
                    print(f"  Recovery complete, resuming at step {step}")

                # --- Training step ---
                # Replicate weights across live devices
                rep_weights = jax.tree.map(
                    lambda w: replicated(w, devices), weights
                )

                # Shard data batch across live devices
                batch_size = DEVICE_BATCH_SIZE * n_devices
                start = (step * batch_size) % len(X)
                stop = start + batch_size
                X_batch = sharded(X[start:stop], devices)
                Y_batch = sharded(Y[start:stop], devices)

                # Compute loss and gradients
                l, grads = loss_and_grad(rep_weights, X_batch, Y_batch)
                new_weights = jax.tree.map(
                    lambda w, g: jax.block_until_ready(
                        w - LEARNING_RATE * g
                    ),
                    rep_weights,
                    grads,
                )

        except Exception as e:
            print(f"  Step {step} FAILED: {e}")
            time.sleep(1)
            continue
        else:
            # Only update on success
            weights = jax.tree.map(
                lambda w: jnp.array(w.addressable_shards[0].data),
                new_weights,
            )
            loss_val = float(l)

            if step % 5 == 0 or epoch_step == STEPS_PER_EPOCH - 1:
                print(
                    f"  Step {epoch_step + 1}/{STEPS_PER_EPOCH} | "
                    f"loss={loss_val:.6f} | "
                    f"devices={n_devices}"
                )
            step += 1

        time.sleep(0.1)

    print(f"\n{'='*60}")
    print(f"  TRAINING COMPLETE ({step} steps)")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
