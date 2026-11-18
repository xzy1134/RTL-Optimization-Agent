"""Bounded, non-streaming DeepSeek chat transport using the Python standard library."""
import json
import math
import os
from pathlib import Path
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from .client import ModelResponse, ModelError, empty_usage, is_strategy_label
from .prompt_builder import build_messages

ENV_FILE = Path(__file__).resolve().parents[2] / '.env'


def read_local_config():
    """Read plain KEY=VALUE data, never source or execute local configuration."""
    values = {}
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            values[key.strip()] = value
    return values


def usable_key(value):
    value = value.strip()
    return value if value and not any(marker in value.lower() for marker in
        ('your-api-key', 'your_api_key', 'replace-me', 'changeme')) else ''


def parse_candidate(content):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate response field')
            result[key] = value
        return result
    try:
        if not isinstance(content, str) or not content.strip():
            raise ValueError('Empty content')
        data = json.loads(content, object_pairs_hook=unique_object)
        if not isinstance(data, dict) or set(data) != {'action', 'strategy_id', 'description', 'rtl'}:
            raise ValueError('Expected exactly action, strategy_id, description and rtl')
        if not isinstance(data['description'], str) or not data['description'].strip():
            raise ValueError('Missing description')
        if not isinstance(data['strategy_id'], str):
            raise ValueError('Invalid strategy id')
        if data['action'] == 'NO_CHANGE':
            if data['rtl'] is not None or data['strategy_id'] != '':
                raise ValueError('NO_CHANGE must have null rtl and empty strategy_id')
        elif data['action'] == 'CANDIDATE':
            if not is_strategy_label(data['strategy_id']):
                raise ValueError('Invalid strategy reporting label')
            if not isinstance(data['rtl'], str) or not data['rtl'].strip():
                raise ValueError('Missing RTL')
        else:
            raise ValueError('Unsupported action')
        return data
    except (ValueError, TypeError) as exc:
        # Do not include model text in diagnostics. Raw sanitized output is archived.
        raise ModelError('RESPONSE_FORMAT', 'Model content does not match the response contract') from exc


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DeepSeekClient:
    mode = 'deepseek'

    def __init__(self, *, model=None, api_url=None,
                 max_output_tokens=2048, request_timeout=45.0, max_retries=1,
                 min_interval=1.0):
        local = read_local_config()
        key = (usable_key(local.get('DEEPSEEK_API_KEY', ''))
               or usable_key(local.get('LLM_API_KEY', ''))
               or usable_key(os.environ.get('DEEPSEEK_API_KEY', '')))
        if not key:
            raise ValueError('API key is not available; configure project .env or DEEPSEEK_API_KEY locally')
        model = model or local.get('DEEPSEEK_MODEL') or local.get('LLM_MODEL') or os.environ.get('DEEPSEEK_MODEL') or 'deepseek-flash'
        api_url = api_url or local.get('DEEPSEEK_API_URL') or os.environ.get('DEEPSEEK_API_URL') or 'https://api.deepseek.com/chat/completions'
        parsed = urllib.parse.urlsplit(api_url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise ValueError('api_url must be an HTTPS endpoint without credentials, query or fragment')
        if not isinstance(model, str) or not model.strip():
            raise ValueError('model must be nonempty')
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 8192:
            raise ValueError('max_output_tokens must be between 1 and 8192')
        if type(max_retries) is not int or not 0 <= max_retries <= 2:
            raise ValueError('max_retries must be between 0 and 2')
        if not math.isfinite(request_timeout) or request_timeout <= 0:
            raise ValueError('request_timeout must be positive and finite')
        if not math.isfinite(min_interval) or min_interval < 0:
            raise ValueError('min_interval must be nonnegative and finite')
        self._api_key = key
        self.model, self.api_url = model, api_url
        self.max_output_tokens, self.request_timeout = max_output_tokens, request_timeout
        self.max_retries, self.min_interval = max_retries, min_interval
        self._last_request = 0.0
        self._opener = urllib.request.build_opener(_NoRedirect())

    @property
    def metadata(self):
        return {'mode': self.mode, 'model': self.model, 'api_url': self.api_url,
                'max_output_tokens': self.max_output_tokens, 'max_retries': self.max_retries,
                'request_timeout': self.request_timeout, 'min_interval': self.min_interval}

    def generate(self, request, *, timeout):
        started = time.perf_counter()
        usage = empty_usage(self.mode)
        usage['calls'] = 1
        artifacts = {'api_request': {'model': self.model, 'messages': build_messages(request),
                     'response_format': {'type': 'json_object'}, 'stream': False,
                     'thinking': {'type': 'disabled'}, 'max_tokens': self.max_output_tokens},
                     'api_attempts': []}
        body = json.dumps(artifacts['api_request'], ensure_ascii=False).encode('utf-8')
        limits = request.constraints.get('api_budget', {})
        max_requests = limits.get('remaining_requests', 1)
        token_budget = limits.get('remaining_tokens', 50000)
        # Conservative preflight estimate, not a provider usage measurement.
        reservation = len(body) + 512 + self.max_output_tokens
        deadline = started + max(0, timeout)

        def fail(code, message):
            usage['api_seconds'] = time.perf_counter() - started
            raise ModelError(code, message, usage=dict(usage), artifacts=artifacts)

        for attempt in range(self.max_retries + 1):
            if usage['real_api_calls'] >= max_requests:
                fail('API_REQUEST_BUDGET', 'API request budget exhausted, including retries')
            if usage['charged_tokens'] + reservation > token_budget:
                fail('TOKEN_BUDGET', 'Remaining token budget is below the conservative request reservation')
            wait = max(0, self._last_request + self.min_interval - time.perf_counter())
            if deadline - time.perf_counter() <= wait:
                fail('MODEL_TIMEOUT', 'No time remains for the next API request')
            if wait:
                time.sleep(wait)
            left = deadline - time.perf_counter()
            if left <= 0:
                fail('MODEL_TIMEOUT', 'Model call deadline exhausted')
            req = urllib.request.Request(self.api_url, data=body, headers={
                'Authorization': 'Bearer ' + self._api_key, 'Content-Type': 'application/json'}, method='POST')
            usage['real_api_calls'] += 1
            usage['retries'] += int(attempt > 0)
            self._last_request = time.perf_counter()
            record = {'attempt': attempt + 1}
            artifacts['api_attempts'].append(record)
            response_data, retry_after, error_code = None, 0.0, None
            try:
                with self._opener.open(req, timeout=min(left, self.request_timeout)) as response:
                    raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    error_code = 'RESPONSE_TOO_LARGE'
                else:
                    text = raw.decode('utf-8').replace(self._api_key, '[REDACTED]')
                    try:
                        response_data = json.loads(text)
                        record['response'] = response_data
                    except ValueError:
                        record['raw_response'] = text
                        error_code = 'RESPONSE_FORMAT'
                record['http_status'] = 200
            except urllib.error.HTTPError as exc:
                record['http_status'] = exc.code
                error_code = 'HTTP_' + str(exc.code)
                # Error bodies may echo credentials. Do not archive or print them.
                retry_header = exc.headers.get('Retry-After', '') if exc.headers else ''
                try:
                    retry_after = max(0.0, float(retry_header))
                    if not math.isfinite(retry_after):
                        retry_after = 0
                except ValueError:
                    pass
                exc.close()
            except (TimeoutError, socket.timeout):
                error_code = 'MODEL_TIMEOUT'
            except (urllib.error.URLError, OSError):
                error_code = 'NETWORK_ERROR'
            except UnicodeError:
                error_code = 'RESPONSE_FORMAT'
            record['runtime_seconds'] = time.perf_counter() - self._last_request
            known = response_data.get('usage') if isinstance(response_data, dict) else None
            valid_usage = (isinstance(known, dict) and
                all(type(known.get(k)) is int and known[k] >= 0 for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')) and
                known['total_tokens'] >= known['prompt_tokens'] + known['completion_tokens'])
            if valid_usage:
                usage['input_tokens'] += known['prompt_tokens']
                usage['output_tokens'] += known['completion_tokens']
                usage['total_tokens'] += known['total_tokens']
                usage['charged_tokens'] += known['total_tokens']
                record['usage'] = known
            else:
                usage['unknown_usage_calls'] += 1
                usage['charged_tokens'] += reservation
                record['usage'] = None
                record['reserved_tokens'] = reservation
            record['error'] = error_code
            if time.perf_counter() >= deadline:
                fail('MODEL_TIMEOUT', 'Model call exceeded remaining runtime')
            if error_code:
                retryable = record.get('http_status') in (429, 500, 502, 503, 504)
                if retryable and attempt < self.max_retries:
                    wait = max(retry_after, 2 ** attempt)
                    if wait >= deadline - time.perf_counter():
                        fail('MODEL_TIMEOUT', 'Retry delay exceeds remaining runtime')
                    # Do not sleep if the next attempt cannot fit its budgets.
                    if usage['real_api_calls'] < max_requests and usage['charged_tokens'] + reservation <= token_budget:
                        time.sleep(wait)
                    continue
                fail(error_code, 'DeepSeek request failed (' + error_code + '); credentials and error body omitted')
            if not valid_usage:
                fail('USAGE_MISSING', 'Provider usage is missing/invalid; stop to avoid unmeasured cost')
            try:
                choice = response_data['choices'][0]
                if choice['finish_reason'] != 'stop':
                    fail('RESPONSE_TRUNCATED', 'Completion did not finish normally; candidate is not usable')
                data = parse_candidate(choice['message']['content'])
            except ModelError as exc:
                fail(exc.code, str(exc))
            except (KeyError, IndexError, TypeError):
                fail('RESPONSE_FORMAT', 'Provider response has an invalid completion envelope')
            usage['api_seconds'] = time.perf_counter() - started
            return ModelResponse(**data, usage=dict(usage), artifacts=artifacts)
