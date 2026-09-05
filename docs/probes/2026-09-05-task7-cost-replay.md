# Probe: Task 7 cost and cache-write dry-run replay

Reads a real stored node dispatch (not a fixture) through the helpers Task 7 added, to confirm
the arithmetic against a transcript that existed before the change.

Artefact: `~/agentdag-eval/spec-round2/runs-S/20260903T184942Z-d38b77/nodes/n-0001/8cf6b506/`
(`transcript.jsonl` and its sibling `record.json`).

Command, run from the repo root with the project venv (`.venv/bin/python - <<'EOF' ... EOF`):

```python
import json
from pathlib import Path

from agentdag.adapters.kernel.executor_claude import charged_total, input_total, tokens_from_usage

node = Path("~/agentdag-eval/spec-round2/runs-S/20260903T184942Z-d38b77/nodes/n-0001/8cf6b506").expanduser()
terminal = next(
    e for e in map(json.loads, (node / "transcript.jsonl").read_text().splitlines()) if "total_cost_usd" in e
)
usage = terminal["usage"]
record = json.loads((node / "record.json").read_text())
print("total_cost_usd :", terminal["total_cost_usd"])
print("charged_total  :", charged_total(usage), "| record:", record["charged_tokens"])
print("input_total    :", input_total(usage), "| record tokens.in:", record["tokens"]["in"])
print("cache_write    :", tokens_from_usage(usage).cache_write)
```

Output, 2026-09-05:

```
total_cost_usd : 5.00020325
charged_total  : 153956 | record: {'opus': 153956}
input_total    : 3309463 | record tokens.in: 3309463
cache_write    : 104290
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
