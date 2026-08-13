from __future__ import annotations

__all__ = ["brand_voice", "episodic", "platform_credentials", "policy", "semantic", "settings", "working"]


def __getattr__(name: str):
    if name in __all__:
        from importlib import import_module

        module = import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
