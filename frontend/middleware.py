from __future__ import annotations

from common.errors import (
    HTML_NOT_FOUND_SKIP_PREFIXES,
    extract_plain_forbidden_detail,
    render_access_denied_page,
    render_not_found_page,
)


class CustomNotFoundPageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if getattr(response, "_caseup_custom_404_rendered", False):
            return response

        if getattr(response, "_caseup_skip_global_404", False):
            return response

        if getattr(response, "status_code", None) != 404:
            return response

        path = request.path_info or "/"
        if path.startswith(HTML_NOT_FOUND_SKIP_PREFIXES):
            return response

        accept = request.headers.get("Accept", "")
        if accept and "text/html" not in accept and "*/*" not in accept:
            return response

        return render_not_found_page(request)


class CustomForbiddenPageMiddleware:
    """Replace bare-text 403 responses (HttpResponseForbidden("...")) with the styled access-denied page."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if getattr(response, "status_code", None) != 403:
            return response

        if getattr(response, "_caseup_custom_403_rendered", False):
            return response

        path = request.path_info or "/"
        if path.startswith(HTML_NOT_FOUND_SKIP_PREFIXES):
            return response

        accept = request.headers.get("Accept", "")
        if accept and "text/html" not in accept and "*/*" not in accept:
            return response

        detail = extract_plain_forbidden_detail(response)
        if detail is None:
            return response

        return render_access_denied_page(request, detail=detail)

