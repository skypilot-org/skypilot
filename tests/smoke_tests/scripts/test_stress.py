import concurrent.futures
import io
import threading
import time

import click

import sky
from sky.utils import common

# Global statistics
stats = {
    'long_server_submissions': 0,
    'long_verifications': 0,
    'short_server_submissions': 0,
    'short_verifications': 0
}
stats_lock = threading.Lock()


def query_status(refresh: common.StatusRefreshMode, cluster_name: str):
    global stats

    # Capture output using stream_and_get's output_stream parameter
    captured_output = io.StringIO()

    # Submit to server and track statistics
    request_id = sky.status(refresh=refresh)
    with stats_lock:
        if refresh == common.StatusRefreshMode.FORCE:
            stats['long_server_submissions'] += 1
        else:
            stats['short_server_submissions'] += 1

    # Stream and get results
    sky.stream_and_get(request_id, output_stream=captured_output)

    # Get the captured output
    output = captured_output.getvalue()
    captured_output.close()

    # Verify cluster name appears in the output and track statistics
    with stats_lock:
        if refresh == common.StatusRefreshMode.FORCE:
            stats['long_verifications'] += 1
        else:
            stats['short_verifications'] += 1

    if cluster_name not in output:
        raise ValueError(
            f"Cluster name '{cluster_name}' not found in status output")

    return output


@click.command()
@click.option('--cluster-name',
              required=True,
              help='Name of the cluster to verify in status output')
@click.option('--long-concurrency',
              default=100,
              help='Number of concurrent long running requests')
@click.option('--short-concurrency',
              default=100,
              help='Number of concurrent short running requests')
def main(cluster_name: str, long_concurrency: int, short_concurrency: int):
    """Test query_status with cluster name verification using thread pool."""
    global stats

    print(f"Testing status queries for cluster: {cluster_name}")
    print(f"Long running requests: {long_concurrency}")
    print(f"Short running requests: {short_concurrency}")
    print("\nProgress Format:")
    print(
        "Thread(finished/total): Long X/Y, Short X/Y | Server(submitted-finished/total): Long X-Y/Z, Short X-Y/Z"
    )
    print("Where: X=finished, Y=total, Z=total for that type")
    print("=" * 80)

    # Thread pool for parallel execution
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        # Submit requests in mixed order (alternating long and short)
        long_futures = []
        short_futures = []

        # Determine the maximum number of requests to submit
        max_requests = max(long_concurrency, short_concurrency)

        for i in range(max_requests):
            # Submit long request if we haven't reached the limit
            if i < long_concurrency:
                future = executor.submit(query_status,
                                         common.StatusRefreshMode.FORCE,
                                         cluster_name)
                long_futures.append(future)

            # Submit short request if we haven't reached the limit
            if i < short_concurrency:
                future = executor.submit(query_status,
                                         common.StatusRefreshMode.NONE,
                                         cluster_name)
                short_futures.append(future)

        # Monitor progress
        total_submitted = long_concurrency + short_concurrency
        long_finished = 0
        short_finished = 0

        print(f"\nSubmitted {total_submitted} requests total:")
        print(f"  - Long running: {long_concurrency}")
        print(f"  - Short running: {short_concurrency}")

        while long_finished < long_concurrency or short_finished < short_concurrency:
            # Check long running requests
            for future in long_futures:
                if future.done() and not future._done_callbacks:
                    long_finished += 1
                    future._done_callbacks = True  # Mark as counted

            # Check short running requests
            for future in short_futures:
                if future.done() and not future._done_callbacks:
                    short_finished += 1
                    future._done_callbacks = True  # Mark as counted

            total_finished = long_finished + short_finished

            # Display progress with statistics
            with stats_lock:
                long_submitted = stats['long_server_submissions']
                long_finished_server = stats['long_verifications']
                short_submitted = stats['short_server_submissions']
                short_finished_server = stats['short_verifications']

                # Format: Thread(Submit/total): Long X/Y, Short X/Y | Server(Submit-finished/total): Long X-Y/Z, Short X-Y/Z
                thread_info = f"Thread({total_finished}/{total_submitted}): "
                thread_parts = []
                if long_concurrency > 0:
                    thread_parts.append(
                        f"Long {long_finished}/{long_concurrency}")
                if short_concurrency > 0:
                    thread_parts.append(
                        f"Short {short_finished}/{short_concurrency}")
                thread_info += ", ".join(thread_parts)

                server_info = f"Server({long_submitted + short_submitted}-{long_finished_server + short_finished_server}/{total_submitted}): "
                server_parts = []
                if long_submitted > 0:
                    server_parts.append(
                        f"Long {long_submitted}-{long_finished_server}/{long_concurrency}"
                    )
                if short_submitted > 0:
                    server_parts.append(
                        f"Short {short_submitted}-{short_finished_server}/{short_concurrency}"
                    )
                server_info += ", ".join(server_parts)

                progress_line = f"\r{thread_info} | {server_info}"
                print(progress_line, end='', flush=True)

            time.sleep(3)  # Update every 500ms

        print(f"\n\n✓ All {total_submitted} requests completed!")

        # Display final statistics
        print("\n" + "=" * 60)
        print("FINAL STATISTICS")
        print("=" * 60)
        with stats_lock:
            print(f"Thread Summary:")
            print(
                f"  Long threads: {long_concurrency} submitted, {long_finished} finished"
            )
            print(
                f"  Short threads: {short_concurrency} submitted, {short_finished} finished"
            )
            print(
                f"  Total threads: {total_submitted} submitted, {total_finished} finished"
            )
            print(f"\nServer Request Summary:")
            print(
                f"  Long requests submitted to server: {stats['long_server_submissions']}"
            )
            print(
                f"  Long requests finished from server: {stats['long_verifications']}"
            )
            print(
                f"  Short requests submitted to server: {stats['short_server_submissions']}"
            )
            print(
                f"  Short requests finished from server: {stats['short_verifications']}"
            )


if __name__ == '__main__':
    main()
