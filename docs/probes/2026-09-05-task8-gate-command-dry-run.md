# Probe: the configured gate command is the one that runs, and the one recorded

Task 8 made the gate command `[kernel] gate_command` and made `Coordinator.gate` read the
wired port instead of being handed an argv. This drives a real `plan-goal` run through the
shipped CLI to show the value an operator types reaches the subprocess, and that the run
records that same value.

## Method

The only substitution is the MODEL. `wire_kernel` is the production one, so the gate port,
the registry description, the config read and the persisted settings are all real code; the
executors mapping it returns is replaced with a double that writes a fixed `plan.json` for
the planner dispatch and reports `done` for the work node. The gate is not doubled at all -
it runs as a subprocess, as it does in production.

The plan carries a `work` entry and a `gate:make-test` entry, with
`done_when = work.status == "done" AND gate.rc == 0`. Both halves are needed: the work half
is what stops decision 4 refusing a root plan a do-nothing run would satisfy, and the gate
half is what makes the run actually dispatch the gate rather than finishing at the work node.

The run directory is under a scratch path outside every git work tree, and is not kept: the
evidence below is quoted whole, because it is small.

## Arm 1: `--set kernel.gate_command='["true"]'`

What the wiring was given, printed from inside the `wire_kernel` wrapper:

```
wire_kernel gate_command  : ('true',)
wired gate port command   : ('true',)
registry description      : run the project's mechanical test gate (true); emits its exit code as rc
```

The gate node's own `input.json`, `brief.md` and record:

```
input.json : {"argv":["true"],"cwd":"wt/root"}
brief.md   : gate: true
record     : status "done", key_facts {"rc": 0}, artefact_refs ["nodes/n-0002/<hash8>/gate.log"]
```

`gate.log` holds the two-line environment header and nothing else, which is what `true`
produces. The planner's prompt carried the command too:

```
- gate:make-test
    does: run the project's mechanical test gate (true); emits its exit code as rc
    args: (none)
    emits: rc
```

## Arm 2, the control: a command that PRINTS and FAILS

`true` succeeding silently is also what a gate that never ran would look like, so the same
run was repeated with `--set kernel.gate_command='["sh", "-c", "echo the configured gate ran;
exit 3"]'`:

```
input.json : {"argv":["sh","-c","echo the configured gate ran; exit 3"],"cwd":"wt/root"}
gate.log   : (header, then) the configured gate ran
record     : status "failed", key_facts {"rc": 3}
```

The configured command's own stdout is in the gate log and its own exit code is in the
record, so arm 1's `rc: 0` is that command's answer rather than a value the coordinator
supplies. Before this task both arms would have recorded `argv: ["make", "test"]`, whatever
the port was built with.
