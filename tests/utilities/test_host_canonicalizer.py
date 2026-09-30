import pytest
import respx

from spb.utilities import host_canonicalizer
from spb.utilities.host_canonicalizer import MIN_TLD_COUNT, TLD_LIST_URL

BUNDLED_TLDS = host_canonicalizer._tlds


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.co.uk/path", "example.co.uk"),
        ("HTTPS://WWW.Evil.COM/Login", "www.evil.com"),
        ("http://evil.com.", "evil.com"),
        ("http://evil.com:8080/x?y#z", "evil.com"),
        ("http://user:pass@evil.com/", "evil.com"),
        # Browsers open the host after the last '@', not the one that looks legitimate.
        ("http://google.com@evil.com/", "evil.com"),
        ("http://google.com:x@evil.com:443", "evil.com"),
        # A backslash ends the authority of a special URL, as in browsers.
        ("http://evil.com\\@google.com/", "evil.com"),
        ("http:evil.com", "evil.com"),
        ("https:////evil.com", "evil.com"),
        ("evil.com/path", "evil.com"),
        ("evil.com:8080/path", "evil.com"),
        ("wss://chat.evil.com", "chat.evil.com"),
        ("ftp://files.evil.com", "files.evil.com"),
        ("custom://files.evil.com/", "files.evil.com"),
        ("http://%65vil.com/", "evil.com"),
        ("http://пример.рф/", "xn--e1afmkfd.xn--p1ai"),
        ("http://xn--e1afmkfd.xn--p1ai/", "xn--e1afmkfd.xn--p1ai"),
        ("http://evil。com/", "evil.com"),
        (" http://evil.com/ ", "evil.com"),
        ("setup.py", "setup.py"),
        ("http://8.8.8.8/", "8.8.8.8"),
        ("http://8.8.8.8:53", "8.8.8.8"),
        # WHATWG IPv4 forms a browser accepts.
        ("http://0x08080808/", "8.8.8.8"),
        ("http://134744072/", "8.8.8.8"),
        ("http://010.010.010.010/", "8.8.8.8"),
        ("http://8.8.2056/", "8.8.8.8"),
        ("http://8.526344/", "8.8.8.8"),
        ("http://[2001:4860:4860::8888]/", "2001:4860:4860::8888"),
        ("http://[2001:4860:4860:0:0:0:0:8888]:443/", "2001:4860:4860::8888"),
        ("http://[::ffff:8.8.8.8]/", "8.8.8.8"),
        ("http://[::FFFF:0808:0808]/", "8.8.8.8"),
    ],
)
def test_urls_yield_their_canonical_host(url: str, expected: str) -> None:
    assert host_canonicalizer.from_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        " ",
        "http://",
        "http:///",
        "https://@",
        "http://:80",
        "http://[]",
        "http://[::1",
        "http://[2001:4860::8888]x",
        "http://evil.com:99999",
        "http://evil.com:8o",
        "mailto:someone@evil.com",
        "tel:+123456789",
        "javascript:alert('evil.com')",
        "data:text/html,evil.com",
        "tg://resolve?domain=evil",
        "file:///etc/passwd",
        "http://localhost/",
        "http://localhost:8080",
        "localhost:8080",
        "http://intranet/",
        "http://evil.local/",
        "http://evil.invalid/",
        "http://evil.test/",
        "http://router.lan",
        "http://example.com/",
        "http://www.example.org/",
        "http://evil.notarealtld/",
        "http://evil..com/",
        "http://.evil.com/",
        "http://evil.com../",
        "http://-evil.com/",
        "http://evil-.com/",
        "http://ev_il.com/",
        "http://evil com/",
        "http://evil%2/",
        "http://evil%zz.com/",
        "http://%ff.com/",
        "http://xn--.com/",
        "http://xn--zz.com/",
        "http://127.0.0.1/",
        "http://0x7f.1/",
        "http://2130706433/",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://169.254.169.254/",
        "http://100.64.0.1/",
        "http://0.0.0.0/",
        "http://255.255.255.255/",
        "http://224.0.0.1/",
        "http://192.0.2.1/",
        "http://198.51.100.1/",
        "http://203.0.113.1/",
        "http://256.1.1.1/",
        "http://1.2.3.4.5/",
        "http://1.2.3.08/",
        "http://0x100000000/",
        "http://99999999999999999999999999/",
        "http://1.2.3.4a/",
        "http://[::1]/",
        "http://[::]/",
        "http://[fe80::1]/",
        "http://[fc00::1]/",
        "http://[ff02::1]/",
        "http://[2001:db8::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[fe80::1%25eth0]/",
        "http://[1:2:3:4:5:6:7:8:9]/",
        "http://[1::2::3]/",
        "http://[12345::1]/",
        "http://[2001:4860:::8888]/",
    ],
)
def test_unacceptable_urls_yield_nothing(url: str | None) -> None:
    assert host_canonicalizer.from_url(url) is None


def test_oversized_names_yield_nothing() -> None:
    assert host_canonicalizer.from_url("http://" + "a" * 64 + ".com/") is None
    assert host_canonicalizer.from_text("a." * 127 + "com") is None
    assert host_canonicalizer.from_text("a" * 63 + ".com") == "a" * 63 + ".com"


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("evil.com", "evil.com"),
        ("Evil.COM", "evil.com"),
        ("evil.com.", "evil.com"),
        ("sub.evil.co.uk", "sub.evil.co.uk"),
        ("xn--e1afmkfd.xn--p1ai", "xn--e1afmkfd.xn--p1ai"),
        ("8.8.8.8", "8.8.8.8"),
        ("2001:4860:4860::8888", "2001:4860:4860::8888"),
        ("2001:4860:4860:0000:0000:0000:0000:8888", "2001:4860:4860::8888"),
        ("[2001:4860:4860::8888]", "2001:4860:4860::8888"),
        ("2001:DB9:0:0:1:0:0:1", "2001:db9::1:0:0:1"),
        ("2001:4860:0:1:0:0:0:1", "2001:4860:0:1::1"),
        ("2001:4860:1:1:1:1:0:1", "2001:4860:1:1:1:1:0:1"),
        ("::ffff:8.8.8.8", "8.8.8.8"),
    ],
)
def test_words_yield_their_canonical_host(word: str, expected: str) -> None:
    assert host_canonicalizer.from_text(word) == expected


@pytest.mark.parametrize(
    "word",
    [
        None,
        "",
        "e.g",
        "i.e",
        "Mr.Smith",
        "3.14",
        "1.2.3",
        "v1.2.3.4",
        "1.2.3.4.5",
        # Leading zeros are ambiguous (octal to some parsers), so plain text does not guess.
        "010.8.8.8",
        "8.8.8.08",
        "0x8.8.8.8",
        "setup.py",
        "notes.md",
        "Main.java",
        "archive.zip",
        "run.sh",
        "lib.rs",
        "readme.txt",
        "config.yml",
        "example.com",
        "evil.localhost",
        "12:30:45",
        "aa:bb:cc:dd:ee:ff",
        "a::b",
        "::",
        "::1",
        "fe80::1",
        "2001:db8::1",
    ],
)
def test_unacceptable_words_yield_nothing(word: str | None) -> None:
    assert host_canonicalizer.from_text(word) is None


def test_ipv6_formatting_follows_rfc5952() -> None:
    # The first of two equally long zero runs is compressed.
    assert host_canonicalizer.format_ipv6([0x2001, 0x4860, 0, 0, 1, 0, 0, 0]) == "2001:4860:0:0:1::"
    assert (
        host_canonicalizer.format_ipv6([0x2001, 0x4860, 0, 0, 1, 0, 0, 1]) == "2001:4860::1:0:0:1"
    )
    # A single zero group is never compressed.
    assert (
        host_canonicalizer.format_ipv6([0x2001, 0x4860, 0, 1, 1, 1, 1, 1])
        == "2001:4860:0:1:1:1:1:1"
    )


def test_parses_an_iana_formatted_tld_list() -> None:
    body = "# Version 2026092400, Last Updated Thu Sep 24 07:07:01 2026 UTC\n"
    body += "".join(f"TLD{index}\r\n" for index in range(MIN_TLD_COUNT))
    body += "XN--P1AI\n\n"

    tlds = host_canonicalizer.parse_tld_list(body)

    assert tlds is not None
    assert len(tlds) == MIN_TLD_COUNT + 1
    assert "tld0" in tlds
    assert "xn--p1ai" in tlds


def test_refuses_responses_that_are_not_a_tld_list() -> None:
    valid = "COM\n" + "\n".join(f"T{index}" for index in range(1_200))
    assert host_canonicalizer.parse_tld_list(valid) is not None

    # Too short: a truncated download.
    assert host_canonicalizer.parse_tld_list("# Version 1\nCOM\nNET\nORG\n") is None
    # Anything that is not a bare label: an HTML error page or proxy notice.
    page = "<html><body>Service Unavailable</body></html>\n"
    assert host_canonicalizer.parse_tld_list(page + valid) is None
    assert host_canonicalizer.parse_tld_list(valid + "\nexample.com") is None
    assert host_canonicalizer.parse_tld_list("") is None


@pytest.mark.anyio
async def test_refresh_installs_the_fetched_list(respx_mock: respx.MockRouter) -> None:
    body = "\n".join(["# Version 1", "COM", "NEWTLD", *(f"T{index}" for index in range(1_000))])
    respx_mock.get(TLD_LIST_URL).respond(text=body)

    try:
        assert await host_canonicalizer.refresh_tlds()
        assert host_canonicalizer.from_text("evil.newtld") == "evil.newtld"
    finally:
        host_canonicalizer._tlds = BUNDLED_TLDS


@pytest.mark.anyio
async def test_refresh_keeps_the_list_when_the_fetch_fails(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(TLD_LIST_URL).respond(503)

    assert not await host_canonicalizer.refresh_tlds()
    assert host_canonicalizer.from_text("evil.com") == "evil.com"
