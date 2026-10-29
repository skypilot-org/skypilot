"""Self-cleanup: terminate a run's sandboxes, then delete its pools.

Called from the trainer's end-of-run trap so each run OWNS and tears down its own
pool(s) — no leaks, no cross-run sharing. Scoped strictly to the pools this run
created (read from the pools-json emitted by dataset_swesmith). Best-effort:
never raises, so it can't fail the trap.

Order matters: delete_pool 500s while any sandbox references the pool (verified),
so we terminate the pool's sandboxes FIRST, then delete the pool.

    python code/pool_cleanup.py <pools-json>          # e.g. ~/sky_workdir/swesmith-pools.json
    python code/pool_cleanup.py --pools poolA poolB   # explicit names
"""
import json
import sys


def _load_pool_names(argv) -> list[str]:
    if len(argv) >= 2 and argv[1] == "--pools":
        return argv[2:]
    if len(argv) >= 2:
        try:
            with open(argv[1]) as f:
                return [p["pool"] for p in json.load(f)]
        except Exception as e:
            print(f"[pool-cleanup] could not read {argv[1]}: {e}")
            return []
    return []


def main() -> None:
    names = set(_load_pool_names(sys.argv))
    if not names:
        print("[pool-cleanup] no pools to clean")
        return
    try:
        try:
            import sky.sandbox as s
        except ImportError:
            import sky_sandbox as s
    except Exception as e:
        print(f"[pool-cleanup] sandbox SDK unavailable: {e}")
        return

    # 1) terminate every sandbox belonging to our pools (any status).
    try:
        boxes = s.ls()
    except Exception as e:
        print(f"[pool-cleanup] ls() failed: {e}")
        boxes = []
    ok = 0
    for b in boxes:
        rec = getattr(b, "_record", {}) if not isinstance(b, dict) else b
        if rec.get("template_name") in names:
            try:
                b.terminate()
                ok += 1
            except Exception:
                pass
    print(
        f"[pool-cleanup] terminated {ok} sandboxes across {len(names)} pool(s)")

    # 2) delete the pools (now unblocked).
    for name in names:
        try:
            s.delete_pool(name)
            print(f"[pool-cleanup] deleted pool: {name}")
        except Exception as e:
            print(
                f"[pool-cleanup] delete {name} failed (may retry manually): {str(e)[:80]}"
            )


if __name__ == "__main__":
    main()
