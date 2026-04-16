from typing import Iterator, Sequence, TypeVar

T = TypeVar("T")


def cast_list(l: Sequence[T]) -> list[T]:
    """Allows us to use immutable data structures while avoiding excessive copying costs"""
    return l if isinstance(l, list) else list(l)


def maybe_next(l: Iterator[T]) -> T | None:
    try:
        return next(l)
    except StopIteration:
        return None
