from spb.utilities import html_escape


def test_converts_html_to_plain_text() -> None:
    html = "<b>Report Action Expired</b>\n\nUse <code>/auth &lt;access_token&gt;</code> first."

    assert (
        html_escape.to_plain_text(html)
        == "Report Action Expired\n\nUse /auth <access_token> first."
    )


def test_keeps_literal_angle_brackets() -> None:
    assert html_escape.to_plain_text("1 < 2 and 3 > 2") == "1 < 2 and 3 > 2"


def test_escapes_html_characters() -> None:
    assert html_escape.escape("<b>Tom & Jerry</b>") == "&lt;b&gt;Tom &amp; Jerry&lt;/b&gt;"
    assert html_escape.escape(None) == ""
