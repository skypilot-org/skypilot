"""Elastic Training Controller

Discovers workers via SkyPilot's /nodes endpoint. Maintains a fixed
set of active workers (MIN_WORKERS) and keeps the rest as standby.
When an active worker dies, promotes a standby to replace it.

Workers keep their rank stable across epochs.
"""
import concurrent.futures
import json
import os
import time
import urllib.request

NODES_URL = (f'http://localhost:'
             f'{os.environ.get("SKYPILOT_DISCOVERY_PORT", 9876)}/nodes')
EPOCHS = int(os.environ.get('TOTAL_EPOCHS', 20))
MIN_WORKERS = int(os.environ.get('MIN_WORKERS', 4))


def get_all_workers():
    """Query /nodes and return all worker nodes."""
    for i in range(5):
        try:
            data = json.loads(
                urllib.request.urlopen(NODES_URL, timeout=30).read())
            break
        except Exception as e:
            print(e)
            if i == 4:
                return []
    nodes = data.get('workers', data.get('nodes', []))
    return sorted(nodes, key=lambda n: n['rank'])


def get_ready_workers():
    """Return workers with app_status == 'ready' (for initial setup)."""
    return [n for n in get_all_workers() if n.get('app_status') == 'ready']


def get_alive_workers():
    """Return workers that are alive (RUNNING status, any app_status).

    Used for liveness checks — a worker in 'training' status is still
    alive, just busy with the previous epoch.
    """
    return [
        n for n in get_all_workers()
        if n.get('status') == 'RUNNING'
    ]


def send_state(worker, state):
    """Push training state to a worker via HTTP."""
    url = f'http://{worker["ip"]}:8080/start'
    data = json.dumps(state).encode()
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header('Content-Type', 'application/json')
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read())
    except Exception as e:
        return {'error': str(e), 'rank': worker['rank']}


def main():
    print('=== CONTROLLER started ===', flush=True)
    with open(os.environ['SKYPILOT_APP_STATUS'], 'w') as f:
        f.write('controller')

    # Wait for enough workers to be ready (initial setup)
    print(f'Waiting for {MIN_WORKERS} ready workers...', flush=True)
    while True:
        ready = get_ready_workers()
        if len(ready) >= MIN_WORKERS:
            break
        print(f'  {len(ready)}/{MIN_WORKERS} ready', flush=True)
        time.sleep(2)

    # Select initial active set
    active_ranks = {w['rank'] for w in ready[:MIN_WORKERS]}
    print(
        f'Active: ranks {sorted(active_ranks)}, '
        f'Standby: {len(ready) - MIN_WORKERS}',
        flush=True)

    # Training loop
    for epoch in range(1, EPOCHS + 1):
        print(f'\n===== EPOCH {epoch}/{EPOCHS} =====', flush=True)

        # Check liveness of active workers using status (not app_status).
        # A worker in 'training' app_status is still alive.
        alive = get_alive_workers()
        alive_ranks = {w['rank'] for w in alive}
        dead = active_ranks - alive_ranks
        if dead:
            print(f'  Lost active: ranks {sorted(dead)}', flush=True)
            active_ranks -= dead
            # Promote alive non-active workers as standby replacements
            for w in alive:
                if (w['rank'] not in active_ranks and
                        len(active_ranks) < MIN_WORKERS):
                    active_ranks.add(w['rank'])
                    print(f'  Promoted rank {w["rank"]} to active', flush=True)

        # Wait if we still don't have enough active workers
        while len(active_ranks) < MIN_WORKERS:
            print(f'  Only {len(active_ranks)} active, waiting...', flush=True)
            time.sleep(2)
            alive = get_alive_workers()
            for w in alive:
                if (w['rank'] not in active_ranks and
                        len(active_ranks) < MIN_WORKERS):
                    active_ranks.add(w['rank'])
                    print(f'  Promoted rank {w["rank"]} to active', flush=True)

        # Build active worker list — use alive workers in active set
        # Re-check: some active-ranked pods may have died between
        # the while loop above and now.
        alive = get_alive_workers()
        active = [w for w in alive if w['rank'] in active_ranks]
        if not active:
            print(f'  No active workers alive, retrying...', flush=True)
            continue
        standby_count = len(alive) - len(active)

        n = len(active)
        peer_ips = [w['ip'] for w in active]
        worker_states = [(w, {
            'epoch': epoch,
            'rank': w['rank'],
            'partition': i,
            'total_partitions': n,
            'peers': peer_ips,
        }) for i, w in enumerate(active)]

        print(f'  Active: {n}, Standby: {standby_count}', flush=True)

        # Push state to active workers concurrently
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
            futures = {
                pool.submit(send_state, w, s): w for w, s in worker_states
            }
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if 'error' in result:
                    rank = result.get('rank', '?')
                    print(f'  rank {rank}: FAILED '
                          f'({result["error"][:50]})',
                          flush=True)
                    if isinstance(rank, int):
                        active_ranks.discard(rank)
                else:
                    results.append(result)

        if results:
            losses = [r['loss'] for r in results]
            avg_loss = sum(losses) / len(losses)
            total_samples = sum(r['samples'] for r in results)
            print(
                f'  Epoch {epoch}: {len(results)}/{n} '
                f'workers, {total_samples} samples, '
                f'avg_loss={avg_loss:.4f} '
                f'(min={min(losses):.4f}, max={max(losses):.4f})',
                flush=True)

    print('\n===== TRAINING COMPLETE =====', flush=True)


if __name__ == '__main__':
    main()
