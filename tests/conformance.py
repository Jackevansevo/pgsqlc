"""Check at runtime that values really have the types their annotations claim."""

import dataclasses
import inspect
import types
import typing
from collections.abc import Awaitable, Callable, Sequence

import asyncpg


def conforms(value: object, hint: object) -> bool:
    """Whether ``value`` is an instance of the (generated-code subset of) ``hint``."""
    if hint is None or hint is types.NoneType:
        return value is None
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)
    if origin is types.UnionType or origin is typing.Union:
        return any(conforms(value, a) for a in args)
    if origin is list:
        return isinstance(value, list) and all(conforms(v, args[0]) for v in value)
    if origin is Sequence:
        return isinstance(value, Sequence) and all(conforms(v, args[0]) for v in value)
    if origin is asyncpg.Range:
        return isinstance(value, asyncpg.Range) and all(
            b is None or conforms(b, args[0]) for b in (value.lower, value.upper)
        )
    if not isinstance(hint, type):
        msg = f"unsupported annotation {hint!r}"
        raise TypeError(msg)
    if not isinstance(value, hint):
        return False
    if dataclasses.is_dataclass(value):
        hints = typing.get_type_hints(type(value))
        return all(
            conforms(getattr(value, f.name), hints[f.name])
            for f in dataclasses.fields(value)
        )
    return True


def _qualified(fn: object) -> str:
    module = getattr(fn, "__module__", None)
    name = getattr(fn, "__qualname__", None)
    if not isinstance(module, str) or not isinstance(name, str):
        msg = f"{fn!r} is not a function"
        raise TypeError(msg)
    return f"{module}.{name}"


class Checker:
    """Calls generated functions and checks their results against annotations."""

    def __init__(self) -> None:
        self.called: set[str] = set()

    async def call[**P, R](
        self, fn: Callable[P, Awaitable[R]], /, *args: P.args, **kwargs: P.kwargs
    ) -> R:
        result = await fn(*args, **kwargs)
        hint = typing.get_type_hints(fn)["return"]
        if not conforms(result, hint):
            msg = f"{_qualified(fn)} returned {result!r}, which is not a {hint}"
            raise AssertionError(msg)
        self.called.add(_qualified(fn))
        return result


def public_functions(module: types.ModuleType) -> set[str]:
    return {
        f"{module.__name__}.{name}"
        for name, obj in vars(module).items()
        if inspect.iscoroutinefunction(obj) and obj.__module__ == module.__name__
    }
