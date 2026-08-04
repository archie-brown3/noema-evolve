# -*- coding: utf-8 -*-
"""
This file define
"""

import pytest

from agents.general_agent.evaluator import GeneralEvaluator
from agents.general_agent.executor import GeneralExecuteAgent
from loongflow.agentsdk.message import Message, MimeType
from loongflow.framework.pes.context import Context, LLMConfig, EvaluatorConfig
from loongflow.framework.pes.executor import Executor
from loongflow.framework.pes.register import register_worker


@pytest.mark.asyncio
async def test_run():
    """test general executor"""
    full_config = {
        "llm_config": LLMConfig(
            model="deepseek-v3.2",
            claude_agent_options={
                "max_turns": 10,
                "max_thinking_tokens": 10000,
            },
        ),
        "max_rounds": 1,
    }
    register_worker("general_executor", "executor", GeneralExecuteAgent)

    evaluator_config = EvaluatorConfig(
        llm_config=LLMConfig(
            model="deepseek-v3.2",
            claude_agent_options={
                "max_turns": 10,
                "max_thinking_tokens": 10000,
            },
        ),
        timeout=1800,
    )

    evaluate_code = ""

    evaluator_config.evaluate_code = evaluate_code

    evaluator = GeneralEvaluator(evaluator_config)

    executor = Executor("general_executor", full_config, evaluator, None)

    message = await executor.run(
        context=Context(
            base_path="./output",
            task="""    Act as an expert software developer. Your task is to iteratively improve the provided codebase. Your task is to write a search function to find a way to place num_circles disjoint disks into the unit square [0,1] x [0,1] in such a way that the sum of their radii is as big as possible.

    Your program will be evaluated with the command: centers, radii, sum_radii = run_packing(num_circles)

    So you have to write a run_packing(num_circles) function that returns three things:

    * centers must be a 2D NumPy array of shape (n,2), where each of the n rows contains an (x, y) coordinate pair for the center of a circle,
    * radii is an array of num_circles non-negative, finite numbers,
    * sum_radii is the sum of the radii of the circles.

    Remember, the circles must be disjoint. This will be checked automatically, with a snippet as follows: for i in range(n): for j in range(i + 1, n): dist = np.sqrt(np.sum((centers[i] - centers[j]) ** 2)) if radii[i] + radii[j] > dist: return False

    Key geometric insights:
    - Circle packings often follow hexagonal patterns in the densest regions
    - Maximum density for infinite circle packing is pi/(2*sqrt(3)) ≈ 0.9069
    - Edge effects make square container packing harder than infinite packing
    - Circles can be placed in layers or shells when confined to a square
    - Similar radius circles often form regular patterns, while varied radii allow better space utilization
    - Perfect symmetry may not yield the optimal packing due to edge effects
    - The run_packing results must be verified by the check_construction function provided in the initial code
    - You have 1000 seconds of runtime""",
        ),
        message=Message.from_content(
            data={
                "best_plan_file_path": "./output/32160dc4-b89e-44af-9061-d896128cbd93/0/planner/best_plan.md",
                "parent_info_file_path": "./output/32160dc4-b89e-44af-9061-d896128cbd93/0/planner/parent_info.json",
            },
            mime_type=MimeType.APPLICATION_JSON,
        ),
    )

    print(message)
