import pytest

from ssm_env_sync import envfile


def test_parse_basic_forms():
    text = (
        "# comment\n"
        "\n"
        "PLAIN=value\n"
        "export EXPORTED=yes\n"
        "SPACED = padded  \n"
        "INLINE=value # trailing comment\n"
        "HASH=a#b\n"
        "EMPTY=\n"
        "SINGLE='keep $this # literal'\n"
        'DOUBLE="line1\\nline2 \\"quoted\\" back\\\\slash" # note\n'
        "URL=https://example.com/a?b=c\n"
    )
    assert envfile.parse(text) == {
        "PLAIN": "value",
        "EXPORTED": "yes",
        "SPACED": "padded",
        "INLINE": "value",
        "HASH": "a#b",
        "EMPTY": "",
        "SINGLE": "keep $this # literal",
        "DOUBLE": 'line1\nline2 "quoted" back\\slash',
        "URL": "https://example.com/a?b=c",
    }


def test_last_duplicate_wins():
    assert envfile.parse("A=1\nA=2\n") == {"A": "2"}


@pytest.mark.parametrize("text", ["not an assignment\n", "1BAD=x\n", "A='open\n", 'A="open\n', "A='x' junk\n"])
def test_parse_errors_report_line_number_not_content(text):
    with pytest.raises(envfile.EnvFileError) as excinfo:
        envfile.parse("OK=1\n" + text)
    message = str(excinfo.value)
    assert message.startswith("line 2:")
    assert "open" not in message and "junk" not in message


@pytest.mark.parametrize(
    "value",
    ["simple", "", "two words", "it's", 'say "hi"', "multi\nline", "tab\there", "a#b c", "$HOME", "back\\slash",
     "'\"mixed\n", " leading", "trailing ", "ünïcode ✓"],
)
def test_format_value_round_trips(value):
    assert envfile.parse(envfile.format_line("K", value)) == {"K": value}


def test_update_text_keeps_layout_and_appends_new_keys():
    text = "# header\nA=1\n\nexport B=2\n# tail\nC=3\n"
    updated = envfile.update_text(text, {"B": "two words", "Z": "9", "D": "4"}, removals={"C"})
    assert updated == "# header\nA=1\n\nexport B='two words'\n# tail\nD=4\nZ=9\n"


def test_update_text_rewrites_every_duplicate():
    assert envfile.update_text("A=1\nA=2\n", {"A": "3"}) == "A=3\nA=3\n"


def test_update_text_on_empty_file():
    assert envfile.update_text("", {"A": "1"}) == "A=1\n"
    assert envfile.update_text("", {}) == ""
