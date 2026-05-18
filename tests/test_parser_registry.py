"""Verify LLM provider selection in parser.registry."""
from __future__ import annotations

import os


def _reload_config(**env):
    """Reset env vars and reload config + all parser modules so they re-bind
    to the new settings object."""
    for k in ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "LLM_PROVIDER"):
        os.environ.pop(k, None)
    for k, v in env.items():
        os.environ[k] = v
    import sys
    # Drop cached parser modules so the next import picks up the new settings.
    for mod in list(sys.modules):
        if mod == "config" or mod.startswith("parser"):
            sys.modules.pop(mod, None)
    import config  # noqa: F401
    return config


def test_auto_picks_anthropic_when_only_key():
    _reload_config(ANTHROPIC_API_KEY="sk-ant-test", LLM_PROVIDER="auto")
    from parser import registry
    p = registry.get_parser()
    assert p.provider_name == "anthropic"


def test_auto_picks_deepseek_when_only_key():
    _reload_config(DEEPSEEK_API_KEY="ds-test", LLM_PROVIDER="auto")
    from parser import registry
    p = registry.get_parser()
    assert p.provider_name == "deepseek"


def test_auto_prefers_anthropic_when_both():
    _reload_config(
        ANTHROPIC_API_KEY="sk-ant-test",
        DEEPSEEK_API_KEY="ds-test",
        LLM_PROVIDER="auto",
    )
    from parser import registry
    p = registry.get_parser()
    assert p.provider_name == "anthropic"


def test_explicit_deepseek_overrides_auto_preference():
    _reload_config(
        ANTHROPIC_API_KEY="sk-ant-test",
        DEEPSEEK_API_KEY="ds-test",
        LLM_PROVIDER="deepseek",
    )
    from parser import registry
    p = registry.get_parser()
    assert p.provider_name == "deepseek"


def test_explicit_anthropic_without_key_fails():
    _reload_config(LLM_PROVIDER="anthropic")
    from parser import registry
    import pytest
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        registry.get_parser()


def test_no_keys_raises():
    _reload_config()  # no keys at all
    from parser import registry
    import pytest
    with pytest.raises(RuntimeError, match="No LLM API key"):
        registry.get_parser()
