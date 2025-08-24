import concurrent.futures
import io
import threading
import time

import click

import sky
from sky.utils import common

# Global storage for request IDs
request_ids = {'long': [], 'short': []}
request_ids_lock = threading.Lock()

# Global statistics
stats = {
    'long_submitted': 0,
    'long_finished': 0,
    'short_submitted': 0,
    'short_finished': 0
}
stats_lock = threading.Lock()


def submit_request(refresh: common.StatusRefreshMode, cluster_name: str):
    """Submit a request to server and return request ID."""
    global stats

    request_id = sky.status(refresh=refresh)

    with stats_lock:
        if refresh == common.StatusRefreshMode.FORCE:
            stats['long_submitted'] += 1
        else:
            stats['short_submitted'] += 1

    return request_id


def query_request(request_id: str, cluster_name: str, request_type: str):
    """Query a specific request ID and verify cluster name."""
    global stats

    # Capture output using stream_and_get's output_stream parameter
    captured_output = io.StringIO()

    # Stream and get results
    sky.stream_and_get(request_id, output_stream=captured_output)

    # Get the captured output
    output = captured_output.getvalue()
    captured_output.close()

    # Verify cluster name appears in the output and track statistics
    with stats_lock:
        if request_type == 'long':
            stats['long_finished'] += 1
        else:
            stats['short_finished'] += 1

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
    print("Phase 1: Submit all requests to server")
    print("Phase 2: Query all request IDs and verify results")
    print("=" * 80)

    # Phase 1: Submit all requests to server
    print("\n=== Phase 1: Submitting all requests to server ===")

    with concurrent.futures.ThreadPoolExecutor(
            max_workers=8) as submit_executor:
        # Submit requests in mixed order (alternating long and short)
        long_futures = []
        short_futures = []

        # Determine the maximum number of requests to submit
        max_requests = max(long_concurrency, short_concurrency)

        for i in range(max_requests):
            # Submit long request if we haven't reached the limit
            if i < long_concurrency:
                future = submit_executor.submit(submit_request,
                                                common.StatusRefreshMode.FORCE,
                                                cluster_name)
                long_futures.append(future)

            # Submit short request if we haven't reached the limit
            if i < short_concurrency:
                future = submit_executor.submit(submit_request,
                                                common.StatusRefreshMode.NONE,
                                                cluster_name)
                short_futures.append(future)

        # Collect all request IDs in mixed order
        long_request_ids = []
        short_request_ids = []

        # Collect results in the same mixed order
        long_index = 0
        short_index = 0

        for i in range(max_requests):
            # Collect long result if we submitted one
            if i < long_concurrency:
                request_id = long_futures[long_index].result()
                long_request_ids.append(request_id)
                long_index += 1

            # Collect short result if we submitted one
            if i < short_concurrency:
                request_id = short_futures[short_index].result()
                short_request_ids.append(request_id)
                short_index += 1

    print(
        f"✓ Submitted {long_concurrency + short_concurrency} requests to server"
    )
    print(f"  - Long requests: {len(long_request_ids)}")
    print(f"  - Short requests: {len(short_request_ids)}")

    # Phase 2: Query all request IDs
    print("\n=== Phase 2: Querying all request results ===")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as query_executor:
        # Submit queries in mixed order (alternating long and short)
        long_query_futures = []
        short_query_futures = []

        # Determine the maximum number of queries to submit
        max_queries = max(len(long_request_ids), len(short_request_ids))

        for i in range(max_queries):
            # Submit query for long request if we have one
            if i < len(long_request_ids):
                future = query_executor.submit(query_request,
                                               long_request_ids[i],
                                               cluster_name, 'long')
                long_query_futures.append(future)

            # Submit query for short request if we have one
            if i < len(short_request_ids):
                future = query_executor.submit(query_request,
                                               short_request_ids[i],
                                               cluster_name, 'short')
                short_query_futures.append(future)

        # Monitor progress
        total_requests = long_concurrency + short_concurrency
        long_finished = 0
        short_finished = 0

        while long_finished < long_concurrency or short_finished < short_concurrency:
            # Check long requests
            for future in long_query_futures:
                if future.done() and not future._done_callbacks:
                    long_finished += 1
                    future._done_callbacks = True

            # Check short requests
            for future in short_query_futures:
                if future.done() and not future._done_callbacks:
                    short_finished += 1
                    future._done_callbacks = True

            total_finished = long_finished + short_finished

            # Display progress
            with stats_lock:
                progress_line = f"\rQuery Progress: {total_finished}/{total_requests} | " \
                              f"Long: {long_finished}/{long_concurrency} | " \
                              f"Short: {short_finished}/{short_concurrency} | " \
                              f"Submitted: Long {stats['long_submitted']}, Short {stats['short_submitted']} | " \
                              f"Finished: Long {stats['long_finished']}, Short {stats['short_finished']}"
                print(progress_line, end='', flush=True)

            time.sleep(1)

        print(f"\n\n✓ All {total_requests} requests completed!")

        # Display final statistics
        print("\n" + "=" * 60)
        print("FINAL STATISTICS")
        print("=" * 60)
        with stats_lock:
            print(f"Server Submissions:")
            print(f"  Long requests submitted: {stats['long_submitted']}")
            print(f"  Short requests submitted: {stats['short_submitted']}")
            print(
                f"  Total submitted: {stats['long_submitted'] + stats['short_submitted']}"
            )
            print(f"\nQuery Results:")
            print(f"  Long requests finished: {stats['long_finished']}")
            print(f"  Short requests finished: {stats['short_finished']}")
            print(
                f"  Total finished: {stats['long_finished'] + stats['short_finished']}"
            )


if __name__ == '__main__':
    main()
