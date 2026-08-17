# Module Reference: Architecture & File Index

## Status

Current: the CLI, configuration, logging and email surface, plus the graph A baseline.

---

## Related Files

### Domain Layer
- `src/agentdag/domain/behaviors.py`  -  Pure domain functions (greeting)
- `src/agentdag/domain/enums.py`  -  Type-safe enums (OutputFormat, DeployTarget)
- `src/agentdag/domain/graph_a.py`  -  Graph A records (WorkResult, Tally, TallySummary, PushIntent) and the pure decisions over them (reduce_tally, stage, dedup_key, is_scratch_target)

### Application Layer
- `src/agentdag/application/ports.py`  -  Callable Protocol definitions for adapter functions
- `src/agentdag/application/graph_a_ports.py`  -  The five graph A ports (GitPort, GatePort, WorkPort, ApprovePort, RunStore) and GraphAWiring, the record holding one run's implementations
- `src/agentdag/application/graph_a.py`  -  Graph A as a deterministic program: `make_scratch_fleet` (mirror the fleet), `run_graph` (map work and gate, tally, stage, approve) and `apply` (the idempotent push)

### Adapters Layer
- `src/agentdag/adapters/config/loader.py`  -  Configuration loading with LRU caching
- `src/agentdag/adapters/config/deploy.py`  -  Configuration deployment
- `src/agentdag/adapters/config/display.py`  -  Configuration display (TOML/JSON output, redaction)
- `src/agentdag/adapters/config/overrides.py`  -  CLI `--set` override parsing and deep-merge
- `src/agentdag/adapters/email/sender.py`  -  SMTP email with EmailConfig (Pydantic)
- `src/agentdag/adapters/email/validation.py`  -  Email recipient validation
- `src/agentdag/adapters/logging/setup.py`  -  lib_log_rich initialization
- `src/agentdag/adapters/cli/`  -  CLI adapter package:
  - `__init__.py`  -  Public facade
  - `constants.py`  -  Shared constants
  - `safe_console.py`  -  Encode-safe terminal output; use `safe_console.echo` instead of `click.echo`
  - `exit_codes.py`  -  POSIX exit codes (ExitCode IntEnum)
  - `traceback.py`  -  Traceback state management
  - `context.py`  -  Click context helpers
  - `root.py`  -  Root command group
  - `main.py`  -  Entry point
  - `commands/info.py`  -  info, hello, fail commands
  - `commands/config.py`  -  config, config-deploy, config-generate-examples commands
  - `commands/email.py`  -  send-email, send-notification commands
  - `commands/logging.py`  -  logdemo command
  - `commands/graph_a.py`  -  graph-a group: the scratch and run commands
- `src/agentdag/adapters/graph_a/`  -  Graph A adapter package, one module per port:
  - `git_cli.py`  -  GitPort over the git CLI (mirror, remove_mirror, clone, inspect, push)
  - `gate_make.py`  -  GatePort: the project's `make test` in a child process, under one host-wide lock
  - `store_fs.py`  -  RunStore: one timestamped run directory holding worktrees, logs, homes, records and done markers
  - `work_claude_sdk.py`  -  WorkPort over the Claude Agent SDK, one isolated client and credential per node
  - `approve_console.py`  -  ApprovePort: the console confirmation asked once before anything is pushed

### Adapters Layer (In-Memory / Testing)
- `src/agentdag/adapters/memory/__init__.py`  -  Public facade + Protocol conformance assertions
- `src/agentdag/adapters/memory/config.py`  -  In-memory config adapters
- `src/agentdag/adapters/memory/email.py`  -  In-memory email adapters
- `src/agentdag/adapters/memory/logging.py`  -  In-memory logging (no-op)

### Composition Layer
- `src/agentdag/composition/__init__.py`  -  Wires adapters to ports
- `src/agentdag/composition/graph_a.py`  -  Builds the GraphAWiring for one run: the production adapters plus a fresh run directory

### Entry Points
- `src/agentdag/__main__.py`  -  Thin shim for `python -m`
- `src/agentdag/__init__.py`  -  Public API exports
- `src/agentdag/__init__conf__.py`  -  Package metadata constants

### Configuration Defaults
- `src/agentdag/adapters/config/defaultconfig.toml`  -  Base defaults
- `src/agentdag/adapters/config/defaultconfig.d/40-layered-config.toml`  -  lib_layered_config integration docs
- `src/agentdag/adapters/config/defaultconfig.d/50-mail.toml`  -  Email defaults
- `src/agentdag/adapters/config/defaultconfig.d/90-logging.toml`  -  Logging defaults

### Tests
- `tests/test_behaviors.py`  -  Domain function tests
- `tests/test_cache_effectiveness.py`  -  LRU cache behavior tests
- `tests/test_cli_core.py`, `tests/test_cli_config.py`, `tests/test_cli_email.py`  -  CLI command tests
- `tests/test_config_overrides.py`  -  `--set` parsing tests
- `tests/test_safe_console.py`  -  Legacy-codepage output tests, plus the guard forbidding direct `click.echo`
- `tests/test_display.py`  -  Config display formatting tests
- `tests/test_cli_exit_codes.py`  -  ExitCode enum tests
- `tests/test_mail.py`  -  Email configuration and sending tests
- `tests/test_metadata.py`  -  Package metadata tests
- `tests/test_module_entry.py`  -  `python -m` entry tests
- `tests/test_ports.py`  -  Protocol conformance tests
- `tests/test_graph_a_domain.py`  -  Graph A pure records and decisions
- `tests/test_graph_a_adapters.py`  -  Graph A adapters over real git repositories and real child processes
- `tests/test_graph_a_run.py`  -  Graph A end to end, with the work node as the only substitution
- `tests/test_cli_graph_a.py`  -  `graph-a` CLI stories through the real root group

---

## Architecture

### Layer Assignments

| Directory/Module         | Layer       | Responsibility                                          |
|--------------------------|-------------|---------------------------------------------------------|
| `domain/`                | Domain      | Pure logic  -  no I/O, logging, or frameworks           |
| `application/ports.py`   | Application | Protocol definitions for adapters                       |
| `application/graph_a.py` | Application | Graph A as a deterministic program over typed records   |
| `adapters/config/`       | Adapters    | Configuration loading, deployment, display              |
| `adapters/email/`        | Adapters    | SMTP email sending                                      |
| `adapters/logging/`      | Adapters    | lib_log_rich initialization                             |
| `adapters/cli/`          | Adapters    | Click CLI framework integration                         |
| `adapters/graph_a/`      | Adapters    | git, gate, run store, work node and approve for graph A |
| `adapters/memory/`       | Adapters    | In-memory implementations for testing                   |
| `composition/`           | Composition | Wires adapters to ports                                 |

### Import Enforcement

Layer boundaries enforced via `import-linter` contracts in `pyproject.toml`:
- **Domain is pure**: Cannot import from adapters or composition
- **Clean Architecture layers**: Validates dependency direction (composition → adapters → application → domain)

Run `lint-imports` to verify compliance.

---

## Exit Codes

POSIX-conventional exit codes defined in `adapters/cli/exit_codes.py`:

| Code | Name                | Usage                                     |
|------|---------------------|-------------------------------------------|
| 0    | `SUCCESS`           | Command completed successfully            |
| 1    | `GENERAL_ERROR`     | Unhandled exception, general failure      |
| 2    | `FILE_NOT_FOUND`    | Attachment or file not found              |
| 13   | `PERMISSION_DENIED` | Cannot write to target directory          |
| 22   | `INVALID_ARGUMENT`  | Invalid CLI argument or section not found |
| 69   | `SMTP_FAILURE`      | SMTP delivery failed                      |
| 78   | `CONFIG_ERROR`      | Missing required configuration            |
| 110  | `TIMEOUT`           | Operation timed out                       |
| 130  | `SIGNAL_INT`        | Interrupted (SIGINT/Ctrl+C)               |
| 141  | `BROKEN_PIPE`       | Output pipe closed                        |
| 143  | `SIGNAL_TERM`       | Terminated (SIGTERM)                      |

---

## CLI Commands

### Root Command

**Command:** `agentdag`

| Option                         | Description                                 |
|--------------------------------|---------------------------------------------|
| `--version`                    | Show version and exit                       |
| `--traceback / --no-traceback` | Show full Python traceback on errors        |
| `--profile NAME`               | Load configuration from a named profile     |
| `--set SECTION.KEY=VALUE`      | Override configuration setting (repeatable) |
| `-h, --help`                   | Show help and exit                          |

### info

Print resolved package metadata.

**Exit codes:** 0

### hello

Emit canonical greeting (`"Hello World"`).

**Exit codes:** 0

### fail

Trigger intentional `RuntimeError` for testing error handling.

**Exit codes:** 1

### config

Display merged configuration from all sources.

| Option                   | Description                    |
|--------------------------|--------------------------------|
| `--format [human\|json]` | Output format (default: human) |
| `--section NAME`         | Show only specific section     |

**Exit codes:** 0, 22 (section not found)

### config-deploy

Deploy default configuration to system or user directories.

| Option                       | Description                              |
|------------------------------|------------------------------------------|
| `--target [app\|host\|user]` | Target layer(s)  -  required, repeatable |
| `--force`                    | Overwrite existing files                 |
| `--profile NAME`             | Deploy to profile subdirectory           |

**Exit codes:** 0, 1, 13 (permission denied)

### config-generate-examples

Generate example configuration files.

| Option              | Description                   |
|---------------------|-------------------------------|
| `--destination DIR` | Target directory  -  required |
| `--force`           | Overwrite existing files      |

**Exit codes:** 0, 1

### send-email

Send email using configured SMTP settings.

| Option                               | Description                     |
|--------------------------------------|---------------------------------|
| `--to ADDRESS`                       | Recipient (repeatable)          |
| `--subject TEXT`                     | Subject line  -  required       |
| `--body TEXT`                        | Plain-text body                 |
| `--body-html TEXT`                   | HTML body                       |
| `--from ADDRESS`                     | Override sender                 |
| `--attachment PATH`                  | File to attach (repeatable)     |
| `--smtp-host HOST:PORT`              | Override SMTP host (repeatable) |
| `--smtp-username USER`               | Override username               |
| `--smtp-password PASS`               | Override password               |
| `--use-starttls / --no-use-starttls` | Override STARTTLS               |
| `--timeout SECONDS`                  | Override timeout                |

**Exit codes:** 0, 2 (file not found), 22, 69 (SMTP failure), 78 (no SMTP hosts)

### send-notification

Send simple plain-text notification email.

| Option                               | Description                     |
|--------------------------------------|---------------------------------|
| `--to ADDRESS`                       | Recipient (repeatable)          |
| `--subject TEXT`                     | Subject  -  required            |
| `--message TEXT`                     | Message  -  required            |
| `--from ADDRESS`                     | Override sender                 |
| `--smtp-host HOST:PORT`              | Override SMTP host (repeatable) |
| `--smtp-username USER`               | Override username               |
| `--smtp-password PASS`               | Override password               |
| `--use-starttls / --no-use-starttls` | Override STARTTLS               |
| `--timeout SECONDS`                  | Override timeout                |

**Exit codes:** 0, 22, 69 (SMTP failure), 78 (no SMTP hosts)

### logdemo

Run logging demonstration.

| Option         | Description                      |
|----------------|----------------------------------|
| `--theme NAME` | Logging theme (default: classic) |

**Exit codes:** 0

### graph-a scratch

Mirror every repository listed in `REAL_REPOS_FILE` into the scratch fleet, and write the list of mirrors to `<scratch>/REPOS.txt`. The real repositories are read once, by `git clone --mirror`, and never written.

| Option          | Description                                                  |
|-----------------|--------------------------------------------------------------|
| `--scratch DIR` | Directory the scratch fleet is built in                      |
| `--refresh`     | Delete an existing mirror and read the real repository again |

**Exit codes:** 0, 22 (two repositories sharing a basename)

### graph-a run

Run graph A over the scratch origins in `REPOS_FILE`, applying the change in `BRIEF_FILE`. Every repository gets its own worktree, work node and gate run; nothing is pushed before the resulting push list is approved on the console.

| Option          | Description                                              |
|-----------------|----------------------------------------------------------|
| `--scratch DIR` | Scratch directory owning the only permitted push targets |
| `--runs DIR`    | Directory holding one timestamped directory per run      |
| `--parallel N`  | How many branches may run at once (minimum 1)            |
| `--model NAME`  | Model each work node runs on                             |
| `--lock PATH`   | Host-wide lock file serialising the gate across branches |

**Exit codes:** 0, 22 (a push target outside the scratch tree)

---

## Profile Validation

Profile names (`--profile` option) are validated using `lib_layered_config.validate_profile_name()`.

### validate_profile()

**Location:** `adapters/config/loader.py`

```python
def validate_profile(profile: str, max_length: int | None = None) -> None:
    """Validate profile name using lib_layered_config."""
```

| Parameter    | Type          | Default  | Description                                 |
|--------------|---------------|----------|---------------------------------------------|
| `profile`    | `str`         | required | Profile name to validate                    |
| `max_length` | `int \| None` | 64       | Maximum length (DEFAULT_MAX_PROFILE_LENGTH) |

### Validation Rules

| Rule             | Description                                          |
|------------------|------------------------------------------------------|
| Maximum length   | 64 characters (configurable via `max_length`)        |
| Character set    | ASCII alphanumeric, hyphens (`-`), underscores (`_`) |
| Start character  | Must start with alphanumeric character               |
| Empty string     | Rejected                                             |
| Windows reserved | CON, PRN, AUX, NUL, COM1-9, LPT1-9 rejected          |
| Path traversal   | `/`, `\`, `..` rejected                              |
| Control chars    | Rejected                                             |

### Error Handling

Raises `ValueError` with descriptive message on invalid input.

---

## Email Configuration

### EmailConfig Fields

The `EmailConfig` Pydantic model (`adapters/email/sender.py`) provides validated, immutable email configuration:

| Field                          | Type          | Default | Description                          |
|--------------------------------|---------------|---------|--------------------------------------|
| `smtp_hosts`                   | `list[str]`   | `[]`    | SMTP servers in `host[:port]` format |
| `from_address`                 | `str \| None` | `None`  | Default sender address               |
| `recipients`                   | `list[str]`   | `[]`    | Default recipient addresses          |
| `smtp_username`                | `str \| None` | `None`  | SMTP authentication username         |
| `smtp_password`                | `str \| None` | `None`  | SMTP authentication password         |
| `use_starttls`                 | `bool`        | `True`  | Enable STARTTLS negotiation          |
| `timeout`                      | `float`       | `30.0`  | Socket timeout in seconds            |
| `raise_on_missing_attachments` | `bool`        | `True`  | Raise on missing attachment files    |
| `raise_on_invalid_recipient`   | `bool`        | `True`  | Raise on invalid recipient addresses |

### Attachment Security Fields

| Field                                    | Type                      | Default      | Description                                   |
|------------------------------------------|---------------------------|--------------|-----------------------------------------------|
| `attachment_allowed_extensions`          | `frozenset[str] \| None`  | `None`       | Whitelist of allowed extensions               |
| `attachment_blocked_extensions`          | `frozenset[str] \| None`  | `None`       | Blacklist of blocked extensions               |
| `attachment_allowed_directories`         | `frozenset[Path] \| None` | `None`       | Whitelist of allowed source directories       |
| `attachment_blocked_directories`         | `frozenset[Path] \| None` | `None`       | Blacklist of blocked directories              |
| `attachment_max_size_bytes`              | `int \| None`             | `26_214_400` | Maximum file size (25 MiB), `None` to disable |
| `attachment_allow_symlinks`              | `bool`                    | `False`      | Whether symlinks are permitted                |
| `attachment_raise_on_security_violation` | `bool`                    | `True`       | Raise or skip on security violation           |

**Notes:**
- `None` values use `btx_lib_mail`'s OS-specific defaults (blocked extensions/directories)
- Empty arrays `[]` in TOML configuration are coerced to `None`
- `max_size_bytes = 0` is coerced to `None` (disable size checking)
- String paths are converted to `Path` objects during validation

### Configuration Loading

`load_email_config_from_dict()` handles the nested `[email.attachments]` TOML section:

```python
# TOML structure:
# [email]
# smtp_hosts = ["smtp.example.com:587"]
# [email.attachments]
# max_size_bytes = 10485760

config = load_email_config_from_dict(config_dict)
# Flattens to: attachment_max_size_bytes = 10485760
```

---

## Testing Infrastructure

### In-Memory Adapters

The `adapters/memory/` package provides lightweight implementations for testing:

| Module              | Protocols Satisfied                                                         |
|---------------------|-----------------------------------------------------------------------------|
| `memory/config.py`  | `GetConfig`, `GetDefaultConfigPath`, `DeployConfiguration`, `DisplayConfig` |
| `memory/email.py`   | `SendEmail`, `SendNotification`, `LoadEmailConfigFromDict`                  |
| `memory/logging.py` | `InitLogging`                                                               |

Use `composition.build_testing()` to wire all in-memory adapters.

### Test Fixtures (conftest.py)

| Fixture                   | Purpose                                        |
|---------------------------|------------------------------------------------|
| `config_factory`          | Creates real `Config` instances from test data |
| `inject_config`           | Injects config into CLI path                   |
| `cli_runner`              | Fresh `CliRunner` per test                     |
| `strip_ansi`              | Strips ANSI escape codes from output           |
| `clear_config_cache`      | Clears LRU cache before tests                  |
| `managed_traceback_state` | Resets/restores traceback configuration        |

---

**Last Updated:** 2026-08-17 (graph A baseline)
