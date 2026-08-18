"""Graph A on the kernel: discover, map(work, gate, scan), tally, stage, approve, apply.

The node ids, kinds, isolation, write sets, requirements, deadlines and budgets are the
ones in ``workflow/design/graphs/A-fleet-migration.md``'s node table, for every DISPATCHED
node. The table's ``m_migrate`` row is the exception: a map is performed by the coordinator
and dispatches no node of its own, so it has no spec here and its row's ``deadline_s`` and
map-wide budget are not enforced by anything yet. This module is the
graph AS CODE: a deterministic program that reaches the world only through the
coordinator's primitives, branches only on typed fields of journaled records, and reads
time only through the coordinator's clock - so re-executing it from the top after a
crash or a suspend reaches the same journal keys and re-dispatches nothing that already
has a result.

Two things this module deliberately does NOT do, and one it must not:

* The Codex A/B arm (``w_migrate_codex@i``, odd branches) is a later milestone; every
  branch runs the claude arm here.
* Nothing is retried, capped or cancelled here - those are coordinator mechanisms, not
  workflow code.
* Nothing may read the wall clock, ``uuid`` or ``random``:
  :func:`~agentdag.application.kernel.workflow_check.assert_deterministic` refuses this
  module's source before the first dispatch if it does, because such a value would reach
  a node's ``input.json`` and silently change its journal key on the next launch.

Contents:
    * :class:`GraphAArgs` - the run's typed arguments, as the CLI takes them.
    * :func:`program` - the graph itself.
    * :func:`perform_push` - the one effect that leaves the process, as ``ap_push`` performs it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from ...domain.errors import KernelError, SpecRejected
from ...domain.graph_a import (
    PushIntent,
    Tally,
    WorkResult,
    is_scratch_target,
    parse_repos_text,
    reduce_tally,
    stage,
)
from ...domain.keys import content_hash
from ...domain.models import (
    ApproveOption,
    ApprovePayload,
    Budget,
    Isolation,
    Kind,
    NodeOutcome,
    NodeSpec,
    NodeStatus,
    Requirement,
    TierRole,
)
from ..kernel.context import BranchRef
from ..kernel.ports import format_stamp

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ...domain.graph_a import TallySummary
    from ...domain.models import ResultRecord
    from ..kernel.context import Coordinator, HasDedupKey

__all__ = ["GraphAArgs", "perform_push", "program"]

_DECIDE_BY_S = 86400
"""How long the push list waits for a human decision (design's node table, ``a_push_list``)."""

_WORK_TOKENS = 400_000
"""Per-branch token budget on the standard row (design's node table, ``w_migrate@i``)."""

_MAP_ID = "m_migrate"
"""The map's id; also the manifest file name the reduce writes."""


class GraphAArgs(BaseModel):
    """What one graph A run is given.

    Attributes:
        repos_file: A file listing one bare scratch clone per line; blank lines and
            ``#`` comments are ignored.
        brief_file: The change to make, as every work node's brief.
        scratch: The scratch directory this run owns; only ``<scratch>/origin`` is ever
            a push target.
        parallel: How many map branches may be in flight at once.
        model: An explicit model alias for the work nodes, or ``None`` to let the tier
            policy resolve the standard row.
    """

    model_config = ConfigDict(frozen=True)

    repos_file: Path
    brief_file: Path
    scratch: Path
    parallel: int = 2
    model: str | None = None


class _BranchOutcome(BaseModel):
    """What one map branch produced besides its record: its tally row and its last node.

    ``last_node_id`` is what ``r_tally`` depends on for that branch - ``g_scan@i`` when
    the branch ran to the end, ``w_migrate@i`` when the work node failed and the gate
    and scan were never dispatched.
    """

    model_config = ConfigDict(frozen=True)

    tally: Tally
    last_node_id: str


async def program(co: Coordinator, args: GraphAArgs) -> None:
    """Run graph A over the fleet ``args.repos_file`` names.

    Args:
        co: The coordinator; every effect goes through one of its primitives.
        args: The run's arguments.

    Raises:
        SpecRejected: two fleet members share a basename (they would share one
            worktree), or a member is not a bare clone under ``<scratch>/origin``. Both
            are checked BEFORE the first dispatch: a push target the run may not write
            to is worth nothing after a fleet's worth of model spend and an approval
            prompt for a push that cannot happen.
        Suspended: ``a_push_list`` has no decision yet - control flow, not an error;
            the coordinator process exits and a later launch with a decision recorded
            resumes exactly here.
    """
    repos_text = args.repos_file.read_text(encoding="utf-8")
    origins = parse_repos_text(repos_text)
    _refuse_unusable_fleet(origins, args.scratch)
    discovered = await co.reduce(
        _discover_spec(), fold=lambda: _discovered(origins), input_obj={"repos_hash": content_hash(repos_text)}
    )
    if not _typed_count(discovered, "n"):
        return  # g_discover halts: nothing to migrate
    summary, deps, branches = await _migrate(co, args, origins)
    tally = await co.reduce(
        _tally_spec(deps), fold=lambda: _tallied(co, summary, branches), input_obj=_tally_input(summary)
    )
    if not _typed_count(tally, "passed_count"):
        return  # route: nothing pushable, so nobody is asked
    intents = stage(summary)
    await co.stage(_stage_spec(), intents=intents, kind="push")
    decision = await co.approve(_approve_spec(), payload=_payload(co, summary, intents))
    if decision.decision != "approve":
        return
    await co.apply(_apply_spec(), intents=intents, kind="push", perform=lambda i: perform_push(co, args.scratch, i))


async def _migrate(
    co: Coordinator, args: GraphAArgs, origins: Sequence[Path]
) -> tuple[TallySummary, list[str], list[BranchRef]]:
    """Fan out one branch per fleet member and fold what they produced.

    Args:
        co: The coordinator.
        args: The run's arguments; the brief and the model come from here.
        origins: The fleet, in file order.

    Returns:
        The reduced tally, the node ids ``r_tally`` depends on (one per branch that
        dispatched anything, in branch order), and one manifest entry per branch.
    """
    brief = args.brief_file.read_text(encoding="utf-8")
    outcomes: dict[int, _BranchOutcome] = {}

    async def body(index: int, origin: Path) -> ResultRecord:
        return await _branch(co, index, origin, brief=brief, model=args.model, outcomes=outcomes)

    records = await co.map(_MAP_ID, list(origins), body)
    rows = [_row_of(outcomes.get(index), origin, records[index]) for index, origin in enumerate(origins)]
    keys = _keys_by_node(co)
    branches = [
        _branch_ref(index, _node_of(outcomes.get(index), index), keys, records[index]) for index in range(len(origins))
    ]
    deps = [outcomes[index].last_node_id for index in sorted(outcomes)]
    return reduce_tally(rows), deps, branches


def _branch_ref(index: int, node_id: str, keys: Mapping[str, str], record: ResultRecord) -> BranchRef:
    """Build one manifest entry; a branch whose node never dispatched has no key to name."""
    return BranchRef(index=index, node_id=node_id, key=keys.get(node_id, "-"), status=record.status.value)


async def _branch(
    co: Coordinator,
    index: int,
    origin: Path,
    *,
    brief: str,
    model: str | None,
    outcomes: dict[int, _BranchOutcome],
) -> ResultRecord:
    """Run one fleet member's branch: clone, work, gate, scan; record its tally row.

    The clone is skipped when the worktree is already there, so a relaunch never
    re-clones over the tree the previous launch's work node committed into.

    Args:
        co: The coordinator.
        index: This branch's position in the fleet.
        origin: The bare scratch clone this branch migrates.
        brief: The work node's brief.
        model: An explicit model alias, or ``None`` to resolve by tier role.
        outcomes: Filled with this branch's tally row and last node id.

    Returns:
        This branch's LAST record - the scan's, or the work node's when it failed.
    """
    name = _worktree_name(origin)
    worktree = co.run_dir.worktree(name)
    _ensure_worktree(co, origin, worktree)
    before = co.snapshot()  # AFTER any stale-staging cleanup: see _ensure_worktree's docstring
    work = await co.work(_work_spec(index, name, model), brief=brief, cwd=worktree)
    if work.status is not NodeStatus.DONE:
        row = Tally(repo=origin, status="work-failed", head_sha=co.git.head_sha(worktree), test_rc=None)
        outcomes[index] = _BranchOutcome(tally=row, last_node_id=f"w_migrate@{index}")
        return work
    gate = await co.gate(_test_spec(index, name), argv=("make", "test"), cwd=worktree)
    head = co.git.head_sha(worktree)  # AFTER the gate, so the sha staged for the push is the one it saw
    scan = await co.scan(_scan_spec(index), watched=f"w_migrate@{index}", before=before, write_set=[f"wt/{name}/**"])
    passed = gate.status is NodeStatus.DONE and scan.status is NodeStatus.DONE
    row = Tally(repo=origin, status="passed" if passed else "failed", head_sha=head, test_rc=_rc_of(gate))
    outcomes[index] = _BranchOutcome(tally=row, last_node_id=f"g_scan@{index}")
    return scan


def _row_of(outcome: _BranchOutcome | None, origin: Path, record: ResultRecord) -> Tally:
    """Return a branch's tally row, or the work-failed row a branch that never got one gets.

    A branch reaches ``None`` here only by raising OUTSIDE any dispatch - a clone that
    blew up, or a misconfiguration :meth:`~agentdag.application.kernel.context.Coordinator.work`
    refuses BEFORE dispatching (an unwired executor, a model no row lists). The synthetic
    record :meth:`~agentdag.application.kernel.context.Coordinator.map` built for it is
    never journaled, so its message survives only if it is copied onto the row here;
    without that the whole fleet reports a bare ``work-failed`` and the run ends ``done``
    with the reason nowhere on disk.
    """
    if outcome is not None:
        return outcome.tally
    reason = record.error.message if record.error is not None else "the branch raised before it dispatched anything"
    return Tally(repo=origin, status="work-failed", head_sha="-", test_rc=None, work=WorkResult(ok=False, error=reason))


def _ensure_worktree(co: Coordinator, origin: Path, worktree: Path) -> None:
    """Clone ``origin`` into ``worktree`` once, so a half-finished clone is never reused.

    The clone runs OUTSIDE any dispatch, so it has no ``started`` line and no crash
    window: nothing tells a later launch whether an existing ``wt/<name>`` is a finished
    clone or one a crash cut in half, and ``exists()`` answers the same for both. A
    half-finished clone is permanent damage - :meth:`GitCli.clone` sets the committer
    identity in its LAST two steps, so a clone interrupted before them leaves a tree
    whose every ``git commit`` fails, the work node records ``failed`` forever, and the
    only remedy is deleting the directory by hand.

    Cloning into a staging path and renaming makes existence mean "the clone completed":
    a rename is atomic, so there is no observable state in between. The staging path sits
    inside the isolation root but is never visible to a scan, because nothing between its
    creation and the rename awaits, so no sibling branch can take a manifest across it.

    This whole call - the stale-staging removal AND the clone - must run BEFORE the
    branch's own :meth:`~agentdag.application.kernel.context.Coordinator.snapshot`, not
    after: a stray ``.partial-<name>`` left by an earlier crash is removed here, and a
    removal that happened INSIDE the branch's own scan window (between its ``before``
    and ``after`` manifests) reads as a stray deletion, permanently failing the very
    branch this cleanup exists to rescue. Calling this first means the branch's own
    ``before`` snapshot is taken only once ``wt/<name>`` already exists and
    ``wt/.partial-<name>`` already does not, so this branch's scan never sees either
    change. The one case this ordering cannot close: under ``parallel > 1``, ANOTHER
    branch's :meth:`~agentdag.application.kernel.context.Coordinator.snapshot` can still
    land while this call is between removing a stale staging dir and renaming a fresh
    one - a sibling's scan window, not this branch's own. ``wt/.partial-*/**`` is listed
    among :meth:`~agentdag.application.kernel.context.Coordinator.scan`'s always-allowed
    prefixes precisely so that residual window is never a finding either.

    Args:
        co: The coordinator, for the git port.
        origin: The bare scratch clone to copy.
        worktree: Where the branch works; left untouched when it is already there.
    """
    if worktree.exists():
        return
    staging = worktree.with_name(f".partial-{worktree.name}")
    if staging.exists():
        co.git.remove_tree(staging)  # a previous launch died mid-clone; git writes objects read-only
    co.git.clone(origin, staging)
    staging.rename(worktree)


def _typed_count(record: ResultRecord, field: str) -> int:
    """Read one typed count off a record, refusing a record that did not succeed.

    A failed node carries EMPTY ``key_facts``, so reading the field straight off it
    raises a bare ``KeyError`` naming nothing. The worse half is that the failed record
    is journaled and SERVED on every later launch, so a resume dies exactly the same way
    however the underlying problem is fixed - naming the node, its status and its own
    error is what tells an operator that this needs a new attempt rather than another
    resume.

    Args:
        record: The record to read.
        field: The ``key_facts`` entry to read, which must be in ``typed_fields``.

    Returns:
        The count.

    Raises:
        KernelError: the record is not ``done``, or carries no such field.
    """
    if record.status is not NodeStatus.DONE:
        reason = record.error.message if record.error is not None else "no error recorded"
        raise KernelError(
            f"node {record.node_id!r} ended {record.status.value} ({reason}), so its {field!r} does not exist; "
            "a resume serves this same record, so this needs a new attempt, not another launch"
        )
    value = record.key_facts.get(field)
    if not isinstance(value, int):
        raise KernelError(f"node {record.node_id!r} reported no integer {field!r}: {value!r}")
    return value


def _node_of(outcome: _BranchOutcome | None, index: int) -> str:
    """Return a branch's last node id, or the map's own synthetic id when it dispatched nothing."""
    return outcome.last_node_id if outcome is not None else f"{_MAP_ID}@{index}"


def _rc_of(gate: ResultRecord) -> int | None:
    """Return the gate's recorded exit code, or ``None`` when it carries no ``rc``."""
    rc = gate.key_facts.get("rc")
    return rc if isinstance(rc, int) else None


def _keys_by_node(co: Coordinator) -> dict[str, str]:
    """Map node id -> the journal key of its most recent dispatch.

    Read back from the journal rather than from the records, because neither
    :class:`~agentdag.domain.models.ResultRecord` nor the coordinator carries a
    dispatch's key, and a manifest entry names one. A node served from the journal on
    this launch has its key from the launch that really dispatched it, which is the
    same key by construction.
    """
    return {line.node_id: line.key for line in co.dispatcher.journal.lines() if line.event == "started"}


def _refuse_unusable_fleet(origins: Sequence[Path], scratch: Path) -> None:
    """Refuse a fleet with duplicate basenames or a target outside the scratch tree.

    Args:
        origins: The proposed fleet, in file order.
        scratch: The scratch directory this run owns.

    Raises:
        SpecRejected: a path is relative, two entries map to the same worktree, or an
            entry does not lie under ``<scratch>/origin``.
    """
    _refuse_relative(scratch, "scratch")
    seen: dict[str, Path] = {}
    for origin in origins:
        _refuse_relative(origin, "fleet member")
        name = _worktree_name(origin)
        first = seen.get(name)
        if first is not None:
            raise SpecRejected(
                f"{first} and {origin} both map to the worktree {name!r}; fleet members must not collide"
            )
        seen[name] = origin
        if not is_scratch_target(origin, scratch):
            raise SpecRejected(f"{origin} is not under {scratch}/origin; a real repo is never a push target")


def _refuse_relative(path: Path, what: str) -> None:
    """Refuse a relative path: it resolves against the process CWD, which no journal records.

    A relative fleet member or scratch root makes the run mean something different from a
    different working directory - at best a refusal on resume, at worst a DIFFERENT
    repository with the same relative name. The CWD is not part of the run state, so the
    only safe answer is to require what a resume can reproduce.

    Raises:
        SpecRejected: ``path`` is not absolute.
    """
    if not path.is_absolute():
        raise SpecRejected(f"{what} {path} is relative; a run must name paths a resume can reproduce")


def _worktree_name(origin: Path) -> str:
    """Return the worktree directory a fleet member migrates in.

    The ONE place the mapping lives, because two members mapping to one worktree is a
    silent data-loss bug: the second branch would skip its clone, run in the first's
    tree and tally the first's commits as its own.

    Example:
        >>> _worktree_name(Path("/s/origin/a.git")), _worktree_name(Path("/s/origin/a"))
        ('a', 'a')
    """
    return origin.name.removesuffix(".git")


def _discovered(origins: Sequence[Path]) -> NodeOutcome:
    """Build ``g_discover``'s outcome: the fleet as typed key facts, never prose."""
    return NodeOutcome(
        status=NodeStatus.DONE,
        key_facts={"items": [str(origin) for origin in origins], "n": len(origins)},
        typed_fields=["items", "n"],
        executor_used="code",
        model_used="-",
        effort_used="-",
    )


def _tally_input(summary: TallySummary) -> dict[str, object]:
    """Build ``r_tally``'s identity input: the rows it folds, as JSON-ready values."""
    return {"rows": [row.model_dump(mode="json") for row in summary.rows]}


def _tallied(co: Coordinator, summary: TallySummary, branches: Sequence[BranchRef]) -> NodeOutcome:
    """Write the tally and the map manifest, and report the three counts the route reads."""
    co.run_dir.write_atomic("artefacts/tally.json", summary.model_dump_json(indent=1))
    manifest = co.write_manifest(_MAP_ID, list(branches))
    return NodeOutcome(
        status=NodeStatus.DONE,
        artefact_refs=["artefacts/tally.json", manifest.relative_to(co.run_dir.root).as_posix()],
        key_facts={"passed_count": summary.passed, "failed_count": summary.failed, "skipped_count": 0},
        typed_fields=["passed_count", "failed_count", "skipped_count"],
        executor_used="code",
        model_used="-",
        effort_used="-",
    )


def _payload(co: Coordinator, summary: TallySummary, intents: Sequence[PushIntent]) -> ApprovePayload:
    """Build ``a_push_list``'s suspend payload: the push list, and the two options.

    The default is ``hold``, whose effect is ``none``: a default is what the deadline
    owner applies unattended, so it may never be the option that pushes (design 3.4).
    """
    listing = "\n".join(f"  {intent.repo}  {intent.head_sha[:12]}" for intent in intents)
    return ApprovePayload(
        text=f"passed {summary.passed} failed {summary.failed}; push list:\n{listing}",
        node_id="a_push_list",
        artefact_refs=[f"intents/push/{intent.dedup_key}.json" for intent in intents],
        options=[
            ApproveOption(id="approve", label="push the listed commits", effect="external"),
            ApproveOption(id="hold", label="hold", effect="none"),
        ],
        default="hold",
        decide_by=_decide_by(co),
        workflow="graph-a",
        run_id=co.run_id,
    )


def _decide_by(co: Coordinator) -> str:
    """Return the decision deadline, derived from the run's OWN start, never from now.

    The payload's content hash IS this node's dispatch identity, so a deadline read
    from the clock would move on every launch and re-dispatch an approve node the
    journal already holds. The run's ``run_started`` timestamp is written once and
    never rewritten, so it replays identically.

    Raises:
        KernelError: the journal has no ``run_started`` line, which
            :func:`~agentdag.application.kernel.run.run_coordinator` always writes
            before the program runs.
    """
    started = co.dispatcher.index.run_started
    if started is None:
        raise KernelError("this run's journal has no run_started line, so the approve deadline has no stable base")
    return format_stamp(datetime.fromisoformat(started.at) + timedelta(seconds=_DECIDE_BY_S))


def perform_push(co: Coordinator, scratch: Path, intent: HasDedupKey) -> str:
    """Push one staged intent to its scratch clone, or report that it is already there.

    Args:
        co: The coordinator, for the git port and the worktree path.
        scratch: The scratch directory this run owns.
        intent: The staged intent; the marker guard is
            :meth:`~agentdag.application.kernel.context.Coordinator.apply`'s job, and
            this is the external-state check that survives a crash between the two.

    Returns:
        ``"pushed"``, or ``"already-present"`` when the branch already points at the
        commit. The REF is read rather than the object: a push whose objects
        transferred and whose ref update was rejected leaves the object behind, and
        calling that already-present would abandon the push forever.

    Raises:
        KernelError: ``intent`` is not a :class:`~agentdag.domain.graph_a.PushIntent`.
        SpecRejected: the target is not a bare clone under ``<scratch>/origin``.
    """
    if not isinstance(intent, PushIntent):
        raise KernelError(f"ap_push was handed {type(intent)!r}, not a PushIntent")
    if not is_scratch_target(intent.repo, scratch):
        raise SpecRejected(f"{intent.repo} is not under {scratch}/origin; a real repo is never a push target")
    branch = co.git.default_branch(intent.repo)
    if co.git.ref_sha(intent.repo, branch) == intent.head_sha:
        return "already-present"
    co.git.push(co.run_dir.worktree(_worktree_name(intent.repo)), intent.repo, branch)
    return "pushed"


def _discover_spec() -> NodeSpec:
    """The fleet scan; a gate because an empty list halts the run."""
    return NodeSpec(node_id="g_discover", kind=Kind.GATE, executor="code", isolation=Isolation.NONE, deadline_s=300)


def _work_spec(index: int, name: str, model: str | None) -> NodeSpec:
    """One branch's work node: the claude arm, in its own worktree."""
    return NodeSpec(
        node_id=f"w_migrate@{index}",
        kind=Kind.WORK,
        tier_role=TierRole.STANDARD,
        model=model,
        isolation=Isolation.WORKTREE,
        write_set=[f"wt/{name}/**"],
        deps=["g_discover"],
        deadline_s=3600,
        budget=Budget(tokens={"sonnet": _WORK_TOKENS}),
    )


def _test_spec(index: int, name: str) -> NodeSpec:
    """One branch's mechanical gate; the shared bmk tool env is a REQUIREMENT, not a write."""
    return NodeSpec(
        node_id=f"g_test@{index}",
        kind=Kind.GATE,
        executor="code",
        isolation=Isolation.DIR,
        write_set=[f"wt/{name}/**"],
        requires=[Requirement(resource="bmk-tool-env", amount=1)],
        deps=[f"w_migrate@{index}"],
        deadline_s=1800,
    )


def _scan_spec(index: int) -> NodeSpec:
    """One branch's isolation scan; it declares no write set of its own.

    It depends on the GATE as well as the work node, because the window it judges runs
    from the branch's own snapshot to after the gate has run: a gate that wrote outside
    the worktree is inside this scan's evidence, so a re-dispatched gate has to make this
    a different call rather than let the old ``done`` record be served over it.
    """
    return NodeSpec(
        node_id=f"g_scan@{index}",
        kind=Kind.GATE,
        executor="code",
        isolation=Isolation.DIR,
        deps=[f"w_migrate@{index}", f"g_test@{index}"],
        deadline_s=300,
    )


def _tally_spec(deps: Sequence[str]) -> NodeSpec:
    """The reduce that closes the map: it depends on every branch's last node."""
    return NodeSpec(
        node_id="r_tally",
        kind=Kind.REDUCE,
        executor="code",
        isolation=Isolation.NONE,
        write_set=[f"manifest/{_MAP_ID}.json"],
        deps=list(deps),
        deadline_s=60,
    )


def _stage_spec() -> NodeSpec:
    """The stage node: one intent file per passed repository, written before anything leaves."""
    return NodeSpec(
        node_id="s_push_intent",
        kind=Kind.STAGE,
        executor="code",
        stage_into="push",
        isolation=Isolation.NONE,
        write_set=["intents/push/*.json"],
        deps=["r_tally"],
        deadline_s=60,
    )


def _approve_spec() -> NodeSpec:
    """The human gate; its deadline is the decision window, not a node runtime."""
    return NodeSpec(
        node_id="a_push_list",
        kind=Kind.APPROVE,
        executor="code",
        isolation=Isolation.NONE,
        deps=["s_push_intent"],
        deadline_s=_DECIDE_BY_S,
    )


def _apply_spec() -> NodeSpec:
    """The one node that leaves the process: each intent is performed once, ever."""
    return NodeSpec(
        node_id="ap_push",
        kind=Kind.APPLY,
        executor="code",
        isolation=Isolation.NONE,
        deps=["a_push_list"],
        deadline_s=900,
    )
