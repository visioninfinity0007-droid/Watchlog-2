#!/usr/bin/env python3
"""Deterministic capacity tests for the Analytics Studio sampler."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from analytics_agent import FairSampler  # noqa: E402


class FairSamplerTests(unittest.TestCase):
    def test_single_camera_keeps_requested_interval(self):
        sampler = FairSampler(4.0)
        plan = [("1", 2.0)]
        self.assertEqual([("1", 2.0, 2.0)], sampler.effective_plan(plan))
        summary = sampler.summary(plan)
        self.assertEqual(1, summary["active_cameras"])
        self.assertEqual(2.0, summary["effective_max_seconds"])
        self.assertEqual("full", summary["quality"])

    def test_many_cameras_are_clamped_to_aggregate_budget(self):
        sampler = FairSampler(2.0)
        plan = [(str(i), 1.0) for i in range(1, 11)]
        effective = sampler.effective_plan(plan)
        self.assertTrue(all(row[2] == 5.0 for row in effective))
        summary = sampler.summary(plan)
        self.assertEqual(10, summary["active_cameras"])
        self.assertEqual(5.0, summary["effective_max_seconds"])
        self.assertEqual("reduced", summary["quality"])

    def test_global_rate_cap_and_fair_round_robin(self):
        sampler = FairSampler(2.0)
        plan = [("1", 0.5), ("2", 0.5), ("3", 0.5), ("4", 0.5)]

        first = sampler.choose(plan, 10.0)
        self.assertEqual("1", first[0])
        # No second inference before the aggregate 2 fps budget allows it.
        self.assertIsNone(sampler.choose(plan, 10.49))
        second = sampler.choose(plan, 10.5)
        third = sampler.choose(plan, 11.0)
        fourth = sampler.choose(plan, 11.5)
        self.assertEqual(["2", "3", "4"], [second[0], third[0], fourth[0]])

    def test_capacity_limited_status_is_explicit(self):
        sampler = FairSampler(1.0)
        plan = [(str(i), 1.0) for i in range(8)]
        summary = sampler.summary(plan)
        self.assertEqual(8.0, summary["effective_max_seconds"])
        self.assertEqual("capacity_limited", summary["quality"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
