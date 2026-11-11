"""Cloud-scale benchmarking and LLM-as-a-judge evaluation via Vertex AI Gen AI Evaluation Service.

Runs Vertex AI EvalTask over agent evaluation traces, applying custom Pointwise rubrics:
1. Groundedness (faithfulness to retrieved catalog/KB passages)
2. Beauty Advisor Persona (empathetic, consultative French dialogue)
3. Safety & Out-of-Scope Refusal (strict boundaries, no leaked secrets)
4. Claim Policy Compliance (no unauthorized refund commitments)

Logs results directly to Vertex AI Experiments for tracking and regression analysis.

Usage:
    # Run against latest ADK eval results (BYOR mode)
    uv run --env-file copilot/.env python -m evals.vertex.run_vertex_eval

    # Dry-run / mock mode without making cloud calls
    uv run python -m evals.vertex.run_vertex_eval --mock
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd

from evals.vertex.rubrics import (
    ALL_RUBRIC_METRICS,
    CLAIM_COMPLIANCE_METRIC,
    GROUNDEDNESS_METRIC,
    PERSONA_METRIC,
    SAFETY_REFUSAL_METRIC,
)

RESULTS_DIR = Path(__file__).resolve().parent / "results"
ADK_RESULTS_DIR = Path(__file__).resolve().parents[1] / "adk" / "results"
EVALSET_FILE = Path(__file__).resolve().parents[1] / "adk" / "beauty_advisor.evalset.json"


def find_latest_adk_report() -> Optional[Path]:
    """Finds the most recent ADK evaluation JSON report."""
    if not ADK_RESULTS_DIR.exists():
        return None
    files = sorted(ADK_RESULTS_DIR.glob("*.json"), key=os.path.getmtime, reverse=True)
    return files[0] if files else None


def build_evaluation_dataframe(report_path: Optional[Path]) -> pd.DataFrame:
    """Prepares the evaluation dataset DataFrame for EvalTask."""
    records = []

    if report_path and report_path.exists():
        data = json.loads(report_path.read_text(encoding="utf-8"))
        for item in data.get("results", []):
            records.append({
                "prompt": item.get("query", ""),
                "response": item.get("response", ""),
                "context": f"Tools called: {', '.join(item.get('tools', []))}",
                "case_name": item.get("case", ""),
            })

    # If no report or empty, fallback to reading the evalset definition
    if not records and EVALSET_FILE.exists():
        cases = json.loads(EVALSET_FILE.read_text(encoding="utf-8"))
        for case in cases:
            data_turn = case.get("data", [{}])[0]
            records.append({
                "prompt": data_turn.get("query", ""),
                "response": data_turn.get("reference", ""),
                "context": f"Expected tools: {json.dumps(data_turn.get('expected_tool_use', []))}",
                "case_name": case.get("name", ""),
            })

    return pd.DataFrame(records)


def run_mock_evaluation(df: pd.DataFrame) -> dict[str, Any]:
    """Simulates Vertex AI EvalTask output for local dry-runs and offline tests."""
    mock_metrics = {}
    detailed_rows = []

    for _, row in df.iterrows():
        case = row["case_name"]
        prompt = row["prompt"]
        response = row["response"]

        # Default high marks for golden responses
        g_score = 5.0
        p_score = 5.0
        s_score = 5.0
        c_score = 5.0

        if "off_topic" in case and "Tokyo" in response:
            s_score = 1.0
        if "claim" in case and "rembours" in response:
            c_score = 1.0

        detailed_rows.append({
            "case_name": case,
            "prompt": prompt,
            "groundedness_score": g_score,
            "persona_quality_score": p_score,
            "safety_refusal_score": s_score,
            "claim_compliance_score": c_score,
        })

    res_df = pd.DataFrame(detailed_rows)
    summary = {
        "mean_groundedness": float(res_df["groundedness_score"].mean()),
        "mean_persona": float(res_df["persona_quality_score"].mean()),
        "mean_safety": float(res_df["safety_refusal_score"].mean()),
        "mean_claim_compliance": float(res_df["claim_compliance_score"].mean()),
        "cases": detailed_rows,
    }
    return summary


def run_vertex_evaluation(
    df: pd.DataFrame,
    project: str,
    location: str,
    experiment_name: str,
    judge_model: str = "gemini-2.5-flash",
) -> dict[str, Any]:
    """Executes real EvalTask on Vertex AI with LLM-as-a-judge rubrics."""
    import vertexai
    from vertexai.evaluation import EvalTask

    vertexai.init(project=project, location=location)

    eval_task = EvalTask(
        dataset=df,
        metrics=ALL_RUBRIC_METRICS,
        experiment=experiment_name,
    )

    run_name = f"eval-run-{int(time.time())}"
    print(f"Submitting EvalTask to Vertex AI (experiment='{experiment_name}', run='{run_name}')...")

    eval_result = eval_task.evaluate(
        model=judge_model,
        experiment_run_name=run_name,
    )

    summary_metrics = dict(eval_result.summary_metrics)
    metrics_table = eval_result.metrics_table

    detailed = []
    if metrics_table is not None and not metrics_table.empty:
        for idx, row in metrics_table.iterrows():
            detailed.append({
                "case_name": df.iloc[idx]["case_name"] if "case_name" in df.columns else f"case_{idx}",
                "prompt": row.get("prompt", ""),
                "groundedness_score": row.get("groundedness_score/mean", row.get("groundedness_score", 0)),
                "persona_quality_score": row.get("persona_quality_score/mean", row.get("persona_quality_score", 0)),
                "safety_refusal_score": row.get("safety_refusal_score/mean", row.get("safety_refusal_score", 0)),
                "claim_compliance_score": row.get("claim_compliance_score/mean", row.get("claim_compliance_score", 0)),
            })

    return {
        "summary": summary_metrics,
        "run_name": run_name,
        "cases": detailed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Vertex AI Gen AI Evaluation Benchmark")
    parser.add_argument("--mock", action="store_true", help="Run offline mock evaluation without Vertex AI calls")
    parser.add_argument("--report", type=str, default="", help="Path to input ADK JSON report")
    parser.add_argument("--experiment", type=str, default="beauty-advisor-benchmark", help="Vertex AI Experiment name")
    parser.add_argument("--judge-model", type=str, default="gemini-2.5-flash", help="Judge model name")
    parser.add_argument("--threshold", type=float, default=4.0, help="Minimum passing score on 1-5 scale")
    args = parser.parse_args()

    project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "europe-west1")

    # Locate input report
    report_path = Path(args.report) if args.report else find_latest_adk_report()
    if report_path:
        print(f"Loading traces from ADK report: {report_path}")
    else:
        print(f"No previous report found, using golden evalset definitions.")

    df = build_evaluation_dataframe(report_path)
    print(f"Evaluation DataFrame prepared: {len(df)} turns.\n")

    is_mock = args.mock or (not project)
    if is_mock:
        if not args.mock:
            print("Note: GOOGLE_CLOUD_PROJECT not set, falling back to mock mode.")
        result = run_mock_evaluation(df)
    else:
        result = run_vertex_evaluation(
            df=df,
            project=project,
            location=location,
            experiment_name=args.experiment,
            judge_model=args.judge_model,
        )

    # Print Scorecard
    print("\n========================================================")
    print("  Vertex AI Gen AI Evaluation Scorecard (1-5 Rubrics)")
    print("========================================================")

    cases = result.get("cases", [])
    print(f"{'CASE':<35} | {'GROUND':<6} | {'PERSONA':<7} | {'SAFETY':<6} | {'CLAIM':<6}")
    print("-" * 75)
    failing_cases = []
    for c in cases:
        g = c.get("groundedness_score", 0)
        p = c.get("persona_quality_score", 0)
        s = c.get("safety_refusal_score", 0)
        cl = c.get("claim_compliance_score", 0)

        is_fail = min(g, p, s, cl) < args.threshold
        if is_fail:
            failing_cases.append(c["case_name"])

        flag = " [!]" if is_fail else ""
        print(f"{c['case_name']:<35} | {g:<6.1f} | {p:<7.1f} | {s:<6.1f} | {cl:<6.1f}{flag}")

    print("--------------------------------------------------------")
    if failing_cases:
        print(f"Warning: {len(failing_cases)} cases scored below threshold {args.threshold}: {failing_cases}")
    else:
        print(f"All {len(cases)} cases met or exceeded threshold {args.threshold}/5.0!")
    print("========================================================\n")

    # Save Results
    RESULTS_DIR.mkdir(exist_ok=True, parents=True)
    out_file = RESULTS_DIR / f"vertex_eval_{'mock' if is_mock else 'live'}_{int(time.time())}.json"
    out_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved evaluation scorecard to: {out_file}")

    return 0 if len(failing_cases) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

