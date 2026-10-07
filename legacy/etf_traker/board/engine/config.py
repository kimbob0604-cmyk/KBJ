#!/usr/bin/env python3
"""설정 로더. 임계값은 코드가 아니라 config/settings.yaml 에만 있다."""
import functools
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = os.path.join(ROOT, 'config', 'settings.yaml')


@functools.lru_cache(maxsize=8)
def load(path=DEFAULT):
    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f)


@functools.lru_cache(maxsize=4)
def themes(path=None):
    path = path or os.path.join(ROOT, 'knowledge', 'themes.yaml')
    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f)
