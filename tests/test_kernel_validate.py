"""RED/GREEN tests for whole-spec validation (design 2.4).

The rules here REFUSE a planner-emitted spec with reasons. Which of them 2.3 rule 4 would
rather CLAMP is an open question for exactly one field (``tier_role``); every rule tested here
is one both design sections state the same way, so none of them turns on that answer.

Every test builds a real ``NodeSpec`` and a real ``RunLimits``; nothing is patched, because
the unit under test is a pure function over two typed values.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Any

from agentdag.adapters.kernel.policy_yaml import load_policy
from agentdag.domain.models import Budget, Isolation, Kind, NodeSpec, Requirement, TierRole
from agentdag.domain.policy import RunLimits
from agentdag.domain.validate import SpecContext, validate_spec


def limits(**over: Any) -> RunLimits:
    """Build run limits whose planner allowlist matches the shipped table plus decision 8."""
    base: dict[str, Any] = {
        "tokens_per_row": {"sonnet": 8_000_000},
        "deadline_ceiling_s": 5400.0,
        "per_kind_ceiling": {"work": TierRole.DEEP},
        "planner_kinds": [Kind.WORK, Kind.GATE, Kind.STAGE, Kind.APPROVE],
        "top_role_budget_floor": 0.05,
    }
    base.update(over)
    return RunLimits(**base)


def spec(**over: Any) -> NodeSpec:
    """Build a minimal spec that passes every rule, so one override isolates one rule."""
    base: dict[str, Any] = {
        "node_id": "w_one",
        "kind": Kind.WORK,
        "executor": "claude",
        "isolation": Isolation.NONE,
        "deadline_s": 60.0,
        "budget": Budget(),
    }
    base.update(over)
    return NodeSpec(**base)


def test_refuses_a_kind_outside_the_planner_allowlist() -> None:
    """``apply`` is never planner-emitted (2.4, and decision 8 keeps it out)."""
    reasons = validate_spec(spec(kind=Kind.APPLY), limits=limits()).reasons

    assert any("apply" in reason for reason in reasons), reasons


def test_admits_stage_and_approve_since_decision_8() -> None:
    """Decision 8 put ``stage`` and ``approve`` inside the allowlist; ``apply`` stays out."""
    for kind in (Kind.STAGE, Kind.APPROVE):
        reasons = validate_spec(spec(kind=kind, executor="code"), limits=limits()).reasons

        assert not any("planner-emittable" in reason for reason in reasons), (kind, reasons)


def test_refuses_a_code_kind_carrying_a_model_executor() -> None:
    """A ``gate`` runs code, so naming a model executor is a spec error (design 2.1)."""
    reasons = validate_spec(spec(kind=Kind.GATE, executor="claude"), limits=limits()).reasons

    assert any("executor" in reason for reason in reasons), reasons


def test_shipped_policy_allowlist_matches_decision_8() -> None:
    """The SHIPPED table is what binds a real run, so decision 8 is asserted against it.

    The fixture above proves the rule reads an allowlist; only this proves the allowlist
    the coordinator actually loads says what the user decided.
    """
    loaded = load_policy(Path(str(files("agentdag.policy") / "tier-policy.yaml")))
    allowed = set(loaded.table.run_limits.planner_kinds)

    assert Kind.STAGE in allowed, allowed
    assert Kind.APPROVE in allowed, allowed
    assert Kind.APPLY not in allowed, allowed


def test_refuses_a_role_on_a_kind_that_resolves_no_model_row() -> None:
    """2.1 and 2.3 rule 1 agree: code and fan-out kinds carry a null ``tier_role``.

    This is NOT the ``per_kind_ceiling`` rule, which is the contested one - it is the
    "resolves no model row at all" rule, which both design sections state the same way.
    """
    for kind, executor in ((Kind.GATE, "code"), (Kind.MAP, None)):
        reasons = validate_spec(spec(kind=kind, executor=executor, tier_role=TierRole.DEEP), limits=limits()).reasons

        assert any("tier_role" in reason for reason in reasons), (kind, reasons)


def test_admits_a_model_kind_that_leaves_the_executor_to_the_policy_row() -> None:
    """A model-executed spec may omit ``executor``: the resolved ROW supplies it.

    Graph A's shipped ``w_migrate`` does exactly this - ``tier_role`` and ``model``, no
    ``executor`` - and ``LoadedPolicy.resolve`` returns ``ResolvedRow(executor=row.executor)``.
    A rule demanding one here refuses a spec that runs today.
    """
    reasons = validate_spec(
        spec(kind=Kind.WORK, executor=None, tier_role=TierRole.STANDARD, model="sonnet"),
        limits=limits(),
    )

    assert not any("executor" in reason for reason in reasons), reasons


def test_still_refuses_a_model_kind_that_claims_the_code_executor() -> None:
    """Omitting the executor is fine; naming the CODE runner for a model kind is not."""
    reasons = validate_spec(spec(kind=Kind.WORK, executor="code"), limits=limits()).reasons

    assert any("executor" in reason for reason in reasons), reasons


def test_refuses_a_tier_role_above_its_kinds_ceiling() -> None:
    """DECISIONS.md item 9: this REFUSES rather than clamping.

    Clamping would be silent this milestone, and shipped ``per_kind_ceiling`` is
    ``{work: deep}`` while 2.3 rule 1 gives judge panels ``top`` - so a silently
    downgraded judge would return a verdict the coordinator branches on.
    """
    reasons = validate_spec(spec(kind=Kind.WORK, tier_role=TierRole.TOP), limits=limits()).reasons

    assert any("ceiling" in reason for reason in reasons), reasons


def test_admits_a_tier_role_at_or_below_its_kinds_ceiling() -> None:
    """The ceiling is inclusive: ``deep`` is allowed where the ceiling IS ``deep``."""
    for role in (TierRole.MECHANICAL, TierRole.STANDARD, TierRole.DEEP):
        reasons = validate_spec(spec(kind=Kind.WORK, tier_role=role), limits=limits()).reasons

        assert not any("ceiling" in reason for reason in reasons), (role, reasons)


def test_refuses_a_role_on_a_model_kind_with_no_ceiling_entry() -> None:
    """An absent ceiling fails CLOSED (user, 2026-08-22): unconfigured is not uncapped.

    Otherwise the safety property is config-shaped rather than code-shaped, and deleting one
    line from per_kind_ceiling silently removes the cap for that kind with no error anywhere.
    """
    uncapped = limits(per_kind_ceiling={})

    reasons = validate_spec(spec(kind=Kind.WORK, tier_role=TierRole.MECHANICAL), limits=uncapped).reasons

    assert any("ceiling" in reason for reason in reasons), reasons


def test_refuses_a_write_set_entry_that_escapes_the_run_root() -> None:
    """Containment is lexical and must survive traversal, which Path.relative_to does not.

    Measured in the insertion review: ``Path('/runs/r1/wt/../../../home/victim')
    .relative_to('/runs/r1')`` returns a path and does NOT raise.
    """
    for escape in ("../../etc/passwd", "/etc/passwd", "wt/../../../etc", "wt/x/../../../../y"):
        reasons = validate_spec(spec(write_set=[escape]), limits=limits()).reasons

        assert any("write_set" in reason for reason in reasons), (escape, reasons)


def test_refuses_a_write_set_entry_whose_first_segment_is_a_glob() -> None:
    """``write_set: ["*"]`` is inside the root and still covers all of it.

    Insertion review finding 6: Coordinator.scan adds every OTHER node's declared write sets to
    its allow-list and fnmatch's ``*`` spans ``/``, so one such entry makes every later scan in
    the run pass - hand-authored nodes included.
    """
    for wildcard in ("*", "**", "*.json"):
        reasons = validate_spec(spec(write_set=[wildcard]), limits=limits()).reasons

        assert any("write_set" in reason for reason in reasons), (wildcard, reasons)


def test_admits_the_write_set_shapes_graph_a_actually_declares() -> None:
    """The shipping graph is the control: these three must not be refused."""
    for good in ("wt/repo-one/**", "manifest/m_fleet.json", "intents/push/*.json", "wt/../other/**"):
        reasons = validate_spec(spec(write_set=[good]), limits=limits()).reasons

        assert not any("write_set" in reason for reason in reasons), (good, reasons)


def test_refuses_a_dep_that_names_no_admitted_node() -> None:
    """2.4: deps must name nodes that exist."""
    context = SpecContext(graph={"g_one": ()})

    reasons = validate_spec(spec(deps=["g_one", "g_missing"]), limits=limits(), context=context).reasons

    assert any("g_missing" in reason for reason in reasons), reasons
    assert not any("g_one" in reason for reason in reasons), reasons


def test_refuses_a_spec_that_would_close_a_cycle() -> None:
    """2.4: deps must leave the graph acyclic. w_new -> b -> a -> w_new is a cycle."""
    context = SpecContext(graph={"a": ("w_new",), "b": ("a",)})

    reasons = validate_spec(spec(node_id="w_new", deps=["b"]), limits=limits(), context=context).reasons

    assert any("cycle" in reason for reason in reasons), reasons


def test_refuses_a_spec_that_depends_on_itself() -> None:
    """The one-node cycle, which a naive reachability walk misses."""
    reasons = validate_spec(spec(node_id="w_self", deps=["w_self"]), limits=limits(), context=SpecContext()).reasons

    assert any("cycle" in reason or "itself" in reason for reason in reasons), reasons


def test_admits_a_dag_and_says_nothing_about_deps_with_no_context() -> None:
    """A caller with no graph to check against gets no dep verdict rather than a false one."""
    context = SpecContext(graph={"a": (), "b": ("a",)})

    assert not any("dep" in r for r in validate_spec(spec(deps=["a", "b"]), limits=limits(), context=context))
    assert not any("dep" in r for r in validate_spec(spec(deps=["anything"]), limits=limits()))


def test_refuses_a_requirement_naming_an_unregistered_resource() -> None:
    """2.4: requires must name only registered resources."""
    context = SpecContext(resources={"bmk-tool-env": 1.0})

    reasons = validate_spec(
        spec(requires=[Requirement(resource="ghost", amount=1.0)]), limits=limits(), context=context
    ).reasons

    assert any("ghost" in reason for reason in reasons), reasons


def test_refuses_a_requirement_asking_more_than_the_registered_capacity() -> None:
    """2.4: amounts must be under their capacity. Asking for 3 of a capacity-1 mutex never runs."""
    context = SpecContext(resources={"bmk-tool-env": 1.0})

    reasons = validate_spec(
        spec(requires=[Requirement(resource="bmk-tool-env", amount=3.0)]), limits=limits(), context=context
    ).reasons

    assert any("capacity" in reason for reason in reasons), reasons


def test_admits_a_requirement_at_exactly_the_registered_capacity() -> None:
    """A mutex of capacity 1 is taken by asking for exactly 1, so the bound is inclusive."""
    context = SpecContext(resources={"bmk-tool-env": 1.0})

    reasons = validate_spec(
        spec(requires=[Requirement(resource="bmk-tool-env", amount=1.0)]), limits=limits(), context=context
    ).reasons

    assert not any("capacity" in reason or "bmk-tool-env" in reason for reason in reasons), reasons


def test_names_the_rules_it_could_not_run_for_want_of_context() -> None:
    """An empty verdict must not read the same as a checked one (user, 2026-08-22).

    Silence was the defect: a caller that forgets the graph got a clean tuple that meant
    nothing, the same shape as the fail-open ceiling bug fixed earlier today.
    """
    verdict = validate_spec(spec(deps=["anything"], requires=[Requirement(resource="r", amount=1.0)]), limits=limits())

    assert verdict.reasons == (), verdict
    assert "deps" in verdict.skipped, verdict
    assert "requires" in verdict.skipped, verdict
    assert not verdict.complete, verdict


def test_skips_nothing_when_the_caller_supplied_what_the_rules_need() -> None:
    """Supplying the context removes the rule from skipped, whether or not it then refuses."""
    context = SpecContext(graph={"a": ()}, resources={"r": 1.0})

    verdict = validate_spec(
        spec(deps=["a"], requires=[Requirement(resource="r", amount=1.0)]), limits=limits(), context=context
    )

    assert verdict.skipped == (), verdict
    assert verdict.complete, verdict
    assert verdict.ok, verdict
