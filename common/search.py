from __future__ import annotations

from django.shortcuts import render


def render_search_not_found(
    request,
    *,
    query: str,
    title: str,
    hint: str,
    reset_url: str,
    reset_label: str = "Сбросить поиск",
):
    response = render(
        request,
        "errors/search_not_found.html",
        {
            "search_query": query,
            "search_not_found_title": title,
            "search_not_found_hint": hint,
            "search_not_found_reset_url": reset_url,
            "search_not_found_reset_label": reset_label,
        },
        status=404,
    )
    setattr(response, "_caseup_custom_404_rendered", True)
    return response