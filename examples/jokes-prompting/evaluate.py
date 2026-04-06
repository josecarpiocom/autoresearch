#!/usr/bin/env python3
"""
evaluate.py — prompt engineering para chistes.

1. Lee prompt.txt (el prompt optimizado por el agente).
2. Lo envía a un LLM generador para producir 10 chistes.
3. Un único juez puntúa cada chiste de 1 a 10 con crítica detallada.
4. Reporta el score del mejor chiste.

Requiere:
    OPENROUTER_API_KEY env var
    Opcional: OPENROUTER_MODEL (modelo juez, default: gemini-3.1-pro-preview)
    Opcional: OPENROUTER_GENERATOR_MODEL (modelo generador, default: gemini-3.1-pro-preview)
"""

import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

# Load .env file if present
def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

_project_root = Path(__file__).resolve().parent.parent.parent
_load_env_file(_project_root / ".env")
_load_env_file(Path(__file__).parent / ".env")

PROMPT_FILE = "prompt.txt"
RUBRIC_FILE = "rubric.md"
FEEDBACK_FILE = "judge_feedback.txt"
GENERATOR_OUTPUT_FILE = "generator_output.txt"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3.1-pro-preview"


# ── API ─────────────────────────────────────────────────────────────────────

def call_openrouter(system_prompt: str, user_prompt: str, model: str, api_key: str,
                    temperature: float = 0.3, max_tokens: int = 4096) -> str:
    resp = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


# ── Step 1: Generate jokes from prompt ──────────────────────────────────────

def generate_jokes(prompt: str, model: str, api_key: str) -> list[str]:
    system = (
        "Eres un escritor de comedia español. Sigues las instrucciones al pie de la letra. "
        "Produces exactamente lo que se te pide, sin meta-comentarios ni explicaciones."
    )
    output = call_openrouter(system, prompt, model, api_key, temperature=0.7)

    Path(GENERATOR_OUTPUT_FILE).write_text(output, encoding="utf-8")

    jokes = [j.strip() for j in output.split("---") if j.strip()]
    return jokes


# ── Step 2: Judge each joke ─────────────────────────────────────────────────

JUDGE_SYSTEM = (
    "Eres un crítico de comedia español exigente pero justo. "
    "Tienes 20 años de experiencia evaluando humor en clubs, TV y redes. "
    "Puntúas de 1 a 10 y das crítica constructiva breve pero útil: "
    "qué funciona, qué falla, y cómo mejorar el chiste."
)


def build_judge_prompt(joke: str, rubric: str) -> str:
    return f"""Evalúa el siguiente chiste.

{rubric}

---

## Chiste:

{joke}

---

Responde con este formato exacto:

**Fortalezas:** (qué funciona bien, 1-2 frases)
**Debilidades:** (qué falla o se puede mejorar, 1-2 frases)
**Consejo:** (una sugerencia concreta para mejorar, 1 frase)

score=N

Donde N es tu puntuación de 1 a 10. DEBE ser tu última línea."""


def judge_joke(joke: str, rubric: str, model: str, api_key: str) -> tuple[int | None, str]:
    try:
        output = call_openrouter(JUDGE_SYSTEM, build_judge_prompt(joke, rubric), model, api_key)
        matches = re.findall(r"score=(\d+)", output)
        if not matches:
            return None, output
        score = int(matches[-1])
        score = max(1, min(10, score))
        return score, output
    except Exception as e:
        return None, f"Error: {e}"


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        print("[!] OPENROUTER_API_KEY not set", file=sys.stderr)
        print("score=0")
        sys.exit(1)

    judge_model = os.environ.get("OPENROUTER_MODEL", "").strip() or DEFAULT_MODEL
    generator_model = os.environ.get("OPENROUTER_GENERATOR_MODEL", "").strip() or DEFAULT_MODEL

    prompt = Path(PROMPT_FILE).read_text(encoding="utf-8").strip()
    rubric = Path(RUBRIC_FILE).read_text(encoding="utf-8").strip()

    if not prompt:
        print("score=0")
        return

    # Step 1: Generate jokes
    print(f"[gen] Generando chistes con {generator_model}...", file=sys.stderr)
    jokes = generate_jokes(prompt, generator_model, api_key)

    if not jokes:
        print("[!] El generador no produjo chistes válidos", file=sys.stderr)
        print("score=0")
        return

    print(f"[gen] {len(jokes)} chiste(s) generados", file=sys.stderr)

    # Step 2: Judge all jokes in parallel
    print(f"[juez] Evaluando con {judge_model}...", file=sys.stderr)

    results: list[tuple[int, int | None, str]] = []
    with ThreadPoolExecutor(max_workers=min(10, len(jokes))) as pool:
        futures = {
            pool.submit(judge_joke, joke, rubric, judge_model, api_key): i
            for i, joke in enumerate(jokes)
        }
        for future in as_completed(futures):
            i = futures[future]
            score, feedback = future.result()
            results.append((i, score, feedback))
            label = f"score={score}" if score is not None else "ERROR"
            print(f"  [chiste {i+1}] {label}", file=sys.stderr)

    results.sort(key=lambda x: x[0])

    # Step 3: Find the winner
    scored = [(i, s, f) for i, s, f in results if s is not None]
    if not scored:
        print("score=0")
        return

    best_idx, best_score, _ = max(scored, key=lambda x: x[1])

    # Save feedback for ALL jokes (so the agent sees everything)
    with open(FEEDBACK_FILE, "w", encoding="utf-8") as f:
        f.write(f"# Evaluación de {len(jokes)} chistes — Ganador: chiste {best_idx + 1} (score={best_score}/10)\n\n")
        for i, score, feedback in results:
            f.write(f"{'=' * 60}\n")
            f.write(f"## Chiste {i + 1} — score={score}/10\n")
            f.write(f"{'=' * 60}\n\n")
            f.write(f"{jokes[i]}\n\n")
            f.write(f"**Crítica:**\n{feedback.strip()}\n\n")

    # Stats
    all_scores = [s for _, s, _ in scored]
    avg = sum(all_scores) / len(all_scores)

    print(f"jokes_generated={len(jokes)}")
    print(f"jokes_scored={len(scored)}")
    print(f"best_joke={best_idx + 1}")
    print(f"best_score={best_score}")
    print(f"avg_score={avg:.1f}")
    print(f"all_scores={sorted(all_scores)}")
    print(f"score={best_score}")


if __name__ == "__main__":
    main()
