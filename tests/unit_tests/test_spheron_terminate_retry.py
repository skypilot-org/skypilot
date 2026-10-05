"""Isolated test of _terminate_when_permitted (no sky import needed)."""
import re, types, time as _t

src = open("sky/provision/spheron/instance.py").read()
start = src.index("_TERMINATE_WINDOW_CEILING_S")
end = src.index("def terminate_instances(")
block = src[start:end]

class SpheronError(Exception): pass
api = types.SimpleNamespace(SpheronError=SpheronError)
logger = types.SimpleNamespace(info=lambda *a, **k: None)
slept = []
time_stub = types.SimpleNamespace(monotonic=_t.monotonic, sleep=lambda s: slept.append(s))
ns = {"api": api, "logger": logger, "time": time_stub, "Any": object}
exec(compile(block, "<patch>", "exec"), ns)
fn = ns["_terminate_when_permitted"]

class FakeClient:
    def __init__(self, remaining):
        self.remaining = list(remaining); self.terminated = []; self.checks = 0
    def can_terminate(self, did):
        self.checks += 1
        r = self.remaining.pop(0) if self.remaining else 0
        return {"canTerminate": r == 0, "reason": "Minimum runtime not met",
                "minimumRuntime": 20, "timeRemaining": r}
    def terminate_deployment(self, did):
        self.terminated.append(did); return {"message": "ok"}

# 1. the LIVE sequence observed 2026-09-22: refused 3/2/1min, ok at 0
slept.clear()
c = FakeClient([3, 2, 1, 0]); fn(c, "dep-1")
assert c.terminated == ["dep-1"], c.terminated
assert len(slept) == 3, slept
assert slept[0] > 120 and slept[1] > 60, slept
print("  PASS waits out the window then terminates; sleeps=%s" % [int(x) for x in slept])

# 2. already permitted -> one check, immediate
slept.clear()
c = FakeClient([0]); fn(c, "dep-2")
assert c.terminated == ["dep-2"] and c.checks == 1
print("  PASS immediate when already permitted")

# 3. never returns success while the instance is live
ns["_TERMINATE_WINDOW_CEILING_S"] = 0.0
exec(compile(block.replace("_TERMINATE_WINDOW_CEILING_S = 25 * 60",
                           "_TERMINATE_WINDOW_CEILING_S = 0.0"), "<patch2>", "exec"), ns)
fn2 = ns["_terminate_when_permitted"]
slept.clear()
c = FakeClient([5,5,5,5])
try:
    fn2(c, "dep-3"); raise AssertionError("returned success while still live")
except SpheronError as e:
    assert "leak a BILLING instance" in str(e), str(e)
    assert c.terminated == []
    print("  PASS raises rather than leaking when the window never opens")
print("ALL PASS")
