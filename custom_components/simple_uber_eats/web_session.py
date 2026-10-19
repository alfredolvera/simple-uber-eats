"""Read browser cookie exports as data, retaining only necessary credentials."""

import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field
from http.cookies import Morsel, SimpleCookie


class CookieInputError(ValueError):
    def __init__(self) -> None:
        super().__init__("Provide valid sid and uev2.id.session cookies")


def _valid(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise CookieInputError()
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise CookieInputError() from None
    return value


def _pairs(source: str) -> list[tuple[str, str]]:
    pairs = []
    parts: list[str] = []
    start = 0
    quoted = escaped = False
    for index, char in enumerate(source):
        if escaped:
            escaped = False
        elif char == "\\" and quoted:
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif char == ";" and not quoted:
            parts.append(source[start:index])
            start = index + 1
    if quoted or escaped:
        raise CookieInputError()
    parts.append(source[start:])
    for part in parts:
        name, separator, value = part.strip().partition("=")
        name = name.strip()
        if not separator or name not in ("sid", "uev2.id.session"):
            continue
        if value.startswith('"'):
            try:
                cookie = SimpleCookie()
                cookie.load(name + "=" + value)
                value = cookie[name].value
            except (KeyError, ValueError):
                raise CookieInputError() from None
        pairs.append((name, _valid(value)))
    return pairs


def _curl_tokens(text: str) -> list[str]:
    """Remove shell comments at word boundaries, without evaluating shell code."""
    cleaned: list[str] = []
    quote: str | None = None
    word_start = True
    index = 0
    while index < len(text):
        char = text[index]
        if char == "#" and quote is None and word_start:
            end = text.find("\n", index)
            if end == -1:
                break
            index = end
            continue
        cleaned.append(char)
        if char == "\\" and quote != "'" and index + 1 < len(text):
            index += 1
            cleaned.append(text[index])
            if text[index] != "\n":
                word_start = False
        elif quote:
            if char == quote:
                quote = None
        elif char in ("'", '"'):
            quote = char
            word_start = False
        else:
            word_start = char.isspace()
        index += 1
    return shlex.split("".join(cleaned), comments=False, posix=True)


@dataclass(frozen=True, slots=True)
class WebSession:
    sid: str = field(repr=False)
    session_id: str = field(repr=False)

    def __post_init__(self) -> None:
        _valid(self.sid)
        _valid(self.session_id)

    @classmethod
    def parse(cls, text: str) -> "WebSession":
        if not isinstance(text, str) or not text.strip() or "\x00" in text:
            raise CookieInputError()
        try:
            if len(text.encode("utf-8")) > 512 * 1024:
                raise CookieInputError()
        except UnicodeEncodeError:
            raise CookieInputError() from None
        sources: list[str] = []
        if re.match(r"^\s*curl(?:\s|$)", text):
            try:
                tokens = _curl_tokens(text)
            except ValueError:
                raise CookieInputError() from None
            index = 1
            while index < len(tokens):
                token = tokens[index]
                value = None
                kind = None
                if token == "--":
                    break
                if token in (
                    "--data",
                    "--data-raw",
                    "--data-binary",
                    "--data-ascii",
                    "--data-urlencode",
                    "-d",
                    "--json",
                    "--url",
                    "-X",
                    "--request",
                    "-A",
                    "--user-agent",
                    "-e",
                    "--referer",
                    "-o",
                    "--output",
                    "--config",
                    "-K",
                ):
                    index += 2
                    continue
                if token in (
                    "--compressed",
                    "--location",
                    "-L",
                    "--insecure",
                    "-k",
                    "--globoff",
                    "-g",
                    "--http1.1",
                    "--http2",
                    "--silent",
                    "-s",
                    "--show-error",
                    "-S",
                ):
                    index += 1
                    continue
                if token.startswith(
                    (
                        "--data=",
                        "--data-raw=",
                        "--data-binary=",
                        "--data-ascii=",
                        "--data-urlencode=",
                        "--json=",
                        "--url=",
                        "--request=",
                        "--user-agent=",
                        "--referer=",
                        "--output=",
                        "--config=",
                    )
                ):
                    index += 1
                    continue
                if token in ("-b", "--cookie", "-H", "--header"):
                    index += 1
                    if index >= len(tokens):
                        raise CookieInputError()
                    value = tokens[index]
                    kind = "cookie" if token in ("-b", "--cookie") else "header"
                elif token.startswith("--cookie="):
                    value, kind = token.split("=", 1)[1], "cookie"
                elif token.startswith("--header="):
                    value, kind = token.split("=", 1)[1], "header"
                elif token.startswith("-b") and len(token) > 2:
                    value, kind = token[2:], "cookie"
                elif token.startswith("-H") and len(token) > 2:
                    value, kind = token[2:], "header"
                elif token.startswith("-"):
                    raise CookieInputError()
                if value is not None:
                    if "\n" in value or "\r" in value:
                        raise CookieInputError()
                    if kind == "cookie":
                        if "=" not in value or value.startswith("@"):
                            raise CookieInputError()
                        sources.append(value)
                    else:
                        name, sep, body = value.partition(":")
                        if sep and name.casefold() == "cookie":
                            sources.append(body.strip())
                index += 1
        elif "\n" in text or re.match(r"^\s*cookie\s*:", text, re.IGNORECASE):
            for line in text.splitlines():
                name, sep, body = line.partition(":")
                if sep and name.strip().casefold() == "cookie":
                    sources.append(body.strip())
            if not sources:
                raise CookieInputError()
        else:
            if "\r" in text:
                raise CookieInputError()
            sources.append(text.strip())
        values: dict[str, str] = {}
        for source in sources:
            for name, value in _pairs(source):
                value = _valid(value.strip())
                if name in values and values[name] != value:
                    raise CookieInputError()
                values[name] = value
        try:
            if (
                not values["sid"].startswith("QA.")
                or "-" not in values["uev2.id.session"]
            ):
                raise CookieInputError()
            return cls(values["sid"], values["uev2.id.session"])
        except KeyError:
            raise CookieInputError() from None

    def cookie_header(self) -> str:
        cookies = SimpleCookie()
        cookies["sid"] = self.sid
        cookies["uev2.id.session"] = self.session_id
        return "; ".join(
            cookies[key].OutputString() for key in ("sid", "uev2.id.session")
        )

    def as_entry_data(self) -> dict[str, str]:
        return {"sid": self.sid, "session_id": self.session_id}

    def rotate(self, cookies: Mapping) -> "WebSession":
        values = self.as_entry_data()
        for source, target in (("sid", "sid"), ("uev2.id.session", "session_id")):
            if source in cookies:
                value = cookies[source]
                values[target] = _valid(
                    value.value if isinstance(value, Morsel) else value
                )
        return WebSession(**values)
