"""Pure-logic tests that don't hit the network."""
from __future__ import annotations

import json

from parser.llm_parser import _extract_json


def test_extract_plain_json():
    s = '{"a": 1, "b": "x"}'
    assert _extract_json(s) == {"a": 1, "b": "x"}


def test_extract_with_fences():
    s = "```json\n{\"a\": 1}\n```"
    assert _extract_json(s) == {"a": 1}


def test_extract_with_prefix():
    s = "Here is the result:\n{\"x\": [1, 2, 3]}\nend"
    assert _extract_json(s) == {"x": [1, 2, 3]}


def test_extract_nested():
    s = '{"a": {"b": {"c": 1}}, "d": [1,2]}'
    assert _extract_json(s) == json.loads(s)
