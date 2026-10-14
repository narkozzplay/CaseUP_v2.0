"""Per-request memoization for hot, rarely changing lookups.

Site configuration, the current project version, the active theme and module states are read
from many places while a single page renders (middleware, context processors, theme template
resolution, subscription checks). Without memoization one dashboard page issued ~100 queries.

The cache lives only for the duration of one HTTP request (RequestCacheMiddleware). Outside a
request (Celery, management commands, plain service calls in tests) every call hits the database
as before. Any model save or delete during the request drops the cache, so a view that changes
settings and then renders never sees stale values.
"""
from __future__ import annotations

import copy
import functools
from contextvars import ContextVar

from django.db.models import Model
from django.db.models.signals import post_delete, post_save


_request_cache: ContextVar[dict | None] = ContextVar("caseup_request_cache", default=None)


def request_memoize(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        cache = _request_cache.get()
        if cache is None:
            return func(*args, **kwargs)
        try:
            key = (func.__module__, func.__qualname__, args, tuple(sorted(kwargs.items())))
            hash(key)
        except TypeError:
            return func(*args, **kwargs)
        if key not in cache:
            cache[key] = func(*args, **kwargs)
        value = cache[key]
        # Callers mutate what they get back (a ModelForm writes into its instance before save),
        # so every caller gets its own shallow copy, just like a fresh query would return.
        return copy.copy(value) if isinstance(value, (dict, list, Model)) else value

    return wrapper


def clear_request_cache(*_args, **_kwargs) -> None:
    cache = _request_cache.get()
    if cache:
        cache.clear()


post_save.connect(clear_request_cache, dispatch_uid="caseup_request_cache_post_save")
post_delete.connect(clear_request_cache, dispatch_uid="caseup_request_cache_post_delete")


class RequestCacheMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _request_cache.set({})
        try:
            return self.get_response(request)
        finally:
            _request_cache.reset(token)
