# -*- coding: utf-8 -*-
"""
This file define
"""

from pathlib import Path

import pytest

from agents.ml_agent.planner.ml_planner import MLPlannerAgent
from loongflow.framework.pes.context import Context, LLMConfig
from loongflow.framework.pes.context.config import DatabaseConfig
from loongflow.framework.pes.database import EvolveDatabase
from loongflow.framework.pes.planner import Planner
from loongflow.framework.pes.register import register_worker


@pytest.mark.asyncio
async def test_run():
    """test ml planner"""
    full_config = {
        "llm_config": LLMConfig(
            model="deepseek-v3.2",
        ),
    }
    register_worker("ml_planner", "planner", MLPlannerAgent)

    db = EvolveDatabase.create_database(DatabaseConfig())

    planner = Planner("ml_planner", full_config, db)

    message = await planner.run(
        context=Context(
            base_path="./output",
            task=Path("./tests/agents/ml_agent/resource/description.md").read_text(encoding="utf-8"),
            metadata={
                "task_data_path": "./tests/agents/ml_agent/resource",
            },
        ),
        message=None,
    )

    print(message)
