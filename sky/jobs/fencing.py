"""Fencing token for managed-job claim ownership.

A FencingToken carries the claim_id a controller received when it claimed a
job (``state.get_waiting_job_async``). State-write functions accept an
optional token: when one is passed, the write is *fenced* -- restricted to
rows still owned by that claim -- and a verified ownership loss raises
``exceptions.JobOwnershipLostError``.

The token is passed explicitly through the call chain (constructor params
and function args), never via a ContextVar: explicit passing is grep-able
and cannot silently fail to propagate across asyncio-task or thread
boundaries.
"""
import dataclasses


@dataclasses.dataclass
class FencingToken:
    """Identity of one claim on one managed job.

    Every holder along a job's call chain (controller coroutine, strategy
    executor, batch-coordinator worker threads) shares the *same* token
    object, so the ``lost`` flag set at one detection site is visible to all
    of them.
    """
    job_id: int
    claim_id: str
    # Set to True by any detection site at the moment ownership loss is
    # verified (before raising JobOwnershipLostError, or in lieu of raising
    # at write sites with skip semantics). Read by cleanup/finally paths to
    # switch their disposition. A plain bool store is atomic under the GIL,
    # so setting it from a worker thread is safe.
    lost: bool = False
    # How the loss was first detected, for the ownership-lost metric:
    # 'fence' (a fenced state write found 0 rows), 'preaction' (a
    # launch/recovery ownership pre-check), 'tick' (the idle-monitor
    # periodic check), or 'collision' (the controller saw its own job back
    # in WAITING). The detecting site sets this before flagging `lost`;
    # 'fence' is the default for write sites.
    detection: str = 'fence'
