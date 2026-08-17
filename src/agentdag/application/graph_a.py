"""Graph A as code: discover, map(worktree, work, gate), tally, stage, approve, apply.

The coordinator is a deterministic program, not a compiled graph object: it calls the
ports as ordinary functions and branches ONLY on typed records, never on prose an agent
produced. The map step is the parallel part (one branch per repository, bounded by a
semaphore); the gate inside it is serialised by the gate adapter's own lock because the
shared tool environment is host-wide.

This is the M1 baseline and it deliberately LACKS the properties later milestones add:
no journal, so a crash mid-run cannot be resumed; no token cap; no unattended approve.
Their absence is the measurement.

Contents:
    * :func:`make_scratch_fleet` - bare mirrors of the real repositories, the only push targets.
    * :func:`apply` - the idempotent push step, guarded against non-scratch targets.
    * :func:`run_graph` - the graph itself.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import TYPE_CHECKING

from ..domain.graph_a import Tally, is_scratch_target, reduce_tally, stage

if TYPE_CHECKING:
    from pathlib import Path

    from ..domain.graph_a import PushIntent
    from .graph_a_ports import ApprovePort, GatePort, GitPort, RunStore, WorkPort

__all__ = ["apply", "make_scratch_fleet", "run_graph"]


def make_scratch_fleet(real_repos: list[Path], scratch: Path, git: GitPort, *, refresh: bool = False) -> list[Path]:
    """Create one bare mirror per real repository under ``<scratch>/origin``.

    The real repositories are read exactly once, by ``git clone --mirror``, and are
    never written. The mirrors are the only push targets the run will accept.

    A mirror that already exists is REUSED as it stands, however stale it has become,
    because a previous run may have pushed to it; pass ``refresh=True`` to throw the
    existing mirrors away and re-read the real repositories.

    Args:
        real_repos: Absolute paths of the real repositories.
        scratch: The scratch directory this run owns.
        git: The git port.
        refresh: Whether to delete an existing mirror and mirror the repository again.

    Returns:
        One bare-clone path per input repository, in input order.

    Raises:
        ValueError: If two repositories share a basename: they would mirror onto the
            same path and one fleet member would silently become the other.
    """
    _refuse_duplicate_basenames(real_repos)
    origin = scratch / "origin"
    origin.mkdir(parents=True, exist_ok=True)
    targets: list[Path] = []
    for repo in real_repos:
        dest = origin / (repo.name + ".git")
        if refresh and dest.exists():
            shutil.rmtree(dest)
        if not dest.exists():
            git.mirror(repo, dest)
        targets.append(dest)
    return targets


def _refuse_duplicate_basenames(repos: list[Path]) -> None:
    """Refuse a fleet whose members do not have distinct basenames.

    Both the scratch mirror and the run's worktree are named after the basename, so two
    repositories sharing one collapse into a single directory: the second overwrites the
    first's work and the tally reports both as the same repository.

    Args:
        repos: The fleet, in input order.

    Raises:
        ValueError: If two entries share a basename, naming both paths.
    """
    seen: dict[str, Path] = {}
    for repo in repos:
        first = seen.get(repo.name)
        if first is not None:
            raise ValueError(f"{first} and {repo} share the basename {repo.name!r} - fleet basenames must be distinct")
        seen[repo.name] = repo


def apply(intents: list[PushIntent], *, scratch_root: Path, git: GitPort, store: RunStore) -> list[str]:
    """Push every staged intent, once.

    Idempotent two ways: a done marker per dedup key short-circuits a replay, and the
    external state is checked as well (a target whose branch ALREADY points at the
    commit is not pushed again), so a crash between the push and the marker cannot
    double-apply. The external check reads the REF rather than asking whether the
    object exists: a push whose objects transferred and whose ref update was then
    rejected leaves the object behind, and calling that "already present" would abandon
    the push forever.

    Every target is validated before anything is pushed, so a bad entry anywhere in the
    list stops the whole apply rather than half of it.

    Args:
        intents: The staged push intents.
        scratch_root: The scratch directory this run owns.
        git: The git port.
        store: The run store holding the worktrees and the done markers.

    Returns:
        One outcome string per intent: ``"pushed"``, ``"already-present"`` or ``"already-done"``.

    Raises:
        ValueError: If a target does not lie under ``<scratch_root>/origin``. A real
            repository is never a push target, so this is a hard stop, not a skip.
    """
    for intent in intents:
        if not is_scratch_target(intent.repo, scratch_root):
            raise ValueError(f"{intent.repo} is not under {scratch_root}/origin - a real repo is never a push target")
    return [_apply_one(intent, git=git, store=store) for intent in intents]


def _apply_one(intent: PushIntent, *, git: GitPort, store: RunStore) -> str:
    """Push one already-validated intent, once, and report what happened.

    Args:
        intent: The staged push, whose target has been checked to be a scratch clone.
        git: The git port.
        store: The run store holding the worktrees and the done markers.

    Returns:
        ``"pushed"``, ``"already-present"`` or ``"already-done"``.
    """
    marker = store.marker(intent.dedup_key)
    if marker.exists():
        return "already-done"
    branch = git.default_branch(intent.repo)
    if git.ref_sha(intent.repo, branch) == intent.head_sha:
        outcome = "already-present"
    else:
        worktree = store.worktree(intent.repo.name.removesuffix(".git"))
        git.push(worktree, intent.repo, branch)
        outcome = "pushed"
    marker.touch()
    return outcome


async def run_graph(
    *,
    origins: list[Path],
    brief: str,
    model: str,
    parallel: int,
    scratch_root: Path,
    git: GitPort,
    gate: GatePort,
    work: WorkPort,
    approve: ApprovePort,
    store: RunStore,
) -> int:
    """Run graph A over a fleet of scratch origins and return the process exit code.

    Args:
        origins: The bare scratch clones to migrate.
        brief: The change to make, as the work node's system prompt.
        model: The model each work node runs on.
        parallel: How many branches may run at once.
        scratch_root: The scratch directory this run owns.
        git: The git port.
        gate: The mechanical gate.
        work: The work node.
        approve: The human in the loop.
        store: Where this run keeps its worktrees, logs and records.

    Returns:
        ``0``. The baseline reports outcomes through the tally record it writes, so a
        red gate on one repository is a result, not a coordinator failure.

    Raises:
        ValueError: If two origins share a basename; they would share one worktree.
    """
    if not origins:
        return 0  # g_discover halts: nothing to migrate
    _refuse_duplicate_basenames(origins)
    semaphore = asyncio.Semaphore(parallel)

    async def branch(origin: Path) -> Tally:  # m_migrate@i
        name = origin.name.removesuffix(".git")
        worktree = store.worktree(name)
        async with semaphore:
            await asyncio.to_thread(git.clone, origin, worktree)
            result = await work.run(worktree, brief, model, store.home(name))
            if not result.ok:
                head = await asyncio.to_thread(git.head_sha, worktree)
                row = Tally(repo=origin, status="work-failed", head_sha=head, test_rc=None, work=result)
            else:
                # g_test@i, serialised against every other branch by the gate's own lock
                rc = await asyncio.to_thread(gate.run, worktree, store.log(f"{name}.test.log"))
                # read AFTER the gate, so the sha staged for the push is the one the gate saw
                head = await asyncio.to_thread(git.head_sha, worktree)
                status = "passed" if rc == 0 else "failed"
                row = Tally(repo=origin, status=status, head_sha=head, test_rc=rc, work=result)
        store.write_json(f"tally/{name}.json", row.model_dump_json(indent=1))
        return row

    rows = list(await asyncio.gather(*(branch(origin) for origin in origins)))
    summary = reduce_tally(rows)  # r_tally
    store.write_json("tally.json", summary.model_dump_json(indent=1))
    intents = stage(summary)  # s_push_intent
    for intent in intents:
        store.write_json(f"intents/{intent.dedup_key}.json", intent.model_dump_json())
    if not intents:
        return 0  # rt_pushable: nothing to push, so nobody is asked
    listing = "\n".join(f"  {intent.repo}  {intent.head_sha[:12]}" for intent in intents)
    prompt = f"passed {summary.passed} failed {summary.failed}; push list:\n{listing}\napprove pushing?"
    if not approve.confirm(prompt):  # a_push_list
        return 0
    apply(intents, scratch_root=scratch_root, git=git, store=store)  # ap_push
    return 0
