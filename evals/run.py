"""Run the golden set against the deployed agent and publish the results.

This is the job that GitHub Actions runs. It produces the JSON the site
renders, and it fails the build when the pass rate drops below the gate, which
is the only thing that makes the number mean anything: a score nobody acts on
is decoration.

    python gishub/evals/run.py                # both languages
    python gishub/evals/run.py --lang en      # one
    python gishub/evals/run.py --limit 3      # a cheap smoke run

Every case is recorded with its trace — the SQL the agent wrote, how many rows
came back, how long the warehouse took — so a failure can be read rather than
guessed at.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import envfile  # noqa: E402

envfile.load()

from agent.fleet_agent import ask, build_client  # noqa: E402
from databricks.client import Databricks  # noqa: E402
from evals.score import score  # noqa: E402

HERE = Path(__file__).parent
GOLDENS = HERE / "goldens.jsonl"
RESULTS = HERE.parent / "site" / "data"
GATE = float(os.environ.get("GISHUB_EVAL_GATE", "0.75"))


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def make_judge(client, deployment: str):
    """A plain completion with no tools: the judge must not be able to query."""

    def judge(instructions: str, prompt: str) -> str:
        response = client.responses.create(
            model=deployment,
            instructions=instructions,
            input=prompt,
        )
        return (response.output_text or "").strip()

    return judge


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="en,es", help="comma separated: en, es")
    ap.add_argument("--limit", type=int, default=0, help="only the first N cases")
    ap.add_argument("--gate", type=float, default=GATE)
    args = ap.parse_args()

    langs = [l.strip() for l in args.lang.split(",") if l.strip()]
    cases = [json.loads(line) for line in GOLDENS.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit:
        cases = cases[: args.limit]

    deployment = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME")
    if not deployment:
        print("AZURE_AI_MODEL_DEPLOYMENT_NAME is not set. Run gishub/agent/smoke_azure.py first.")
        return 2

    client = build_client()
    db = Databricks()
    started = time.monotonic()

    if True:
        judge = make_judge(client, deployment)
        print(f"model {deployment}")
        print(f"{len(cases)} cases x {len(langs)} language(s)\n")

        records = []
        for case in cases:
            for lang in langs:
                question = case[f"question_{lang}"]
                try:
                    result = ask(client, deployment, question, db)
                except Exception as exc:  # a crashed case is a failed case
                    result = {
                        "question": question, "answer": "",
                        "error": f"{type(exc).__name__}: {exc}",
                        "trace": [], "tool_calls": 0, "blocked_calls": 0,
                        "tokens": {"input": 0, "output": 0}, "latency_ms": 0,
                    }
                # One case must never take the run down with it. A suite that
                # stops at the first surprise reports nothing about the
                # fifteen cases behind it.
                try:
                    scored = score(case, result, judge)
                except Exception as exc:
                    scored = {
                        "passed": False,
                        "detail": f"scoring failed: {type(exc).__name__}: {exc}"[:200],
                        "scoring_error": True,
                    }
                mark = "pass" if scored["passed"] else "FAIL"
                print(
                    f"  {case['id']:4s} {lang}  {mark:4s}  "
                    f"{result['latency_ms']:>6}ms  {result['tool_calls']} call(s)  "
                    f"{scored['detail'][:78]}"
                )
                records.append(
                    {
                        "id": case["id"], "lang": lang, "type": case["type"],
                        "question": question, "answer": result.get("answer", ""),
                        "passed": scored["passed"], "detail": scored["detail"],
                        "scoring": scored, "trace": result.get("trace", []),
                        "tool_calls": result.get("tool_calls", 0),
                        "blocked_calls": result.get("blocked_calls", 0),
                        "tokens": result.get("tokens", {}),
                        "latency_ms": result.get("latency_ms", 0),
                        "error": result.get("error"),
                    }
                )

    passed = sum(1 for r in records if r["passed"])
    rate = passed / len(records) if records else 0.0

    def rate_for(pred) -> dict:
        subset = [r for r in records if pred(r)]
        n = sum(1 for r in subset if r["passed"])
        return {"passed": n, "total": len(subset), "rate": round(n / len(subset), 4) if subset else None}

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": git_sha(),
        "run_url": (
            f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}"
            f"/actions/runs/{os.environ['GITHUB_RUN_ID']}"
            if os.environ.get("GITHUB_RUN_ID") else None
        ),
        "model_deployment": deployment,
        "agent_version": None,
        "gate": args.gate,
        "summary": {
            "passed": passed,
            "total": len(records),
            "rate": round(rate, 4),
            "by_type": {t: rate_for(lambda r, t=t: r["type"] == t) for t in ("aggregate", "unanswerable", "refuse")},
            "by_lang": {l: rate_for(lambda r, l=l: r["lang"] == l) for l in langs},
            "total_tokens": sum(r["tokens"].get("input", 0) + r["tokens"].get("output", 0) for r in records),
            "median_latency_ms": sorted(r["latency_ms"] for r in records)[len(records) // 2] if records else 0,
            "wall_seconds": int(time.monotonic() - started),
            "queries_blocked_by_guard": sum(r["blocked_calls"] for r in records),
        },
        "cases": records,
    }

    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "latest.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    # Only complete runs enter the history. A --limit run measures a different
    # thing, and plotting 0/2 beside 28/32 on the same axis invites a reader to
    # compare two numbers that are not comparable.
    if not args.limit and len(langs) > 1:
        history_path = RESULTS / "history.json"
        history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
        history.append(
            {k: report[k] for k in ("generated_at", "commit", "model_deployment")}
            | {"summary": report["summary"]}
        )
        history_path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        print("(partial run — not recorded in the history)")

    s = report["summary"]
    print(f"\n{'=' * 62}")
    print(f"  {passed}/{len(records)} passed  ({rate:.0%})   gate {args.gate:.0%}")
    for t, v in s["by_type"].items():
        if v["total"]:
            print(f"    {t:14s} {v['passed']}/{v['total']}")
    for l, v in s["by_lang"].items():
        if v["total"]:
            print(f"    {l:14s} {v['passed']}/{v['total']}")
    print(f"  {s['total_tokens']:,} tokens   median {s['median_latency_ms']:,} ms   {s['wall_seconds']}s wall")
    print(f"  guard blocked {s['queries_blocked_by_guard']} query(ies)")
    print("=" * 62)

    if rate < args.gate:
        print(f"\nFAILED: {rate:.0%} is below the {args.gate:.0%} gate.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
