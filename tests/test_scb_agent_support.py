"""Tests for the pure half of the SlopCodeBench agent type.

The agent body ships as data (``deploy/slopcodebench/agentdag_scb_agent/*.py.tmpl``) because
importing it would drag the harness package ``slop_code`` into pyright's walk and into
pytest's collection, and this repo does not have it. So nothing in this repo can import the
agent module - which left its launch command, its token and cost arithmetic and its terminal
line reader with no automated coverage at all.

``support.py.tmpl`` is the half that imports nothing from ``slop_code``. This module executes
that template and exercises it directly, which is what makes those helpers testable without
either relaxing the collection rules or hand-building a third-party API surface that drifts.
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.os_agnostic

SUPPORT_TEMPLATE = (
    Path(__file__).resolve().parents[1] / "deploy" / "slopcodebench" / "agentdag_scb_agent" / "support.py.tmpl"
)

SUPPORT_MODULE_NAME = "agentdag_scb_agent_support"

RUNS_DIR = "/tmp/agent_home/agentdag-runs"
KEYFILE = "/tmp/agent_home/.agentdag/oauth-token"
POLICY_FILE = "/tmp/agent_home/agentdag-policy.yaml"


def load_support() -> ModuleType:
    """Execute the shipped support template and hand it back as a module.

    Executed rather than imported: the file is deliberately not named ``.py``, and giving it
    that name is what the layout rule forbids. It is registered in ``sys.modules`` first
    because ``dataclasses`` resolves a class's string annotations through
    ``sys.modules[cls.__module__]``, which is how the real import would supply them.
    """
    source = SUPPORT_TEMPLATE.read_text(encoding="utf-8")
    module = ModuleType(SUPPORT_MODULE_NAME)
    module.__file__ = str(SUPPORT_TEMPLATE)
    sys.modules[SUPPORT_MODULE_NAME] = module
    exec(compile(source, str(SUPPORT_TEMPLATE), "exec"), module.__dict__)  # noqa: S102 - the template is repo source
    return module


@pytest.fixture(name="support", scope="module")
def support_fixture() -> ModuleType:
    """The support template, executed once for this module."""
    return load_support()


def a_spec(support: ModuleType, **overrides: object) -> object:
    """A launch spec with the values the arm actually runs, minus whatever a test varies."""
    fields: dict[str, object] = {
        "goal": "build it",
        "workflow": "plan-goal",
        "workspace": "/workspace",
        "runs_dir": RUNS_DIR,
        "keyfile": KEYFILE,
        "settings": {"kernel.permission_mode": "bypassPermissions", "kernel.deny_tools": []},
    }
    fields.update(overrides)
    return support.LaunchSpec(**fields)


def heredoc_body(command: str, *, marker: str) -> str:
    """The launcher the command writes, taken back out of its heredoc."""
    after_opener = command.split(f"<<'{marker}'\n", 1)[1]
    return after_opener.rsplit(f"\n{marker}\n", 1)[0]


# --- setting_argument -------------------------------------------------------------------


def test_setting_argument_passes_a_string_through(support: ModuleType) -> None:
    assert support.setting_argument("bypassPermissions") == "bypassPermissions"


@pytest.mark.parametrize(
    ("value", "rendered"),
    [
        ([], "[]"),
        ([".venv/bin/python", "-m", "pytest", "-q"], '[".venv/bin/python","-m","pytest","-q"]'),
        (100, "100"),
        (True, "true"),
        (None, "null"),
        ({"a": 1}, '{"a":1}'),
    ],
)
def test_setting_argument_writes_anything_else_as_compact_json(
    support: ModuleType, value: object, rendered: str
) -> None:
    """An explicit empty list is how the full tool set reaches agentdag; a blank is refused."""
    assert support.setting_argument(value) == rendered


# --- launch_argv ------------------------------------------------------------------------


def test_launch_argv_is_the_documented_command(support: ModuleType) -> None:
    assert support.launch_argv(a_spec(support)) == [
        "agentdag",
        "--set",
        "kernel.permission_mode=bypassPermissions",
        "--set",
        "kernel.deny_tools=[]",
        "--set",
        f"credentials.claude_oauth_token_file={KEYFILE}",
        "run",
        "start",
        "plan-goal",
        "--arg",
        "goal=build it",
        "--arg",
        "workspace=/workspace",
        "--runs",
        RUNS_DIR,
        "--foreground",
    ]


def test_launch_argv_emits_no_policy_when_the_arm_names_none(support: ModuleType) -> None:
    """Unset means the run takes agentdag's shipped tier table, so no flag at all."""
    assert "--policy" not in support.launch_argv(a_spec(support))


def test_launch_argv_emits_the_policy_the_arm_names(support: ModuleType) -> None:
    argv = support.launch_argv(a_spec(support, policy=POLICY_FILE))
    assert argv[argv.index("--policy") + 1] == POLICY_FILE
    assert argv.index("--policy") > argv.index("start"), "--policy is an option of run start"
    assert argv[-1] == "--foreground"


def test_launch_argv_puts_the_agent_owned_setting_after_the_config_ones(support: ModuleType) -> None:
    """The keyfile is the agent's own; a config repeating it would be a silent second --set."""
    argv = support.launch_argv(a_spec(support))
    owned = argv.index(f"credentials.claude_oauth_token_file={KEYFILE}")
    assert owned > argv.index("kernel.deny_tools=[]")
    assert owned < argv.index("run")


# --- launch_command ---------------------------------------------------------------------


def test_launch_command_is_pinned_character_for_character(support: ModuleType) -> None:
    """The whole text, so any change to how a checkpoint is launched has to be deliberate."""
    launched = (
        "agentdag"
        " --set kernel.permission_mode=bypassPermissions"
        " --set 'kernel.deny_tools=[]'"
        f" --set credentials.claude_oauth_token_file={KEYFILE}"
        " run start plan-goal"
        " --arg 'goal=build it'"
        " --arg workspace=/workspace"
        f" --runs {RUNS_DIR}"
        " --foreground"
    )
    expected = "\n".join(
        [
            "set -eu",
            f"mkdir -p {RUNS_DIR}",
            f"cat > {RUNS_DIR}/.launch.sh <<'AGENTDAG_LAUNCH_EOF'",
            f"echo $$ > {RUNS_DIR}/.launch.pgid",
            f"exec {launched}",
            "AGENTDAG_LAUNCH_EOF",
            f"exec setsid --wait /bin/sh {RUNS_DIR}/.launch.sh",
            "",
        ]
    )
    assert support.launch_command(a_spec(support)) == expected


def test_launch_command_waits_for_the_process_it_launched(support: ModuleType) -> None:
    """Measured on slop-code:python3.12 under docker exec, util-linux setsid 2.41.5.

    The shell running this is already a process-group leader, so setsid forks. Bare, the
    parent returned in 2.05s with an EMPTY stdout while the child ran on; with --wait it
    returned in 5.06s carrying the child's own output. An empty stdout is read as "printed
    no terminal line", which the harness answers by retrying - so the bare form starts up to
    three detached, unharvested coordinator runs in one workspace.
    """
    command = support.launch_command(a_spec(support))
    assert "exec setsid --wait /bin/sh " in command
    assert "\nexec setsid /bin/sh " not in command


def test_launch_command_delivers_the_argv_verbatim_through_the_heredoc(support: ModuleType) -> None:
    """A goal is a checkpoint prompt: newlines, quotes, dollars and backticks all occur."""
    spec = a_spec(support, goal='line one\nit\'s "quoted" $HOME `date` \\ end')
    body = heredoc_body(support.launch_command(spec), marker=support.HEREDOC_MARKER)
    _, _, launched = body.partition("\nexec ")
    assert shlex.split(launched) == support.launch_argv(spec)


def test_launch_command_records_the_process_group_it_is_killed_by(support: ModuleType) -> None:
    body = heredoc_body(support.launch_command(a_spec(support)), marker=support.HEREDOC_MARKER)
    assert body.splitlines()[0] == f"echo $$ > {RUNS_DIR}/{support.PGID_FILE_NAME}"


def test_launch_command_refuses_a_goal_carrying_the_heredoc_delimiter(support: ModuleType) -> None:
    """Otherwise the heredoc ends early and the rest of the prompt runs as shell."""
    spec = a_spec(support, goal=f"do it\n{support.HEREDOC_MARKER}\nrm -rf /")
    with pytest.raises(ValueError, match=support.HEREDOC_MARKER):
        support.launch_command(spec)


# --- terminal_line ----------------------------------------------------------------------


def test_terminal_line_reads_a_finished_run(support: ModuleType) -> None:
    assert support.terminal_line("credential: keyfile\nrun 20260906T0101Z-ab12 done\n") == (
        "20260906T0101Z-ab12",
        "done",
    )


def test_terminal_line_reads_a_suspended_run_with_its_node(support: ModuleType) -> None:
    run_id, outcome = support.terminal_line("run r1 suspended at approve-plan\n")
    assert (run_id, outcome) == ("r1", "suspended at approve-plan")
    assert outcome[len(support.SUSPENDED_PREFIX) :] == "approve-plan"


def test_terminal_line_reads_any_other_terminal_status(support: ModuleType) -> None:
    assert support.terminal_line("run r1 failed\n") == ("r1", "failed")


def test_terminal_line_ignores_an_advisory_that_precedes_the_outcome(support: ModuleType) -> None:
    """``run <id>: ...`` is an advisory, not an outcome; the colon is what tells them apart."""
    stdout = "run r1: state.json carries no settings block\nrun r1 done\n"
    assert support.terminal_line(stdout) == ("r1", "done")
    assert support.terminal_line("run r1: state.json carries no settings block\n") is None


@pytest.mark.parametrize("stdout", ["", "\n", "credential: keyfile\n", "running\n"])
def test_terminal_line_reports_no_line_rather_than_guessing(support: ModuleType, stdout: str) -> None:
    assert support.terminal_line(stdout) is None


# --- tokens_of and spend_of -------------------------------------------------------------


def test_tokens_of_takes_the_cache_components_out_of_the_input_total(support: ModuleType) -> None:
    """agentdag's ``in`` is the input TOTAL; the harness's ``input`` is the uncached part."""
    tokens = support.tokens_of({"in": 1000, "out": 40, "cache_read": 800, "cache_write": 100, "reasoning": 7})
    assert (tokens.input, tokens.output, tokens.cache_read, tokens.cache_write, tokens.reasoning) == (
        100,
        40,
        800,
        100,
        7,
    )


def test_tokens_of_clamps_rather_than_reporting_a_negative_input(support: ModuleType) -> None:
    assert support.tokens_of({"in": 10, "cache_read": 900}).input == 0


@pytest.mark.parametrize("block", [{}, {"in": None, "out": None, "cache_read": None}])
def test_tokens_of_reads_a_missing_or_null_count_as_zero(support: ModuleType, block: dict[str, object]) -> None:
    assert support.tokens_of(block) == support.Tokens()


def test_spend_of_totals_the_cost_and_counts_what_carried_one(support: ModuleType) -> None:
    records = [
        {"cost_usd": 0.25, "tokens": {"in": 100, "out": 10}},
        {"cost_usd": None, "tokens": {"in": 5, "out": 1}},
        {"cost_usd": 0.75, "tokens": {"in": 200, "out": 20}},
    ]
    spend = support.spend_of(records)
    assert spend.cost_usd == pytest.approx(1.0)
    assert spend.costed_records == 2
    assert (spend.tokens.input, spend.tokens.output) == (305, 31)


def test_spend_of_reports_zero_costed_records_rather_than_a_silent_zero_cost(support: ModuleType) -> None:
    """The caller refuses this: a checkpoint costed at zero is measurement corruption."""
    spend = support.spend_of([{"cost_usd": None, "tokens": {"in": 5}}, {"tokens": {"in": 5}}])
    assert spend.costed_records == 0
    assert spend.cost_usd == 0.0
    assert spend.tokens.input == 10


def test_spend_of_on_no_records_is_empty_and_uncosted(support: ModuleType) -> None:
    assert support.spend_of([]) == support.Spend()


def test_spend_of_ignores_a_tokens_field_that_is_not_a_mapping(support: ModuleType) -> None:
    spend = support.spend_of([{"cost_usd": 1.0, "tokens": None}, {"cost_usd": 1.0, "tokens": "nope"}])
    assert spend.tokens == support.Tokens()
    assert spend.costed_records == 2


# --- parsed_record_list ------------------------------------------------------------------


def test_parsed_record_list_reads_a_json_array_of_dicts(support: ModuleType) -> None:
    records = [{"cost_usd": 1.0}, {"cost_usd": 2.0}]
    assert support.parsed_record_list(json.dumps(records)) == records


def test_parsed_record_list_returns_none_for_a_non_array_line(support: ModuleType) -> None:
    """A JSON object, not a JSON array, is what a non-array line looks like on the wire."""
    assert support.parsed_record_list(json.dumps({"cost_usd": 1.0})) is None


def test_parsed_record_list_returns_none_for_a_line_with_no_leading_bracket(support: ModuleType) -> None:
    """Fails the ``startswith("[")`` guard, so ``json.loads`` is never called."""
    assert support.parsed_record_list("credential: keyfile") is None


def test_parsed_record_list_returns_none_for_malformed_json_after_the_bracket_guard(
    support: ModuleType,
) -> None:
    """Passes the ``startswith("[")`` guard and reaches ``json.loads``, which raises
    ``json.JSONDecodeError`` - this is the only test that exercises that ``except`` branch."""
    assert support.parsed_record_list("[not valid json") is None


def test_parsed_record_list_drops_array_entries_that_are_not_objects(support: ModuleType) -> None:
    mixed = [1, "x", None, {"cost_usd": 1.0}, [1, 2], {"cost_usd": 2.0}]
    assert support.parsed_record_list(json.dumps(mixed)) == [{"cost_usd": 1.0}, {"cost_usd": 2.0}]


def test_parsed_record_list_strips_surrounding_whitespace(support: ModuleType) -> None:
    assert support.parsed_record_list(f"  {json.dumps([{'cost_usd': 1.0}])}  \n") == [{"cost_usd": 1.0}]


def test_parsed_record_list_supports_the_reversed_scan_records_relies_on(support: ModuleType) -> None:
    """``_records`` scans a run's stdout in reverse and takes the first line that parses, so a
    non-JSON or non-array line has to return ``None`` rather than raise - otherwise a stray log
    line would break the scan before it reaches the run's own, later array."""
    lines = [
        json.dumps([{"cost_usd": 1.0}]),
        "a stray log line printed after the first array",
        json.dumps([{"cost_usd": 2.0}]),
    ]
    found = None
    for line in reversed(lines):
        found = support.parsed_record_list(line)
        if found is not None:
            break
    assert found == [{"cost_usd": 2.0}]


# --- uncosted_refusal --------------------------------------------------------------------


def test_uncosted_refusal_is_none_when_a_record_costed(support: ModuleType) -> None:
    records = [{"cost_usd": None}, {"cost_usd": 1.0}]
    assert support.uncosted_refusal(records) is None


def test_uncosted_refusal_names_a_run_that_never_dispatched(support: ModuleType) -> None:
    """No records at all is what a planner that never dispatched a node leaves behind, not a
    node that ran and reported no cost - the message must say which one this is."""
    message = support.uncosted_refusal([])
    assert message is not None
    assert "dispatch" in message


def test_uncosted_refusal_names_the_uncosted_record_count_when_records_exist(support: ModuleType) -> None:
    records = [{"cost_usd": None}, {"tokens": {"in": 5}}]
    message = support.uncosted_refusal(records)
    assert message is not None
    assert "2" in message
    assert "dispatch" not in message


# --- count_steps ------------------------------------------------------------------------


def write_transcript(root: Path, *, node: str, attempt: str, lines: list[str]) -> None:
    """Write one node attempt's transcript where ``count_steps`` looks for it."""
    path = root / "nodes" / node / attempt / "transcript.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def assistant(message_id: str) -> str:
    """One assistant record as the transcript stores it."""
    return json.dumps({"type": "AssistantMessage", "message_id": message_id})


def test_count_steps_counts_distinct_assistant_messages_across_nodes(tmp_path: Path, support: ModuleType) -> None:
    write_transcript(tmp_path, node="planner", attempt="1", lines=[assistant("m1"), assistant("m2"), assistant("m1")])
    write_transcript(tmp_path, node="worker", attempt="1", lines=[assistant("m3")])
    write_transcript(tmp_path, node="worker", attempt="2", lines=[assistant("m3"), assistant("m4")])
    assert support.count_steps(tmp_path) == 4


def test_count_steps_ignores_everything_that_is_not_an_assistant_message(tmp_path: Path, support: ModuleType) -> None:
    write_transcript(
        tmp_path,
        node="planner",
        attempt="1",
        lines=[
            "",
            "   ",
            "not json at all",
            json.dumps({"type": "UserMessage", "message_id": "u1"}),
            json.dumps({"type": "AssistantMessage"}),
            json.dumps({"type": "AssistantMessage", "message_id": ""}),
            json.dumps({"type": "AssistantMessage", "message_id": 7}),
            json.dumps(["AssistantMessage", "m9"]),
            assistant("m1"),
        ],
    )
    assert support.count_steps(tmp_path) == 1


def test_count_steps_is_zero_when_the_run_has_no_transcripts(tmp_path: Path, support: ModuleType) -> None:
    (tmp_path / "nodes").mkdir()
    assert support.count_steps(tmp_path) == 0


# --- skip_node_homes --------------------------------------------------------------------


def test_skip_node_homes_drops_a_home_at_node_attempt_depth(tmp_path: Path, support: ModuleType) -> None:
    ignore = support.skip_node_homes(tmp_path)
    directory = str(tmp_path / "nodes" / "planner" / "1")
    assert ignore(directory, ["home", "transcript.jsonl", "brief.md"]) == {"home"}


@pytest.mark.parametrize(
    "parts",
    [(), ("nodes",), ("nodes", "planner"), ("nodes", "planner", "1", "artefacts"), ("journal", "x", "y")],
)
def test_skip_node_homes_keeps_a_home_anywhere_else(
    tmp_path: Path, support: ModuleType, parts: tuple[str, ...]
) -> None:
    """The filter is a depth-and-prefix test, so every other depth has to be left alone."""
    ignore = support.skip_node_homes(tmp_path)
    assert ignore(str(tmp_path.joinpath(*parts)), ["home", "state.json"]) == set()
