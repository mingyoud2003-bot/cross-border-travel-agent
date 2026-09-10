from argparse import Namespace

import pytest

from scripts.load_test import percentile, run


def test_percentile_uses_nearest_rank():
    values = [1, 2, 3, 4, 100]

    assert percentile(values, 0.5) == 3
    assert percentile(values, 0.95) == 100


def test_chat_load_test_requires_explicit_cost_confirmation():
    args = Namespace(
        target="chat",
        confirm_model_cost=False,
        concurrency=1,
        base_url="http://127.0.0.1:1",
        timeout=1,
        requests=1,
        message="你好",
    )

    with pytest.raises(SystemExit, match="confirm-model-cost"):
        import asyncio

        asyncio.run(run(args))
