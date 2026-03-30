"""Dummy JAX distributed training script with fault tolerance.

Runs a simple matrix multiply training loop across all available nodes.
Handles worker failures gracefully by checkpointing and detecting peer changes.
"""
import json
import os
import subprocess
import time

import jax
import jax.numpy as jnp
import numpy as np


CHECKPOINT_DIR = "/tmp/jax_ckpt"
BATCH_SIZE = 64
HIDDEN_DIM = 256
NUM_EPOCHS = int(os.environ.get("NUM_EPOCHS", 50))
STEPS_PER_EPOCH = int(os.environ.get("STEPS_PER_EPOCH", 20))


def get_peer_count():
    """Get current number of live peers via skypilot_nodes."""
    try:
        result = subprocess.run(
            ["skypilot_nodes"], capture_output=True, text=True, timeout=5
        )
        data = json.loads(result.stdout)
        return data.get("count", 1)
    except Exception:
        return 1


def save_checkpoint(params, epoch, step, loss):
    """Save training state to local checkpoint."""
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    ckpt = {
        "epoch": epoch,
        "step": step,
        "loss": float(loss),
        "param_w1": np.array(params["w1"]),
        "param_w2": np.array(params["w2"]),
    }
    path = os.path.join(CHECKPOINT_DIR, "latest.npz")
    np.savez(path, **ckpt)
    print(f"  Checkpoint saved: epoch={epoch} step={step} loss={loss:.4f}")


def load_checkpoint():
    """Load latest checkpoint if available."""
    path = os.path.join(CHECKPOINT_DIR, "latest.npz")
    if not os.path.exists(path):
        return None
    ckpt = dict(np.load(path, allow_pickle=True))
    print(
        f"  Resuming from checkpoint: epoch={ckpt['epoch']} "
        f"step={ckpt['step']} loss={float(ckpt['loss']):.4f}"
    )
    return ckpt


def init_params(key):
    """Initialize simple 2-layer MLP parameters."""
    k1, k2 = jax.random.split(key)
    return {
        "w1": jax.random.normal(k1, (HIDDEN_DIM, HIDDEN_DIM)) * 0.01,
        "w2": jax.random.normal(k2, (HIDDEN_DIM, 1)) * 0.01,
    }


def forward(params, x):
    """Forward pass: x -> relu(x @ w1) @ w2."""
    h = jnp.maximum(x @ params["w1"], 0)
    return h @ params["w2"]


def loss_fn(params, x, y):
    """MSE loss."""
    preds = forward(params, x)
    return jnp.mean((preds - y) ** 2)


def train_step(params, x, y, lr=0.001):
    """Single training step with gradient descent."""
    loss, grads = jax.value_and_grad(loss_fn)(params, x, y)
    params = jax.tree.map(lambda p, g: p - lr * g, params, grads)
    return params, loss


def generate_batch(key):
    """Generate a random batch of data."""
    k1, k2 = jax.random.split(key)
    x = jax.random.normal(k1, (BATCH_SIZE, HIDDEN_DIM))
    # Target: sum of first 10 features (simple regression task)
    y = jnp.sum(x[:, :10], axis=1, keepdims=True)
    return x, y


def main():
    rank = int(os.environ.get("SKYPILOT_NODE_RANK", 0))
    num_nodes = int(os.environ.get("SKYPILOT_NUM_NODES", 1))

    print(f"=== JAX Training Node {rank}/{num_nodes} ===")
    print(f"JAX version: {jax.__version__}")
    print(f"Devices: {jax.devices()}")

    # Initialize or resume
    rng = jax.random.PRNGKey(42)
    params = init_params(rng)
    start_epoch = 0
    start_step = 0

    ckpt = load_checkpoint()
    if ckpt is not None:
        params = {
            "w1": jnp.array(ckpt["param_w1"]),
            "w2": jnp.array(ckpt["param_w2"]),
        }
        start_epoch = int(ckpt["epoch"])
        start_step = int(ckpt["step"]) + 1
        if start_step >= STEPS_PER_EPOCH:
            start_epoch += 1
            start_step = 0

    if rank == 0:
        # Controller: run training and monitor peers
        prev_peers = num_nodes
        for epoch in range(start_epoch, NUM_EPOCHS):
            print(f"\n{'='*50}")
            print(f"  EPOCH {epoch + 1}/{NUM_EPOCHS}")
            print(f"{'='*50}")

            for step in range(start_step, STEPS_PER_EPOCH):
                step_key = jax.random.fold_in(rng, epoch * STEPS_PER_EPOCH + step)
                x, y = generate_batch(step_key)
                params, loss = train_step(params, x, y)

                # Check peers periodically
                if step % 5 == 0:
                    peers = get_peer_count()
                    status = "OK"
                    if peers < prev_peers:
                        status = f"LOST {prev_peers - peers} workers!"
                    elif peers > prev_peers:
                        status = f"GAINED {peers - prev_peers} workers"
                    prev_peers = peers

                    print(
                        f"  Step {step}/{STEPS_PER_EPOCH} | "
                        f"loss={float(loss):.4f} | "
                        f"peers={peers} [{status}]"
                    )

                # Checkpoint every 10 steps
                if step % 10 == 0:
                    save_checkpoint(params, epoch, step, loss)

            start_step = 0  # Reset for next epoch
            save_checkpoint(params, epoch, STEPS_PER_EPOCH - 1, loss)
            print(f"Epoch {epoch + 1} complete | loss={float(loss):.4f}")

        print(f"\n{'='*50}")
        print("  TRAINING COMPLETE")
        print(f"{'='*50}")

    else:
        # Worker: simulate distributed compute
        print(f"Worker {rank} running distributed compute loop")
        step = 0
        while True:
            step_key = jax.random.fold_in(rng, step)
            x, y = generate_batch(step_key)
            # Workers do local forward passes to simulate distributed training
            loss = loss_fn(params, x, y)

            if step % 12 == 0:
                peers = get_peer_count()
                print(
                    f"Worker {rank} | step={step} | "
                    f"local_loss={float(loss):.4f} | peers={peers}"
                )
            step += 1
            time.sleep(5)


if __name__ == "__main__":
    main()
