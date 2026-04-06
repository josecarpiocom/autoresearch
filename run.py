#!/usr/bin/env python3
"""
autoresearch — autonomous iterative optimization.

Modify → Verify → Keep/Discard → Repeat.

Usage:
    python run.py                  # run until max_iterations or stuck
    python run.py --once           # single iteration
    python run.py --baseline       # just measure and record baseline
"""

from __future__ import annotations

import argparse
import filecmp
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        sys.exit("Python < 3.11 requires 'pip install tomli'")


# ── Config ──────────────────────────────────────────────────────────────────

CONFIG_FILE = "autoresearch.toml"
RESULTS_FILE = "autoresearch-results.tsv"
GIT_ROOT: Path | None = None
AGENT_COMMAND_ENV = "AUTORESEARCH_AGENT_COMMAND"
HYPOTHESIS_FILE = ".autoresearch_hypothesis"

# ── Agent presets ──────────────────────────────────────────────────────────

AGENT_PRESETS: dict[str, dict] = {
    "claude": {
        "template": 'cat {prompt_file} | claude -p --model {model} --allowedTools Edit,Write,Read,Bash -',
        "default_model": "claude-sonnet-4-6",
    },
    "codex": {
        "template": 'codex exec --full-auto --model {model} "$(cat {prompt_file})"',
        "default_model": "gpt-5.4",
    },
    "gemini": {
        "template": 'gemini --yolo --model {model} -p "$(cat {prompt_file})"',
        "default_model": "gemini-3.1-pro-preview",
    },
    "cursor": {
        "template": 'cursor-agent -p --force "$(cat {prompt_file})"',
        "default_model": "",  # cursor doesn't expose a --model flag
        "warning": "Cursor CLI is in beta — headless mode may hang. See https://forum.cursor.com/t/150246",
    },
}

def build_agent_command(preset: str, model: str) -> str:
    """Build agent command from a preset name and optional model override."""
    if preset not in AGENT_PRESETS:
        available = ", ".join(sorted(AGENT_PRESETS))
        sys.exit(f"[!] Unknown agent preset '{preset}'. Available: {available}")

    info = AGENT_PRESETS[preset]
    if warning := info.get("warning"):
        print(f"  [!] WARNING: {warning}")

    effective_model = model or info["default_model"]
    cmd = info["template"]
    if effective_model:
        cmd = cmd.replace("{model}", effective_model)
    else:
        # Strip --model {model} if no model available
        cmd = cmd.replace(" --model {model}", "").replace(" {model}", "")
    return cmd


@dataclass
class Config:
    # problem
    goal: str = ""
    metric: str = "metric"
    direction: str = "higher"  # "higher" | "lower"

    # verify
    verify_command: str = "python verify.py"
    verify_pattern: str = r"{metric}=(-?[\d.]+)"
    verify_timeout: int = 120

    # scope
    edit: list[str] = field(default_factory=list)
    read: list[str] = field(default_factory=list)
    frozen: list[str] = field(default_factory=list)

    # agent
    agent_preset: str = ""     # "claude" | "codex" | "cursor"
    agent_model: str = ""      # model override (e.g. "claude-opus-4-20250514", "o4-mini")
    agent_command: str = ""    # custom command (overrides preset+model)

    # context
    notes: str = ""

    # setup
    setup_command: str = ""

    # context (program file)
    program_file: str = ""

    # loop
    max_iterations: int = 50
    stop_after_discards: int = 10
    branch_per_session: bool = True

    @classmethod
    def from_toml(cls, path: str = CONFIG_FILE) -> Config:
        with open(path, "rb") as f:
            raw = tomllib.load(f)

        p = raw.get("problem", {})
        v = raw.get("verify", {})
        s = raw.get("scope", {})
        a = raw.get("agent", {})
        c = raw.get("context", {})
        lo = raw.get("loop", {})
        se = raw.get("setup", {})

        return cls(
            goal=p.get("goal", ""),
            metric=p.get("metric", "metric"),
            direction=p.get("direction", "higher"),
            verify_command=v.get("command", "python verify.py"),
            verify_pattern=v.get("pattern", r"{metric}=([\d.]+)"),
            verify_timeout=v.get("timeout", 120),
            edit=s.get("edit", []),
            read=s.get("read", []),
            frozen=s.get("frozen", []),
            agent_preset=a.get("preset", ""),
            agent_model=a.get("model", ""),
            agent_command=a.get("command", ""),
            notes=c.get("notes", ""),
            setup_command=se.get("command", ""),
            program_file=c.get("program", ""),
            max_iterations=lo.get("max_iterations", 50),
            stop_after_discards=lo.get("stop_after_discards", 10),
            branch_per_session=lo.get("branch_per_session", True),
        )

    @property
    def metric_pattern(self) -> str:
        return self.verify_pattern.replace("{metric}", re.escape(self.metric))

    def is_better(self, new: float, old: float) -> bool:
        if self.direction == "lower":
            return new < old
        return new > old


# ── Git helpers ─────────────────────────────────────────────────────────────

def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        detail = stderr or stdout or f"exit {result.returncode}"
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def git_short_hash() -> str:
    return git("rev-parse", "--short", "HEAD")


def git_has_changes() -> bool:
    return bool(git("status", "--porcelain"))


def _normalize_scope_path(path: str) -> str:
    return Path(path).as_posix().rstrip("/")


def _absolute_candidates(path: str) -> list[Path]:
    raw = Path(path)
    bases = [Path.cwd()]
    if GIT_ROOT is not None and GIT_ROOT != Path.cwd():
        bases.append(GIT_ROOT)
    return [candidate.resolve() for candidate in [(base / raw) for base in bases]]


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _path_in_scope(path: str, scope: list[str]) -> bool:
    for allowed in scope:
        for changed_path in _absolute_candidates(path):
            for allowed_path in _absolute_candidates(allowed):
                if changed_path == allowed_path or _is_relative_to(changed_path, allowed_path):
                    return True
    return False


def git_changed_paths() -> list[str]:
    lines = git("status", "--porcelain").splitlines()
    paths: list[str] = []
    for line in lines:
        if not line:
            continue
        # Porcelain format: XY PATH — XY is exactly 2 status chars.
        # When Y is a space (e.g. "M "), line[3:] eats the first path char.
        # Strip the 2-char status prefix and any leading spaces safely.
        raw_path = line[2:].lstrip(" ")
        if " -> " in raw_path:
            raw_path = raw_path.split(" -> ", 1)[1]
        paths.append(raw_path)
    return paths


def git_has_changes_in_scope(scope: list[str]) -> bool:
    return any(_path_in_scope(path, scope) for path in git_changed_paths())


def git_changed_paths_outside_scope(scope: list[str], ignore: set[str] | None = None) -> list[str]:
    ignore = ignore or set()
    return sorted({
        path for path in git_changed_paths()
        if not _path_in_scope(path, scope) and path not in ignore
    })


def git_path_is_tracked(path: str) -> bool:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", path],
        capture_output=True, text=True, timeout=30,
    )
    return result.returncode == 0


def git_commit(paths: list[str], message: str) -> str:
    git("add", "--", *paths)
    git("commit", "-m", message, "--only", "--", *paths)
    return git_short_hash()


def _snapshot_target(snapshot_dir: Path, path: str) -> Path:
    return snapshot_dir / Path(path)


def _clear_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _copy_path(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)


def snapshot_paths(paths: list[str]) -> Path:
    snapshot_dir = Path(tempfile.mkdtemp(prefix="autoresearch_state_"))
    refresh_snapshot(paths, snapshot_dir)
    return snapshot_dir


def refresh_snapshot(paths: list[str], snapshot_dir: Path) -> None:
    for path in paths:
        snapshot_path = _snapshot_target(snapshot_dir, path)
        _clear_path(snapshot_path)

        src = Path(path)
        if src.exists():
            _copy_path(src, snapshot_path)


def _paths_equal(current: Path, snapshot: Path) -> bool:
    if current.exists() != snapshot.exists():
        return False
    if not current.exists():
        return True
    if current.is_dir() != snapshot.is_dir():
        return False
    if current.is_file():
        return filecmp.cmp(current, snapshot, shallow=False)

    cmp = filecmp.dircmp(current, snapshot)
    if cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files:
        return False
    return all(
        _paths_equal(Path(sub.left), Path(sub.right))
        for sub in cmp.subdirs.values()
    )


def paths_match_snapshot(paths: list[str], snapshot_dir: Path) -> bool:
    return all(
        _paths_equal(Path(path), _snapshot_target(snapshot_dir, path))
        for path in paths
    )


def revert_paths_to_snapshot(paths: list[str], snapshot_dir: Path) -> None:
    if not paths:
        return

    for path in sorted(set(paths)):
        target = Path(path)
        snapshot_path = _snapshot_target(snapshot_dir, path)

        _clear_path(target)

        if snapshot_path.exists():
            _copy_path(snapshot_path, target)


def git_recent_log(n: int = 10) -> str:
    # Show only commits made in this session branch (after branch creation)
    try:
        branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()
        if branch.startswith("autoresearch/"):
            # Use reflog to find the commit where this branch was created
            reflog = git("reflog", "show", branch, "--format=%H %gs").strip()
            for line in reversed(reflog.splitlines()):
                if "Created from" in line:
                    branch_start = line.split()[0]
                    log = git("log", "--oneline", f"-{n}", f"{branch_start}..HEAD")
                    if log.strip():
                        return log
                    return "(new session — no iterations yet)"
    except Exception:
        pass
    return git("log", "--oneline", f"-{n}")


def git_diff_summary(paths: list[str] | None = None) -> str:
    """One-line summary of current uncommitted changes."""
    args = ["diff", "--stat", "HEAD"]
    if paths:
        args += ["--", *paths]
    stat = git(*args).splitlines()
    # Last line is the summary e.g. "2 files changed, 14 insertions(+), 8 deletions(-)"
    if stat:
        return stat[-1].strip()
    fallback = ["diff", "--name-only"]
    if paths:
        fallback += ["--", *paths]
    return git(*fallback)


def git_current_branch() -> str:
    return git("rev-parse", "--abbrev-ref", "HEAD")


def git_create_session_branch() -> str:
    """Create and switch to a dated session branch."""
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    branch = f"autoresearch/{stamp}"
    git("checkout", "-b", branch)
    return branch


# ── Setup ──────────────────────────────────────────────────────────────────

def run_setup(cfg: Config) -> bool:
    """Run the one-time setup command. Returns True on success."""
    if not cfg.setup_command:
        return True
    print("── Running setup...")
    cmd = cfg.setup_command.replace("{python}", sys.executable)
    try:
        result = subprocess.run(
            cmd, shell=True, timeout=600,
        )
        if result.returncode != 0:
            print(f"[!] Setup failed (exit {result.returncode})")
            return False
        print("  setup complete.")
        return True
    except subprocess.TimeoutExpired:
        print("[!] Setup timed out after 600s")
        return False


# ── Verify ──────────────────────────────────────────────────────────────────

def run_verify(cfg: Config) -> float | None:
    """Run the verify command and extract the metric value."""
    cmd = cfg.verify_command.replace("{python}", sys.executable)
    proc = subprocess.Popen(
        cmd, shell=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=cfg.verify_timeout)
    except subprocess.TimeoutExpired:
        # Kill the entire process group to avoid zombie children
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            proc.kill()
        proc.wait()
        print(f"  [!] verify timed out after {cfg.verify_timeout}s")
        return None

    output = stdout + stderr

    if proc.returncode != 0:
        print(f"  [!] verify failed (exit {proc.returncode})")
        print(f"  stderr: {stderr[:500]}")
        return None

    match = re.search(cfg.metric_pattern, output)
    if not match:
        print(f"  [!] could not find '{cfg.metric}=...' in verify output")
        print(f"  output: {output[:500]}")
        return None

    return float(match.group(1))


# ── Agent ───────────────────────────────────────────────────────────────────

def build_prompt(cfg: Config, iteration: int, best: float, history: str) -> str:
    """Build the full context prompt for the agent."""
    lines = [
        f"# Goal: {cfg.goal}",
        f"# Metric: {cfg.metric} ({'higher' if cfg.direction == 'higher' else 'lower'} is better)",
        f"# Current best: {cfg.metric}={best}",
        "",
        "## Files you can edit:",
        *[f"  - {f}" for f in cfg.edit],
        "",
        "## Read-only (for context):",
        *[f"  - {f}" for f in cfg.read],
        "",
        "## Frozen (never touch):",
        *[f"  - {f}" for f in cfg.frozen],
        "",
    ]

    if cfg.notes.strip():
        lines += ["## Domain context:", cfg.notes.strip(), ""]

    if cfg.program_file:
        program_path = Path(cfg.program_file)
        if program_path.exists():
            lines += [
                "## Program (agent guidelines):",
                program_path.read_text().strip(),
                "",
            ]

    lines += [
        "## Recent experiment history:",
        history if history else "(no history yet)",
        "",
        "## Recent git log:",
        git_recent_log(),
        "",
        "## Rules:",
        "1. Make exactly ONE change. Do not combine multiple hypotheses.",
        "2. Write your hypothesis to .autoresearch_hypothesis — one short line, max 80 chars (e.g. 'Replace MA crossover with RSI mean-reversion filter').",
        "3. ONLY modify files listed under 'Files you can edit'. Do NOT touch any other file.",
        "4. The editable path list is exact. Do not invent similar paths, prefixes, parent dirs, or typos.",
        "5. If only one file is editable, modify that exact file and no other path.",
        "6. Do NOT run the verify command — the framework handles that.",
        f"7. Iteration: {iteration}",
    ]
    return "\n".join(lines)


def call_agent(cfg: Config, prompt: str) -> str:
    """Invoke the agent command with the given prompt. Returns hypothesis if found."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, prefix="autoresearch_prompt_"
    ) as f:
        f.write(prompt)
        prompt_file = f.name

    try:
        cmd = cfg.agent_command.replace("{prompt_file}", prompt_file)
        result = subprocess.run(cmd, shell=True, timeout=600)
    finally:
        os.unlink(prompt_file)

    if result.returncode != 0:
        print(f"  [!] agent command exited with code {result.returncode}")

    # Read hypothesis written by agent, then remove the file
    hypothesis = ""
    h_path = Path(HYPOTHESIS_FILE)
    if h_path.exists():
        hypothesis = h_path.read_text().strip().splitlines()[0]
        h_path.unlink()

    return hypothesis


def resolve_agent_command(
    cfg: Config,
    cli_command: str | None,
    cli_agent: str | None,
    cli_model: str | None,
) -> str:
    """Resolve the final agent command.

    Priority: CLI --agent-command > env > config command > preset+model.
    CLI --agent and --model override config preset/model respectively.
    """
    # 1. Explicit command always wins
    if cli_command:
        return cli_command

    env_override = os.environ.get(AGENT_COMMAND_ENV, "").strip()
    if env_override:
        return env_override

    if cfg.agent_command:
        return cfg.agent_command

    # 2. Build from preset + model
    preset = cli_agent or cfg.agent_preset or "claude"
    model = cli_model or cfg.agent_model or ""
    return build_agent_command(preset, model)


# ── Results log ─────────────────────────────────────────────────────────────

HEADER = "iteration\tcommit\tmetric\tdelta\tstatus\tdescription\n"


def init_results(cfg: Config) -> None:
    """Create the results file if it doesn't exist."""
    if not Path(RESULTS_FILE).exists():
        with open(RESULTS_FILE, "w") as f:
            f.write(f"# metric: {cfg.metric}  direction: {cfg.direction}\n")
            f.write(HEADER)


def append_result(
    iteration: int,
    commit: str,
    metric: float,
    delta: float,
    status: str,
    description: str,
) -> None:
    with open(RESULTS_FILE, "a") as f:
        f.write(
            f"{iteration}\t{commit}\t{metric:.6f}\t{delta:+.6f}"
            f"\t{status}\t{description}\n"
        )


def read_last_lines(n: int = 15) -> str:
    """Return the last N lines of the results file."""
    path = Path(RESULTS_FILE)
    if not path.exists():
        return ""
    lines = path.read_text().strip().splitlines()
    return "\n".join(lines[-n:])


def count_consecutive_discards() -> int:
    """Count how many consecutive discards at the tail of the log."""
    path = Path(RESULTS_FILE)
    if not path.exists():
        return 0
    lines = path.read_text().strip().splitlines()
    count = 0
    for line in reversed(lines):
        if line.startswith("#") or line.startswith("iteration"):
            break
        parts = line.split("\t")
        if len(parts) >= 5 and parts[4] in ("discard", "crash"):
            count += 1
        else:
            break
    return count


def get_last_iteration() -> int:
    """Return the last iteration number from the results file."""
    path = Path(RESULTS_FILE)
    if not path.exists():
        return -1
    lines = path.read_text().strip().splitlines()
    for line in reversed(lines):
        if line.startswith("#") or line.startswith("iteration"):
            continue
        parts = line.split("\t")
        if parts and parts[0].isdigit():
            return int(parts[0])
    return -1


def get_best_metric(cfg: Config) -> float | None:
    """Return the best metric value from the results file."""
    path = Path(RESULTS_FILE)
    if not path.exists():
        return None
    lines = path.read_text().strip().splitlines()
    best = None
    for line in lines:
        if line.startswith("#") or line.startswith("iteration"):
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            try:
                val = float(parts[2])
            except ValueError:
                continue
            if best is None or cfg.is_better(val, best):
                best = val
    return best


# ── Main loop ───────────────────────────────────────────────────────────────

def run_baseline(cfg: Config) -> float | None:
    """Measure and record the baseline metric."""
    print("── Measuring baseline...")
    metric = run_verify(cfg)
    if metric is None:
        print("[!] Baseline verification failed. Fix your verify command.")
        return None

    print(f"  baseline {cfg.metric}={metric:.6f}")
    init_results(cfg)
    append_result(
        iteration=0,
        commit=git_short_hash(),
        metric=metric,
        delta=0.0,
        status="baseline",
        description=f"initial measurement — {cfg.metric}={metric:.6f}",
    )
    return metric


def run_iteration(
    cfg: Config,
    iteration: int,
    best: float,
    accepted_snapshot: Path,
    initial_dirty_outside_scope: set[str],
) -> float:
    """Run one modify→verify→decide cycle. Returns the new best metric."""
    print(f"\n{'='*60}")
    print(f"  Iteration {iteration}")
    print(f"  Current best: {cfg.metric}={best:.6f}")
    print(f"{'='*60}")

    # 1. Build prompt with full context
    history = read_last_lines()
    prompt = build_prompt(cfg, iteration, best, history)

    # 2. Call agent to modify files
    print("  [1/3] Agent is working...")
    hypothesis = call_agent(cfg, prompt)
    ignored_paths = set(initial_dirty_outside_scope)
    ignored_paths.add(HYPOTHESIS_FILE)
    disallowed_paths = git_changed_paths_outside_scope(cfg.edit, ignore=ignored_paths)
    if disallowed_paths:
        print(f"  [!] Agent touched out-of-scope files, reverting them: {', '.join(disallowed_paths)}")
        # Revert only out-of-scope files: restore tracked, remove untracked
        for p in disallowed_paths:
            if git_path_is_tracked(p):
                subprocess.run(["git", "checkout", "--", p], capture_output=True)
            else:
                try:
                    Path(p).unlink()
                except FileNotFoundError:
                    pass

    # 3. Check if agent actually changed anything
    if paths_match_snapshot(cfg.edit, accepted_snapshot):
        print("  [!] Agent made no changes. Skipping.")
        append_result(iteration, "-", best, 0.0, "no-op", "agent made no changes")
        return best

    # Capture what changed before verifying
    change_summary = git_diff_summary(cfg.edit)
    description = f"{hypothesis} | {change_summary}" if hypothesis else change_summary
    print(f"  hypothesis: {hypothesis}" if hypothesis else f"  change: {change_summary}")

    # 4. Verify
    print("  [2/3] Verifying...")
    metric = run_verify(cfg)

    if metric is None:
        print("  [!] Verification failed. Reverting.")
        revert_paths_to_snapshot(cfg.edit, accepted_snapshot)
        append_result(iteration, "-", best, 0.0, "crash", description)
        return best

    delta = metric - best
    improved = cfg.is_better(metric, best)

    # 5. Decide: keep or discard
    print(f"  [3/3] {cfg.metric}={metric:.6f} (delta={delta:+.6f})")

    if improved:
        print(f"  >>> KEEP (improved)")
        commit_hash = git_commit(
            cfg.edit,
            f"iteration {iteration}: {cfg.metric}={metric:.6f} ({delta:+.6f})\n\n{description}"
        )
        refresh_snapshot(cfg.edit, accepted_snapshot)
        append_result(iteration, commit_hash, metric, delta, "keep", description)
        return metric
    else:
        direction_word = "higher" if cfg.direction == "higher" else "lower"
        print(f"  <<< DISCARD (not {direction_word})")
        revert_paths_to_snapshot(cfg.edit, accepted_snapshot)
        append_result(iteration, "-", metric, delta, "discard", description)
        return best


def main() -> None:
    parser = argparse.ArgumentParser(description="autoresearch — autonomous iterative optimization")
    parser.add_argument("--once", action="store_true", help="run a single iteration")
    parser.add_argument("-n", "--iterations", type=int, default=None, help="number of iterations to run")
    parser.add_argument("--baseline", action="store_true", help="measure baseline only")
    parser.add_argument("--config", default=CONFIG_FILE, help="path to config file")
    parser.add_argument(
        "--agent-command",
        default=None,
        help=f"override agent command (raw); takes precedence over ${AGENT_COMMAND_ENV}",
    )
    presets = ", ".join(sorted(AGENT_PRESETS))
    parser.add_argument(
        "--agent",
        default=None,
        help=f"agent preset ({presets}); overrides config [agent].preset",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="model name for the agent preset; overrides config [agent].model",
    )
    args = parser.parse_args()

    cfg = Config.from_toml(args.config)
    cfg.agent_command = resolve_agent_command(cfg, args.agent_command, args.agent, args.model)

    print(f"autoresearch — {cfg.goal}")
    print(f"  metric: {cfg.metric} ({cfg.direction} is better)")
    print(f"  verify: {cfg.verify_command}")
    print(f"  agent:  {cfg.agent_command[:60]}...")
    print()

    # Ensure we're in a git repo (walk up to find .git)
    git_root = next(
        (p for p in [Path.cwd(), *Path.cwd().parents] if (p / ".git").exists()),
        None,
    )
    if git_root is None:
        sys.exit("[!] Not a git repository. Run 'git init' first.")
    global GIT_ROOT
    GIT_ROOT = git_root.resolve()

    initial_dirty_paths = set(git_changed_paths())
    initial_dirty_outside_scope = {
        path for path in initial_dirty_paths
        if not _path_in_scope(path, cfg.edit)
    }
    if initial_dirty_outside_scope:
        print("  [!] Existing changes outside scope will be ignored:")
        for path in sorted(initial_dirty_outside_scope):
            print(f"      {path}")

    # Create session branch (isolates experiments)
    if cfg.branch_per_session and not args.baseline:
        branch = git_create_session_branch()
        print(f"  branch: {branch}")
    else:
        print(f"  branch: {git_current_branch()}")

    # Run one-time setup
    if cfg.setup_command:
        if not run_setup(cfg):
            sys.exit(1)

    # Get or measure baseline
    init_results(cfg)
    best = get_best_metric(cfg)

    if args.baseline:
        if best is not None:
            print(f"[!] Baseline already recorded ({cfg.metric}={best:.6f}). Delete {RESULTS_FILE} to re-baseline.")
            return
        best = run_baseline(cfg)
        if best is None:
            sys.exit(1)
        return

    if best is None:
        best = run_baseline(cfg)
        if best is None:
            sys.exit(1)

    accepted_snapshot = snapshot_paths(cfg.edit)

    try:
        start_iteration = get_last_iteration() + 1
        if args.once:
            n = 1
        elif args.iterations is not None:
            n = args.iterations
        else:
            n = cfg.max_iterations
        max_iter = start_iteration + n

        for i in range(start_iteration, max_iter):
            # Check consecutive discards
            consec = count_consecutive_discards()
            if consec >= cfg.stop_after_discards:
                print(f"\n[!] {consec} consecutive discards. Agent appears stuck. Stopping.")
                break

            best = run_iteration(cfg, i, best, accepted_snapshot, initial_dirty_outside_scope)
    finally:
        shutil.rmtree(accepted_snapshot, ignore_errors=True)

    print(f"\nDone. Best {cfg.metric}={best:.6f}")
    print(f"Results: {RESULTS_FILE}")


if __name__ == "__main__":
    main()
