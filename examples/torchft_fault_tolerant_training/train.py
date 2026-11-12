"""Train with torchft so replica groups can fail and rejoin without a restart.

Every SkyPilot node runs one torchft replica group. The groups average their
gradients on every step through torchft's fault-tolerant all-reduce. When a
node is lost, the remaining groups discard at most the step in flight and keep
training at a smaller world size. The replacement node joins the next quorum
and copies the model and optimizer state from a healthy group.

With several GPUs on a node, the launch script starts one process per GPU.
Every GPU holds a full copy of the model: the same GPU index of each group
runs the fault-tolerant all-reduce, and the GPUs of one group then average
among themselves over NCCL.
"""

import argparse
import datetime
import os
import socket
import time

import torch
from torch import nn
from torch import optim
import torch.distributed as dist
import torchft
from torchft import manager as torchft_manager
from torchft.checkpointing import pg_transport

LIGHTHOUSE_PORT = 29510
STORE_PORT = 29600


def log(message: str) -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    print(f'{now.strftime("%H:%M:%S.%f")[:-3]} {message}', flush=True)


def wait_for_lighthouse(host: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with socket.create_connection((host, LIGHTHOUSE_PORT), timeout=5):
                return
        except OSError as error:
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f'Lighthouse {host}:{LIGHTHOUSE_PORT} was not reachable '
                    f'within {timeout}s; last error: {error}') from error
            time.sleep(2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--lighthouse-host', required=True)
    parser.add_argument('--steps', type=int, default=1000)
    parser.add_argument('--step-seconds', type=float, default=1.0)
    parser.add_argument('--log-every', type=int, default=10)
    parser.add_argument('--min-replicas', type=int, default=1)
    parser.add_argument('--process-group',
                        choices=['gloo', 'nccl'],
                        default='gloo')
    parser.add_argument('--timeout', type=float, default=10.0)
    parser.add_argument('--quorum-timeout', type=float, default=600.0)
    parser.add_argument('--quorum-retries', type=int, default=119)
    parser.add_argument('--lighthouse-timeout', type=float, default=900.0)
    parser.add_argument('--group-rank', type=int, default=0)
    parser.add_argument('--group-size', type=int, default=1)
    parser.add_argument('--hidden-size', type=int, default=1024)
    parser.add_argument('--batch-size', type=int, default=4096)
    args = parser.parse_args()

    pod_ip = os.environ['POD_IP']
    # torchft advertises socket.gethostname() as the address of each group's
    # rendezvous store, and a pod's hostname does not resolve from other pods.
    socket.gethostname = lambda: pod_ip

    node_rank = int(os.environ['SKYPILOT_NODE_RANK'])
    name = f'trainer {node_rank}'
    if args.group_size > 1:
        name += f' gpu {args.group_rank}'
    wait_for_lighthouse(args.lighthouse_host, args.lighthouse_timeout)

    if not torch.cuda.is_available():
        raise RuntimeError('SkyPilot assigned a GPU but PyTorch cannot use it')
    torch.cuda.set_device(args.group_rank)
    device = torch.device('cuda', args.group_rank)

    timeout = datetime.timedelta(seconds=args.timeout)
    if args.process_group == 'nccl':
        process_group = torchft.ProcessGroupNCCL(timeout=timeout)
        transport_device = device
    else:
        process_group = torchft.ProcessGroupGloo(timeout=timeout)
        # Gloo sends from host memory only.
        transport_device = torch.device('cpu')

    # A fixed regression target, so the loss shows whether a replacement
    # really received the trained weights.
    torch.manual_seed(0)
    teacher = nn.Sequential(nn.Linear(64, 256), nn.Tanh(),
                            nn.Linear(256, 1)).to(device)
    for parameter in teacher.parameters():
        parameter.requires_grad_(False)

    # Every copy of the model starts identical: the GPUs of one group exchange
    # gradients, never weights.
    torch.manual_seed(1)
    model = nn.Sequential(
        nn.Linear(64, args.hidden_size),
        nn.ReLU(),
        nn.Linear(args.hidden_size, args.hidden_size),
        nn.ReLU(),
        nn.Linear(args.hidden_size, args.hidden_size),
        nn.ReLU(),
        nn.Linear(args.hidden_size, 1),
    ).to(device)
    base_optimizer = optim.AdamW(model.parameters(), lr=1e-3)

    def load_state_dict(state_dict):
        model.load_state_dict(state_dict['model'])
        base_optimizer.load_state_dict(state_dict['optim'])

    def state_dict():
        return {
            'model': model.state_dict(),
            'optim': base_optimizer.state_dict(),
        }

    # torchrun normally provides the group's store. With several processes it
    # comes with the group's own process group, which the GPUs of one node use
    # among themselves.
    store = None
    if args.group_size > 1:
        dist.init_process_group(
            backend='nccl',
            init_method=f'tcp://{pod_ip}:{STORE_PORT}',
            rank=args.group_rank,
            world_size=args.group_size,
        )
    else:
        # The store server stops when this object is collected.
        store = dist.TCPStore(host_name=pod_ip,
                              port=STORE_PORT,
                              is_master=True,
                              wait_for_workers=False)

    manager = torchft.Manager(
        pg=process_group,
        min_replica_size=args.min_replicas,
        load_state_dict=load_state_dict,
        state_dict=state_dict,
        replica_id=f'trainer_{node_rank}',
        store_addr=pod_ip,
        store_port=STORE_PORT,
        rank=args.group_rank,
        world_size=args.group_size,
        lighthouse_addr=f'http://{args.lighthouse_host}:{LIGHTHOUSE_PORT}',
        hostname=pod_ip,
        timeout=timeout,
        quorum_timeout=datetime.timedelta(seconds=args.quorum_timeout),
        # Without retries a trainer hangs when the lighthouse is replaced.
        # torchft spaces them quorum_timeout / (quorum_retries + 1) apart.
        quorum_retries=args.quorum_retries,
        world_size_mode=torchft_manager.WorldSizeMode.DYNAMIC,
        # The default HTTP transport advertises the pod's hostname.
        checkpoint_transport=pg_transport.PGTransport(process_group,
                                                      timeout=timeout,
                                                      device=transport_device),
    )
    ddp_model = torchft.DistributedDataParallel(manager, model)
    optimizer = torchft.Optimizer(manager, base_optimizer)
    criterion = nn.MSELoss()
    log(f'{name}: started on {torch.cuda.get_device_name(device)} '
        f'ip={pod_ip} process_group={args.process_group}')

    generator = torch.Generator(device=device)
    generator.manual_seed(1000 + 64 * node_rank + args.group_rank)
    replicas = None
    joined_at = None
    while manager.current_step() < args.steps:
        started_at = time.monotonic()
        previous_step = manager.current_step()
        optimizer.zero_grad()
        inputs = torch.randn(args.batch_size,
                             64,
                             device=device,
                             generator=generator)
        loss = criterion(ddp_model(inputs), teacher(inputs))
        loss.backward()
        if args.group_size > 1:
            for parameter in model.parameters():
                dist.all_reduce(parameter.grad, op=dist.ReduceOp.SUM)
                parameter.grad /= args.group_size
        optimizer.step()

        step = manager.current_step()
        if joined_at is None:
            joined_at = step
        if manager.num_participants() != replicas:
            change = manager.num_participants()
            if replicas is not None:
                change = f'{replicas} -> {change}'
            log(f'{name}: step {step}: replica groups {change}')
            replicas = manager.num_participants()
        if step == previous_step:
            log(f'{name}: step {step} discarded after '
                f'{time.monotonic() - started_at:.1f}s')
        elif step % args.log_every == 0:
            log(f'{name}: step {step} loss={loss.item():.5f} '
                f'replica_groups={replicas}')
        time.sleep(args.step_seconds)

    log(f'{name}: training complete at step {manager.current_step()} '
        f'(joined at step {joined_at}) loss={loss.item():.5f}')
    manager.shutdown(wait=False)
    del store


if __name__ == '__main__':
    main()
