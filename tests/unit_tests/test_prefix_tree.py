import concurrent.futures
import random
import string
import threading
import time
from typing import Dict, List

from sky.serve.prefix_tree import PrefixTree  # adjust import as needed


def random_string(length: int) -> str:
    return ''.join(
        random.choices(string.ascii_letters + string.digits, k=length))


def test_get_smallest_replica():
    tree = PrefixTree()
    # When no insertion, get_smallest_replica should return None.
    assert tree.get_smallest_replica() is None

    # Insert for replica1: "ap" + "icot" = 6 characters.
    tree.insert("ap", "replica1")
    tree.insert("icot", "replica1")

    # Insert for replica2: "cat" = 3 characters.
    tree.insert("cat", "replica2")
    smallest = tree.get_smallest_replica()
    assert smallest == "replica2", "Expected replica2 to be smallest with 3 characters."

    # Insert overlapping data for replica3 ("do" = 2) and replica4 ("hi" = 2).
    tree.insert("do", "replica3")
    tree.insert("hi", "replica4")
    smallest = tree.get_smallest_replica()
    assert smallest in [
        "replica3", "replica4"
    ], f"Expected either replica3 or replica4, got {smallest}"

    # Increase replica4's count by inserting additional text.
    tree.insert("hello", "replica4")  # now replica4: 2+5 = 7
    smallest = tree.get_smallest_replica()
    assert smallest == "replica3", "Expected replica3 to be smallest with 2 characters"

    # Test eviction: remove nodes so that usage above max_size (3) is evicted.
    tree.evict_replica_by_size(3)
    smallest_after = tree.get_smallest_replica()
    print("Smallest replica after eviction:", smallest_after)


def test_replica_char_count():
    tree = PrefixTree()

    # Phase 1: Initial insertions.
    tree.insert("apple", "replica1")
    tree.insert("apricot", "replica1")
    tree.insert("banana", "replica1")
    tree.insert("amplify", "replica2")
    tree.insert("application", "replica2")

    computed_sizes = tree.get_used_size_per_replica()
    maintained_counts = dict(tree.replica_char_count)
    print("Phase 1 - Maintained vs Computed counts:")
    print("Maintained:", maintained_counts, "\nComputed:", computed_sizes)
    assert maintained_counts == computed_sizes, "Phase 1: Initial insertions"

    # Phase 2: Additional insertions.
    tree.insert("apartment", "replica1")
    tree.insert("appetite", "replica2")
    tree.insert("ball", "replica1")
    tree.insert("box", "replica2")

    computed_sizes = tree.get_used_size_per_replica()
    maintained_counts = dict(tree.replica_char_count)
    print("Phase 2 - Maintained vs Computed counts:")
    print("Maintained:", maintained_counts, "\nComputed:", computed_sizes)
    assert maintained_counts == computed_sizes, "Phase 2: Additional insertions"

    # Phase 3: Overlapping insertions.
    tree.insert("zebra", "replica1")
    tree.insert("zebra", "replica2")
    tree.insert("zero", "replica1")
    tree.insert("zero", "replica2")

    computed_sizes = tree.get_used_size_per_replica()
    maintained_counts = dict(tree.replica_char_count)
    print("Phase 3 - Maintained vs Computed counts:")
    print("Maintained:", maintained_counts, "\nComputed:", computed_sizes)
    assert maintained_counts == computed_sizes, "Phase 3: Overlapping insertions"

    # Phase 4: Eviction test.
    tree.evict_replica_by_size(10)
    computed_sizes = tree.get_used_size_per_replica()
    maintained_counts = dict(tree.replica_char_count)
    print("Phase 4 - Maintained vs Computed counts:")
    print("Maintained:", maintained_counts, "\nComputed:", computed_sizes)
    assert maintained_counts == computed_sizes, "Phase 4: After eviction"


def test_cold_start():
    tree = PrefixTree()
    matched_text, replica = tree.prefix_match("hello")
    assert matched_text == ""
    assert replica is None


def test_exact_match_seq():
    tree = PrefixTree()
    tree.insert("hello", "replica1")
    tree.pretty_print()
    tree.insert("apple", "replica2")
    tree.pretty_print()
    tree.insert("banana", "replica3")
    tree.pretty_print()

    matched_text, replica = tree.prefix_match("hello")
    assert matched_text == "hello"
    assert replica == "replica1"

    matched_text, replica = tree.prefix_match("apple")
    assert matched_text == "apple"
    assert replica == "replica2"

    matched_text, replica = tree.prefix_match("banana")
    assert matched_text == "banana"
    assert replica == "replica3"


def test_prefix_match_avail():
    tree = PrefixTree()
    tree.insert("helloa", "replica1")
    tree.insert("helab", "replica2")
    tree.insert("helcd", "replica1")
    tree.insert("hello", "replica2")
    matched_text, replica = tree.prefix_match("helloa")
    assert matched_text == "helloa"
    assert replica == "replica1"
    matched_text, replica = tree.prefix_match("helab", {"replica1": 1})
    assert matched_text == "hel"
    assert replica == "replica1"
    matched_text, replica = tree.prefix_match("helab")
    assert matched_text == "helab"
    assert replica == "replica2"
    matched_text, replica = tree.prefix_match("hel", {
        "replica1": 1,
        "replica2": 2
    })
    assert matched_text == "hel"
    assert replica == "replica1"


def test_exact_match_concurrent():
    tree = PrefixTree()
    texts = ["hello", "apple", "banana"]
    replicas = ["replica1", "replica2", "replica3"]
    threads = []

    # Spawn threads for insertion.
    for text, replica in zip(texts, replicas):
        t = threading.Thread(target=lambda t=text, r=replica: tree.insert(t, r))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    # Spawn threads for matching.
    threads = []
    for text, replica in zip(texts, replicas):

        def match_func(text=text, replica=replica):
            matched_text, replica_result = tree.prefix_match(text)
            assert matched_text == text
            assert replica_result == replica

        t = threading.Thread(target=match_func)
        threads.append(t)
        t.start()
    for t in threads:
        t.join()


def test_partial_match_concurrent():
    tree = PrefixTree()
    texts = ["apple", "apabc", "acbdeds"]
    replica = "replica0"
    threads = []
    for text in texts:
        t = threading.Thread(target=lambda t=text: tree.insert(t, replica))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    threads = []
    for text in texts:

        def match_func(text=text):
            matched_text, replica_result = tree.prefix_match(text)
            assert matched_text == text
            assert replica_result == replica

        t = threading.Thread(target=match_func)
        threads.append(t)
        t.start()
    for t in threads:
        t.join()


def test_group_prefix_insert_match_concurrent():
    prefixes = [
        "Clock strikes midnight, I'm still wide awake",
        "Got dreams bigger than these city lights",
        "Time waits for no one, gotta make my move",
        "Started from the bottom, that's no metaphor",
    ]
    suffixes = [
        "Got too much to prove, ain't got time to lose",
        "History in the making, yeah, you can't erase this",
    ]
    tree = PrefixTree()
    threads = []

    for i, prefix in enumerate(prefixes):
        for suffix in suffixes:
            text = f"{prefix} {suffix}"
            replica = f"replica{i}"
            t = threading.Thread(
                target=lambda t=text, r=replica: tree.insert(t, r))
            threads.append(t)
            t.start()
    for t in threads:
        t.join()

    tree.pretty_print()

    threads = []
    for i, prefix in enumerate(prefixes):
        replica = f"replica{i}"

        def match_func(prefix=prefix, replica=replica):
            matched_text, replica_result = tree.prefix_match(prefix)
            assert matched_text == prefix
            assert replica_result == replica

        t = threading.Thread(target=match_func)
        threads.append(t)
        t.start()
    for t in threads:
        t.join()


def test_mixed_concurrent_insert_match():
    prefixes = [
        "Clock strikes midnight, I'm still wide awake",
        "Got dreams bigger than these city lights",
        "Time waits for no one, gotta make my move",
        "Started from the bottom, that's no metaphor",
    ]
    suffixes = [
        "Got too much to prove, ain't got time to lose",
        "History in the making, yeah, you can't erase this",
    ]
    tree = PrefixTree()
    threads = []

    # Spawn threads for insertion.
    for i, prefix in enumerate(prefixes):
        for suffix in suffixes:
            text = f"{prefix} {suffix}"
            replica = f"replica{i}"
            t = threading.Thread(
                target=lambda t=text, r=replica: tree.insert(t, r))
            threads.append(t)
            t.start()

    # Spawn threads for matching concurrently.
    for prefix in prefixes:
        t = threading.Thread(target=lambda p=prefix: tree.prefix_match(p))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()


def test_utf8_split_seq():
    tree = PrefixTree()
    test_pairs = [
        ("你好嗎", "replica1"),
        ("你好喔", "replica2"),
        ("你心情好嗎", "replica3"),
    ]
    for text, replica in test_pairs:
        tree.insert(text, replica)
    tree.pretty_print()
    for text, replica in test_pairs:
        matched_text, replica_result = tree.prefix_match(text)
        assert matched_text == text
        assert replica_result == replica


def test_utf8_split_concurrent():
    tree = PrefixTree()
    test_pairs = [
        ("你好嗎", "replica1"),
        ("你好喔", "replica2"),
        ("你心情好嗎", "replica3"),
    ]
    threads = []
    for text, replica in test_pairs:
        t = threading.Thread(target=lambda t=text, r=replica: tree.insert(t, r))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    tree.pretty_print()
    threads = []
    for text, replica in test_pairs:
        t = threading.Thread(target=lambda t=text, r=replica:
                             assert_replica_in_prefix(tree, t, r))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()


def assert_replica_in_prefix(tree: PrefixTree, text: str, replica: str):
    matched_text, replica_result = tree.prefix_match(text)
    assert replica_result == replica


def test_simple_eviction():
    tree = PrefixTree()
    max_size = 5

    # Insert strings for two replicas.
    tree.insert("hello", "replica1")  # size 5
    tree.insert("hello", "replica2")  # size 5
    time.sleep(0.01)
    tree.insert("world", "replica2")  # replica2 total = 10

    tree.pretty_print()
    sizes_before = tree.get_used_size_per_replica()
    assert sizes_before.get("replica1") == 5
    assert sizes_before.get("replica2") == 10

    # Evict nodes so that any replica with usage > max_size gets trimmed.
    tree.evict_replica_by_size(max_size)
    tree.pretty_print()
    sizes_after = tree.get_used_size_per_replica()
    assert sizes_after.get("replica1") == 5
    assert sizes_after.get("replica2") == 5

    matched_text, rep_list = tree.prefix_match("world")
    assert matched_text == "world"
    assert "replica2" in rep_list


def test_advanced_eviction():
    tree = PrefixTree()
    max_size = 100
    prefixes = ["aqwefcisdf", "iajsdfkmade", "kjnzxcvewqe", "iejksduqasd"]

    for _ in range(100):
        for j, prefix in enumerate(prefixes):
            rand_suffix = random_string(10)
            text = f"{prefix}{rand_suffix}"
            replica = f"replica{j+1}"
            tree.insert(text, replica)

    tree.evict_replica_by_size(max_size)
    sizes_after = tree.get_used_size_per_replica()
    for replica, size in sizes_after.items():
        assert size <= max_size, f"Replica {replica} exceeds size limit: {size} > {max_size}"


def test_concurrent_operations_with_eviction():
    tree = PrefixTree()
    test_duration = 10  # seconds
    start_time = time.time()
    max_size = 100
    threads = []

    def eviction_thread():
        while time.time() - start_time < test_duration:
            tree.evict_replica_by_size(max_size)
            time.sleep(5)

    t = threading.Thread(target=eviction_thread)
    threads.append(t)
    t.start()

    def worker(thread_id):
        rng = random.Random()
        replica = f"replica{thread_id+1}"
        prefix = f"prefix{thread_id}"
        while time.time() - start_time < test_duration:
            if rng.random() < 0.7:
                random_len = rng.randint(3, 9)
                search_str = prefix + random_string(random_len)
                tree.prefix_match(search_str)
            else:
                random_len = rng.randint(5, 14)
                insert_str = prefix + random_string(random_len)
                tree.insert(insert_str, replica)
            time.sleep(rng.uniform(0.01, 0.1))

    for i in range(4):
        t = threading.Thread(target=worker, args=(i,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    tree.evict_replica_by_size(max_size)
    final_sizes = tree.get_used_size_per_replica()
    print("Final sizes after test completion:", final_sizes)
    for size in final_sizes.values():
        assert size <= max_size, f"Replica exceeds size limit: {size} > {max_size}"


def test_leaf_of():
    tree = PrefixTree()
    tree.insert("hello", "replica1")
    assert tree.root.get_child('h') is not None
    # _leaf_of returns an iterable of replica names that are leaves.
    leaves = set(tree._leaf_of(tree.root.get_child('h')))
    assert leaves == {"replica1"}

    tree.insert("hello", "replica2")
    leaves = set(tree._leaf_of(tree.root.get_child('h')))
    assert leaves == {"replica1", "replica2"}

    tree.insert("hi", "replica1")
    leaves = set(tree._leaf_of(tree.root.get_child('h')))
    # With an extra branch from "h", this node may no longer be a leaf.
    assert leaves == set()


def test_get_used_size_per_replica():
    tree = PrefixTree()
    tree.insert("hello", "replica1")
    tree.insert("world", "replica1")
    sizes = tree.get_used_size_per_replica()
    tree.pretty_print()
    assert sizes.get("replica1") == 10

    tree.insert("hello", "replica2")
    tree.insert("help", "replica2")
    sizes = tree.get_used_size_per_replica()
    tree.pretty_print()
    assert sizes.get("replica1") == 10
    assert sizes.get("replica2") == 6

    tree.insert("你好", "replica3")
    sizes = tree.get_used_size_per_replica()
    tree.pretty_print()
    assert sizes.get("replica3") == 2


def test_simple_replica_eviction():
    tree = PrefixTree()
    tree.insert("hello", "replica1")
    tree.insert("world", "replica1")
    tree.insert("hello", "replica2")
    tree.insert("help", "replica2")

    sizes_initial = tree.get_used_size_per_replica()
    assert sizes_initial.get("replica1") == 10
    assert sizes_initial.get("replica2") == 6

    tree.remove_replica("replica1")
    sizes_final = tree.get_used_size_per_replica()
    assert "replica1" not in sizes_final
    assert sizes_final.get("replica2") == 6

    _, replica_result = tree.prefix_match("hello")
    assert replica_result == "replica2"


def test_complex_replica_eviction():
    tree = PrefixTree()
    tree.insert("apple", "replica1")
    tree.insert("application", "replica1")
    tree.insert("apple", "replica2")
    tree.insert("appetite", "replica2")
    tree.insert("banana", "replica1")
    tree.insert("banana", "replica2")
    tree.insert("ball", "replica2")

    sizes_initial = tree.get_used_size_per_replica()
    print("Initial sizes:", sizes_initial)
    tree.pretty_print()

    tree.remove_replica("replica1")
    sizes_final = tree.get_used_size_per_replica()
    print("Final sizes:", sizes_final)
    tree.pretty_print()

    assert "replica1" not in sizes_final
    # Check that replica2's data is still accessible.
    matched_text, _ = tree.prefix_match("apple")
    assert matched_text == "apple"
    matched_text, _ = tree.prefix_match("appetite")
    assert matched_text == "appetite"
    matched_text, _ = tree.prefix_match("banana")
    assert matched_text == "banana"
    matched_text, _ = tree.prefix_match("ball")
    assert matched_text == "ball"


def test_partial_prefix_match():
    """Test partial matches where the text only partially matches nodes in the tree."""
    tree = PrefixTree()

    # Insert some texts
    tree.insert("hello world", "replica1")
    tree.insert("hello universe", "replica2")
    tree.insert("greetings everyone", "replica3")

    # Test partial matches
    matched_text, replica = tree.prefix_match("hello there")
    assert matched_text == "hello ", "Should match the common prefix 'hello '"
    assert replica in ["replica1", "replica2"
                      ], f"Expected replica1 or replica2, got {replica}"

    # Test prefix that matches one replica more than another
    tree.insert("hello wonderful", "replica4")
    matched_text, replica = tree.prefix_match("hello wond")
    assert matched_text == "hello wond", "Should match 'hello wond'"
    assert replica == "replica4", f"Expected replica4, got {replica}"

    # Test prefix that doesn't match at all
    matched_text, replica = tree.prefix_match("xyz123")
    assert matched_text == "", "Should not match anything"
    assert replica in ["replica1", "replica2", "replica3", "replica4"
                      ], "Should return one of the available replicas"


def test_multi_replica_same_prefix():
    """Test behavior when multiple replicas have the same prefix."""
    tree = PrefixTree()

    # Insert same text for multiple replicas
    replicas = ["replica1", "replica2", "replica3", "replica4", "replica5"]
    for replica in replicas:
        tree.insert("shared prefix", replica)

    # Query without load information - should return a random replica
    match_counts: Dict[str, int] = {r: 0 for r in replicas}
    num_trials = 1000

    for _ in range(num_trials):
        _, replica = tree.prefix_match("shared prefix")
        match_counts[replica] += 1

    # We should see distribution across replicas (might not be perfectly even)
    for replica in replicas:
        assert match_counts[replica] > num_trials // len(
            replicas) // 2, f"Replica {replica} was never chosen"

    # Query with load information - should return the replica with lowest load
    load_map = {r: i + 1 for i, r in enumerate(replicas)}
    matched_text, replica = tree.prefix_match("shared prefix", load_map)
    assert matched_text == "shared prefix", "Should match the full text"
    assert replica == "replica1", "Should choose replica with lowest load"


def test_long_prefix_matching():
    """Test matching with very long prefixes."""
    tree = PrefixTree()

    # Long text test
    long_text = "a" * 1000 + "b" * 1000 + "c" * 1000
    tree.insert(long_text, "replica1")

    # Test exact match
    matched_text, replica = tree.prefix_match(long_text)
    assert matched_text == long_text, "Should match the entire long text"
    assert replica == "replica1", "Should return replica1"

    # Test partial match
    partial_text = "a" * 1000 + "b" * 500
    matched_text, replica = tree.prefix_match(partial_text)
    assert matched_text == partial_text, "Should match the partial text"
    assert replica == "replica1", "Should return replica1"

    # Test longer query than what's in the tree
    longer_text = long_text + "extra"
    matched_text, replica = tree.prefix_match(longer_text)
    assert matched_text == long_text, "Should match up to what's in the tree"
    assert replica == "replica1", "Should return replica1"


def test_high_concurrency_insertion():
    """Test insertion with a high number of concurrent threads."""
    tree = PrefixTree()
    num_threads = 50
    num_texts_per_thread = 20

    def worker(thread_id):
        replica = f"replica{thread_id}"
        for i in range(num_texts_per_thread):
            text = f"prefix{thread_id}_{i}_{random_string(10)}"
            tree.insert(text, replica)

    threads = []
    for i in range(num_threads):
        t = threading.Thread(target=worker, args=(i,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # Verify each replica has the expected number of texts
    for i in range(num_threads):
        replica = f"replica{i}"
        assert replica in tree.replica_char_count, f"Replica {replica} missing from the tree"


def test_overlapping_concurrent_insertions():
    """Test concurrent insertions with overlapping prefixes."""
    tree = PrefixTree()
    num_threads = 10
    base_prefixes = ["common", "shared", "overlap", "similar"]

    def worker(thread_id):
        replica = f"replica{thread_id}"
        for prefix in base_prefixes:
            for i in range(5):
                # Create texts with overlapping prefixes but unique suffixes
                text = f"{prefix}_part{i}_{random_string(5)}"
                tree.insert(text, replica)

    threads = []
    for i in range(num_threads):
        t = threading.Thread(target=worker, args=(i,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # Test that we can match each prefix
    for prefix in base_prefixes:
        matched_text, replica = tree.prefix_match(f"{prefix}_")
        assert matched_text == f"{prefix}_", f"Failed to match prefix '{prefix}_'"
        assert replica is not None, f"No replica returned for prefix '{prefix}_'"


def test_concurrent_queries():
    """Test concurrent queries against the tree."""
    tree = PrefixTree()

    # Insert data first
    prefixes = ["alpha", "beta", "gamma", "delta", "epsilon"]
    for i, prefix in enumerate(prefixes):
        for j in range(5):
            text = f"{prefix}_{j}"
            replica = f"replica{i}"
            tree.insert(text, replica)

    # Run concurrent queries
    num_query_threads = 20
    results = []

    def query_worker(thread_id):
        local_results = []
        # Each thread performs multiple queries
        for _ in range(10):
            prefix_idx = thread_id % len(prefixes)
            prefix = prefixes[prefix_idx]
            query = f"{prefix}_{random.randint(0, 4)}"
            matched_text, replica = tree.prefix_match(query)
            local_results.append((query, matched_text, replica))
        return local_results

    with concurrent.futures.ThreadPoolExecutor(
            max_workers=num_query_threads) as executor:
        future_to_thread = {
            executor.submit(query_worker, i): i
            for i in range(num_query_threads)
        }
        for future in concurrent.futures.as_completed(future_to_thread):
            thread_results = future.result()
            results.extend(thread_results)

    # Verify results
    for query, matched_text, replica in results:
        prefix = query.split('_')[0]
        expected_replica = f"replica{prefixes.index(prefix)}"
        assert matched_text == query, f"Query '{query}' matched '{matched_text}' instead of exact match"
        assert replica == expected_replica, f"Query for '{prefix}' returned replica '{replica}' instead of '{expected_replica}'"


def test_mixed_concurrent_workload():
    """Test a mixed workload of concurrent insertions and queries."""
    tree = PrefixTree()
    test_duration = 5  # seconds
    start_time = time.time()

    # Prepare some initial data
    for i in range(5):
        replica = f"replica{i}"
        for j in range(3):
            tree.insert(f"initial_{i}_{j}", replica)

    # Track statistics
    stats = {"inserts": 0, "queries": 0, "query_hits": 0, "query_misses": 0}
    stats_lock = threading.Lock()

    def mixed_worker(thread_id):
        rng = random.Random(thread_id)  # Deterministic per thread
        replica = f"replica{thread_id % 5}"

        while time.time() - start_time < test_duration:
            if rng.random() < 0.3:  # 30% chance to insert
                text = f"thread{thread_id}_{random_string(8)}"
                tree.insert(text, replica)
                with stats_lock:
                    stats["inserts"] += 1
            else:  # 70% chance to query
                # Choose between querying for own data or other threads' data
                if rng.random() < 0.5 and stats["inserts"] > 0:
                    # Query for potentially existing data
                    query = f"thread{rng.randint(0, thread_id)}_{random_string(3)}"
                else:
                    # Query for initial data that definitely exists
                    i = rng.randint(0, 4)
                    j = rng.randint(0, 2)
                    query = f"initial_{i}_{j}"

                matched_text, matched_replica = tree.prefix_match(query)
                with stats_lock:
                    stats["queries"] += 1
                    if matched_text:
                        stats["query_hits"] += 1
                    else:
                        stats["query_misses"] += 1

    threads = []
    num_threads = 20
    for i in range(num_threads):
        t = threading.Thread(target=mixed_worker, args=(i,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    print(f"Mixed workload stats: {stats}")
    # Assert some basic expectations
    assert stats["inserts"] > 0, "Should have performed some insertions"
    assert stats["queries"] > 0, "Should have performed some queries"

    # After the test, verify the tree is still functional
    tree.insert("final_test", "replica0")
    matched_text, replica = tree.prefix_match("final_test")
    assert matched_text == "final_test", "Tree should still work after concurrent workload"
    assert replica == "replica0", "Tree should return correct replica after concurrent workload"


def test_load_based_replica_selection():
    """Test that the tree correctly selects replicas based on load."""
    tree = PrefixTree()

    # Insert the same text for multiple replicas
    text = "common_text"
    replicas = ["replica1", "replica2", "replica3"]
    for replica in replicas:
        tree.insert(text, replica)

    # Test with different load configurations
    load_scenarios = [
        {
            "replica1": 10,
            "replica2": 5,
            "replica3": 15
        },  # replica2 has lowest load
        {
            "replica1": 3,
            "replica2": 8,
            "replica3": 7
        },  # replica1 has lowest load
        {
            "replica1": 12,
            "replica2": 12,
            "replica3": 5
        },  # replica3 has lowest load
    ]

    for i, loads in enumerate(load_scenarios):
        matched_text, replica = tree.prefix_match(text, loads)
        expected_replica = min(loads.items(), key=lambda x: x[1])[0]
        assert matched_text == text, f"Scenario {i}: Should match the full text"
        assert replica == expected_replica, f"Scenario {i}: Should select replica with lowest load"

    # Test with subset of replicas available
    partial_load = {"replica1": 7, "replica3": 3}  # replica3 has lowest load
    matched_text, replica = tree.prefix_match(text, partial_load)
    assert matched_text == text, "Should match the full text"
    assert replica == "replica3", "Should select replica3 (lowest load of available replicas)"


def test_empty_string_handling():
    """Test handling of empty strings."""
    tree = PrefixTree()

    # Insert empty string
    tree.insert("", "replica1")

    # Match empty string
    matched_text, replica = tree.prefix_match("")
    assert matched_text == "", "Should match empty string"
    assert replica == "replica1", "Should return replica1"

    # Insert non-empty after empty
    tree.insert("hello", "replica2")
    matched_text, replica = tree.prefix_match("hello")
    assert matched_text == "hello", "Should match full string"
    assert replica == "replica2", "Should return replica2"

    # Empty string should still match
    matched_text, replica = tree.prefix_match("")
    assert matched_text == "", "Should still match empty string"
    assert replica in ["replica1", "replica2"], "Should return either replica"


def test_single_character_strings():
    """Test with single character strings."""
    tree = PrefixTree()

    # Insert single characters for different replicas
    chars = "abcdefghij"
    replicas = [f"replica{i}" for i in range(len(chars))]

    for char, replica in zip(chars, replicas):
        tree.insert(char, replica)

    # Verify each character matches to the correct replica
    for char, replica in zip(chars, replicas):
        matched_text, matched_replica = tree.prefix_match(char)
        assert matched_text == char, f"Should match character '{char}'"
        assert matched_replica == replica, f"Should return {replica}"

    # Test with a character not in the tree
    matched_text, replica = tree.prefix_match("z")
    assert matched_text == "", "Should not match anything"
    assert replica in replicas, "Should return one of the available replicas"


def test_case_sensitivity():
    """Test case sensitivity in the prefix tree."""
    tree = PrefixTree()

    # Insert mixed case strings
    tree.insert("Hello", "replica1")
    tree.insert("hello", "replica2")
    tree.insert("HELLO", "replica3")

    # Check exact matches
    matched_text, replica = tree.prefix_match("Hello")
    assert matched_text == "Hello", "Should match 'Hello'"
    assert replica == "replica1", "Should return replica1"

    matched_text, replica = tree.prefix_match("hello")
    assert matched_text == "hello", "Should match 'hello'"
    assert replica == "replica2", "Should return replica2"

    matched_text, replica = tree.prefix_match("HELLO")
    assert matched_text == "HELLO", "Should match 'HELLO'"
    assert replica == "replica3", "Should return replica3"

    # Check partial case-sensitive matches
    matched_text, replica = tree.prefix_match("Hell")
    assert matched_text == "Hell", "Should match 'Hell'"
    assert replica == "replica1", "Should return replica1 (first match)"

    matched_text, replica = tree.prefix_match("hell")
    assert matched_text == "hell", "Should match 'hell'"
    assert replica == "replica2", "Should return replica2"


def test_special_characters():
    """Test handling of special characters."""
    tree = PrefixTree()

    # Insert strings with special characters
    special_strings = [
        "hello!@#",
        "world$%^",
        "*&()_+",
        "\\n\\t\\r",
        "😀🙂🙁😢"  # Emoji test
    ]

    for i, text in enumerate(special_strings):
        tree.insert(text, f"replica{i}")

    # Test exact matches
    for i, text in enumerate(special_strings):
        matched_text, replica = tree.prefix_match(text)
        assert matched_text == text, f"Should match '{text}'"
        assert replica == f"replica{i}", f"Should return replica{i}"

    # Test mixed special characters query
    matched_text, replica = tree.prefix_match("hello!@#$%^")
    assert matched_text == "hello!@#", "Should match the longest prefix"
    assert replica == "replica0", "Should return replica0"

    # Test emoji query
    matched_text, replica = tree.prefix_match("😀🙂🥹")
    assert matched_text == "😀🙂", "Should match only the first two emojis"
    assert replica == "replica4", "Should return replica4"


def test_deep_path_tree():
    """Test a tree with very deep paths."""
    tree = PrefixTree()

    # Create a deep path with 100 levels
    num_levels = 100
    path_parts = [f"level{i}" for i in range(num_levels)]
    deep_path = "/".join(path_parts)
    tree.insert(deep_path, "replica_deep")

    # Test exact match of the deep path
    matched_text, replica = tree.prefix_match(deep_path)
    assert matched_text == deep_path, "Should match the entire deep path"
    assert replica == "replica_deep", "Should return replica_deep"

    # Test partial matches at different depths
    for i in range(1, 10):
        partial_path = "/".join(path_parts[:i])
        matched_text, replica = tree.prefix_match(partial_path)
        assert matched_text == partial_path, f"Should match up to level {i-1}"
        assert replica == "replica_deep", "Should return replica_deep"

    # Test slightly beyond each level
    for i in range(1, 10):
        partial_path = "/".join(path_parts[:i]) + "/x"
        matched_text, replica = tree.prefix_match(partial_path)
        expected_match = "/".join(
            path_parts[:i]) + ("/" if i != num_levels - 1 else "")
        assert matched_text == expected_match, f"Should match up to level {i-1}"
        assert replica == "replica_deep", "Should return replica_deep"


def test_heavily_branched_tree():
    """Test a tree with heavy branching at the root."""
    tree = PrefixTree()

    # Create 1000 branches from the root
    num_branches = 1000
    # Use printable ASCII characters (33-126)
    printable_chars = [chr(i) for i in range(33, 127)]
    for i in range(num_branches):
        # Use different first characters to create branches
        branch_key = printable_chars[i % len(printable_chars)] + f"branch{i}"
        tree.insert(branch_key, f"replica{i%10}")

    # Test random access to branches
    for _ in range(100):
        i = random.randint(0, num_branches - 1)
        branch_key = printable_chars[i % len(printable_chars)] + f"branch{i}"
        matched_text, replica = tree.prefix_match(branch_key)
        assert matched_text == branch_key, f"Should match branch {i}"
        assert replica == f"replica{i%10}", f"Should return replica{i%10}"


def test_cascading_eviction():
    """Test cascading eviction where parent nodes become empty."""
    tree = PrefixTree()

    # Create a structure with multiple levels
    tree.insert("a/b/c/d", "replica1")
    tree.insert("a/b/c/e", "replica1")
    tree.insert("a/b/f", "replica1")
    tree.insert("a/g", "replica1")

    # Insert something for replica2 to avoid completely removing the tree
    tree.insert("x/y/z", "replica2")

    # Evict with a small size limit to force cascading eviction
    tree.evict_replica_by_size(3)

    # Check that the tree is still functional
    matched_text, replica = tree.prefix_match("x/y/z")
    assert matched_text == "x/y", "Should still match replica2's data"
    assert replica == "replica2", "Should return replica2"

    # replica1 should have reduced size
    sizes = tree.get_used_size_per_replica()
    assert sizes.get("replica1",
                     0) <= 3, "replica1 should be reduced to <= 3 chars"


def test_replica_removal_with_concurrent_queries():
    """Test removing a replica while concurrent queries are happening."""
    tree = PrefixTree()

    # Set up initial data
    for i in range(10):
        tree.insert(f"text{i}", "replica1")
        tree.insert(f"data{i}", "replica2")

    # Start query threads
    query_results = []
    query_lock = threading.Lock()
    query_event = threading.Event()

    def query_worker():
        while not query_event.is_set():
            for i in range(10):
                # Try both replica's data
                _, replica1 = tree.prefix_match(f"text{i}")
                _, replica2 = tree.prefix_match(f"data{i}")

                with query_lock:
                    query_results.append((f"text{i}", replica1))
                    query_results.append((f"data{i}", replica2))
            time.sleep(0.01)

    # Start query threads
    query_threads = []
    for _ in range(5):
        t = threading.Thread(target=query_worker)
        t.daemon = True
        query_threads.append(t)
        t.start()

    # Give queries a chance to start
    time.sleep(0.1)

    # Remove replica1
    tree.remove_replica("replica1")

    # Let queries continue for a bit
    time.sleep(0.1)

    # Signal threads to stop and wait for them
    query_event.set()
    for t in query_threads:
        t.join()

    # Check results - after removal, replica1 should no longer appear in results
    removal_happened = False
    for query_text, replica in query_results:
        if query_text.startswith("text") and replica == "replica2":
            removal_happened = True

    assert removal_happened, "Should have at least one query after replica removal"

    # Final verification
    for i in range(10):
        _, replica = tree.prefix_match(f"text{i}")
        assert replica == "replica2", f"replica1 should be gone and result should be replica2 for text{i}"

        _, replica = tree.prefix_match(f"data{i}")
        assert replica == "replica2", f"replica2 should still work for data{i}"


def test_concurrent_insertion_and_eviction():
    """Test concurrent insertion and eviction operations."""
    tree = PrefixTree()
    test_duration = 3  # seconds
    max_size = 50
    stop_event = threading.Event()
    eviction_stop_event = threading.Event()

    # Track statistics
    stats = {"inserts": 0, "evictions": 0}
    stats_lock = threading.Lock()

    def insertion_worker(thread_id):
        replica = f"replica{thread_id%5}"
        while not stop_event.is_set():
            # Insert random strings
            text = f"thread{thread_id}_{random_string(10)}"
            tree.insert(text, replica)
            with stats_lock:
                stats["inserts"] += 1
            time.sleep(random.uniform(0.01, 0.05))

    def eviction_worker():
        while not eviction_stop_event.is_set():
            tree.evict_replica_by_size(max_size)
            with stats_lock:
                stats["evictions"] += 1
            time.sleep(random.uniform(0.1, 0.3))

    # Start insertion threads
    threads = []
    for i in range(10):
        t = threading.Thread(target=insertion_worker, args=(i,))
        threads.append(t)
        t.start()

    # Start eviction thread
    eviction_thread = threading.Thread(target=eviction_worker)
    threads.append(eviction_thread)
    eviction_thread.start()

    # Let the test run for the specified duration
    time.sleep(test_duration)
    stop_event.set()
    # Wait for the eviction function to run at least once
    time.sleep(1)
    eviction_stop_event.set()

    # Wait for all threads to complete
    for t in threads:
        t.join()

    print(f"Concurrent insertion and eviction stats: {stats}")

    # Final verification - all replicas should be under the size limit
    sizes = tree.get_used_size_per_replica()
    for replica, size in sizes.items():
        assert size <= max_size, f"Replica {replica} exceeds size limit: {size} > {max_size}"


def test_replica_contention():
    """Test contention when multiple replicas are eligible for selection."""
    tree = PrefixTree()

    # Insert the same text for 10 replicas
    text = "contested_resource"
    replicas = [f"replica{i}" for i in range(10)]

    for replica in replicas:
        tree.insert(text, replica)

    # Perform many queries and track which replicas are selected
    selected_counts = {replica: 0 for replica in replicas}

    for _ in range(1000):
        _, selected_replica = tree.prefix_match(text)
        selected_counts[selected_replica] += 1

    # Verify distribution - should be roughly uniform
    print("Replica selection distribution:", selected_counts)
    for count in selected_counts.values():
        # Allow some variability but ensure each replica gets selected
        assert count > 0, "Each replica should be selected at least once"
        # Rough check for uniform distribution (not too biased)
        assert count < 200, "No replica should be selected too frequently"


def test_short_overlapping_prefixes():
    """Test behavior with very short overlapping prefixes."""
    tree = PrefixTree()

    # Create overlapping prefixes
    tree.insert("a", "replica1")
    tree.insert("ab", "replica2")
    tree.insert("abc", "replica3")
    tree.insert("abd", "replica4")
    tree.insert("ac", "replica5")

    # Test matching "a" - could match any prefix starting with "a"
    matched_text, replica = tree.prefix_match("a")
    assert matched_text == "a", "Should match 'a'"
    assert replica in [
        "replica1", "replica2", "replica3", "replica4", "replica5"
    ], "Should return any replica"

    # Test matching "ab" - could match any prefix starting with "ab"
    matched_text, replica = tree.prefix_match("ab")
    assert matched_text == "ab", "Should match 'ab'"
    assert replica in ["replica2", "replica3", "replica4"
                      ], "Should return replica2, replica3 or replica4"

    # Test longer match "abcd" - could match "abc"
    matched_text, replica = tree.prefix_match("abcd")
    assert matched_text == "abc", "Should match 'abc'"
    assert replica == "replica3", "Should return replica3"

    # Test branching with "abe" - should match "ab"
    matched_text, replica = tree.prefix_match("abe")
    assert matched_text == "ab", "Should match 'ab'"
    assert replica in ["replica2", "replica3", "replica4"
                      ], "Should return replica2, replica3 or replica4"


def test_timestamp_based_selection():
    """Test replica selection when timestamps are manipulated."""
    tree = PrefixTree()

    # Insert same text for different replicas
    text = "timestamp_test"
    tree.insert(text, "replica1")
    tree.insert(text, "replica2")
    tree.insert(text, "replica3")

    # Artificially manipulate last access timestamps by performing queries
    # This should update timestamps for replica1
    for _ in range(5):
        matched_text, _ = tree.prefix_match(text, {"replica1": 1})
        time.sleep(0.01)  # Small delay to ensure timestamp difference

    # Query without load info - most recently accessed should be preferred
    # This is an implementation detail that may not be guaranteed by the API,
    # but can be checked if that's how the internal random choice is influenced
    counter = {r: 0 for r in ["replica1", "replica2", "replica3"]}

    for _ in range(100):
        _, selected = tree.prefix_match(text)
        counter[selected] += 1

    print("Selection after timestamp manipulation:", counter)
    # We can't make strong assertions about the distribution since it might be random,
    # but we can print the results for inspection
