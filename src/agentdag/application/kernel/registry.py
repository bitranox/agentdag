"""The op registry: named ops the composition root wires, and what dispatching one needs.

A :class:`~agentdag.domain.plan.Plan` (Task 29) names its steps by OP NAME
(:attr:`~agentdag.domain.plan.Entry.op`), never by a concrete dispatch mechanism - the
composition root REGISTERS what each name means (:class:`OpSpec`), and :mod:`.plan_validate`
refuses a plan that names an op nobody registered, by ABSENCE rather than a closed enum the
domain layer would have to know every op to define.

:class:`OpRegistry` is built once, at composition time
(:func:`~agentdag.composition.kernel.build_op_registry`), and never needs a live
:class:`~agentdag.application.kernel.context.Coordinator` while it is built - only the BODY a
registered :class:`OpSpec` builds needs one, and it needs it only once dispatch actually
happens (a later task). That is why :attr:`OpSpec.build` takes a :class:`PlanContext` as a
SEPARATE, later-supplied argument rather than closing over a coordinator at registration time:
a composition root whose op set needed a running coordinator to even construct would have
nothing to validate a plan against before a run starts.

Contents:
    * :class:`UnregisteredOpError` - a plan named an op nobody registered.
    * :class:`PlanContext` - what a registered op's body needs beyond the ``Entry`` itself.
    * :data:`Body` - the fully-bound async call an ``OpSpec.build`` hands back.
    * :class:`OpSpec` - one registered op: its args model, its output contract, whether it
      can change run state, and how to build its body.
    * :class:`OpRegistry` - op name -> :class:`OpSpec`, refusing a duplicate registration
      and an absent lookup by two different, typed signals.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...domain.kernel_errors import KernelError
from ...domain.models import Decision, ResultRecord

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import BaseModel

    from ...domain.plan import Entry
    from .context import Coordinator
    from .subtree import StopScope

__all__ = ["Body", "OpRegistry", "OpSpec", "PlanContext", "UnregisteredOpError"]


class UnregisteredOpError(KernelError):
    """A plan names an op that nothing registered - refused by absence, not a closed enum."""


@dataclass(frozen=True, slots=True)
class PlanContext:
    """What a registered op's :attr:`OpSpec.build` needs beyond the ``Entry`` itself.

    Not a type Task 29 shipped: :class:`~agentdag.domain.plan.Plan`/:class:`~agentdag.domain.
    plan.Entry` are pure planner output that names an op, never resolves one, so nothing
    upstream of this task had a reason to define what "the rest of what a body needs" is.
    Kept to exactly the two fields the ops this task registers actually read: ``work`` and
    ``gate:make-test`` are the only ones that take a ``cwd``
    (:meth:`~agentdag.application.kernel.context.Coordinator.work`,
    :meth:`~agentdag.application.kernel.context.Coordinator.gate`), and every op needs the
    coordinator itself to dispatch through. A field only a later task turns out to need
    belongs to that task, not guessed at here.
    """

    co: Coordinator
    cwd: Path

    stopping: StopScope | None = None
    """The scope of the PLAN whose entries these bodies belong to, or None outside one.

    Per PLAN, not per run: the execute loop builds a context carrying the scope of the pass
    it is running, so a body closed over this one asks about ITS subtree. A run-wide scope
    would let one plan's refutation notify a sibling's nodes, which is the "re-plan wrong
    rather than late" direction the design rejects.

    An op body turns it into the per-node predicate ``Coordinator.work`` takes, so the
    executor is handed a plain callable and never learns this type exists."""


Body = Callable[[], Awaitable[ResultRecord | Decision]]
"""The fully-bound async call an :attr:`OpSpec.build` hands back: no more arguments to give it,
just ``await body()``.

Deliberately NOT :data:`agentdag.application.kernel.dispatch.Body`
(``Callable[[Path], Awaitable[NodeOutcome]]``, "what ``_dispatch`` takes" per the shape a node
directory-scoped body has): every op this task registers with a real body
(:func:`~agentdag.composition.kernel.build_op_registry`) is wired straight to a
:class:`~agentdag.application.kernel.context.Coordinator` PRIMITIVE (``work``, ``gate``,
``scan``, ``reduce``, ``approve``) - methods that already call ``_dispatch`` internally and
return a :class:`~agentdag.domain.models.ResultRecord` (or, for ``approve``, a
:class:`~agentdag.domain.models.Decision`), not the raw per-node-directory body those methods
build and pass to ``_dispatch`` themselves. This is the seam ``build`` actually needs: a
zero-argument thunk over an already-bound call, matching what every one of those primitives
already produces. A generic dispatcher that hands an ``OpSpec`` a node directory and expects
``dispatch.Body`` back is a different design that a later task can choose instead, once it is
clear the registry needs it.
"""


@dataclass(frozen=True, slots=True)
class OpSpec:
    """One registered op: how a plan entry naming it validates, and how its body is built.

    Attributes:
        name: The op name a :class:`~agentdag.domain.plan.Entry` names via its own ``op``
            field - the registry's key, matched exactly (``"gate:make-test"`` and a
            hypothetical ``"gate:other-check"`` are two different, independently registered
            ops; there is no prefix-matching mechanism).
        args_model: Validates :attr:`~agentdag.domain.plan.Entry.args` for an entry naming
            this op. ``extra="forbid"`` so an arg this op does not understand refuses the
            whole plan (decision 1) rather than being silently ignored.
        output_contract: The ``key_facts`` names this op's body can ever emit. A condition
            (``holds_while``, ``done_when``, an entry's own ``acceptance``) may reference a
            field of an entry naming this op only when that field is a member here - the
            ceiling :func:`~agentdag.application.kernel.plan_validate.validate_plan` checks
            every ``FieldRef`` against.
        can_change_state: Whether a record from this op can be the reason a ROOT plan is
            done. Set from WHAT THE BODY DOES, never from the op's NAME: ``True`` when a
            record this op produces can distinguish "this work FINISHED" from "this work
            NEVER STARTED", ``False`` when the op only OBSERVES - its reading is the same
            before the work and after it, so no value it can report tells the two apart.

            A ``gate:*`` op comes out ``False`` under that test (``make test`` is green
            before a refactor and green after, so ``rc == 0`` proves nothing about the
            work), and so does the read-only ``scan``, which carries no ``gate:`` prefix
            at all: a clean isolation scan reads identically whether anything ran. The
            prefix is a CONSEQUENCE of the test on those bodies, never the test itself -
            keying the flag on the name is what let a read-only op register as ``True``.
            ``reduce:count`` is the other direction: it dispatches nothing itself, yet its
            count is 0 with nothing done and N once N nodes passed, so the record does
            distinguish the two and the flag is ``True``.

            Each registration in
            :func:`~agentdag.composition.kernel.build_op_registry` records the one-line
            reason for its own value beside the flag. See
            :func:`~agentdag.application.kernel.plan_validate.validate_plan`'s root rule
            (decision 4) for what the flag is then used for.
        build: Given the entry naming this op and the :class:`PlanContext` to dispatch
            through, produce this call's :data:`Body`. NOT invoked by validation:
            :func:`~agentdag.application.kernel.plan_validate.validate_plan` checks an
            entry's op, args and referenced fields against this ``OpSpec`` without ever
            calling ``build`` - wiring ``build`` into an actual dispatch is a later task.
    """

    name: str
    args_model: type[BaseModel]
    output_contract: frozenset[str]
    can_change_state: bool
    build: Callable[[Entry, PlanContext], Body]


class OpRegistry:
    """Op name -> :class:`OpSpec`, built once at composition time.

    Mutable by design: :func:`~agentdag.composition.kernel.build_op_registry` calls
    :meth:`register` once per op while building the registry, then hands out an object
    nothing else mutates. There is no ``unregister`` - an op, once shipped, stays known for
    the life of the process; retiring one is a composition-root edit, not a runtime action.
    """

    def __init__(self) -> None:
        """Start empty; :meth:`register` is the only way to add an op."""
        self._ops: dict[str, OpSpec] = {}

    def register(self, op: OpSpec) -> None:
        """Add ``op``, keyed by its own name.

        Args:
            op: The op to register.

        Raises:
            KernelError: ``op.name`` is already registered - two ops cannot share a name,
                because a plan entry naming it could then mean either one.
        """
        if op.name in self._ops:
            raise KernelError(f"op {op.name!r} is already registered")
        self._ops[op.name] = op

    def get(self, name: str) -> OpSpec:
        """Return the op registered as ``name``.

        Args:
            name: The op name to look up, exactly as an entry's own ``op`` field carries it.

        Returns:
            The registered :class:`OpSpec`.

        Raises:
            UnregisteredOpError: nothing registered ``name`` - a plan entry naming it is refused
                by absence (:func:`~agentdag.application.kernel.plan_validate.validate_plan`),
                never by a closed enum this registry would have to be extended to accept.
        """
        try:
            return self._ops[name]
        except KeyError as exc:
            raise UnregisteredOpError(f"no op named {name!r} is registered") from exc

    def names(self) -> frozenset[str]:
        """Return every registered op name."""
        return frozenset(self._ops)
