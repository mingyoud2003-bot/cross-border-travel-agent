from __future__ import annotations

import argparse
from datetime import date, timedelta

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the portfolio demo over HTTP.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    travel_date = (date.today() + timedelta(days=45)).isoformat()
    messages = [
        f"{travel_date}从伦敦去巴黎，现金票400英镑，奖励票20000 Avios，应该怎么选？",
        "税费50英镑，我是BA Silver。",
        "现金票价改成200英镑。",
    ]

    with httpx.Client(
        base_url=args.base_url.rstrip("/"), timeout=90, trust_env=False
    ) as client:
        client.get("/ready").raise_for_status()
        created = client.post("/api/sessions")
        created.raise_for_status()
        session_id = created.json()["session_id"]
        print(f"session={session_id[:8]}…")
        for turn, message in enumerate(messages, start=1):
            response = client.post(
                "/api/chat", json={"session_id": session_id, "message": message}
            )
            response.raise_for_status()
            payload = response.json()
            tools = [item["tool"] for item in payload["trace"]["tools"]]
            print(f"\nturn={turn} task={payload['state']['current_task']} tools={tools}")
            print(payload["message"])


if __name__ == "__main__":
    main()
