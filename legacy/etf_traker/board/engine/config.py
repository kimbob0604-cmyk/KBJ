#!/usr/bin/env python3
"""설정 로더. 임계값은 코드가 아니라 설정 파일에만 있다.

KBJ P3 묶음 E1(docs/p3_design.md §1.3): 신고가 엔진 절(newhigh·proximity·volume·resistance·giveback·
themes·display·integrity·detect·rankings)은 레포 루트 `config/board.yaml` 한 곳으로 옮겼다(숫자는
한 곳). `load()` 는 board/config/settings.yaml(나머지 절 — 뉴스·서술·트리거 등)에 그 파일을 합쳐
예전과 같은 dict 를 돌려준다. 지식 사전(themes·sectors·sector_map)은 `config/knowledge/` 로 옮겼다.
"""
import functools
import os

import yaml

from kbj.engines.board.config import BoardConfig, knowledge_path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = os.path.join(ROOT, 'config', 'settings.yaml')
# 옮긴 자체 사전(themes·sectors·sector_map)이 있는 폴더 — 레포 루트 config/knowledge
KNOWLEDGE = str(knowledge_path('themes.yaml').parent)


@functools.lru_cache(maxsize=8)
def load(path=DEFAULT):
    with open(path, encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    # 엔진 절은 config/board.yaml 이 기본이고, 파일에 같은 절이 있으면 파일이 이긴다(시험이 임시
    # 설정 파일로 임계값을 바꿔 부르는 경우)
    eng = BoardConfig.load().to_dict()
    eng.pop('service', None)            # kbj 서비스만 읽는 절
    eng.update(data)
    return eng


@functools.lru_cache(maxsize=4)
def themes(path=None):
    path = path or os.path.join(KNOWLEDGE, 'themes.yaml')
    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f)
