#!/usr/bin/env python3
"""
Anthropic API 호출 — 서술 생성.

모델 배분은 CLAUDE.md 7장 그대로 config/settings.yaml 에 둔다. 섹터 서술은 실시간,
뉴스 클러스터링·부트스트랩 분류는 Batch API(입출력 50% 할인)를 쓰는 전제다.

캐시: 매일 동일한 접두사(문체 규칙 + 테마 사전)를 system 블록에 두고 cache_control 을
건다. 섹터별 병렬 호출이 같은 접두사를 공유하므로 첫 호출 이후는 캐시 히트가 된다.
usage.cache_read_input_tokens 를 매 호출 기록해서 실제로 히트하는지 확인한다.

키가 없으면 예외를 던진다. 조용히 빈 문자열을 돌려주지 않는다.
"""
import concurrent.futures as cf
import json
import os


class NoKey(RuntimeError):
    pass


class Truncated(RuntimeError):
    """응답이 `max_tokens` 에서 끊겼다. `strict_stop=True` 로 부른 쪽만 받는다.

    잘린 텍스트는 JSON 이 아니라 `json.loads` 가 터지는데, 그 예외만 보면 상한이
    모자란 것인지 모델이 형식을 못 지킨 것인지 가릴 수 없다. 같은 상한으로 재시도
    하면 세 번 다 같은 자리에서 잘린다 — 다이제스트 ① 이 그 사고를 냈다
    (XDIGEST.md 3-2, DECISIONS D-NEXT-A).

    기본값은 예전 그대로(끊긴 텍스트를 돌려준다)다. 보드 서술은 조금 잘려도
    나가는 편이 나으므로 동작을 바꾸지 않는다.
    """

    def __init__(self, max_tokens):
        super().__init__(f'출력이 상한에서 잘림 (max_tokens {max_tokens:,})')
        self.max_tokens = max_tokens


def client():
    try:
        import anthropic
    except ImportError as e:                    # noqa: BLE001
        raise NoKey('anthropic 패키지가 없다. pip install anthropic') from e
    if not (os.environ.get('ANTHROPIC_API_KEY') or os.environ.get('ANTHROPIC_AUTH_TOKEN')):
        # ant auth login 프로필도 SDK 가 알아서 읽는다. 여기서 막지 않고 호출에 맡긴다.
        pass
    return anthropic.Anthropic()


class Usage:
    """토큰·캐시 집계. 캐시가 실제로 히트하는지 매 실행 확인한다."""

    def __init__(self):
        self.calls = 0
        self.input = self.output = self.cache_read = self.cache_write = 0

    def add(self, u):
        self.calls += 1
        self.input += getattr(u, 'input_tokens', 0) or 0
        self.output += getattr(u, 'output_tokens', 0) or 0
        self.cache_read += getattr(u, 'cache_read_input_tokens', 0) or 0
        self.cache_write += getattr(u, 'cache_creation_input_tokens', 0) or 0

    def line(self):
        hit = (self.cache_read / (self.cache_read + self.cache_write) * 100
               if (self.cache_read + self.cache_write) else 0)
        return (f'호출 {self.calls} · 입력 {self.input:,} · 출력 {self.output:,} · '
                f'캐시 읽기 {self.cache_read:,} / 쓰기 {self.cache_write:,} '
                f'(히트 {hit:.0f}%)')


def _text(resp, strict_stop=False, max_tokens=None):
    """응답에서 텍스트만. thinking 블록이 섞여 오므로 type 을 확인한다."""
    if strict_stop and getattr(resp, 'stop_reason', None) == 'max_tokens':
        raise Truncated(max_tokens or 0)
    if getattr(resp, 'stop_reason', None) == 'refusal':
        d = getattr(resp, 'stop_details', None)
        raise RuntimeError(f'모델이 거부함: {getattr(d, "category", "")} '
                           f'{getattr(d, "explanation", "")}')
    return '\n'.join(b.text for b in resp.content if b.type == 'text').strip()


# 기본 상한. 인자로 넘기지 않으면 이 값이다 — 보드 서술은 예전부터 이 값으로 돈다.
ASK_MAX_TOKENS = 8000
# JSON 응답 기본 상한. 주장 추출(compose.extract_claims)이 이 값으로 돈다.
JSON_MAX_TOKENS = 4000


# `output_config.effort` 를 받지 않는 모델. 실측으로 채운다 — 이름 규칙으로
# 짐작하지 않는다(2장 4번). 설정(`writer.no_effort_models`)이 먼저고, 설정에 없던
# 모델이 400 으로 거절하면 그 이름이 실행 중에 여기 들어온다.
#
# 2026-09-22 실측: `claude-haiku-4-5` 가 다이제스트 ① 배정에서
# `400 invalid_request_error: This model does not support the effort parameter.`
# (request_id req_011CfHqvwCaBQmdNioFkvm1v) — 분석이 통째로 죽고 결손만 나갔다.
NO_EFFORT = set()


def _skips_effort(cfg, model):
    listed = cfg['writer'].get('no_effort_models') or ()
    return model in NO_EFFORT or model in listed


def _is_effort_refusal(e):
    """400 이 effort 때문인가. 다른 400 을 이것으로 오인하면 진짜 오류를 가린다."""
    if getattr(e, 'status_code', None) != 400:
        return False
    return 'effort' in str(e).lower()


def ask(cl, cfg, system, prompt, usage=None, schema=None, max_tokens=None,
        model=None, strict_stop=False):
    """한 번 호출. schema 를 주면 JSON 을 강제한다.

    `model`·`max_tokens` 는 None 이면 예전 값(narrate · 8000)이다. 인자를 더한
    이유는 다이제스트 ① 배정을 Haiku 로 돌리고 출력 상한을 건수에 비례해 잡아야
    하기 때문이다 (XDIGEST.md 3-2, DECISIONS D-NEXT-A). 보드 호출은 이 인자를
    쓰지 않으므로 한 글자도 달라지지 않는다.

    `strict_stop=True` 면 `stop_reason == 'max_tokens'` 를 `Truncated` 로 올린다.
    부르는 쪽이 상한을 올려 다시 부를 수 있게 하는 것이고, 기본값은 예전처럼
    끊긴 텍스트를 그대로 돌려준다.
    """
    m = cfg['writer']['models']
    name = model or m['narrate']
    kw = dict(model=name,
              max_tokens=max_tokens or ASK_MAX_TOKENS,
              system=system, messages=[{'role': 'user', 'content': prompt}])
    eff = cfg['writer'].get('effort')
    if eff and not _skips_effort(cfg, name):
        kw['output_config'] = {'effort': eff}
    if schema:
        kw.setdefault('output_config', {})['format'] = {
            'type': 'json_schema', 'schema': schema}
    try:
        resp = cl.messages.create(**kw)
    except Exception as e:                        # noqa: BLE001 - 아래에서 가린다
        if not _is_effort_refusal(e) or 'effort' not in (kw.get('output_config') or {}):
            raise
        # 이 모델은 effort 를 받지 않는다. 설정에 없던 모델이라 여기서 알았다 —
        # 사유를 남기고 이 프로세스 안에서는 다시 붙이지 않는다. 지우기만 하고
        # 조용히 넘어가지 않는다(CLAUDE.md 2장 6번).
        NO_EFFORT.add(name)
        kw['output_config'].pop('effort')
        if not kw['output_config']:
            kw.pop('output_config')
        resp = cl.messages.create(**kw)
    if usage is not None:
        usage.add(resp.usage)
    return _text(resp, strict_stop=strict_stop, max_tokens=kw['max_tokens'])


def ask_many(cl, cfg, system, prompts, usage=None, workers=4, log=None,
             schema=None, model=None, max_tokens=None, strict_stop=False):
    """섹터별 병렬 호출 (CLAUDE.md 7장).

    prompts 는 [(키, 프롬프트)]. 반환 {키: 텍스트 또는 None}, {키: 사유}.
    하나 실패해도 나머지는 살린다. 실패한 섹션은 리포트에서 빠지고 사유가 남는다.

    `schema`·`model`·`max_tokens` 는 `ask` 로 그대로 넘어간다. 예전에는 넘기지
    않아 병렬 호출에 JSON 스키마를 강제할 수 없었다 — 다이제스트 ② 가 소주제
    8개를 병렬로 부르면서 스키마가 필요해졌다 (XDIGEST.md 3-2).
    """
    out, bad = {}, {}
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(ask, cl, cfg, system, p, usage=usage, schema=schema,
                          max_tokens=max_tokens, model=model,
                          strict_stop=strict_stop): k
                for k, p in prompts}
        for f in cf.as_completed(futs):
            k = futs[f]
            try:
                out[k] = f.result()
            except Exception as e:               # noqa: BLE001
                bad[k] = f'{type(e).__name__}: {e}'
                if log:
                    log(f'   서술 실패 [{k}]: {e}')
    return out, bad


def ask_json(cl, cfg, system, prompt, schema, usage=None, max_tokens=None,
             model=None, strict_stop=False):
    """스키마를 강제하고 JSON 으로 파싱한다.

    `max_tokens` 가 None 이면 예전 값(4000)이다. 인자를 더한 이유 — 게시물 150건의
    id 를 돌려주는 다이제스트 ① 출력이 4000 에서 잘려 `json.loads` 가 터졌고,
    그러면 3-6 의 '분석 실패' 경로로 매일 빈 다이제스트가 나갔다 (D-NEXT-A).
    """
    txt = ask(cl, cfg, system, prompt, usage=usage, schema=schema,
              max_tokens=max_tokens or JSON_MAX_TOKENS, model=model,
              strict_stop=strict_stop)
    return json.loads(txt)
