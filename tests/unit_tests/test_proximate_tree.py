import random
import string
import threading
import time

from sky.serve.proximate_tree import ProximateTree  # adjust import as needed


def random_string(length: int) -> str:
    return ''.join(
        random.choices(string.ascii_letters + string.digits, k=length))


def test_get_smallest_replica():
    tree = ProximateTree()
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

    # Increase replica4’s count by inserting additional text.
    tree.insert("hello", "replica4")  # now replica4: 2+5 = 7
    smallest = tree.get_smallest_replica()
    assert smallest == "replica3", "Expected replica3 to be smallest with 2 characters"

    # Test eviction: remove nodes so that usage above max_size (3) is evicted.
    tree.evict_tenant_by_size(3)
    smallest_after = tree.get_smallest_replica()
    print("Smallest replica after eviction:", smallest_after)


def test_replica_char_count():
    tree = ProximateTree()

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
    tree.evict_tenant_by_size(10)
    computed_sizes = tree.get_used_size_per_replica()
    maintained_counts = dict(tree.replica_char_count)
    print("Phase 4 - Maintained vs Computed counts:")
    print("Maintained:", maintained_counts, "\nComputed:", computed_sizes)
    assert maintained_counts == computed_sizes, "Phase 4: After eviction"


def test_cold_start():
    tree = ProximateTree()
    matched_text, replica = tree.prefix_match("hello")
    assert matched_text == ""
    assert replica is None


def test_exact_match_seq():
    tree = ProximateTree()
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
    tree = ProximateTree()
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
    tree = ProximateTree()
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
    tree = ProximateTree()
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
    tree = ProximateTree()
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
    tree = ProximateTree()
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
    tree = ProximateTree()
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
    tree = ProximateTree()
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


def assert_replica_in_prefix(tree: ProximateTree, text: str, replica: str):
    matched_text, replica_result = tree.prefix_match(text)
    assert replica_result == replica


def test_simple_eviction():
    tree = ProximateTree()
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
    tree.evict_tenant_by_size(max_size)
    tree.pretty_print()
    sizes_after = tree.get_used_size_per_replica()
    assert sizes_after.get("replica1") == 5
    assert sizes_after.get("replica2") == 5

    matched_text, rep_list = tree.prefix_match("world")
    assert matched_text == "world"
    assert "replica2" in rep_list


def test_advanced_eviction():
    tree = ProximateTree()
    max_size = 100
    prefixes = ["aqwefcisdf", "iajsdfkmade", "kjnzxcvewqe", "iejksduqasd"]

    for _ in range(100):
        for j, prefix in enumerate(prefixes):
            rand_suffix = random_string(10)
            text = f"{prefix}{rand_suffix}"
            replica = f"replica{j+1}"
            tree.insert(text, replica)

    tree.evict_tenant_by_size(max_size)
    sizes_after = tree.get_used_size_per_replica()
    for replica, size in sizes_after.items():
        assert size <= max_size, f"Replica {replica} exceeds size limit: {size} > {max_size}"


def test_concurrent_operations_with_eviction():
    tree = ProximateTree()
    test_duration = 10  # seconds
    start_time = time.time()
    max_size = 100
    threads = []

    def eviction_thread():
        while time.time() - start_time < test_duration:
            tree.evict_tenant_by_size(max_size)
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

    tree.evict_tenant_by_size(max_size)
    final_sizes = tree.get_used_size_per_replica()
    print("Final sizes after test completion:", final_sizes)
    for size in final_sizes.values():
        assert size <= max_size, f"Replica exceeds size limit: {size} > {max_size}"


def test_leaf_of():
    tree = ProximateTree()
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
    tree = ProximateTree()
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
    tree = ProximateTree()
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
    tree = ProximateTree()
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
    # Check that replica2’s data is still accessible.
    matched_text, _ = tree.prefix_match("apple")
    assert matched_text == "apple"
    matched_text, _ = tree.prefix_match("appetite")
    assert matched_text == "appetite"
    matched_text, _ = tree.prefix_match("banana")
    assert matched_text == "banana"
    matched_text, _ = tree.prefix_match("ball")
    assert matched_text == "ball"
