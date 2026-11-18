"""Offline provider-contract tests. HTTP responses and usage are synthetic fixtures."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import urllib.error

from rtl_agent.llm.client import ModelRequest, ModelError
from rtl_agent.llm.deepseek_client import DeepSeekClient, parse_candidate, _NoRedirect
from rtl_agent.llm.prompt_builder import build_messages
from rtl_agent.agent.graph import run_search
from rtl_agent.agent.state import SearchBudget
from rtl_agent.eda import EDAConfig
from rtl_agent.eda.common import ROOT

KEY = 'UNIT_TEST_CREDENTIAL_NEVER_A_REAL_KEY'
FIXTURES = ROOT / 'rtl_agent/examples'


def request(**budget):
    return ModelRequest(original_rtl='module t; endmodule', parent_rtl='module t; endmodule',
        top='t', primary_strategy='resource_sharing', strategies=[{'id': 'resource_sharing'}],
        metrics={'parent_area': 75.278, 'parent_depth': 6}, feedback=[],
        constraints={'depth_limit': 6, 'api_budget': {'remaining_requests': 5,
                    'remaining_tokens': 200000, **budget}})


def envelope(content=None, *, usage=True, finish='stop'):
    if content is None:
        content = {'action': 'NO_CHANGE', 'strategy_id': '', 'description': 'No proposal', 'rtl': None}
    result = {'choices': [{'finish_reason': finish, 'message': {'content': json.dumps(content) if isinstance(content, dict) else content}}]}
    if usage:
        result['usage'] = {'prompt_tokens': 20, 'completion_tokens': 30, 'total_tokens': 50}
    return result


def response(data):
    obj = MagicMock()
    obj.__enter__.return_value = obj
    obj.read.return_value = json.dumps(data).encode() if not isinstance(data, bytes) else data
    return obj


class DeepSeekTests(unittest.TestCase):
    def setUp(self):
        self.config_dir = tempfile.TemporaryDirectory()
        self.env_file = Path(self.config_dir.name)/'.env'
        self.config_patch = patch('rtl_agent.llm.deepseek_client.ENV_FILE', self.env_file)
        self.config_patch.start()
        self.env = patch.dict(os.environ, {'DEEPSEEK_API_KEY': KEY})
        self.env.start()
        self.builder = patch('rtl_agent.llm.deepseek_client.urllib.request.build_opener')
        self.open = self.builder.start().return_value.open
        self.sleep = patch('rtl_agent.llm.deepseek_client.time.sleep')
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()
        self.builder.stop()
        self.env.stop()
        self.config_patch.stop()
        self.config_dir.cleanup()

    def client(self, **kwargs):
        return DeepSeekClient(min_interval=0, **kwargs)

    def test_local_file_precedes_environment_and_is_not_logged(self):
        file_key = 'LOCAL_TEST_CREDENTIAL_ONLY'
        self.env_file.write_text('# local config\nDEEPSEEK_API_KEY="' + file_key + '"\n'
                                 'DEEPSEEK_MODEL=file-model\n'
                                 'DEEPSEEK_API_URL=https://api.deepseek.com/chat/completions\n')
        self.open.return_value = response(envelope())
        client = self.client()
        output = client.generate(request(), timeout=5)
        self.assertEqual(self.open.call_args.args[0].get_header('Authorization'), 'Bearer ' + file_key)
        self.assertEqual(client.model, 'file-model')
        self.assertNotIn(file_key, json.dumps(output.artifacts))
        self.assertNotIn(file_key, json.dumps(client.metadata))
        self.assertEqual(self.client(model='cli-model').model, 'cli-model')

    def test_teacher_key_and_model_supported_but_not_anthropic_endpoint(self):
        self.env_file.write_text("LLM_API_KEY='TEACHER_TEST_CREDENTIAL_ONLY'\nLLM_MODEL=deepseek-flash\n"
                                 'LLM_BASE_URL=https://api.deepseek.com/anthropic\n')
        self.open.return_value = response(envelope())
        client = self.client()
        client.generate(request(), timeout=5)
        self.assertEqual(self.open.call_args.args[0].get_header('Authorization'), 'Bearer TEACHER_TEST_CREDENTIAL_ONLY')
        self.assertEqual(client.api_url, 'https://api.deepseek.com/chat/completions')

    def test_placeholder_falls_back_without_executing_file_content(self):
        marker = Path(self.config_dir.name)/'must-not-exist'
        self.env_file.write_text('DEEPSEEK_API_KEY=sk-your-api-key\n'
                                 f'UNUSED=$(touch {marker})\n')
        self.open.return_value = response(envelope())
        self.client().generate(request(), timeout=5)
        self.assertEqual(self.open.call_args.args[0].get_header('Authorization'), 'Bearer ' + KEY)
        self.assertFalse(marker.exists())

    def test_request_auth_json_mode_and_known_usage(self):
        self.open.return_value = response(envelope())
        client = self.client(model='deepseek-flash')
        output = client.generate(request(), timeout=5)
        sent = self.open.call_args.args[0]
        payload = json.loads(sent.data)
        self.assertEqual(sent.full_url, 'https://api.deepseek.com/chat/completions')
        self.assertEqual(sent.get_header('Authorization'), 'Bearer ' + KEY)
        self.assertEqual(payload['response_format'], {'type': 'json_object'})
        self.assertEqual(payload['thinking'], {'type': 'disabled'})
        self.assertEqual(payload['messages'], build_messages(request()))
        context = json.loads(payload['messages'][1]['content'].split('\n', 1)[1])
        self.assertEqual(context['parent_rtl_ref'], 'original_rtl')
        self.assertEqual(context['primary_strategy'], 'resource_sharing')
        self.assertEqual(context['strategies'][0]['id'], 'resource_sharing')
        self.assertEqual(output.usage['input_tokens'], 20)
        self.assertEqual(output.usage['output_tokens'], 30)
        self.assertEqual(output.usage['total_tokens'], 50)
        self.assertEqual(output.usage['real_api_calls'], 1)
        self.assertNotIn(KEY, json.dumps(output.artifacts))
        self.assertNotIn(KEY, json.dumps(client.metadata))

    def test_candidate_contract(self):
        candidate = {'action': 'CANDIDATE', 'strategy_id': 'resource_sharing',
                     'description': 'Share operands', 'rtl': (FIXTURES/'mock_shared.v').read_text()}
        self.open.return_value = response(envelope(candidate))
        output = self.client().generate(request(), timeout=5)
        self.assertEqual(output.rtl, candidate['rtl'])
        self.assertEqual(output.strategy_id, 'resource_sharing')

    def test_parser_rejects_invalid_shapes_and_labels(self):
        invalid = ['', '```json\n{}\n```', '[]', '{"action":"NO_CHANGE","action":"CANDIDATE"}',
                   json.dumps({'action': 'CANDIDATE', 'strategy_id': '', 'description': 'x', 'rtl': 'x'}),
                   json.dumps({'action': 'NO_CHANGE', 'strategy_id': '', 'description': 'x', 'rtl': 'not null'}),
                   json.dumps({'action': 'NO_CHANGE', 'strategy_id': '', 'description': 'x', 'rtl': None, 'area': 1})]
        for content in invalid:
            with self.subTest(content=content), self.assertRaises(ModelError):
                parse_candidate(content)

    def test_parser_accepts_fused_and_new_strategy_labels(self):
        for label in ('resource_sharing+width_reduction', 'custom_factoring'):
            data = {'action':'CANDIDATE', 'strategy_id':label, 'description':'Different structure', 'rtl':'module t; endmodule'}
            self.assertEqual(parse_candidate(json.dumps(data))['strategy_id'], label)

    def test_format_error_preserves_known_usage_and_does_not_retry(self):
        self.open.return_value = response(envelope('not JSON'))
        with self.assertRaises(ModelError) as caught:
            self.client().generate(request(), timeout=5)
        self.assertEqual(caught.exception.code, 'RESPONSE_FORMAT')
        self.assertEqual(caught.exception.usage['total_tokens'], 50)
        self.assertEqual(self.open.call_count, 1)

    def test_truncation_and_empty_content(self):
        for data, code in [(envelope(finish='length'), 'RESPONSE_TRUNCATED'),
                            (envelope('   '), 'RESPONSE_FORMAT')]:
            self.open.return_value = response(data)
            with self.assertRaises(ModelError) as caught:
                self.client().generate(request(), timeout=5)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(caught.exception.usage['total_tokens'], 50)

    def test_missing_usage_is_unknown_and_stops(self):
        self.open.return_value = response(envelope(usage=False))
        with self.assertRaises(ModelError) as caught:
            self.client().generate(request(), timeout=5)
        self.assertEqual(caught.exception.code, 'USAGE_MISSING')
        self.assertEqual(caught.exception.usage['unknown_usage_calls'], 1)
        self.assertGreater(caught.exception.usage['charged_tokens'], 0)
        self.assertEqual(self.open.call_count, 1)

    def test_rate_limit_retry_is_counted(self):
        self.open.side_effect = [urllib.error.HTTPError('https://api.deepseek.com', 429, 'limited', {'Retry-After': '0'}, io.BytesIO(b'ignored')),
                                 response(envelope())]
        output = self.client(max_retries=1).generate(request(), timeout=5)
        self.assertEqual(output.usage['real_api_calls'], 2)
        self.assertEqual(output.usage['retries'], 1)
        self.assertEqual(output.usage['unknown_usage_calls'], 1)
        self.assertEqual(output.usage['total_tokens'], 50)
        self.assertGreater(output.usage['charged_tokens'], 50)

    def test_retry_cannot_exceed_request_budget(self):
        self.open.side_effect = urllib.error.HTTPError('https://api.deepseek.com', 503, 'busy', {}, None)
        with self.assertRaises(ModelError) as caught:
            self.client(max_retries=2).generate(request(remaining_requests=1), timeout=5)
        self.assertEqual(caught.exception.code, 'API_REQUEST_BUDGET')
        self.assertEqual(self.open.call_count, 1)

    def test_server_retries_are_bounded(self):
        self.open.side_effect = urllib.error.HTTPError('https://api.deepseek.com', 500, 'busy', {}, None)
        with self.assertRaises(ModelError) as caught:
            self.client(max_retries=1).generate(request(), timeout=5)
        self.assertEqual(self.open.call_count, 2)
        self.assertEqual(caught.exception.usage['real_api_calls'], 2)

    def test_auth_error_body_is_not_logged_or_retried(self):
        self.open.side_effect = urllib.error.HTTPError('https://api.deepseek.com', 401, KEY, {}, io.BytesIO(KEY.encode()))
        with self.assertRaises(ModelError) as caught:
            self.client().generate(request(), timeout=5)
        self.assertNotIn(KEY, str(caught.exception))
        self.assertNotIn(KEY, json.dumps(caught.exception.artifacts))
        self.assertEqual(self.open.call_count, 1)

    def test_network_timeout_is_accounted_without_automatic_retry(self):
        self.open.side_effect = TimeoutError(KEY)
        with self.assertRaises(ModelError) as caught:
            self.client().generate(request(), timeout=5)
        self.assertEqual(caught.exception.code, 'MODEL_TIMEOUT')
        self.assertEqual(caught.exception.usage['real_api_calls'], 1)
        self.assertEqual(caught.exception.usage['unknown_usage_calls'], 1)
        self.assertNotIn(KEY, str(caught.exception))

    def test_token_preflight_sends_no_request(self):
        with self.assertRaises(ModelError) as caught:
            self.client().generate(request(remaining_tokens=1), timeout=5)
        self.assertEqual(caught.exception.code, 'TOKEN_BUDGET')
        self.assertEqual(caught.exception.usage['real_api_calls'], 0)
        self.open.assert_not_called()

    def test_timeout_and_retry_after_do_not_start_extra_requests(self):
        with self.assertRaises(ModelError):
            self.client().generate(request(), timeout=0)
        self.open.assert_not_called()
        self.open.side_effect = urllib.error.HTTPError('https://api.deepseek.com', 429, 'limited', {'Retry-After': '99'}, None)
        with self.assertRaises(ModelError) as caught:
            self.client().generate(request(), timeout=5)
        self.assertEqual(caught.exception.code, 'MODEL_TIMEOUT')
        self.assertEqual(self.open.call_count, 1)

    def test_missing_key_and_unsafe_url(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': ''}):
            with self.assertRaisesRegex(ValueError, 'not available'):
                self.client()
        for url in ('http://example.com', 'https://user:password@example.com', 'https://example.com?key=x'):
            with self.assertRaises(ValueError):
                self.client(api_url=url)
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example'))

    def test_graph_with_stubbed_http_uses_real_eda_and_accounts_usage(self):
        candidate = {'action': 'CANDIDATE', 'strategy_id': 'resource_sharing', 'description': 'Share multipliers',
                     'rtl': (FIXTURES/'mock_shared.v').read_text()}
        self.open.side_effect = [response(envelope(candidate)), response(envelope())]
        with tempfile.TemporaryDirectory(dir=ROOT/'work/rtl_agent') as root:
            state = run_search(self.client(), original_rtl=FIXTURES/'mock_baseline.v', top='t',
                 config=EDAConfig.from_tools_env(timeout_seconds=10), depth_limit=6, outdir=root,
                 budget=SearchBudget(5, 5, 5, 30, 2, 200000))
            result = json.loads(Path(state['report_path']).read_text())
            self.assertLess(result['best']['area'], result['baseline']['area'])
            self.assertEqual(result['best']['equivalence'], 'PASS')
            self.assertEqual(result['model_usage']['input_tokens'], 40)
            self.assertEqual(result['model_usage']['output_tokens'], 60)
            self.assertEqual(result['model_usage']['total_tokens'], 100)
            self.assertEqual(result['model_usage']['real_api_calls'], 2)
            self.assertTrue(result['token_usage_complete'])
            self.assertEqual(result['model']['mode'], 'deepseek')
            self.assertTrue((Path(state['run_dir'])/'round-0001/api_attempts.json').exists())

    def test_graph_stops_on_request_budget(self):
        candidate = {'action': 'CANDIDATE', 'strategy_id': 'resource_sharing', 'description': 'Share multipliers',
                     'rtl': (FIXTURES/'mock_shared.v').read_text()}
        self.open.return_value = response(envelope(candidate))
        with tempfile.TemporaryDirectory(dir=ROOT/'work/rtl_agent') as root:
            state = run_search(self.client(), original_rtl=FIXTURES/'mock_baseline.v', top='t',
                 config=EDAConfig.from_tools_env(timeout_seconds=10), depth_limit=6, outdir=root,
                 budget=SearchBudget(5, 5, 5, 30, 1, 200000))
            self.assertEqual(state['stop_reason'], 'API_REQUEST_BUDGET')
            self.assertEqual(self.open.call_count, 1)

    def test_graph_preserves_usage_on_format_failure_and_reports_feedback(self):
        self.open.side_effect = [response(envelope('invalid JSON')), response(envelope())]
        with tempfile.TemporaryDirectory(dir=ROOT/'work/rtl_agent') as root:
            state = run_search(self.client(), original_rtl=FIXTURES/'mock_baseline.v', top='t',
                 config=EDAConfig.from_tools_env(timeout_seconds=10), depth_limit=6, outdir=root,
                 budget=SearchBudget(5, 5, 5, 30, 2, 200000))
            result = json.loads(Path(state['report_path']).read_text())
            first = json.loads(Path(state['history'][0]).read_text())
            second_request = json.loads((Path(state['run_dir'])/'round-0002/request.json').read_text())
            self.assertEqual(first['reason'], 'RESPONSE_FORMAT')
            self.assertEqual(first['synthesis'], {})
            self.assertEqual(second_request['feedback'][0]['reason'], 'RESPONSE_FORMAT')
            self.assertEqual(result['model_usage']['total_tokens'], 100)
            self.assertEqual(result['model_usage']['real_api_calls'], 2)
            self.assertEqual(result['candidate_count'], 0)

    def test_no_change_can_be_followed_by_a_new_provider_candidate(self):
        candidate = {'action':'CANDIDATE', 'strategy_id':'custom_factoring', 'description':'New direction',
                     'rtl':(FIXTURES/'mock_shared.v').read_text()}
        self.open.side_effect = [response(envelope()), response(envelope(candidate))]
        with tempfile.TemporaryDirectory(dir=ROOT/'work/rtl_agent') as root:
            state = run_search(self.client(), original_rtl=FIXTURES/'mock_baseline.v', top='t',
                 config=EDAConfig.from_tools_env(timeout_seconds=10), depth_limit=6, outdir=root,
                 budget=SearchBudget(5, 5, 5, 30, 2, 200000))
            self.assertEqual(state['best']['equivalence'], 'PASS')
            self.assertLess(state['best']['area'], state['baseline']['area'])
            self.assertEqual(state['model_usage']['real_api_calls'], 2)
            self.assertEqual(state['stop_reason'], 'API_REQUEST_BUDGET')

    def test_graph_missing_usage_stops_without_another_request(self):
        self.open.return_value = response(envelope(usage=False))
        with tempfile.TemporaryDirectory(dir=ROOT/'work/rtl_agent') as root:
            state = run_search(self.client(), original_rtl=FIXTURES/'mock_baseline.v', top='t',
                 config=EDAConfig.from_tools_env(timeout_seconds=10), outdir=root,
                 budget=SearchBudget(5, 5, 5, 30, 5, 200000))
            result = json.loads(Path(state['report_path']).read_text())
            self.assertEqual(result['stop_reason'], 'USAGE_MISSING')
            self.assertFalse(result['token_usage_complete'])
            self.assertEqual(result['model_usage']['unknown_usage_calls'], 1)
            self.assertGreater(result['model_usage']['charged_tokens'], 0)
            self.assertEqual(self.open.call_count, 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
