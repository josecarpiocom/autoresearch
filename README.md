# autoresearch

Autonomous iterative optimization with Git in the loop.

> The value in AI agents is not in the model call -- it's in the feedback loop. A model that edits, measures, and keeps only what works will outperform a model that generates once and hopes for the best. Every time.

Everybody uses AI to generate code. Write a prompt, get an output, ship it. One shot.

But ask them what happens when the output is wrong. How they measure quality. How they iterate without regressing. How they keep the good parts and discard the bad. Silence.

**Because there was no loop. And without a loop, there is no compounding.**

autoresearch is a Python framework that turns any measurable task into an autonomous optimization loop. An agent edits code. A verify command measures the result. The framework keeps only changes that improve the target metric. Git is the memory.

Karpathy's [autoresearch](https://github.com/karpathy/autoresearch) introduced this idea for ML training -- agents iterating on research code against a loss function. Powerful concept. But limited to one domain and one agent.

The core insight is more general than ML. The pattern **edit, measure, keep or discard** works anywhere you have a metric. Trading strategies. Prompt engineering. Algorithm design. Configuration tuning. Content optimization. Any problem where you can write a command that prints a number.

This is a Python rewrite from scratch. Not a fork. The original runs as a shell script with hardcoded assumptions about ML workflows. This version is a deterministic Python framework with explicit contracts: TOML config, scope enforcement, multi-agent support, structured results, and Git as the checkpoint system. The flow is predictable and inspectable at every step.

---

## Why This Matters

The industry treats AI agents as single-turn tools. Prompt in, code out, done. The interesting problems don't work that way.

The pattern is always the same: **edit, measure, decide, repeat.** autoresearch makes that pattern automatic.

What makes it different:

- **Git is the checkpoint system.** Every improvement is a commit. Every regression is a revert. `git log` is your experiment history.
- **The metric is the only judge.** No subjective evaluation. The verify command prints a number. The number decides.
- **Any agent, any model.** Claude, Codex, Gemini, Cursor, or a custom command. The framework doesn't care who edits -- only whether the edit improved the metric.
- **Scope enforcement.** The agent can only touch files you allow. Everything else gets reverted automatically.
- **Deterministic Python.** No shell scripts, no hidden state. TOML config, explicit contracts, structured results. You can read the loop top to bottom and know exactly what happens.
- **Zero dependencies.** Python 3.11+ and Git. That's it. Example-specific deps are separate.

---

## Beyond ML Training

Karpathy's original targets ML research: an agent edits training code, measures loss, iterates. That's one application. The framework doesn't care what the metric represents. If you can measure it, you can optimize it.

**Trading strategies.** The agent edits a strategy file. The verifier runs it against out-of-sample market data with transaction costs and reports the Sharpe ratio. The agent never sees where the train/test split is. 40 iterations of autonomous strategy refinement, each one committed to Git if it improves the metric.

**Prompt engineering.** The agent doesn't generate content -- it generates the *instructions* that generate content. A separate model produces output from those instructions. A judge scores the output. The agent reads the scores and critique, then refines the instructions. This is meta-optimization: the model improving its own prompts through measured feedback. The transferable artifact is the prompt, not a single piece of content.

**Algorithm optimization.** An agent edits a sorting algorithm. The verifier benchmarks it against a fixed dataset and reports execution time. The baseline is bubble sort. After 20 iterations, you have a Git history of incremental algorithmic improvements, each one provably faster than the last.

**Configuration tuning.** Edit a config file. Verify runs the system and reports latency, throughput, error rate, or whatever matters. The agent explores the parameter space one change at a time, keeping only what improves the target metric.

**Infrastructure as code.** Edit Terraform, Kubernetes manifests, or CI pipelines. Verify deploys to a staging environment and runs a health check. The metric could be deployment time, resource cost, or test pass rate.

**Content optimization.** Edit a blog post, landing page copy, or email template. Verify sends it to a panel of judge models that score readability, persuasion, or engagement. The agent iterates based on structured feedback.

**Compiler flags and build optimization.** Edit compiler flags or build configuration. Verify compiles and benchmarks. The metric is binary size, compilation time, or runtime performance.

**SQL query optimization.** Edit a query. Verify runs `EXPLAIN ANALYZE` and extracts execution time. The agent rewrites joins, adds indexes, restructures CTEs -- keeping only changes that make the query faster.

The pattern is always the same. What changes is the metric and the domain. The framework is indifferent.

---

## The Loop

```text
1. Build prompt (goal + scope + history + git log + program.md)
2. Call agent
3. Enforce scope (revert unauthorized edits)
4. Verify (run command, extract metric)
5. If improved: git commit. If not: git revert.
6. Append result to autoresearch-results.tsv
7. Repeat
```

Every iteration produces one row in the results file and, if successful, one commit. After 20 iterations you have a clear trail: what was tried, what worked, what didn't, and why.

---

## Quick Start

```bash
git clone https://github.com/josecarpiocom/autoresearch.git
cd autoresearch
```

### Trading -- maximize out-of-sample Sharpe

```bash
cd examples/trading
pip install pandas numpy requests
python download_data.py
python ../../run.py --baseline
python ../../run.py -n 10
python ../../analyze.py
```

The agent edits `train.py` (a trading strategy). The frozen verifier `prepare.py` splits data into train/test, computes the Sharpe ratio with transaction costs, and reports the out-of-sample metric. The agent never sees where the split is.

### Prompt engineering -- optimize a joke-writing prompt

```bash
cd examples/jokes-prompting
export OPENROUTER_API_KEY="sk-or-..."
python ../../run.py --baseline
python ../../run.py -n 10
python ../../analyze.py
```

The agent doesn't write jokes -- it writes the **prompt** that writes jokes. `evaluate.py` sends the prompt to a generator LLM, gets 10 jokes, and a judge scores each one 1-10 with detailed critique. The agent reads the critique and iterates on the prompt.

This is the pattern that LLMs have always struggled with: **meta-optimization.** The model isn't generating content. It's generating the instructions that generate content. And improving those instructions based on measured results.

---

## Problem Definition

Everything is driven by `autoresearch.toml`:

```toml
[problem]
goal = "Maximize out-of-sample Sharpe ratio"
metric = "sharpe"
direction = "higher"

[verify]
command = "{python} prepare.py"
pattern = '{metric}=(-?[\d.]+)'
timeout = 120

[scope]
edit = ["train.py"]
read = ["program.md"]
frozen = ["data/", "prepare.py"]

[agent]
preset = "claude"

[context]
notes = "Domain notes injected into every prompt."
program = "program.md"

[loop]
max_iterations = 40
stop_after_discards = 10
branch_per_session = true
```

The contracts are simple:

- **Agent**: receives a prompt, edits only files in `scope.edit`, optionally writes a hypothesis to `.autoresearch_hypothesis`.
- **Verify**: prints output containing `metric=VALUE`. Must not mutate the repo.
- **Framework**: compares, commits or reverts, records everything.

---

## Agent Support

The framework supports multiple agent CLIs via presets:

| Preset | Default Model | Command |
|--------|--------------|---------|
| `claude` | `claude-sonnet-4-6` | Claude Code CLI |
| `codex` | `gpt-5.4` | OpenAI Codex CLI |
| `gemini` | `gemini-3.1-pro-preview` | Google Gemini CLI |
| `cursor` | -- | Cursor CLI (beta) |

```bash
python run.py --agent claude
python run.py --agent codex --model gpt-5.3-codex
python run.py --agent gemini
python run.py --agent-command "cat {prompt_file} | my_custom_agent -"
```

Override priority: `--agent-command` > env `AUTORESEARCH_AGENT_COMMAND` > config `agent.command` > preset + model.

---

## Two Example Patterns

The included examples demonstrate two fundamentally different optimization patterns. Every new problem you create will be a variation of one of these.

### Direct optimization (trading)

```text
Agent edits code --> Verifier measures metric --> Keep or discard
```

One model, one file, one metric. The agent modifies `train.py` directly. The frozen verifier runs the strategy against hidden test data with transaction costs. The agent never sees where the split is -- it optimizes blind against the out-of-sample Sharpe.

This is the pattern for: algorithm optimization, config tuning, SQL queries, compiler flags, build systems. Anything where the agent edits the artifact directly and a deterministic benchmark scores the result.

### Meta-optimization (jokes-prompting)

```text
Agent edits prompt --> Generator produces content --> Judge scores content --> Keep or discard
```

Three models in the loop: the agent (optimizer), the generator (executor), and the judge (evaluator). The agent optimizes **instructions**, not content. The content is generated fresh every iteration. The judge provides structured critique that feeds back into the next prompt iteration.

This is the pattern for: prompt engineering, content optimization, email copy, landing pages, documentation. Anything where the artifact is a set of instructions and the quality is measured through the output those instructions produce.

This is the more interesting pattern. It produces a transferable artifact (the prompt), not a single piece of content. And it compounds: each iteration's critique makes the next prompt sharper.

---

## Results and Analysis

Every iteration appends to `autoresearch-results.tsv`:

```text
iteration  commit   metric      delta       status   description
0          a1b2c3d  -2.234116   +0.000000   baseline initial measurement
1          e4f5g6h  -1.800000   +0.434116   keep     faster MA regime filter
2          -        -2.500000   -0.265884   discard  breakout threshold tweak
```

Statuses: `baseline`, `keep`, `discard`, `crash`, `no-op`.

`analyze.py` prints a summary: baseline vs best, improvement percentage, keep/discard ratio, top improvements, frontier iterations, and an ASCII chart.

---

## Creating a New Problem

1. Create a directory.
2. Write a verify script that prints the metric.
3. Write an editable file for the agent.
4. Add `autoresearch.toml`.
5. Optionally add `program.md` with domain guidance.
6. Commit everything. Run.

Good problems: parameter-free algorithm optimization, trading strategies on fixed data, prompt engineering with deterministic evaluation, code generation with measurable scores.

Bad problems: nondeterministic evaluations, metrics with high variance, tasks where verify mutates repo state.

---

## Repository Structure

```text
autoresearch/
├── run.py                          # Framework core (~800 lines)
├── analyze.py                      # Post-run analysis
└── examples/
    ├── trading/                    # Direct optimization
    │   ├── autoresearch.toml
    │   ├── train.py                # Editable strategy
    │   ├── prepare.py              # Frozen verifier
    │   ├── program.md              # Agent guidance
    │   └── download_data.py        # Data fetcher
    └── jokes-prompting/            # Meta-optimization
        ├── autoresearch.toml
        ├── prompt.txt              # Editable prompt
        ├── evaluate.py             # Generator + judge
        ├── rubric.md               # Scoring criteria
        └── program.md              # Theme and constraints
```

---

## Requirements

- Python 3.11+ (or 3.10 with `tomli`)
- Git
- At least one agent CLI: [Claude Code](https://claude.ai/code), [Codex](https://github.com/openai/codex), [Gemini CLI](https://github.com/google-gemini/gemini-cli), or a custom command

Example-specific:
- `trading/`: `pip install pandas numpy requests`
- `jokes-prompting/`: `OPENROUTER_API_KEY` env var

---

## CLI Reference

```bash
python run.py                    # Run until max_iterations or stuck
python run.py --baseline         # Measure baseline only
python run.py --once             # Single iteration
python run.py -n 5               # Run N iterations
python run.py --agent codex      # Use Codex preset
python run.py --model claude-opus-4-6  # Override model
python run.py --config path.toml # Custom config path
```

---

## Design Principles

- Git is the memory and checkpoint system.
- The verify command is the only source of truth.
- The editable scope must stay small.
- One change per iteration is easier to reason about than batch edits.
- `program.md` is where humans inject strategy. The framework stores nothing there.

---

## What This Is Not

- Not a sandbox. The agent runs as a local shell command with full access.
- Not a training framework. It optimizes artifacts, not model weights.
- Not a CI system. It's a tight loop meant for experimentation, not deployment.

Intentionally narrow. The point is a clean feedback loop, not a platform.

---

## License

MIT
