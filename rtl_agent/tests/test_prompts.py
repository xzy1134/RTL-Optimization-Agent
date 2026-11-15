from dataclasses import asdict
import json
import unittest

from rtl_agent.llm.client import ModelRequest
from rtl_agent.llm.prompt_builder import build_messages


class PromptTests(unittest.TestCase):
    def request(self):
        return ModelRequest(
            original_rtl='module t; // $context {{RTL}} "原始"\nendmodule',
            parent_rtl='module t; // `timescale and $display\nendmodule',
            top='t', primary_strategy='resource_sharing',
            strategies=[{'id': 'resource_sharing', 'allow_llm': True}],
            metrics={'parent_area': 75.278, 'parent_depth': 6},
            feedback=[{'reason': 'FORMAL_FAIL'}],
            constraints={'depth_limit': None, 'timing_metric': 'logic_depth'})

    def test_preserves_request_data_and_verilog_characters(self):
        request = self.request()
        messages = build_messages(request)
        self.assertEqual([m['role'] for m in messages], ['system', 'user'])
        data = json.loads(messages[1]['content'].split('\n', 1)[1])
        self.assertEqual(data, asdict(request))
        self.assertIn('NO_CHANGE', messages[0]['content'])

    def test_same_request_same_messages(self):
        self.assertEqual(build_messages(self.request()), build_messages(self.request()))

    def test_depth_constraint_and_feedback_in_context(self):
        messages = build_messages(self.request())
        data = json.loads(messages[1]['content'].split('\n', 1)[1])
        self.assertIsNone(data['constraints']['depth_limit'])
        self.assertEqual(data['feedback'][0]['reason'], 'FORMAL_FAIL')
        self.assertEqual(data['strategies'][0]['id'], 'resource_sharing')
