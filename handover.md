# Handover: agentdag, 2026-09-02 ~17:40. C1's cold control is spent; nothing else is in flight.

> **The `RESEARCH/` paths point into a private companion repo.** These documents cite it by
> repo-qualified path for the design documents, probe scripts and measurement notes they were
> derived from. The `RESEARCH/` prefix names that repo; it is deliberately not a relative path,
> because no relative path from here resolves to it. These citations do not resolve in a clone of
> this repo. They are kept rather than stripped because a claim that names its source is evidence
> of where it came from even when the source is not public.

Read `OPEN-WORK.md` FIRST and this second. The backlog says what is worth doing; this says only
where the last session stopped.

## In flight

**Nothing is part-done.** No half-written file, no uncommitted edit of mine, no background job of
mine still running.

**One anomaly that is NOT mine:** `CLAUDE.md.bak` is staged-added-and-deleted in the index (`AD` in
`git status --porcelain`). Another session created, staged and removed it; `CLAUDE.md` itself also
changed on disk during the session. I left the index entry alone rather than unstaging it, because
its content exists only in the index and destroying another session's staged state is not mine to
do. Commit by pathspec until it clears, or a bare `git commit` sweeps it in.

## Committed, or not

Everything this session produced is committed AND pushed. The invariant to check, rather than a
sha that this file cannot state about its own commit:

    git status --porcelain          # only the foreign CLAUDE.md.bak entry
    git log --oneline @{u}..HEAD    # empty, once this handover's own commit is pushed

The last CI watch was armed on `1373557` and had not reported when this was written.

## Decided this session, with the reason

1. **Rank 15: keep rebuilding, adopt nothing from Claude Code's agent teams** (user). The reason is
   representational, not economic: the durable gated layer there is exactly ONE level deep by
   explicit refusal, so a graph cannot exist there as gated work, and the four capabilities adopting
   would buy are the four already built and tested. Recorded in `OPEN-WORK.md` 15,
   `PLANS/build-plan-high.md` risk 7, and `PLANS/build-plan-mid.md`'s D2 gap paragraph.
   **It did NOT retire risk 8** - Routines is still NOT ASSESSED, so this was decided on two of
   three surfaces, and a Routines read that moves a differentiator row RE-OPENS it.
2. **A green `make test` on a clean tree is standing authority to push this repo** (user). Push
   only: not a tag, not `make release`, and it does not touch the openvmm confirmation rule.
   Captured as `feedback-a-green-gate-is-authority-to-push-agentdag`.
3. **Peer contact with the `agentswarm` session stays open** (user): answer direct questions from
   source, send corrections owed, and the volunteered cautions are included.
4. **Their measured figures are NOT written into any agentdag document** (mine). n=1 per round,
   rounds varying more than one variable, and a cross-repo citation would dangle for any reader not
   on this machine. Their findings entered only as design constraints in `OPEN-WORK.md` 65 and 77,
   in our own words.

## Decided against, so it is not redone as an oversight

- **The two `src/` truth fixes are still NOT applied** (`executor_claude.py:345`'s false
  "non-default" docstring, `60-kernel.toml:102-104`'s unfireable-backstop comment). They belong with
  the safety work at backlog 38 and are the FIRST item of its `next:` field. Third session running.
- **`why-agentdag.md:98`'s refutation narrative was NOT removed.** Grepped: the refuted claim lives
  in that doc and nowhere else, so deleting the sentence loses the correction rather than relocating
  it. Filed as backlog 68 with "write it into the plans first" as the next action.
- **My six model scores were not filed beside the packet.** They sit in the session scratchpad at
  `/tmp/claude-1000/-media-srv-main-softdev-projects-public-KI-agentdag/e9edbdde-e018-407d-9788-6984c8f4e804/scratchpad/e1_model_scores_SEALED.md`
  (sha256 `f98e2250...0743d294`), because a file of model scores in that directory is one mislabel
  away from being read later as the human control. That path dies with the scratchpad; the scores
  themselves are reproduced in `OPEN-WORK.md` 25.

## Still open, untouched - one line each, detail in OPEN-WORK.md

- Rank 25 USER: C1. Its state CHANGED today; read the line before acting on it.
- Rank 30 USER: does the non-idempotent-resume finding earn a differentiator row, and in whose words.
- Rank 38 FOUND: the unattended-safety chain, five composing defects.
- Rank 40 FOUND: build component 5's judge op and the completion ladder.
- Rank 50 FOUND: 167 unframed memory bodies.
- Rank 55 FOUND: nothing bounds an unattended run.
- Rank 58 FOUND: a judgement has a durable slot in the record and neither producer nor consumer.
- Rank 60 FOUND: the degenerate-dispatch rule.
- Rank 65 FOUND: three scheduler defects that make `--parallel` mean less than it says.
- Rank 68 FOUND: a refuted claim's obituary living in a doc and nowhere else.
- Rank 70 FOUND: the ragged-table check's placement in `repo-gate`.
- Rank 75 FOUND: no P4 arm kills the supervisor or reboots the machine.
- Rank 77 FOUND: an unattended run cannot be watched, listed or costed.
- Rank 80 FOUND: 3 of 8 P4 resume runs never reached an END, unexplained.
- Rank 85 FOUND: the confound jig exists and was not reached for.
- Rank 87 FOUND: no verb for the edit-and-re-run loop; a config-fix resume is served a stale failure.

## The exact next action

**`OPEN-WORK.md` rank 25, and it is a question to the USER, not work to start.** It is the
top-ranked open item and it is blocked on them, which per this backlog's own rule is a reason to go
and ask rather than to skip it.

Ask which of three C1 becomes, and do not begin any FOUND item until it is answered, because (b)
and (c) change whether rank 40's judge op is worth building at all:

    (a) a DIFFERENT human scores the packet cold and C1 runs as designed;
    (b) C1 is recorded NOT-RUN, and the panel's other 24 verdicts stay unvalidated - which is
        exactly the state C2's already-collected arms are parked in;
    (c) the checkpoint is redefined around the zero-variance observation, a weaker claim than
        the one it was built to make.

The evidence that makes this live: the sealed key's own falsifier says the panel scored arm A at
exactly 2.00 six times with ZERO VARIANCE across six unrelated tasks. A model read of the same six
gave 2,2,3,2,2,3, the two 3s for a nameable property two flat-scored graphs lack. That indicts the
panel's discrimination but CANNOT fire the falsifier, which is written about a human's scores.

## Files that matter

    OPEN-WORK.md                                          read before this file; rank 25 changed today
    PLANS/build-plan-high.md                              differentiator table; agent-teams block with
                                                          byte offsets; risks 6, 7, 8 (order restored)
    PLANS/build-plan-mid.md                               D2's gap paragraph, now answered and closed
    docs/why-agentdag.md                                  section 2: both halves of the inversion now
                                                          provenance-marked; line 98 is backlog 68
    docs/architecture-overview.md                         section 3 now says the sentence describes the
                                                          mechanism rather than arguing for it
    RESEARCH/workflow/probes/e1_control_packet.md         the six pairs; key beside it, now OPENED
    src/agentdag/adapters/kernel/executor_claude.py:345   false "non-default" docstring, backlog 38
    src/agentdag/adapters/config/defaultconfig.d/60-kernel.toml:102-104
                                                          unfireable-backstop comment, backlog 38
    src/agentdag/application/kernel/context.py:562        the budget fail-open, backlog 55
    src/agentdag/application/kernel/execute.py:26         "every entry runs in ctx.cwd", backlog 65

## How to verify this still stands

    git status --porcelain                     # only the foreign CLAUDE.md.bak entry
    grep -c '^- \[ \]' OPEN-WORK.md            # 16 open
    .venv/bin/python -m pytest tests/ -q       # no src/ change this session
    grep -c 'skills/toolbox/tools' PLANS/ -r   # 0; the jigs ship in bitranox:compuse-toolbox >= 5.301.0

`PLANS/`, `OPEN-WORK.md` and this file are TRACKED here, so an overwritten handover is recoverable
from git. `EXECUTION-USER-REVIEW.md` is a SYMLINK into the private research repo and is gitignored
here; this session's decisions are its newest entry.

Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not delete
it - if this session ends badly it is the only record of where things stood.
