"""Kernel records: what a node is (spec), what it returned (record), what a run is (state).

Mirrors the JSON schemas shipped in ``agentdag/schemas/`` (design 2.1, 2.2, 3.1, 3.4);
``tests/test_kernel_schemas.py`` proves the two agree. Frozen where a value enters a
journal key. No I/O here.

Contents:
    * :class:`Kind`, :class:`NodeStatus`, :class:`ErrorType`, :class:`Isolation`,
      :class:`TierRole`, :class:`ExecutorKind`, :class:`RunStatus`,
      :class:`SuspendReason` - the kernel's closed vocabularies, each a
      :class:`~enum.StrEnum`.
    * :class:`Budget`, :class:`Requirement` - per-node limits.
    * :class:`NodeSpec` - what the planner emits and the coordinator dispatches (2.1).
    * :class:`Tokens`, :class:`NodeError`, :class:`KnowledgeUsed`, :class:`SandboxGuarantees` -
      the small shapes a result carries.
    * :class:`NodeOutcome` - what a node body returns.
    * :class:`ResultRecord` - the only thing the coordinator branches on (2.2).
    * :class:`LockHolder`, :class:`RunState` - the run's own state.
    * :class:`ApproveOption`, :class:`ApprovePayload`, :class:`Decision` - the approve
      node's suspend payload and its recorded decision (3.4).
    * :class:`RetryGrant` - an operator granting a failed node one more attempt, written
      as ``retries/<node_id>.<hash8>.json`` before it is folded into the journal.
    * :class:`CancelIntent` - a whole-run cancel request, written as
      ``decisions/_run.cancel.json`` before it is folded into the journal (3.4, O25).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer

__all__ = [
    "CODE_KINDS",
    "FAN_OUT_KINDS",
    "ApproveOption",
    "ApprovePayload",
    "Budget",
    "CancelIntent",
    "CredentialVerdict",
    "Decision",
    "ErrorType",
    "ExecutorKind",
    "Isolation",
    "Kind",
    "KnowledgeUsed",
    "LockHolder",
    "MarkerPhase",
    "NodeError",
    "NodeOutcome",
    "NodeSpec",
    "NodeStatus",
    "Requirement",
    "ResultRecord",
    "RetryGrant",
    "RunSettings",
    "RunState",
    "RunStatus",
    "SandboxGuarantees",
    "SuspendReason",
    "TierRole",
    "Tokens",
]


class Kind(StrEnum):
    """The dispatched primitive a node runs (design 2.1, 4)."""

    WORK = "work"
    GATE = "gate"
    SYNTH = "synth"
    PLANNER = "planner"
    APPROVE = "approve"
    MAP = "map"
    REDUCE = "reduce"
    WAIT = "wait"
    BATCH = "batch"
    STAGE = "stage"
    APPLY = "apply"


CODE_KINDS = frozenset({Kind.GATE, Kind.REDUCE, Kind.WAIT, Kind.STAGE, Kind.APPLY, Kind.APPROVE})
"""The kinds the coordinator runs as code, which carry ``executor: "code"`` (design 2.1)."""

FAN_OUT_KINDS = frozenset({Kind.MAP, Kind.BATCH})
"""Fan-out and fold performed by the coordinator itself, which carry NO executor (design 2.1)."""


class NodeStatus(StrEnum):
    """The closed vocabulary the coordinator branches on (design 2.2, C5)."""

    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"
    NEEDS_CONTEXT = "needs_context"
    NEEDS_CONTINUATION = "needs_continuation"
    REFUSED = "refused"
    CANCELLED = "cancelled"


class ErrorType(StrEnum):
    """The closed error vocabulary of design 2.2."""

    AGENTS_EMPTY_RESULT = "agents_empty_result"
    AUTH_FAILURE = "auth_failure"
    DEADLINE = "deadline"
    EXECUTOR_ERROR = "executor_error"
    RATE_LIMITED = "rate_limited"
    SCHEMA_MISMATCH = "schema_mismatch"
    KNOWLEDGE_UNAVAILABLE = "knowledge_unavailable"
    BUDGET_EXCEEDED = "budget_exceeded"
    CANCELLED_BY_USER = "cancelled_by_user"
    CONTINUATION_LIMIT = "continuation_limit"


class Isolation(StrEnum):
    """How a node's working directory is prepared (design 2.1, C8)."""

    WORKTREE = "worktree"
    DIR = "dir"
    NONE = "none"


class TierRole(StrEnum):
    """The role the tier policy resolves to a model row (design 2.1, 2.3)."""

    MECHANICAL = "mechanical"
    STANDARD = "standard"
    DEEP = "deep"
    TOP = "top"


class ExecutorKind(StrEnum):
    """The two executor families slice 1 knows by name.

    An ``mcp:<server>/<tool>`` string stays free text on :class:`NodeSpec` rather
    than a member here - the set of MCP-exposed executors is open, not closed.
    """

    CLAUDE = "claude"
    CODE = "code"


class MarkerPhase(StrEnum):
    """Which half of an apply's two-phase record a marker file is (design 9, stage/apply).

    ``ATTEMPTED`` is written BEFORE the effect and ``DONE`` after it. One done-marker
    alone cannot describe the window between them, because it is written only once the
    effect has returned: a crash in between leaves an effect applied and unrecorded. With
    both, a replay can tell an effect that MAY already have landed from one that never
    started, per dedup key rather than per node - which is the granularity that matters,
    since one apply node carries every intent its stage node staged.
    """

    ATTEMPTED = "attempted"
    DONE = "done"


class RunStatus(StrEnum):
    """The run's own lifecycle state (design 3.4)."""

    RUNNING = "running"
    SUSPENDED = "suspended"
    DONE = "done"
    FAILED = "failed"
    CRASHED = "crashed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


class CredentialVerdict(StrEnum):
    """What an out-of-band check of the credential found, when the executor could not tell.

    Exists because the provider's CLI reports quota exhaustion and a rejected credential
    with the SAME error text and the same null status field, so no amount of string
    matching separates them - only asking the API directly does.

    ``INDETERMINATE`` is a real answer, not a failure to produce one: the check itself can
    be refused, time out, or find the network gone. It must never be read as either of the
    other two, because a classification invented from no evidence is exactly the kind of
    fact this kernel branches on.
    """

    RATE_LIMITED = "rate_limited"
    UNAUTHORIZED = "unauthorized"
    INDETERMINATE = "indeterminate"


class SuspendReason(StrEnum):
    """Why a run handed control back, which decides what has to happen before it resumes.

    ``RunStatus.SUSPENDED`` alone cannot say this, and the two answers want opposite
    things from the operator: ``DECISION`` waits on a person and cannot move until one
    answers, while ``QUOTA`` waits on the provider and needs only time, then a plain
    ``run resume``. An operator told the wrong one either sits waiting for a question
    nobody will ask, or goes looking for a payload that was never written.

    ``CREDENTIAL`` waits on neither: it needs the credential repaired before a resume can
    get past the same node. It exists as its own member rather than folding into ``QUOTA``
    because the provider's CLI reports quota and a rejected credential identically, and the
    whole point of separating them is that the operator is told which one to act on.
    """

    DECISION = "decision"
    QUOTA = "quota"
    CREDENTIAL = "credential"


class Budget(BaseModel):
    """Per-node budget: tokens per model row are the measured and capped quantity.

    ``usd`` is an optional derived ceiling that binds only metered rows
    (design 2.1/3.4, decided 2026-08-17).
    """

    model_config = ConfigDict(frozen=True)

    tokens: dict[str, int] = Field(default_factory=dict)
    usd: float | None = None


class Requirement(BaseModel):
    """One shared resource a node consumes, scheduled against the host-level lease store."""

    model_config = ConfigDict(frozen=True)

    resource: str
    amount: float


class NodeSpec(BaseModel):
    """What the planner emits and the coordinator validates and dispatches (design 2.1).

    ``continuation`` is the 3.8 successor counter. ``compact`` asks a long serial
    work node's executor to compress its own history at ``trigger_tokens``, keeping
    the last ``keep_last_n`` turns/messages (design 2.1, section 4 "compact").

    ``brief_ref`` is filled by whoever first PERSISTS this spec to disk (a later task) -
    the dispatcher itself never writes a ``NodeSpec``, only ``brief.md``, ``input.json``
    and ``record.json`` under ``nodes/<node_id>/<hash8>/``. It is empty only in memory,
    while a workflow module still holds a spec it authored. The journal key is computed
    from the brief's CONTENT hash, never from ``brief_ref``, so a spec that moves to a
    different path without its brief text changing is still the same call. The schema's
    ``minLength: 1`` binds a spec once it is on disk, not this in-memory default.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str = Field(min_length=1)
    kind: Kind
    brief_ref: str = ""
    executor: str | None = None
    tier_role: TierRole | None = None
    model: str | None = None
    effort: str | None = None
    knowledge: list[str] = Field(default_factory=list)
    stage_into: str | None = None
    write_set: list[str] = Field(default_factory=list)
    requires: list[Requirement] = Field(default_factory=list[Requirement])
    isolation: Isolation = Isolation.NONE
    deps: list[str] = Field(default_factory=list)
    deadline_s: float
    budget: Budget = Field(default_factory=Budget)
    attempt: int = 0
    continuation: int = 0
    compact: dict[str, int] | None = None


class Tokens(BaseModel):
    """Measured token usage for one dispatch, per field (design 2.2).

    The schema's field name is ``in`` - a Python keyword - so the model attribute is
    ``in_`` with ``in`` kept as the wire alias.

    Example:
        >>> Tokens(**{"in": 1, "out": 2, "cache_read": 3, "reasoning": 0}).model_dump(by_alias=True)
        {'in': 1, 'out': 2, 'cache_read': 3, 'reasoning': 0}
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    in_: int | None = Field(alias="in")
    out: int | None
    cache_read: int | None
    reasoning: int | None


class NodeError(BaseModel):
    """Present when a :class:`ResultRecord` status is anything but done (design 2.2)."""

    model_config = ConfigDict(frozen=True)

    type: ErrorType
    message: str
    transient: bool


class KnowledgeUsed(BaseModel):
    """One knowledge-index dataset a node actually read from, recorded from day one (design 2.2, 3.2)."""

    model_config = ConfigDict(frozen=True)

    dataset: str
    content_hash: str


class SandboxGuarantees(BaseModel):
    """What a sandbox adapter actually enforces for one node - journaled per node (Task 19).

    A port whose adapters differ silently in what they enforce is worse than no port: every
    later claim about a run becomes ambiguous ("was that node contained?"). So a
    :class:`~agentdag.application.kernel.sandbox.Sandbox` adapter DECLARES what it enforces
    (:meth:`~agentdag.application.kernel.sandbox.Sandbox.guarantees`), the coordinator stamps
    that declaration onto every dispatched node's :class:`ResultRecord`
    (:meth:`~agentdag.application.kernel.context.Coordinator._dispatch`), and a run's own
    records can answer the containment question without trusting the adapter's name alone.

    Lives here in the domain, not in ``application/kernel/sandbox.py`` where
    :class:`~agentdag.application.kernel.sandbox.Sandbox` itself is defined, because
    :class:`ResultRecord` (domain) carries one and domain code may never import from the
    application layer (the import-linter layer contract) - the same reason :class:`NodeError`
    and :class:`Tokens` are domain models a port's adapter builds but never defines its own
    copy of.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    adapter: str
    """Which adapter produced this: ``"none"`` | ``"container"`` | ``"vm"``. Free text, like
    :class:`ExecutorKind`'s own ``mcp:<server>/<tool>`` escape hatch - a later adapter names
    itself here without a change to this model."""

    filesystem: bool
    """Whether the node is BLOCKED from reaching paths outside its declared mounts."""

    network_egress: bool
    """Whether the node's outbound network access is restricted to an allowlist."""

    separate_uid: bool
    """Whether the node runs as an operating-system user distinct from the coordinator's own."""


class NodeOutcome(BaseModel):
    """What a node body hands back; the dispatcher completes it into a :class:`ResultRecord`."""

    model_config = ConfigDict(frozen=True)

    status: NodeStatus
    artefact_refs: list[str] = Field(default_factory=list)
    key_facts: dict[str, Any] = Field(default_factory=dict)
    typed_fields: list[str] = Field(default_factory=list)
    tokens: Tokens | None = None
    charged_tokens: dict[str, int] = Field(default_factory=dict)
    executor_used: str
    model_used: str
    effort_used: str
    knowledge_used: list[KnowledgeUsed] = Field(default_factory=list[KnowledgeUsed])
    error: NodeError | None = None

    @model_serializer(mode="wrap")
    def _drop_absent_error(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Omit ``error`` entirely when unset instead of writing it out as ``null``.

        The result-record schema types ``error`` as an object only (no ``null`` in
        its ``type`` list), so a dumped record with no error must not carry the key
        at all - only a nullable field like ``tokens`` may legitimately serialise
        as ``null``.
        """
        data = handler(self)
        if data.get("error") is None:
            data.pop("error", None)
        return data


class ResultRecord(NodeOutcome):
    """Design 2.2: the ONLY thing the coordinator branches on."""

    node_id: str
    attempt: int
    continuation: int = 0
    """Which link of a design 3.8 handover chain produced this record; 0 for a node that
    never handed over. Sits next to :attr:`attempt` because the two are the same KIND of
    counter - both are identity fields (``domain/keys.py``), so incrementing either gives
    a different journal key and therefore a genuine re-run rather than the old record
    served back - but they count different things: ``attempt`` counts tries at the same
    work, ``continuation`` counts handovers that carry work FORWARD. Defaulted so a
    journal line written before 3.8 still validates."""

    input_hash: str
    duration_s: float
    cost_usd: float | None = None
    sandbox: SandboxGuarantees | None = None
    """What sandbox boundary was actually in force for this node (Task 19), stamped by
    :meth:`~agentdag.application.kernel.context.Coordinator._dispatch` from the wired
    :class:`~agentdag.application.kernel.sandbox.Sandbox`'s own :meth:`~agentdag.application.
    kernel.sandbox.Sandbox.guarantees`. Optional and omitted-when-absent (see
    :meth:`_drop_absent_optional_objects`) so a pre-Task-19 journal line - every M2 run's own
    records carry no such field - still round-trips and still validates against
    ``result-record.schema.json``, which lists ``sandbox`` as an optional property, never a
    required-but-nullable one."""

    @model_serializer(mode="wrap")
    def _drop_absent_optional_objects(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Omit ``error``/``sandbox`` entirely when unset, instead of writing them out as ``null``.

        Both fields are typed as an object only in their own schemas (no ``null`` in either
        one's ``type`` list) and neither is in ``required``, so a record carrying neither must
        not carry either KEY at all. This REPLACES (not extends)
        :meth:`NodeOutcome._drop_absent_error`'s own ``@model_serializer`` for
        :class:`ResultRecord` instances: pydantic uses exactly one model-level serializer per
        class, the most-derived one in the MRO, so this method re-does that check for
        ``error`` too rather than only adding ``sandbox``'s (verified directly: a bare
        override with a DIFFERENT method name still shadows the parent's for every
        :class:`ResultRecord` dump, since pydantic looks up the decorator by class, not by
        method name).
        """
        data = handler(self)
        for key in ("error", "sandbox"):
            if data.get(key) is None:
                data.pop(key, None)
        return data


class LockHolder(BaseModel):
    """Identifies the live coordinator process holding a run dir's lock."""

    model_config = ConfigDict(frozen=True)

    host: str
    boot_id: str
    pid: int
    pid_start_time: str


class RunSettings(BaseModel):
    """The kernel settings a run was started with, carried on its ``state.json``.

    Resolved ONCE by ``run start`` from the launching command's config and CLI overrides, and
    read back by every later launch of the same run - the background child, a resume, an
    approve, a retry - instead of each of those re-reading whatever config its own process
    happens to see. A background child is a fresh process that loads config from files alone,
    so without this a value given by env, ``--set`` or ``--profile`` bound only the command
    that typed it; and a resume under a changed config would silently change the run.

    Paths are strings because the block is the on-disk record: ``policy_path`` names the tier
    policy YAML the run's ceilings were read from, and ``credential_file`` names the OAuth
    keyfile the run's nodes authenticate with, or is ``""`` for the credential copy - never a
    token, only where one is read from.
    """

    model_config = ConfigDict(frozen=True)

    policy_path: str = Field(min_length=1)
    parallel: int = Field(ge=1)
    max_turns: int = Field(ge=1)
    default_node_tokens: int = Field(ge=1)
    deny_bash: tuple[str, ...]
    deny_tools: tuple[str, ...]
    notify: str = Field(min_length=1)
    credential_file: str


class RunState(BaseModel):
    """The run's own state.json (design 3.4): mutable, unlike the frozen kernel records above."""

    run_id: str
    workflow: str
    args: dict[str, Any]
    owner: str
    status: RunStatus
    cursor: str | None = None
    cursor_payload_hash: str | None = None
    """The content hash of the payload the run is suspended ON, alongside ``cursor``'s node id.

    A decision is recorded per (node id, payload hash), so the node id alone does not say WHICH
    payload a decider is being asked about: a run that suspends again on a CHANGED payload keeps
    the same ``cursor`` and moves this. ``None`` whenever the run is not suspended.
    """
    suspend_reason: SuspendReason | None = None
    """What the suspended run is waiting FOR, alongside ``cursor``'s node id.

    ``None`` whenever the run is not suspended, and also on a state file written before this
    field existed - which reads correctly either way, since every suspend that predates it was
    an approve node's. A quota suspend writes no payload, so ``cursor_payload_hash`` is ``None``
    for one and an operator cannot tell the two apart from the cursor alone.
    """
    policy_version: str
    settings: RunSettings | None = None
    """What the run was started with (:class:`RunSettings`), read by every later launch.

    ``None`` only on a state file written before this field existed; such a run's next launch
    resolves its settings from the current config, as every launch did then, and says so.
    """
    tokens_by_row: dict[str, int] = Field(default_factory=dict)
    holder: LockHolder | None = None


class ApproveOption(BaseModel):
    """One choice offered to the reviewer of an approve node."""

    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    effect: str = Field(pattern="^(none|external)$")


class ApprovePayload(BaseModel):
    """The typed suspend payload an approve node writes before the coordinator exits (design 3.4)."""

    model_config = ConfigDict(frozen=True)

    text: str
    node_id: str
    artefact_refs: list[str]
    options: list[ApproveOption]
    default: str
    decide_by: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$")
    workflow: str
    run_id: str


class Decision(BaseModel):
    """A decision recorded against an approve node, mirroring the approve_decision journal line.

    ``payload_hash`` is the content hash of the payload this decision was made FOR, and it is
    half the decision's IDENTITY (design 3.4's binding, the idempotency key D2 took from DBOS's
    ``send(..., idempotency_key)``): a decision is recorded per (node id, payload hash), not per
    node id, so a human approving a push list has that approval bound to that exact list and a
    CHANGED list (a retry turning a failed repo into a passed one, or a worktree edited by hand
    between the suspend and the resume) simply does not match - the run suspends again on the
    new payload rather than applying an approval nobody gave.
    It is REQUIRED - a decision that names no payload has half an identity and cannot be
    built, let alone filed under :meth:`~agentdag.application.kernel.ports.RunDir.write_decision`
    (which names the file from it) or carried onto the journal's ``approve_decision`` line.
    There is no legacy, hash-less shape to fall back to.
    """

    model_config = ConfigDict(frozen=True)

    node_id: str
    decision: str
    reason: str = ""
    by: str
    token_id: str
    payload_hash: str = Field(min_length=1)


class RetryGrant(BaseModel):
    """An operator granting ONE more attempt to a failed node (``agentdag run retry``).

    Written as ``retries/<node_id>.<hash8(key)>.json`` and folded into the journal as a
    :class:`~agentdag.domain.journal.RetryGrantLine` by the next launch, the way an approve
    :class:`Decision` is folded from ``decisions/``. Carries no ``at``: the timestamp is
    stamped by whatever FOLDS it, from the injected clock at that moment (design 3.3), never
    by the CLI process, which has no coordinator clock to read.

    ``key`` is the journal key of the FAILED attempt this grant answers for, and it is the
    grant's self-limiting half. Because the attempt it authorises runs under ``attempt + 1`` - an
    identity field - that attempt lands on a DIFFERENT key, so the grant can never be matched
    twice. One grant, one attempt, with no counter to keep and no way for an unattended run to
    loop on it.

    ``node_id`` names the file and is the other HALF of the match the coordinator makes. A
    journal key carries no node id (design 3.2's identity table), so two nodes whose work is
    identical share one key; matching the key alone would run the authorised attempt once PER
    twin - one grant buying N dispatches and N charges - because a freshly granted key is one
    the journal does not hold yet, so nothing serves the retried record to the second node.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str = Field(min_length=1)
    key: str = Field(min_length=1)
    reason: str = ""
    by: str = Field(min_length=1)
    token_id: str = Field(min_length=1)


class CancelIntent(BaseModel):
    """A whole-run cancel request (design 3.4, O25), written as ``decisions/_run.cancel.json``.

    Written by ``run cancel`` (temp+rename, the same write discipline
    :meth:`~agentdag.application.kernel.ports.RunDir.write_decision` uses for an approve
    decision) and later folded into the journal as a
    :class:`~agentdag.domain.journal.CancelRequestedLine` by whichever process holds the
    run's lock next. Carries no ``at``: like a :class:`Decision`'s own intent file, the
    timestamp is stamped by whatever FOLDS this into the journal, read from the injected
    clock at that moment (design 3.3) - never written by the CLI process that only wrote
    the intent, which has no coordinator clock to read.

    ``node_id`` is always ``None`` here (a whole-run cancel); the per-node cancel intent
    reserved as ``decisions/<node_id>.cancel.json`` is a distinct, not-yet-built shape
    (design 3.1) this model does not cover.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(min_length=1)
    node_id: None = None
    by: str = Field(min_length=1)
    token_id: str = Field(min_length=1)
