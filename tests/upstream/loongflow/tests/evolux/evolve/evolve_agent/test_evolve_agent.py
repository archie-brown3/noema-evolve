#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for evolux.evolve.planner
"""

import asyncio
import unittest

from loongflow.framework.pes import PESAgent
from loongflow.framework.pes.context import load_config


class TestPESAgent(unittest.TestCase):
    def test_evolve_agent(self):
        asyncio.run(self._test_evolve_agent())

    async def _test_evolve_agent(self):
        config = load_config("./tests/evolux/evolve/evolve_agent/config.yaml")
        evolve_agent = PESAgent(config)

        result_msg = await evolve_agent.run()
        print(result_msg)


if __name__ == "__main__":
    unittest.main()
