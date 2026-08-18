# agentdag

<!-- Badges -->
[![CI](https://github.com/bitranox/agentdag/actions/workflows/default_cicd_public.yml/badge.svg)](https://github.com/bitranox/agentdag/actions/workflows/default_cicd_public.yml)
[![CodeQL](https://github.com/bitranox/agentdag/actions/workflows/codeql.yml/badge.svg)](https://github.com/bitranox/agentdag/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Open in Codespaces](https://img.shields.io/badge/Codespaces-Open-blue?logo=github&logoColor=white&style=flat-square)](https://codespaces.new/bitranox/agentdag?quickstart=1)
[![PyPI](https://img.shields.io/pypi/v/agentdag.svg)](https://pypi.org/project/agentdag/)
[![PyPI - Downloads](https://img.shields.io/pypi/dm/agentdag.svg)](https://pypi.org/project/agentdag/)
[![Code Style: Ruff](https://img.shields.io/badge/Code%20Style-Ruff-46A3FF?logo=ruff&labelColor=000)](https://docs.astral.sh/ruff/)
[![codecov](https://codecov.io/gh/bitranox/agentdag/graph/badge.svg?token=UFBaUDIgRk)](https://codecov.io/gh/bitranox/agentdag)
[![Maintainability](https://qlty.sh/badges/041ba2c1-37d6-40bb-85a0-ec5a8a0aca0c/maintainability.svg)](https://qlty.sh/gh/bitranox/projects/agentdag)
[![security: bandit](https://img.shields.io/badge/security-bandit-yellow.svg)](https://github.com/PyCQA/bandit)


`agentdag` is a coordinator for small graphs of AI-agent nodes. It dispatches each node, gates what the node produced on something mechanical, and branches only on typed records, never on prose an agent wrote.

The graph A baseline is the first coordinator graph it ships (0.0.1 on PyPI is the scaffold alone): a fleet migration that gives every repository its own worktree, its own agent and its own gate run, and pushes what passed to scratch clones after one console approval. It deliberately has no journal, no token cap and no unattended approve. Those are later milestones, and what their absence costs is what this baseline is for. See [Graph A: fleet migration](#graph-a-fleet-migration) below.

- CLI entry point styled with rich-click (rich output + click ergonomics).
- Layered configuration system with lib_layered_config (defaults → app → host → user → .env → env).
- Rich structured logging with lib_log_rich (console, journald, eventlog, Graylog/GELF).
- Exit-code and messaging helpers powered by lib_cli_exit_tools.
- Metadata helpers ready for packaging, testing, and release automation.


### Python 3.12+ Baseline

- The project targets **Python 3.12 and newer**.
- Runtime dependencies require current stable releases (`rich-click>=1.9.6`
  and `lib_cli_exit_tools>=2.2.4`). Dev dependencies (pytest, ruff, pyright,
  bandit, etc.) specify minimum version constraints to ensure compatibility.
- CI workflows exercise GitHub's rolling runner images (`ubuntu-latest`,
  `macos-latest`, `windows-latest`) and cover CPython 3.12 through 3.14
  alongside the latest available 3.x release provided by Actions.

---

## Install - recommended via uv

[uv](https://docs.astral.sh/uv/) is an ultrafast Python package manager written in Rust (10-20x faster than pip/poetry).

### Install uv (if not already installed) 
```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Copy the actual binaries
cp /root/.local/bin/uv /usr/local/bin/uv
cp /root/.local/bin/uvx /usr/local/bin/uvx

# Ensure world-executable
chmod 755 /usr/local/bin/uv /usr/local/bin/uvx

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### One-shot run (no install needed)

```bash
uvx agentdag@latest --help
```

### Persistent install as CLI tool

```bash
# Install latest python
install_latest_python_gcc.sh
# pin uv to the latest python
uv python pin /opt/python-latest/bin/python3
# One-time install, persists from the git repo
uv tool install --python /opt/python-latest/bin/python3 --from "git+https://github.com/bitranox/agentdag.git" agentdag
# or One-time install, persists from PyPi
uv tool install --python /opt/python-latest/bin/python3 agentdag
# Update (requires network)
uv tool upgrade agentdag
# Run
agentdag --help
```

### Persistent install as CLI tool
```bash
# install the CLI tool (isolated environment, added to PATH)
uv tool install agentdag

# upgrade to latest
uv tool upgrade agentdag
```

### Install as project dependency

```bash
uv venv && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
uv pip install agentdag
```

For alternative install paths (pip, pipx, source builds, etc.), see
[INSTALL.md](INSTALL.md). All supported methods register the `agentdag` command on your PATH.

---

## Configuration

See [CONFIG.md](CONFIG.md) for detailed documentation on the layered configuration system, including precedence rules, profile support, and customization best practices.

---

## Quick Start

```bash
# Install
uv tool install agentdag

# Verify
agentdag --version

# deploy config files
agentdag deploy-config --target app

# Try it out
agentdag hello
agentdag info
agentdag config
```

---

## Usage

The CLI leverages [rich-click](https://github.com/ewels/rich-click) so help output, validation errors, and prompts render with Rich styling while keeping the familiar click ergonomics.

### Available Commands

```bash
# Graph A fleet migration (see the section below)
agentdag graph-a scratch real-repos.txt
agentdag graph-a run /tmp/agentdag-scratch/REPOS.txt brief.md

# Display package information
agentdag info

# Greeting and error-handling demos
agentdag hello
agentdag fail
agentdag --traceback fail

# Configuration management
agentdag config                         # Show current configuration
agentdag config --format json           # Show as JSON
agentdag config --section lib_log_rich  # Show specific section
agentdag config --profile production    # Use a named profile

# Deploy configuration templates to target directories
# Without profile:
agentdag config-deploy --target app    # → /etc/xdg/{slug}/config.toml
agentdag config-deploy --target host   # → /etc/xdg/{slug}/hosts/{hostname}.toml
agentdag config-deploy --target user   # → ~/.config/{slug}/config.toml

# With profile:
agentdag config-deploy --target app --profile production   # → /etc/xdg/{slug}/profile/production/config.toml
agentdag config-deploy --target host --profile production  # → /etc/xdg/{slug}/profile/production/hosts/{hostname}.toml
agentdag config-deploy --target user --profile production  # → ~/.config/{slug}/profile/production/config.toml

# With custom permissions (POSIX only):
agentdag config-deploy --target user --file-mode 640       # Files with rw-r----- (640)
agentdag config-deploy --target user --dir-mode 750        # Directories with rwxr-x--- (750)
agentdag config-deploy --target app --no-permissions       # Skip permission setting (use umask)

# Profile names: alphanumeric, hyphens, underscores; max 64 chars; must start with letter/digit
# See CONFIG.md for full validation rules

# Deploy configuration examples
agentdag config-generate-examples --destination ./examples

# Load configuration from an explicit .env file (skips upward directory search)
agentdag --env-file /path/to/.env config
agentdag --env-file ./environments/production.env send-notification ...

# Override configuration at runtime (repeatable --set)
agentdag --set lib_log_rich.console_level=DEBUG config
agentdag --set email.smtp_hosts='["smtp.example.com:587"]' config --format json

# Logging demo
agentdag logdemo
agentdag --set lib_log_rich.console_level=DEBUG logdemo

# Send email
agentdag send-email \
    --to recipient@example.com \
    --subject "Test Email" \
    --body "Hello from bitranox!"

# Send email with HTML body and attachments
agentdag send-email \
    --to recipient@example.com \
    --subject "Monthly Report" \
    --body "See attached." \
    --body-html "<h1>Report</h1><p>Details in the PDF.</p>" \
    --attachment report.pdf

# Send plain-text notification
agentdag send-notification \
    --to ops@example.com \
    --subject "Deploy OK" \
    --message "Application deployed successfully"

# All commands work with any entry point
python -m agentdag info
uvx agentdag info
```

---

## Graph A: fleet migration

Graph A applies one brief to a fleet of repositories: each repository gets its own worktree, its own agent node and its own gate run, and only what passed the gate is offered for pushing.

Two commands, in the order they are used:

```bash
# 1. read the real repositories once and mirror each into the scratch fleet
agentdag graph-a scratch real-repos.txt --scratch /tmp/agentdag-scratch

# a mirror that already exists is reused as it stands, however stale;
# --refresh throws it away and reads the real repository again
agentdag graph-a scratch real-repos.txt --refresh

# 2. run the graph over the mirrors that step wrote
agentdag graph-a run /tmp/agentdag-scratch/REPOS.txt brief.md --parallel 2 --model sonnet
```

`real-repos.txt` holds one path per line; blank lines and lines starting with `#` are ignored. `brief.md` is the change to make, handed to every node as its system prompt. `graph-a scratch` writes the list of mirrors to `<scratch>/REPOS.txt`, which is what `graph-a run` reads.

`graph-a run` prints the run directory as it starts and the path of its `tally.json` when it finishes. That file holds one row per repository, with the gate's exit code and the node's turn and token counts.

### The scratch-clone rule

A run never writes to a real repository.

- `graph-a scratch` reads each real repository exactly once, with `git clone --mirror`.
- The bare clones under `<scratch>/origin/` are the only push targets a run accepts. A target anywhere else stops the run before the first node is dispatched, and the same check guards the push step itself.
- Neither clone keeps a remote: the mirror does not point back at the real repository and the worktree does not point at the mirror, so a node's reflex `git push` has nowhere to go. This is not containment. A node with unrestricted Bash can still push to any path it can name, and that needs a sandbox, which the baseline does not have.

### The gate runs one at a time

The gate is `make test` in the node's own worktree, run as a separate process; the coordinator reads its exit code and nothing else. All gate runs are serialised by one host-wide lock file (`--lock`, default `<tmp>/agentdag-bmk-tool-env.lock`), because the build tool environment is shared across the whole host and two gates running at once rebuild it under each other. `--parallel` therefore bounds the agent nodes, not the gates.

### One credential per node

Each node runs with `CLAUDE_CONFIG_DIR` pointing at its own directory under the run store, holding its own copy of `~/.claude/.credentials.json`, created owner-only. A node's token refresh lands in that copy rather than in the operator's file, and parallel nodes never share one credential file. An operator with no credential file is not an error: the node then fails with the CLI's own "not logged in" message.

### What the baseline does not have

- No journal. The run store is a fresh timestamped directory per invocation, so a crash after the nodes have run and before the approval throws that work away.
- No token or spend cap. `max_turns` per node is a turn count, not a budget.
- No unattended approve. The run blocks on a console confirmation, so it cannot run on a schedule or in CI.

---

## Coordinator (agentdag run)

`agentdag run` is the general coordinator kernel: a journal, a resumable run directory, a
tier policy and a Claude executor with an allowlisted per-node credential. It runs the SAME
`graph-a` workflow the baseline above runs, plus a `hold`-by-default human approve step and
a crash window that re-dispatches only what a journal shows never finished. `agentdag graph-a
...` stays in the repo unchanged as the M1 BASELINE - the control this kernel is measured
against until M5 compares the two.

Codex (a second executor arm) and a per-turn token spend cap are NOT in this version.

One-time setup: the run directory needs to exist and be writable before the first `run start`.

```bash
sudo install -d -m 0700 -o "$USER" -g "$USER" /var/lib/agentdag/runs
```

Five verbs, over one run directory per run id:

```bash
# start a run; --foreground drives the coordinator in-process (the testable path);
# without it, the coordinator is launched detached (a systemd --user scope on Linux,
# a plain child process elsewhere) and the command returns immediately
agentdag run start graph-a \
  --arg repos_file=/tmp/agentdag-scratch/REPOS.txt --arg brief_file=brief.md \
  --arg scratch=/tmp/agentdag-scratch --runs /var/lib/agentdag/runs

# print state.json's fields and the journal's last event
agentdag run status <run-id>

# print one line per result the journal holds, or --json for the same as JSON
agentdag run records <run-id>

# relaunch a run that suspended, crashed, or otherwise is not yet `done`
agentdag run resume <run-id> --reason manual

# record a decision for a suspended approve node, then relaunch (unless --no-relaunch)
agentdag run approve <run-id> a_push_list --decision approve
```

A run directory (`<runs>/<run-id>/`) holds `journal.jsonl` (the append-only, replayable
log - one JSON line per event), `audit.jsonl` (a copy, written first), `state.json`
(status, cursor, token totals), `lock` (the exclusive run lock), and per-node subdirectories:
`decisions/`, `intents/`, `artefacts/`, `wt/` (worktrees), `nodes/` (each dispatch's brief,
input and record), `manifest/` (map/reduce manifests) and `done/` (apply markers).

The Claude executor authenticates each node from one of two sources, chosen once per CLI
invocation and printed at `run start`: the config's `[credentials] claude_oauth_token_file`
keyfile if that path exists, else a private owner-only copy of the operator's own
`~/.claude/.credentials.json`. Either way, the CLI itself never reads the credential's
content - only the executor does, inside the coordinator process, at the point it actually
dispatches a node.

---

## Email Sending

The application includes email sending capabilities via [btx-lib-mail](https://pypi.org/project/btx-lib-mail/), supporting both simple notifications and rich HTML emails with attachments.

#### Email Configuration

Configure email settings via environment variables, `.env` file, or configuration files:

**Environment Variables:**

Environment variables use the format: `<PREFIX>___<SECTION>__<KEY>=value`
- Triple underscore (`___`) separates PREFIX from SECTION
- Double underscore (`__`) separates SECTION from KEY

```bash
export AGENTDAG___EMAIL__SMTP_HOSTS="smtp.gmail.com:587,smtp.backup.com:587"
export AGENTDAG___EMAIL__FROM_ADDRESS="alerts@myapp.com"
export AGENTDAG___EMAIL__SMTP_USERNAME="your-email@gmail.com"
export AGENTDAG___EMAIL__SMTP_PASSWORD="your-app-password"
export AGENTDAG___EMAIL__USE_STARTTLS="true"
export AGENTDAG___EMAIL__TIMEOUT="60.0"
```

**Configuration File**:
```toml
[email]
smtp_hosts = ["smtp.gmail.com:587", "smtp.backup.com:587"]  # Fallback to backup if primary fails
from_address = "alerts@myapp.com"
smtp_username = "myuser@gmail.com"
smtp_password = "secret_password"  # Consider using environment variables for sensitive data
use_starttls = true
timeout = 60.0
```

**`.env` File:**
```bash
# Email configuration for local testing
AGENTDAG___EMAIL__SMTP_HOSTS=smtp.gmail.com:587
AGENTDAG___EMAIL__FROM_ADDRESS=noreply@example.com
```

#### Gmail Configuration Example

For Gmail, create an [App Password](https://support.google.com/accounts/answer/185833) instead of using your account password:

```bash
AGENTDAG___EMAIL__SMTP_HOSTS=smtp.gmail.com:587
AGENTDAG___EMAIL__FROM_ADDRESS=your-email@gmail.com
AGENTDAG___EMAIL__SMTP_USERNAME=your-email@gmail.com
AGENTDAG___EMAIL__SMTP_PASSWORD=your-16-char-app-password
```

#### Send Simple Email

```bash
# Send basic email to one recipient
agentdag send-email \
    --to recipient@example.com \
    --subject "Test Email" \
    --body "Hello from bitranox!"

# Send to multiple recipients
agentdag send-email \
    --to user1@example.com \
    --to user2@example.com \
    --subject "Team Update" \
    --body "Please review the latest changes"
```

#### Send HTML Email with Attachments

```bash
agentdag send-email \
    --to recipient@example.com \
    --subject "Monthly Report" \
    --body "Please find the monthly report attached." \
    --body-html "<h1>Monthly Report</h1><p>See attached PDF for details.</p>" \
    --attachment report.pdf \
    --attachment data.csv
```

#### Send Notifications

For simple plain-text notifications, use the convenience command:

```bash
# Single recipient
agentdag send-notification \
    --to ops@example.com \
    --subject "Deployment Success" \
    --message "Application deployed successfully to production at $(date)"

# Multiple recipients
agentdag send-notification \
    --to admin1@example.com \
    --to admin2@example.com \
    --subject "System Alert" \
    --message "Database backup completed successfully"
```

#### Programmatic Email Usage

```python
from agentdag.adapters.email.sender import EmailConfig
from agentdag.composition import send_email, send_notification

# Configure email
config = EmailConfig(
    smtp_hosts=["smtp.gmail.com:587"],
    from_address="alerts@myapp.com",
    smtp_username="myuser@gmail.com",
    smtp_password="app-password",
    timeout=60.0,
)

# Send simple email
send_email(
    config=config,
    recipients="recipient@example.com",
    subject="Test Email",
    body="Hello from Python!",
)

# Send email with HTML and attachments
from pathlib import Path

send_email(
    config=config,
    recipients=["user1@example.com", "user2@example.com"],
    subject="Report",
    body="See attached report",
    body_html="<h1>Report</h1><p>Details in attachment</p>",
    attachments=[Path("report.pdf")],
)

# Send notification
send_notification(
    config=config,
    recipients="ops@example.com",
    subject="Deployment Complete",
    message="Production deployment finished successfully",
)
```

#### Email Troubleshooting

**Connection Failures:**
- Verify SMTP hostname and port are correct
- Check firewall allows outbound connections on SMTP port
- Test connectivity: `telnet smtp.gmail.com 587`

**Authentication Errors:**
- For Gmail: Use App Password, not account password
- Ensure username/password are correct
- Check for 2FA requirements

**Emails Not Arriving:**
- Check recipient's spam folder
- Verify `from_address` is valid and not blacklisted
- Review SMTP server logs for delivery status

## Further Documentation

- [Install Guide](INSTALL.md)
- [Development Handbook](DEVELOPMENT.md)
- [Contributor Guide](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)
- [Module Reference](docs/systemdesign/module_reference.md)
- [License](LICENSE)
