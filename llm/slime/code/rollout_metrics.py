"""Custom rollout-logging hook: pass@k + sandbox-timing aggregates.

Wired via ``--custom-rollout-log-function-path rollout_metrics.log_rollout_metrics``.
slime calls this from ``_log_rollout_data`` (slime/ray/rollout.py:1291-1295)
with the FLAT list of every sample in the step (already flattened at
rollout.py:662-663). Returning a falsy value lets slime's own default logging
(rollout/*, perf/*) still run afterward -- we only ADD metrics, never replace.

Two things generate() can't compute alone (it sees one rollout at a time):

  * pass@k -- needs the whole n_samples group. Group identity is
    ``Sample.group_index``, set by the data source (slime/rollout/data_source.py:112)
    and preserved through deepcopy + our finish_session (trajectory.py:238
    copies group_index onto every emitted Sample). pass@1 = group mean reward,
    pass@k = 1 if any rollout in the group solved.
  * timing aggregates -- generate() stamps per-rollout timings on
    metadata["agent_timing"]; we dedup by session_id (a fork emits >1 sample
    with identical timing) and reduce to mean/p50/max.

Reward-splitting note: finish_session splits a rollout's reward evenly across
its fan-out samples (trajectory.py:323-326), so we FIRST sum reward back per
session_id to recover the rollout reward, then group sessions by group_index.
That is correct at single-turn (1 sample/session) and stays correct once
multi-turn forking appears.
"""

from __future__ import annotations

from collections import defaultdict
import logging

logger = logging.getLogger(__name__)


def _pctl(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[i]


def _reduce(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    return {
        "mean": sum(values) / len(values),
        "p50": _pctl(values, 0.5),
        "p95": _pctl(values, 0.95),
        "p99": _pctl(values, 0.99),
        "max": max(values),
    }


def compute_metrics(samples) -> dict[str, float]:
    """Pure function (unit-testable): flat sample list -> metric dict."""
    # --- per-session reduction (one authoritative reward + timing per rollout) -
    # Read the binary rollout reward generate() stamped on metadata["agent_reward"]
    # (set once per rollout). We must NOT sum s.reward: at multi-turn a rollout
    # fans out into per-turn samples and slime re-weights their per-sample reward,
    # so summing inflates pass@1 by ~turns (observed 1.56 vs a true 0.83). Fall
    # back to summing s.reward only for older data without the stamp.
    session_reward: dict[str, float] = {}
    session_reward_fallback: dict[str, float] = defaultdict(float)
    session_group: dict[str, int] = {}
    session_timing: dict[str, dict] = {}
    for s in samples:
        sid = s.session_id or f"idx-{s.index}"
        session_group[sid] = s.group_index
        md = s.metadata or {}
        if "agent_reward" in md:
            session_reward[sid] = float(md["agent_reward"])
        else:
            session_reward_fallback[sid] += float(s.reward or 0.0)
        if "agent_timing" in md and sid not in session_timing:
            session_timing[sid] = md["agent_timing"]
    for sid, r in session_reward_fallback.items():
        session_reward.setdefault(sid, r)

    # --- pass@k over groups of rollouts ---
    group_rewards: dict[int, list[float]] = defaultdict(list)
    for sid, r in session_reward.items():
        group_rewards[session_group.get(sid)].append(r)
    pass_at_1 = [sum(rs) / len(rs) for rs in group_rewards.values() if rs
                ]  # per-group mean
    pass_at_k = [
        1.0 if any(r >= 1.0
                   for r in rs) else 0.0
        for rs in group_rewards.values()
        if rs
    ]

    metrics: dict[str, float] = {}
    if pass_at_1:
        metrics["pass@1"] = sum(pass_at_1) / len(pass_at_1)
    if pass_at_k:
        metrics["pass@k"] = sum(pass_at_k) / len(pass_at_k)
        metrics["num_groups"] = float(len(pass_at_k))

    # --- multi-turn shape (turns) + packed-vs-forked ratio (masked_frac) ------
    # turns = metadata["n_turns"] (= agent.n_calls): >1 confirms the agent iterates.
    # masked_frac is NOT a masking-correctness check (that is guaranteed by
    # slime's TrajectoryManager: observations never enter a trained response
    # region -- proven both ways in test_masking.py). It is a packed-vs-forked
    # SIGNAL: a linear chain packs into one sample only while each turn extends
    # the prior as an exact token-prefix; mini-swe's chat-template re-tokenization
    # drifts at turn boundaries, so turns FORK into per-turn samples whose response
    # is one turn (all trainable -> masked_frac 0) with observations in the
    # stripped leading prompt. masked_frac > 0 therefore means turns PACKED (obs
    # as loss_mask=0 spans); ~0 means they forked. Both are correct; the number
    # just tells us which path slime took. Skip aborted samples (loss_mask=[0]).
    turns: list[float] = []
    masked_fracs: list[float] = []
    for s in samples:
        if getattr(s, "remove_sample", False):
            continue
        md = s.metadata or {}
        if "n_turns" in md:
            turns.append(float(md["n_turns"]))
        lm = getattr(s, "loss_mask", None)
        if lm:  # fraction of response tokens that are masked (observations)
            masked_fracs.append(1.0 - (sum(lm) / len(lm)))
    if turns:
        metrics["turns/mean"] = sum(turns) / len(turns)
        metrics["turns/max"] = max(turns)
    if masked_fracs:  # >0 confirms multi-turn observation masking is engaged
        metrics["masked_frac/mean"] = sum(masked_fracs) / len(masked_fracs)
        metrics["masked_frac/max"] = max(masked_fracs)

    # --- sandbox timing aggregates (deduped per session) ---
    # claim_sec = sandbox CREATE time, one per rollout -> percentiles are per-rollout.
    # (p50/p95/p99 for all; the create-time distribution is the warm-pool/cold-create
    #  signal we want when tuning image caching on R2E-like per-repo images.)
    for key in ("claim_sec", "agent_sec", "eval_sec", "exec_sec"):
        vals = [float(t[key]) for t in session_timing.values() if key in t]
        for stat, v in _reduce(vals).items():
            metrics[f"sandbox_{key}/{stat}"] = v
    # exec_call = per-CALL exec wall-time, flattened across every action in the step
    # -> per-call percentiles (distinct from exec_sec which is per-rollout total).
    exec_calls = [
        d for t in session_timing.values() for d in (t.get("exec_calls") or [])
    ]
    for stat, v in _reduce([float(d) for d in exec_calls]).items():
        metrics[f"sandbox_exec_call/{stat}"] = v
    metrics["sandbox_exec_call/count"] = float(len(exec_calls))
    exec_counts = [
        float(t["exec_count"])
        for t in session_timing.values()
        if "exec_count" in t
    ]
    if exec_counts:
        metrics["sandbox_exec_count/mean"] = sum(exec_counts) / len(exec_counts)
    metrics["num_rollouts"] = float(len(session_reward))
    return metrics


def log_rollout_metrics(rollout_id, args, samples, rollout_extra_metrics,
                        rollout_time) -> bool:
    """slime custom-rollout-log hook. Returns False so slime's default logging
    still runs (we only add agent/* keys)."""
    try:
        metrics = compute_metrics(samples)
    except Exception as e:  # never break a training step over a metric
        logger.warning("[metrics] compute failed: %s", e)
        return False

    # Greppable stdout line (works even when wandb is disabled).
    logger.info("[metrics] step=%s %s", rollout_id, metrics)
    print(f"[metrics] step={rollout_id} " +
          " ".join(f"{k}={v:.4g}" for k, v in metrics.items()),
          flush=True)

    # Emit to wandb via slime's own logger if enabled (same path as the built-in
    # rollout/perf metrics: logging_utils.log -> wandb.log).
    try:
        from slime.utils import logging_utils
        from slime.utils.metric_utils import compute_rollout_step
        from slime.utils.metric_utils import dict_add_prefix

        if getattr(args, "use_wandb", False):
            log_dict = dict_add_prefix(metrics, "agent/")
            log_dict["rollout/step"] = compute_rollout_step(args, rollout_id)
            logging_utils.log(args, log_dict, step_key="rollout/step")
    except Exception as e:
        logger.warning("[metrics] wandb emit skipped: %s", e)

    return False  # let slime log its default rollout/* + perf/* metrics too
