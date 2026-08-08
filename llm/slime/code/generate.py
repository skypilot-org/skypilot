"""Coding-agent RL: per-sample generate() for slime over SkyPilot sandboxes.

    --custom-generate-function-path generate.generate   (code/ on PYTHONPATH)

Adapted from slime/examples/coding_agent_rl/generate.py with one relocation:
the agent runs TRAINER-SIDE (this process), not as a CLI inside the sandbox.
The agent IS upstream mini-swe-agent (DefaultAgent + LitellmTextbasedModel +
the stock mini_textbased.yaml templates); we implement only its two protocol
seams: Environment = sky.sandbox exec (sandbox_env.SandboxEnvironment) and
Model = litellm pointed at slime's OpenAIAdapter on localhost with
api_key=session_id (litellm sends it as Authorization: Bearer, which is
exactly the adapter's session routing). Per sample:

    claim sandbox from warm pool -> stage task files -> DefaultAgent.run in a
    thread (model turns: adapter; actions: sandbox exec) -> stage eval files
    -> run eval_cmd (exit 0 => reward 1.0) -> finish_session drains the
    adapter trajectory into loss-masked Sample(s)

DefaultAgent is synchronous, so each in-flight rollout runs its loop via
asyncio.to_thread (thread-per-inflight-rollout; fine at our batch sizes).
The sandbox executes ONLY untrusted code; token capture and observation
loss-masking live entirely in the adapter/TrajectoryManager. Crashes and
timeouts return the abort-sample shape (mirrors coding_agent_rl's
_abort_result) and the sandbox is always terminated in finally.

Dataset-agnostic sample schema (see _get_metadata; produced by e.g.
dataset_mbpp.py): metadata carries task, files, eval_files, eval_cmd, pool,
workdir, instance_id.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import dataclasses
import logging
import os
import secrets
import time
import traceback
from typing import Any

os.environ.setdefault("MSWEA_SILENT_STARTUP",
                      "1")  # upstream knob: no banner per Ray worker

from minisweagent import package_dir as _miniswe_package_dir
from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.exceptions import Submitted
from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel
import sandbox_env
from slime.agent.adapters import OpenAIAdapter
from slime.agent.aiohttp_threaded import FilteredAccessLogger
from slime.agent.aiohttp_threaded import run_app_in_thread
from slime.utils.misc import SingletonMeta
from slime.utils.processing_utils import load_tokenizer
from slime.utils.types import Sample
import yaml

logger = logging.getLogger(__name__)

# --- rollout thread pool ------------------------------------------------------
# Each rollout runs its whole synchronous agent loop (staging, agent.run, eval)
# via asyncio.to_thread, which dispatches to the event loop's DEFAULT
# ThreadPoolExecutor. That default is min(32, cpu+4) workers -> at a rollout batch
# > 32, only ~32 rollouts run at once no matter how many are launched, so
# concurrent generation pins at ~32 and the inference engine starves (queue empty,
# KV low) regardless of pool size / batch / engine count. Size the pool to the
# batch so every in-flight rollout runs concurrently. Set once, on first generate().
_ROLLOUT_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None


def _ensure_rollout_executor() -> None:
    global _ROLLOUT_EXECUTOR
    if _ROLLOUT_EXECUTOR is not None:
        return

    def _int_env(name, default):  # robust to unset / empty-string / malformed
        try:
            return int(os.environ.get(name) or default)
        except (TypeError, ValueError):
            return default

    batch = _int_env("GLOBAL_BATCH_SIZE",
                     0) or (_int_env("ROLLOUT_BATCH_SIZE", 8) *
                            _int_env("N_SAMPLES_PER_PROMPT", 8))
    max_workers = max(64,
                      batch + 16)  # one thread per in-flight rollout + headroom
    _ROLLOUT_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix="rollout")
    asyncio.get_running_loop().set_default_executor(_ROLLOUT_EXECUTOR)
    logger.info(
        "[agent] rollout thread pool: max_workers=%d (default was min(32,cpu+4)); batch=%d",
        max_workers,
        batch,
    )


# Upstream config, verbatim (system/instance templates, mswea_bash_command
# fence, observation template with 10k elision, format-error template).
# Changing any of it later is a config override, not a fork.
_MINI_CONFIG = yaml.safe_load(
    (_miniswe_package_dir / "config" / "mini_textbased.yaml").read_text())


@dataclasses.dataclass(frozen=True)
class AgentConfig:
    litellm_model_name: str  # "openai/..." = litellm's generic OpenAI-compatible provider
    max_turns: int  # failure-tail ceiling only
    exec_timeout_sec: int
    eval_timeout_sec: int
    rollout_guard_sec: int
    sandbox_lifetime_sec: int  # server-side auto-reap; set well above the rollout guard so a slow tail is never reaped mid-rollout (it's only a leak backstop)
    adapter_port: int  # 0 = ephemeral; localhost-only, so no fixed port needed
    sglang_url: str | None  # override for the adapter's upstream; see _AdapterService
    default_pool: str
    default_workdir: str
    local_exec: bool  # dev-only: upstream LocalEnvironment instead of sandboxes

    @classmethod
    def from_env(cls) -> AgentConfig:
        max_turns = int(os.environ.get("AGENT_MAX_TURNS", "5"))
        exec_timeout = int(os.environ.get("AGENT_EXEC_TIMEOUT_SEC", "60"))
        eval_timeout = int(os.environ.get("AGENT_EVAL_TIMEOUT_SEC", "120"))
        # guard covers worst case: every turn hits the exec timeout, plus eval,
        # plus slack for model latency and sandbox claim.
        guard = int(os.environ.get("AGENT_ROLLOUT_GUARD_SEC", "0") or
                    0) or (max_turns * exec_timeout + eval_timeout + 300)
        return cls(
            litellm_model_name=os.environ.get("AGENT_LITELLM_MODEL",
                                              "openai/slime-actor"),
            max_turns=max_turns,
            exec_timeout_sec=exec_timeout,
            eval_timeout_sec=eval_timeout,
            rollout_guard_sec=guard,
            # Leak backstop only (a crashed trainer's boxes self-destruct after this).
            # Sit it an hour past the worst-case rollout guard so a slow-tail rollout is
            # never reaped mid-flight — but bounded, so a hard trainer crash doesn't leak
            # boxes for hours and starve the next run's pool.
            sandbox_lifetime_sec=int(
                os.environ.get("AGENT_SANDBOX_LIFETIME_SEC", "0") or 0) or
            (guard + 3600),
            adapter_port=int(os.environ.get("ADAPTER_PORT", "0")),
            sglang_url=os.environ.get("AGENT_SGLANG_URL") or None,
            default_pool=os.environ.get("AGENT_POOL", "swesmith"),
            default_workdir=os.environ.get("AGENT_WORKDIR", "/workspace"),
            local_exec=os.environ.get("AGENT_LOCAL_EXEC", "") == "1",
        )


CONFIG = AgentConfig.from_env()


class _AdapterService(metaclass=SingletonMeta):
    """One OpenAIAdapter per rollout-actor process, bound to localhost.

    Unlike coding_agent_rl's service, no ADAPTER_PUBLIC_HOST: the only client
    is the in-process litellm model, so 127.0.0.1 with an ephemeral port
    suffices (and nothing outside the pod can reach the policy endpoint).
    """

    def __init__(self, args) -> None:
        self.tokenizer = load_tokenizer(args.hf_checkpoint,
                                        trust_remote_code=True)
        self.max_context_len = int(
            getattr(args, "rollout_max_context_len", 0) or 0)
        # args.sglang_router_ip/port DOES resolve under
        # --rollout-external-engine-addrs: slime still starts a trainer-local
        # sglang_router and writes its ip/port back onto args
        # (slime/backends/sglang_utils/external.py:185-187, dispatched from
        # slime/ray/rollout.py:1103-1104 inside RolloutManager.__init__,
        # rollout.py:436 — i.e. before any rollout runs in this process), and
        # the external engine registers itself as a router worker
        # (sglang_engine.py:190-199). slime's own rollout path posts to the
        # same URL (sglang_rollout.py:81,159). AGENT_SGLANG_URL is a debug
        # escape hatch to bypass the router and hit the engine directly
        # (e.g. http://sglang-0.$SKYPILOT_JOBGROUP_NAME:30000); leave unset
        # in normal runs.
        sglang_url = CONFIG.sglang_url or f"http://{args.sglang_router_ip}:{args.sglang_router_port}"
        self.adapter = OpenAIAdapter(
            tokenizer=self.tokenizer,
            sglang_url=sglang_url,
            tool_parser=getattr(args, "sglang_tool_call_parser", None) or None,
            reasoning_parser=getattr(args, "sglang_reasoning_parser", None) or
            None,
        )
        self.app_handle = run_app_in_thread(
            self.adapter.app,
            host="127.0.0.1",
            port=CONFIG.adapter_port,
            thread_name="openai-adapter",
            # client disconnect must cancel the handler so the adapter's
            # fire-and-forget /abort_request frees the sglang slot (see the
            # comment in coding_agent_rl/generate.py:151-154).
            runner_kwargs={
                "handler_cancellation": True,
                "access_log_class": FilteredAccessLogger
            },
        )
        self.adapter_url = f"http://127.0.0.1:{self.app_handle.port}"
        logger.info(
            "[agent] tokenizer=%s adapter=%s sglang_upstream=%s max_context_len=%s",
            args.hf_checkpoint,
            self.adapter_url,
            sglang_url,
            self.max_context_len,
        )


def _make_model(adapter_url: str, session_id: str) -> LitellmTextbasedModel:
    """Upstream litellm text-based model pointed at the adapter.

    api_key=session_id is the whole session-routing story: litellm's OpenAI
    provider sends it as ``Authorization: Bearer <sid>``, which the adapter
    resolves via sid_from_bearer (slime/agent/adapters/common.py:367).
    Sampling params (temperature, max_new_tokens, ...) come from the
    session's defaults registered in open_session, not from litellm kwargs.
    """
    model_cfg = dict(_MINI_CONFIG.get("model") or {})
    model_kwargs = {
        **(model_cfg.pop("model_kwargs", None) or {}),  # upstream: drop_params etc.
        "api_base": f"{adapter_url}/v1",
        "api_key": session_id,
        "timeout": 900,  # generous per-turn read timeout; the rollout guard bounds the total
    }
    return LitellmTextbasedModel(
        model_name=CONFIG.litellm_model_name,
        model_kwargs=model_kwargs,
        # "slime-actor" has no litellm pricing entry; cost is meaningless here.
        cost_tracking="ignore_errors",
        **model_cfg,  # upstream observation_template + format_error_template
    )


def _make_env(sb, workdir: str):
    """Environment seam: sandbox-backed, or upstream LocalEnvironment (dev-only)."""
    env_vars = dict((_MINI_CONFIG.get("environment") or {}).get("env") or
                    {})  # PAGER=cat etc.
    if sb is None:
        return LocalEnvironment(cwd=workdir,
                                env=env_vars,
                                timeout=CONFIG.exec_timeout_sec)
    return sandbox_env.SandboxEnvironment(sb,
                                          cwd=workdir,
                                          env=env_vars,
                                          timeout=CONFIG.exec_timeout_sec)


def _make_agent(model, env) -> DefaultAgent:
    agent_cfg = dict(_MINI_CONFIG.get("agent") or
                     {})  # upstream templates, verbatim
    agent_cfg.pop("mode",
                  None)  # interactive-CLI knob; not an AgentConfig field
    agent_cfg["step_limit"] = CONFIG.max_turns
    agent_cfg[
        "cost_limit"] = 0  # disabled: adapter turns cost $0; step limit + guard cap the run
    return DefaultAgent(model, env, **agent_cfg)


def _get_metadata(sample: Sample) -> dict[str, Any]:
    """Normalize the dataset row schema (see module docstring)."""
    m = sample.metadata or {}
    return {
        "instance_id": str(
            m.get("instance_id") or sample.label or sample.index or "unknown"),
        "pool": m.get("pool") or CONFIG.default_pool,
        "image":
            m.get("image") or
            None,  # per-repo env image: on-demand creates (no-pool mode / pool-claim fallback)
        "workdir": m.get("workdir") or CONFIG.default_workdir,
        "task": m.get("task") or _coerce_prompt(sample.prompt),
        "files": m.get("files") or {},  # staged before the agent loop
        "setup_cmd": m.get(
            "setup_cmd"
        ),  # optional: run post-claim, pre-agent (SWE-smith buggy-state)
        "eval_kind": m.get("eval_kind") or
                     "cmd",  # "cmd" (MBPP eval_cmd) | "swesmith" (host-parse)
        "eval_files": m.get("eval_files")
                      or {},  # staged AFTER the loop (hidden graders)
        "eval_cmd": m.get("eval_cmd"),  # exit 0 == solved (cmd kind)
        "test_cmd": m.get("test_cmd"),  # repo test command (swesmith kind)
        "swesmith_inst":
            m.get("swesmith_inst"),  # F2P/P2P/repo for host-side log_parser
    }


def _coerce_prompt(prompt) -> str:
    # Same fallback as coding_agent_rl/swe.py:_coerce_prompt: plain string or
    # first user message of a conversation.
    if isinstance(prompt, str):
        return prompt
    if isinstance(prompt, list):
        for m in prompt:
            if isinstance(m, dict) and m.get("role") == "user" and isinstance(
                    m.get("content"), str):
                return m["content"]
    return ""


def _stage_files(env, files: dict[str, str], workdir: str) -> None:
    """Sync staging helper (runs inside asyncio.to_thread)."""
    if isinstance(env, sandbox_env.SandboxEnvironment):
        env.write_files(files)
    else:  # upstream LocalEnvironment (AGENT_LOCAL_EXEC)
        sandbox_env.stage_files_locally(workdir, files)


class _TimedEnv:
    """Transparent wrapper that accumulates per-action exec wall-time.

    Wraps EITHER our SandboxEnvironment or upstream LocalEnvironment so total
    sandbox-exec time is captured uniformly (a counter inside SandboxEnvironment
    would miss the local-exec smoke). mini-swe's DefaultAgent only calls
    ``execute`` / ``get_template_vars`` / ``serialize`` and reads ``.config``;
    everything but ``execute`` delegates to the inner env via ``__getattr__``.
    """

    def __init__(self, inner) -> None:
        self.inner = inner
        self.exec_seconds = 0.0
        self.exec_count = 0
        self.exec_durations: list[float] = [
        ]  # per-call wall-times (for p50/p95/p99)

    def execute(self, *args, **kwargs):
        t = time.perf_counter()
        try:
            return self.inner.execute(*args, **kwargs)
        finally:
            # ``finally`` so a Submitted-raising action still counts its time
            dt = time.perf_counter() - t
            self.exec_seconds += dt
            self.exec_count += 1
            self.exec_durations.append(round(dt, 3))

    def __getattr__(self, name):  # config, get_template_vars, serialize, ...
        return getattr(self.inner, name)


def _evaluate(env, eval_cmd: str, timeout_sec: int) -> float:
    """Binary reward: eval_cmd exit 0 => 1.0; crash/timeout/nonzero => 0.0.

    Sync (runs inside asyncio.to_thread). If the test suite's stdout happens
    to start with the submit sentinel, env.execute raises Submitted; that
    requires returncode 0, so it still counts as a pass.
    """
    try:
        output = env.execute({"command": eval_cmd}, timeout=timeout_sec)
    except Submitted:
        return 1.0
    except Exception as e:  # noqa: BLE001 - a bad rollout must never raise out of eval
        logger.warning("[agent] eval_cmd failed to run: %s", e)
        return 0.0
    return 1.0 if output.get("returncode") == 0 else 0.0


# SWE-smith prep: capture the agent's fix as a patch, restore the hidden tests
# (the bug commit is HEAD~1: instance_id tip = bug + tests REMOVED), reapply the
# fix on top, so the repo test command grades the agent's edits against tests it
# never saw. Mirrors swesmith/harness/utils.py. Source
# files are identical between the two commits (only test files differ), so the
# agent's source diff applies cleanly onto HEAD~1.
_SWESMITH_PREP = (
    "set -e; cd {workdir}; "
    "git add -A; git diff --cached HEAD > /tmp/agent.patch || true; "
    "git checkout -f HEAD~1; "
    "git apply /tmp/agent.patch || git apply --3way /tmp/agent.patch || true")


def _evaluate_swesmith(env, md: dict, timeout_sec: int) -> float:
    """SWE-smith reward: reapply the agent's fix onto the tests-present commit,
    run the repo test command in the sandbox, parse the output HOST-side via
    swesmith's registry (log_parser). Reward 1.0 iff every FAIL_TO_PASS and
    PASS_TO_PASS test is PASSED. swesmith/swebench are imported here (trainer
    process), NOT in the sandbox — the env images don't ship them.
    """
    inst = md.get("swesmith_inst") or {}
    test_cmd = md.get("test_cmd")
    if not test_cmd or not inst:
        logger.warning("[agent] swesmith eval missing test_cmd/inst for %s",
                       md.get("instance_id"))
        return 0.0
    f2p = inst.get("FAIL_TO_PASS") or []
    p2p = inst.get("PASS_TO_PASS") or []
    try:
        prep = env.execute(
            {"command": _SWESMITH_PREP.format(workdir=md["workdir"])},
            timeout=180)
        if prep.get("returncode") != 0:
            logger.warning("[agent] swesmith prep rc=%s for %s",
                           prep.get("returncode"), md.get("instance_id"))
            return 0.0
        out = env.execute({"command": test_cmd}, timeout=timeout_sec)
        # Host-side parse (lazy import: only the trainer has swesmith installed).
        from swebench.harness.constants import TestStatus
        from swesmith.profiles import registry

        passed = TestStatus.PASSED.value
        status = registry.get_from_inst(inst).log_parser(out.get("output", ""))
        ok = all(status.get(t) == passed for t in f2p) and all(
            status.get(t) == passed for t in p2p)
        return 1.0 if ok else 0.0
    except Exception as e:  # noqa: BLE001 - a bad rollout must never raise out of eval
        logger.warning("[agent] swesmith eval failed for %s: %s",
                       md.get("instance_id"), e)
        return 0.0


async def generate(args, sample: Sample,
                   sampling_params: dict[str, Any]) -> list[Sample]:
    """Per-sample agent rollout with wall-clock guard (rollout_guard_sec)."""
    _ensure_rollout_executor(
    )  # size the to_thread pool to the batch (see above)
    state = _AdapterService(args)
    md = _get_metadata(sample)
    instance_id = md["instance_id"]
    if md["eval_kind"] == "cmd" and not md["eval_cmd"]:
        return _abort_result(sample, "missing_eval_cmd", instance_id)
    if md["eval_kind"] == "swesmith" and not md.get("test_cmd"):
        return _abort_result(sample, "missing_test_cmd", instance_id)

    session_id = sample.session_id = _session_id(sample, instance_id)
    state.adapter.open_session(
        session_id,
        sampling_defaults=sampling_params,
        max_context_tokens=state.max_context_len,
    )
    t0 = time.time()
    sb = None
    try:
        async with asyncio.timeout(CONFIG.rollout_guard_sec):
            # (a) sandbox claim latency
            t_claim = 0.0
            if not CONFIG.local_exec:
                _c = time.perf_counter()
                sb = await sandbox_env.claim_sandbox(
                    pool=md["pool"],
                    lifetime_sec=CONFIG.sandbox_lifetime_sec,
                    workdir=md["workdir"],
                    image=md.get(
                        "image"
                    ),  # on-demand create: no-pool mode, or pool-claim fallback
                )
                t_claim = time.perf_counter() - _c
            env_raw = _make_env(sb, md["workdir"])
            env = _TimedEnv(env_raw)  # (d) accumulate per-action exec time
            agent = _make_agent(_make_model(state.adapter_url, session_id), env)
            await asyncio.to_thread(_stage_files, env_raw, md["files"],
                                    md["workdir"])
            # Optional per-task setup run post-claim, pre-agent (e.g. SWE-smith
            # establishing the buggy state in a shared per-repo image). No-op for
            # MBPP (no setup_cmd). Runs via env_raw so it isn't counted as an
            # agent action in the exec timer; a nonzero rc aborts the rollout.
            if md.get("setup_cmd"):
                # The buggy-state `git checkout -f` fails transiently under load
                # (exec hiccup / box not fully settled / slow disk with 100s of
                # concurrent boxes). Retry with backoff; log the real rc+output if
                # it ultimately fails. (Single-shot aborted ~11% of rollouts.)
                setup_out: dict = {}
                for _attempt in range(3):
                    setup_out = await asyncio.to_thread(
                        env_raw.execute, {"command": md["setup_cmd"]},
                        timeout=CONFIG.exec_timeout_sec)
                    if setup_out.get("returncode") == 0:
                        break
                    if _attempt < 2:
                        await asyncio.sleep(2 * (_attempt + 1))
                if setup_out.get("returncode") != 0:
                    logger.warning(
                        "[agent] setup_cmd rc=%s for %s (3 tries): %s",
                        setup_out.get("returncode"),
                        instance_id,
                        str(setup_out.get("output", ""))[-300:],
                    )
                    return _abort_result(sample, "setup_cmd_failed",
                                         instance_id)

            # (b) agent-loop wall time. DefaultAgent.run is sync (model.query +
            # env.execute block), so each in-flight rollout gets a worker thread.
            _a = time.perf_counter()
            exit_info = await asyncio.to_thread(agent.run, md["task"])
            t_agent = time.perf_counter() - _a
            # capture agent-loop exec time BEFORE eval so it isn't polluted by it
            t_exec = env.exec_seconds
            n_exec = env.exec_count

            # (c) eval time -- run via env_raw to keep the exec counter loop-only
            _e = time.perf_counter()
            if md["eval_kind"] == "swesmith":
                # SWE-smith: reapply agent fix onto tests-present commit, run repo
                # test cmd, parse host-side. No eval_files staged (grading is not
                # an in-sandbox script). See _evaluate_swesmith.
                reward = await asyncio.to_thread(_evaluate_swesmith, env_raw,
                                                 md, CONFIG.eval_timeout_sec)
            else:
                # MBPP/cmd: stage hidden graders AFTER the loop (policy can't read
                # or edit them), then run eval_cmd (exit 0 == solved).
                await asyncio.to_thread(_stage_files, env_raw, md["eval_files"],
                                        md["workdir"])
                reward = await asyncio.to_thread(_evaluate, env_raw,
                                                 md["eval_cmd"],
                                                 CONFIG.eval_timeout_sec)
            t_eval = time.perf_counter() - _e

            samples = await state.adapter.finish_session(
                session_id,
                base_sample=sample,
                reward=
                reward,  # split evenly across fan-out samples by the manager
            )
            if not samples:
                return _abort_result(sample, "adapter_session_empty",
                                     instance_id)
            # Per-rollout timing rides sample.metadata; rollout_metrics.py dedups
            # by session_id (a fork emits >1 sample, all sharing these numbers).
            timing = {
                "claim_sec": round(
                    t_claim, 3),  # sandbox CREATE/claim time (one per rollout)
                "agent_sec": round(t_agent, 3),
                "eval_sec": round(t_eval, 3),
                "exec_sec": round(t_exec, 3),
                "exec_count": n_exec,
                "exec_calls": list(env.exec_durations
                                  ),  # per-call exec wall-times (percentiles)
            }
            for s in samples:
                # slime's TrajectoryManager.to_sample does NOT propagate
                # session_id onto emitted samples, and at multi-turn a rollout
                # fans out into >1 per-turn sample that all share base_sample.index
                # (the PROMPT index, not rollout-unique). Stamp our unique sid so
                # rollout_metrics can group per-rollout (else pass@1/timing sum
                # across a whole group -> pass@1 > 1). See test_masking.py.
                s.session_id = session_id
                s.metadata = {
                    **(s.metadata or {}),
                    "exit_status": exit_info.get("exit_status", ""),
                    "n_turns": agent.n_calls,
                    "agent_timing": timing,
                    # Authoritative binary rollout reward. Metrics must read THIS,
                    # not sum s.reward: at multi-turn a rollout fans out into
                    # per-turn samples and slime re-weights their per-sample
                    # reward (summing them inflates pass@1 by ~turns). See
                    # rollout_metrics.compute_metrics.
                    "agent_reward": float(reward),
                }
            logger.info(
                "[agent] %s: reward=%.2f exit=%s turns=%d elapsed=%.1fs segments=%d "
                "claim=%.2fs agent=%.2fs eval=%.2fs exec=%.2fs(n=%d)",
                instance_id,
                reward,
                exit_info.get("exit_status", ""),
                agent.n_calls,
                time.time() - t0,
                len(samples),
                t_claim,
                t_agent,
                t_eval,
                t_exec,
                n_exec,
            )
            return samples

    except asyncio.TimeoutError:
        logger.warning(
            "[agent] %s: wall_clock_timeout after %.1fs (guard=%ds)",
            instance_id,
            time.time() - t0,
            CONFIG.rollout_guard_sec,
        )
        return _abort_result(sample, "wall_clock_timeout", instance_id)
    except Exception as e:
        logger.warning("[agent] %s: rollout failed: %s\n%s", instance_id, e,
                       traceback.format_exc())
        return _abort_result(sample, f"exception:{type(e).__name__}",
                             instance_id)
    finally:
        if sb is not None:  # never leak a claimed sandbox
            await sandbox_env.terminate_sandbox(sb)
        await state.adapter.drop_session(session_id)  # cleanup only, idempotent


def _session_id(sample: Sample, instance_id: str) -> str:
    # Same scheme as coding_agent_rl/generate.py:_session_id; sids must be
    # unique per agent run (open_session raises on duplicates).
    if sample.session_id:
        return sample.session_id
    if sample.index is not None and sample.group_index is not None:
        return f"agent-{instance_id}-{sample.index}-{sample.group_index}"
    return f"agent-{instance_id}-{secrets.token_hex(8)}"


def _abort_result(sample: Sample, reason: str,
                  instance_id: str) -> list[Sample]:
    """Mark ``sample`` aborted in place; verbatim shape from
    slime/examples/coding_agent_rl/generate.py:_abort_result."""
    sample.tokens = [0, 0]
    sample.response = ""
    sample.response_length = 1
    sample.loss_mask = [0]
    sample.rollout_log_probs = [0.0]
    sample.reward = 0.0
    sample.remove_sample = True
    sample.status = Sample.Status.ABORTED
    sample.metadata = {**(sample.metadata or {}), "abort_reason": reason}
    logger.warning("[agent] %s aborted: %s", instance_id, reason)
    return [sample]
