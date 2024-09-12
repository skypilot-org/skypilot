# Script to send queries to an OpenAI API server
import requests
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

SKY_CALLBACK_ENABLED = True

try:
    import sky_callback
    print("SkyCallback enabled.")
except ImportError:
    print("SkyCallback disabled.")
    SKY_CALLBACK_ENABLED = False

# Configure query
url = "http://127.0.0.1:8000/v1/chat/completions"
headers = {
    "Content-Type": "application/json"
}
payload = {
    "model": "google/gemma-2b-it",
    "messages": [
        {"role": "user", "content": "Who are you?"}
    ]
}


class DummyContextManager:
    def __enter__(self):
        pass

    def __exit__(self, exc_type, exc_value, traceback):
        pass


step_ctx_manager = DummyContextManager if not SKY_CALLBACK_ENABLED else sky_callback.step


# Function to benchmark a single query
def benchmark_single_query():
    start_time = time.time()
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        end_time = time.time()
        time_taken = end_time - start_time
        return time_taken, response.status_code, response.text
    except requests.RequestException as e:
        return None, None, str(e)


# Function to run the benchmark in batches
def benchmark_in_batches(num_requests, batch_size, max_workers=5):
    response_times = []
    total_batches = (num_requests + batch_size - 1) // batch_size  # Total number of batches

    if SKY_CALLBACK_ENABLED:
        sky_callback.init(total_steps=total_batches)

    for batch in range(total_batches):
        # Use step context manager to track each batch
        with step_ctx_manager():
            print(f"\nSending batch {batch + 1}/{total_batches}...")

            # Calculate the number of requests in this batch
            requests_in_batch = min(batch_size,
                                    num_requests - batch * batch_size)

            # Use ThreadPoolExecutor to send requests in parallel
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(benchmark_single_query) for _ in
                           range(requests_in_batch)]

                # As each future completes, process the result
                for i, future in enumerate(as_completed(futures)):
                    time_taken, status_code, response_text = future.result()
                    if time_taken:
                        response_times.append(time_taken)
                        print(
                            f"Batch {batch + 1} - Request {i + 1}: {status_code}, Time taken: {time_taken:.4f} seconds")
                    else:
                        print(
                            f"Batch {batch + 1} - Request {i + 1} failed: {response_text}")

    # Calculate average response time
    if response_times:
        average_time = sum(response_times) / len(response_times)
        print(f"\nAverage response time: {average_time:.4f} seconds")
    else:
        print("\nNo successful requests.")


# Number of total requests, batch size, and number of threads (concurrent workers)
num_requests = 50  # Total number of requests to send
batch_size = 5  # Number of requests per batch
max_workers = 5  # Number of concurrent threads within each batch

print(
    f"Benchmarking {num_requests} requests to {url} in batches of {batch_size}...\n")
benchmark_in_batches(num_requests, batch_size, max_workers)
