import os
from uuid import uuid4

from agents import Runner, SQLiteSession

from agent import run_config_for, travel_agent
from settings import load_local_env
from state import TravelState

def print_usage(result):
    """Print token usage for the current agent run."""
    usage = result.context_wrapper.usage

    print("\n--- Usage ---")
    print(f"API requests: {usage.requests}")
    print(f"Input tokens: {usage.input_tokens}")
    print(f"Output tokens: {usage.output_tokens}")
    print(f"Total tokens: {usage.total_tokens}")


def run_cli() -> None:
    # Every CLI process gets isolated in-memory conversation history.
    session = SQLiteSession(f"travel-demo-{uuid4().hex}", ":memory:")
    travel_state = TravelState()

    print("Travel Agent 已启动。输入 exit 退出。\n")

    while True:
        user_input = input("You: ").strip()

        if user_input.lower() in {"exit", "quit"}:
            print("Travel Agent 已退出。")
            break

        if not user_input:
            continue

        travel_state.set_current_user_message(user_input)

        result = Runner.run_sync(
            travel_agent,
            user_input,
            session=session,
            context=travel_state,
            run_config=run_config_for(travel_state),
        )

        print("\nTravel Agent:")
        print(result.final_output)

        print_usage(result)
        print("\n--- Current State ---")
        print(travel_state.summary())

        print()


def main() -> None:
    load_local_env()
    port = os.environ.get("PORT")
    if port:
        import uvicorn

        uvicorn.run("app:app", host="0.0.0.0", port=int(port))
        return
    run_cli()


if __name__ == "__main__":
    main()
