from urllib.parse import urlsplit, urlunsplit

from django import template

register = template.Library()


def _strip_en_prefix(path: str) -> str:
    if path == "/en":
        return "/"
    if path.startswith("/en/"):
        return path[3:]
    return path or "/"


@register.simple_tag
def switch_lang_url(request, language_code: str) -> str:
    full_path = request.get_full_path() if request else "/"
    parsed = urlsplit(full_path)

    base_path = _strip_en_prefix(parsed.path or "/")
    if not base_path.startswith("/"):
        base_path = f"/{base_path}"

    if language_code == "en":
        target_path = "/en/" if base_path == "/" else f"/en{base_path}"
    else:
        target_path = base_path

    return urlunsplit(("", "", target_path, parsed.query, parsed.fragment))