"""Turns hosts found in untrusted text into canonical Federation entity hosts.

The OFD specification requires a host entity to already be in canonical form, and a server hashes
the host exactly as sent, so Example.COM and example.com would otherwise become two different
entities. A canonical host here is one of:

- a domain name of lowercase ASCII labels (internationalized names converted to punycode), at least
  two labels long, without a trailing dot, under a delegated top-level domain;
- a public IPv4 address as four decimal octets without leading zeros;
- a public IPv6 address in its RFC 5952 compressed lowercase form. An IPv4-mapped IPv6 address is
  reported as the IPv4 address it maps to, since both reach the same host.

Private, loopback, link-local, documentation and otherwise non-routable addresses, and the RFC 2606
example domains, are rejected: they identify no one in particular, so publishing them would only
add noise.

Two entry points reflect how much the input can be trusted to be a host at all. from_url takes
something already known to be a link and parses its host the way a browser would (the WHATWG URL
rules), including the hexadecimal and octal IPv4 forms a browser accepts, so an obfuscated link such
as http://0x7f.1/ resolves to the address it really opens. from_text takes a word picked out of
free text and accepts only the strict forms, since there a dotted word is as likely to be a file
name or a version number as a host.

Nothing here performs a DNS lookup.
"""

import logging
import re
from importlib.resources import files

import httpx

log = logging.getLogger(__name__)

TLD_RESOURCE = files("spb") / "resources" / "tlds.txt"

TLD_LIST_URL = "https://data.iana.org/TLD/tlds-alpha-by-domain.txt"
"""IANA's list of delegated top-level domains, fetched at startup to replace the bundled copy."""

MIN_TLD_COUNT = 1_000
"""The fewest entries a fetched list must have to be used. The root zone holds well over a thousand
TLDs, so anything shorter is a truncated or wrong response, never a real list."""

TLD_FETCH_TIMEOUT_SECONDS = 15

MAX_DOMAIN_LENGTH = 253
MAX_LABEL_LENGTH = 63
MAX_PORT = 65_535

SPECIAL_SCHEMES = {"http", "https", "ws", "wss", "ftp"}
"""Schemes whose URLs always carry a host and treat a backslash like a slash (WHATWG)."""

RESERVED_DOMAINS = ("example.com", "example.net", "example.org")
"""Special-use names under a TLD that is otherwise accepted (RFC 2606)."""

FILE_EXTENSION_TLDS = {"java", "md", "mov", "pl", "pm", "ps", "py", "rs", "sh", "zip"}
"""Delegated TLDs that are more often file extensions than domains when they appear in plain text
(setup.py, notes.md, Main.java). Such a word is only accepted as a domain when it was marked up as a
link."""

_TLD_LINE = re.compile(r"[A-Za-z0-9-]{1,63}")
_SCHEME = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*):")
_PORT_THEN_PATH = re.compile(r"[0-9]+(?:[/?#].*)?", re.DOTALL)


_tlds = frozenset(
    line.strip().lower()
    for line in TLD_RESOURCE.read_text(encoding="ascii").splitlines()
    if line.strip() and not line.startswith("#")
)
"""The delegated TLDs in use: the bundled snapshot at first, replaced by IANA's current list once
refresh_tlds succeeds. Swapped as a whole, so readers always see one complete list."""


def from_url(url: str | None) -> str | None:
    """Returns the canonical host of a link.

    Accepts a full URL (https://user@Example.com:8443/path) or a scheme-less link as Telegram
    detects them (example.com/path). Links whose scheme has no host, such as mailto: or tg: deep
    links, yield nothing.

    Returns:
        The canonical host, or None when the link has no acceptable host.
    """
    host = url_host(url)
    return _canonical_host(host, lenient_ipv4=True, from_plain_text=False) if host else None


def from_text(candidate: str | None) -> str | None:
    """Returns the canonical host of a word taken from free text.

    The word may be a domain name, a dotted-decimal IPv4 address, or an IPv6 address, optionally in
    brackets. Leading-zero IPv4 octets are rejected rather than guessed at, since 010 means 8 to
    some parsers and 10 to others.

    Returns:
        The canonical host, or None when the word is not an acceptable host.
    """
    if not candidate:
        return None
    return _canonical_host(candidate, lenient_ipv4=False, from_plain_text=True)


def url_host(url: str | None) -> str | None:
    """Extracts the raw host of a link.

    Follows the WHATWG URL rules closely enough to agree with a browser on which host a link opens:
    user information is skipped up to the last @, a backslash ends the authority of a special URL,
    and the port is dropped.

    Returns:
        The percent-decoded host (bracketed for IPv6), or None when there is none.
    """
    if url is None:
        return None

    text = _strip_control_characters(url)
    if not text:
        return None

    scheme = _SCHEME.match(text)
    if scheme is None:
        # A link Telegram detected without a scheme; browsers open these as http.
        special = True
        rest = text
    else:
        name = scheme.group(1).lower()
        after_scheme = text[scheme.end() :]
        if name in SPECIAL_SCHEMES:
            special = True
            rest = after_scheme.lstrip("/\\")
        elif after_scheme.startswith("//"):
            special = False
            rest = after_scheme[2:]
        elif _PORT_THEN_PATH.fullmatch(after_scheme):
            # "example.com:8080/path" is a scheme-less link with a port, not a scheme.
            special = True
            rest = text
        else:
            # mailto:, tel:, javascript:, data: and the like have no host.
            return None

    end = next(
        (
            index
            for index, character in enumerate(rest)
            if character in "/?#" or (special and character == "\\")
        ),
        len(rest),
    )
    host_and_port = rest[:end].rpartition("@")[2]
    if not host_and_port:
        return None

    host: str | None
    if host_and_port.startswith("["):
        close = host_and_port.find("]")
        if close < 0:
            return None
        host = host_and_port[: close + 1]
        after = host_and_port[close + 1 :]
        if after and not after.startswith(":"):
            return None
        port = after[1:]
    else:
        host, _, port = host_and_port.partition(":")
        host = _percent_decode(host)

    if not host or not _valid_port(port):
        return None
    return host


def _canonical_host(raw: str, *, lenient_ipv4: bool, from_plain_text: bool) -> str | None:
    """Canonicalizes a raw host, bracketed when it is an IPv6 literal from a URL.

    Args:
        raw: The host.
        lenient_ipv4: Whether to accept the WHATWG IPv4 forms (hex, octal, fewer than four parts).
        from_plain_text: Whether the host was picked out of plain text rather than a link.
    """
    if raw.startswith("["):
        if not raw.endswith("]") or len(raw) < 3:
            return None
        return _canonical_ipv6(raw[1:-1])
    if ":" in raw:
        return _canonical_ipv6(raw)

    ascii_host = _to_ascii(raw)
    if ascii_host is None:
        return None
    ascii_host = ascii_host.removesuffix(".")
    if not ascii_host or ascii_host.endswith("."):
        return None

    if _ends_in_number(ascii_host):
        address = parse_whatwg_ipv4(ascii_host) if lenient_ipv4 else parse_strict_ipv4(ascii_host)
        return _format_ipv4(address) if address >= 0 and is_public_ipv4(address) else None
    return _canonical_domain(ascii_host, from_plain_text)


def _canonical_domain(domain: str, from_plain_text: bool) -> str | None:
    """Validates a lowercase ASCII domain name without a trailing dot."""
    if len(domain) > MAX_DOMAIN_LENGTH:
        return None

    labels = domain.split(".")
    if len(labels) < 2 or not all(_valid_label(label) for label in labels):
        return None

    tld = labels[-1]
    if tld not in _tlds and tld != "onion":
        return None
    if from_plain_text and tld in FILE_EXTENSION_TLDS:
        return None
    if any(domain == reserved or domain.endswith(f".{reserved}") for reserved in RESERVED_DOMAINS):
        return None
    return domain


def _valid_label(label: str) -> bool:
    """Returns whether a label is 1 to 63 lowercase letters, digits and inner hyphens.

    A punycode label (xn--) must also decode to Unicode and encode back to itself.
    """
    if not label or len(label) > MAX_LABEL_LENGTH or label[0] == "-" or label[-1] == "-":
        return False
    if not all(
        "a" <= character <= "z" or "0" <= character <= "9" or character == "-"
        for character in label
    ):
        return False
    if label.startswith("xn--"):
        try:
            unicode = label.encode("ascii").decode("idna")
            return unicode != label and unicode.encode("idna").decode("ascii").lower() == label
        except UnicodeError:
            return False
    return True


def _to_ascii(host: str) -> str | None:
    """Converts a hostname to its lowercase ASCII-compatible encoding, None when it has none."""
    try:
        return host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None


def _ends_in_number(host: str) -> bool:
    """Returns whether the last label is decimal digits, or hexadecimal digits after 0x."""
    last = host.rpartition(".")[2]
    if not last:
        return False
    if all("0" <= character <= "9" for character in last):
        return True
    return last[:2] in ("0x", "0X") and all(character in _HEX_DIGITS for character in last[2:])


_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def parse_strict_ipv4(host: str) -> int:
    """Parses an address in the strict x.x.x.x form, without leading zeros.

    Returns:
        The 32-bit address, or -1 when the host is not in that form.
    """
    parts = host.split(".")
    if len(parts) != 4:
        return -1

    address = 0
    for part in parts:
        if not part or len(part) > 3 or (len(part) > 1 and part[0] == "0"):
            return -1
        if not all("0" <= character <= "9" for character in part):
            return -1
        octet = int(part)
        if octet > 255:
            return -1
        address = (address << 8) | octet
    return address


def parse_whatwg_ipv4(host: str) -> int:
    """Parses an address the way a browser does: up to four parts, each decimal, hexadecimal (0x)
    or octal (leading zero), the last filling the remaining bytes.

    Returns:
        The 32-bit address, or -1 when the host is not an address.
    """
    parts = host.split(".")
    if len(parts) > 4:
        return -1

    numbers = []
    for part in parts:
        number = _parse_ipv4_number(part)
        if number is None:
            return -1
        numbers.append(number)

    if any(number > 255 for number in numbers[:-1]):
        return -1
    if numbers[-1] >= 256 ** (5 - len(numbers)):
        return -1

    address = numbers[-1]
    for index, number in enumerate(numbers[:-1]):
        address += number << (8 * (3 - index))
    return address


def _parse_ipv4_number(part: str) -> int | None:
    """Parses one part of a WHATWG IPv4 address in decimal, hexadecimal (0x) or octal (0).

    Returns:
        The number, or None when the part is empty or has a digit outside its radix.
    """
    if not part:
        return None

    radix = 10
    digits = part
    if len(part) >= 2 and part[:2] in ("0x", "0X"):
        radix = 16
        digits = part[2:]
    elif len(part) >= 2 and part[0] == "0":
        radix = 8
        digits = part[1:]

    if not digits:
        return 0
    # Anything this long is far beyond 32 bits in every radix; refuse before converting.
    if len(digits) > 40:
        return None
    try:
        # int() also accepts signs, underscores and surrounding whitespace, which a host may not
        # carry, so every character is checked first.
        if not all(character in _HEX_DIGITS for character in digits):
            return None
        return int(digits, radix)
    except ValueError:
        return None


def is_public_ipv4(address: int) -> bool:
    """Returns whether a 32-bit IPv4 address lies outside every reserved and private range."""
    a = (address >> 24) & 0xFF
    b = (address >> 16) & 0xFF
    c = (address >> 8) & 0xFF

    return not (
        a == 0  # "this network"
        or a == 10  # private
        or (a == 100 and (b & 0xC0) == 64)  # shared address space (CGNAT)
        or a == 127  # loopback
        or (a == 169 and b == 254)  # link-local
        or (a == 172 and (b & 0xF0) == 16)  # private
        or (a == 192 and b == 0 and c == 0)  # IETF protocol assignments
        or (a == 192 and b == 0 and c == 2)  # documentation (TEST-NET-1)
        or (a == 192 and b == 88 and c == 99)  # deprecated 6to4 relay anycast
        or (a == 192 and b == 168)  # private
        or (a == 198 and (b & 0xFE) == 18)  # benchmarking
        or (a == 198 and b == 51 and c == 100)  # documentation (TEST-NET-2)
        or (a == 203 and b == 0 and c == 113)  # documentation (TEST-NET-3)
        or a >= 224  # multicast, reserved, broadcast
    )


def _format_ipv4(address: int) -> str:
    return ".".join(str((address >> shift) & 0xFF) for shift in (24, 16, 8, 0))


def _canonical_ipv6(text: str) -> str | None:
    """Returns the canonical form of a public IPv6 address, or of the public IPv4 address an
    IPv4-mapped address maps to."""
    groups = parse_ipv6(text)
    if groups is None:
        return None

    if groups[:6] == [0, 0, 0, 0, 0, 0xFFFF]:
        address = (groups[6] << 16) | groups[7]
        return _format_ipv4(address) if is_public_ipv4(address) else None
    return format_ipv6(groups) if is_public_ipv6(groups) else None


def parse_ipv6(text: str) -> list[int] | None:
    """Parses an IPv6 address in any RFC 4291 text form.

    That is eight groups, one :: standing for one or more zero groups, and an optional
    dotted-decimal IPv4 tail. Zone identifiers are refused; they only mean something on the machine
    that wrote them.

    Returns:
        The eight 16-bit groups, or None when the text is not an address.
    """
    if not text or "%" in text:
        return None

    double_colon = text.find("::")
    if double_colon >= 0 and text.find("::", double_colon + 1) >= 0:
        return None

    if double_colon < 0:
        head = _parse_ipv6_groups(text, allow_ipv4_tail=True)
        tail: list[int] | None = []
    else:
        head = _parse_ipv6_groups(text[:double_colon], allow_ipv4_tail=False)
        tail = _parse_ipv6_groups(text[double_colon + 2 :], allow_ipv4_tail=True)
    if head is None or tail is None:
        return None

    total = len(head) + len(tail)
    if (double_colon < 0 and total != 8) or (double_colon >= 0 and total > 7):
        return None

    return head + [0] * (8 - total) + tail


def _parse_ipv6_groups(text: str, *, allow_ipv4_tail: bool) -> list[int] | None:
    if not text:
        return []

    groups = []
    parts = text.split(":")
    for index, part in enumerate(parts):
        if index == len(parts) - 1 and allow_ipv4_tail and "." in part:
            ipv4 = parse_strict_ipv4(part)
            if ipv4 < 0:
                return None
            groups += [ipv4 >> 16, ipv4 & 0xFFFF]
            continue
        if not part or len(part) > 4 or not all(character in _HEX_DIGITS for character in part):
            return None
        groups.append(int(part, 16))
    return groups if len(groups) <= 8 else None


def is_public_ipv6(groups: list[int]) -> bool:
    """Returns whether an IPv6 address is global unicast (2000::/3, the only range IANA allocates
    from) and not one of its documentation, benchmarking or ORCHID blocks."""
    first, second = groups[0], groups[1]
    if (first & 0xE000) != 0x2000:
        return False
    return not (
        (first == 0x2001 and second == 0x0DB8)  # documentation
        or (first == 0x2001 and second == 0x0002 and groups[2] == 0)  # benchmarking
        or (first == 0x2001 and (second & 0xFFF0) == 0x0010)  # ORCHID
        or (first == 0x2001 and (second & 0xFFF0) == 0x0020)  # ORCHIDv2
        or (first == 0x3FFF and (second & 0xF000) == 0)  # documentation
    )


def format_ipv6(groups: list[int]) -> str:
    """Formats an IPv6 address as RFC 5952 recommends.

    That is lowercase hex without leading zeros, with the first longest run of two or more zero
    groups replaced by ::.
    """
    best_start = -1
    best_length = 1
    index = 0
    while index < 8:
        if groups[index] != 0:
            index += 1
            continue
        start = index
        while index < 8 and groups[index] == 0:
            index += 1
        if index - start > best_length:
            best_start = start
            best_length = index - start

    if best_start < 0:
        return ":".join(f"{group:x}" for group in groups)

    head = ":".join(f"{group:x}" for group in groups[:best_start])
    tail = ":".join(f"{group:x}" for group in groups[best_start + best_length :])
    return f"{head}::{tail}"


def _strip_control_characters(text: str) -> str:
    """Trims control characters and spaces from both ends, and removes tabs and line breaks."""
    stripped = text.strip("".join(chr(code) for code in range(0x21)))
    return stripped.replace("\t", "").replace("\n", "").replace("\r", "")


def _valid_port(port: str) -> bool:
    """Returns whether a port is absent, or 1 to 5 decimal digits no greater than 65535."""
    if not port:
        return True
    return (
        len(port) <= 5
        and all("0" <= character <= "9" for character in port)
        and int(port) <= MAX_PORT
    )


def _percent_decode(host: str) -> str | None:
    """Decodes %XX escapes in a URL host as UTF-8, as browsers do before interpreting it.

    Returns:
        The decoded host, or None for a malformed escape or invalid UTF-8.
    """
    if "%" not in host:
        return host

    literal = host.encode("utf-8")
    decoded = bytearray()
    index = 0
    while index < len(literal):
        if literal[index] != ord("%"):
            decoded.append(literal[index])
            index += 1
            continue
        escape = literal[index + 1 : index + 3]
        if len(escape) < 2 or not all(chr(byte) in _HEX_DIGITS for byte in escape):
            return None
        decoded.append(int(escape, 16))
        index += 3

    try:
        return decoded.decode("utf-8")
    except UnicodeDecodeError:
        return None


async def refresh_tlds() -> bool:
    """Fetches IANA's current list of delegated TLDs and uses it in place of the bundled snapshot,
    which goes stale as new TLDs are delegated.

    The list is kept in memory only; nothing is written to disk. A response is used only when it
    parses as a TLD list of plausible size, so an outage, a proxy error page or a truncated download
    leaves the current list in place rather than emptying it.

    Returns:
        True when the fetched list was installed.
    """
    global _tlds  # noqa: PLW0603 - the list is swapped as a whole, see _tlds

    try:
        async with httpx.AsyncClient(
            timeout=TLD_FETCH_TIMEOUT_SECONDS, follow_redirects=True
        ) as client:
            response = await client.get(TLD_LIST_URL)
    except httpx.HTTPError as e:
        log.warning(
            "Could not fetch the TLD list from %s: %s; keeping the %s TLDs already loaded",
            TLD_LIST_URL,
            e,
            len(_tlds),
        )
        return False

    if response.status_code != 200:
        log.warning(
            "Could not fetch the TLD list from %s (HTTP %s); keeping the %s TLDs already loaded",
            TLD_LIST_URL,
            response.status_code,
            len(_tlds),
        )
        return False

    fetched = parse_tld_list(response.text)
    if fetched is None:
        log.warning(
            "The TLD list from %s was not a valid list; keeping the %s TLDs already loaded",
            TLD_LIST_URL,
            len(_tlds),
        )
        return False

    previous = len(_tlds)
    _tlds = fetched
    log.info("Loaded %s TLDs from %s (bundled list had %s)", len(fetched), TLD_LIST_URL, previous)
    return True


def parse_tld_list(body: str) -> frozenset[str] | None:
    """Parses a list in IANA's format: # comment lines, then one TLD per line.

    Returns:
        The lowercased TLDs, or None when any line is not a TLD label or the list is implausibly
        short.
    """
    parsed = set()
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not _TLD_LINE.fullmatch(stripped):
            return None
        parsed.add(stripped.lower())
    return frozenset(parsed) if len(parsed) >= MIN_TLD_COUNT else None
