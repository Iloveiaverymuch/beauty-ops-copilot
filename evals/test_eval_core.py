"""ADK-native eval as a pytest (this is the CI gate in D3.2). Needs Vertex + the MCP server running.
Writes require human approval in prod; in evals approval is turned off (M365_CONFIRM_WRITES=0) because the
eval runner cannot answer the pause. The governance plugin still enforces everything else.

Run: M365_CONFIRM_WRITES=0 uv run --env-file copilot/.env pytest evals/test_eval_core.py -q -s
"""
import asyncio
import os

os.environ.setdefault("M365_CONFIRM_WRITES", "0")  # must be set before copilot.agent is imported

from google.adk.evaluation.agent_evaluator import AgentEvaluator  # noqa: E402


def test_core_evalset():
    asyncio.run(AgentEvaluator.evaluate(
        agent_module="copilot",
        eval_dataset_file_path_or_dir="evals/core/copilot_core.evalset.json",
        num_runs=int(os.getenv("EVAL_NUM_RUNS", "3")),
    ))
