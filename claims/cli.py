"""Back-office / ops CLI for the claims workflow.

    uv run --env-file copilot/.env python -m claims.cli pending
    uv run --env-file copilot/.env python -m claims.cli status CLM-XXXXXX
    CLAIMS_BUS=pubsub uv run --env-file copilot/.env python -m claims.cli decide CLM-XXXXXX approve alice
    uv run --env-file copilot/.env python -m claims.cli retry CLM-XXXXXX
"""
import json
import sys

from claims import service


def main(argv: list[str]) -> None:
    cmd, *args = argv or ["pending"]
    if cmd == "pending":
        out = [{k: s[k] for k in ("claim_id", "status", "amount_eur", "rule")} for s in service.pending()]
    elif cmd == "status":
        out = service.status(args[0])
    elif cmd == "decide":
        out = {"published_message_id": service.submit_decision(args[0], args[1], args[2] if len(args) > 2 else "reviewer")}
    elif cmd == "retry":
        out = service.retry(args[0])
    else:
        raise SystemExit(__doc__)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
