# Probe: Task 7 cost and cache-write dry-run replay

Reads a real stored node dispatch (not a fixture) through the helpers Task 7 added, to confirm
the arithmetic against a transcript that existed before the change.

Artefact: `~/agentdag-eval/spec-round2/runs-S/20260903T184942Z-d38b77/nodes/n-0001/8cf6b506/`
(`transcript.jsonl` and its sibling `record.json`).

Command (finds the transcript's one `total_cost_usd` line, runs its `usage` through
`charged_total`/`input_total`/`tokens_from_usage` from `executor_claude`, asserts against `record.json`):

```bash
.venv/bin/python replay_task7_cost.py
```

Output:

```
replayed transcript: ~/agentdag-eval/spec-round2/runs-S/20260903T184942Z-d38b77/nodes/n-0001/8cf6b506/transcript.jsonl
terminal total_cost_usd : 5.00020325
charged_total(usage)    : 153956
record charged_tokens   : {'opus': 153956}
input_total(usage)      : 3309463 | record tokens.in: 3309463
tokens_from_usage(usage): {'in_': 3309463, 'out': 49574, 'cache_read': 3205081, 'reasoning': None, 'cache_write': 104290}
record cost_usd (stored, written before this change): None
ALL ASSERTIONS PASSED
```

The three figures the brief named:

| figure                               | value      |
|--------------------------------------|------------|
| `total_cost_usd`                     | 5.00020325 |
| charged total (`opus`)               | 153956     |
| `tokens_from_usage(...).cache_write` | 104290     |

Proves the new helpers reproduce, from raw usage, both the figure `record.json` already carried
and one it could not express before this task (`cache_write`), on a transcript that predates
the change rather than a fixture built to fit it.
