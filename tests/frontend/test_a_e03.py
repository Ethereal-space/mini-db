import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from minidb.contracts.ast import SelectStmt
from minidb.contracts.errors import SyntaxError
from minidb.contracts.extensions import ExtensionStatement
from minidb.frontend import Frontend
from minidb.frontend.formatter import ast_to_data, format_ast
from .helpers import position_at


def parse_distinct(source):
    result = Frontend(enabled_extensions={"distinct"}).parse(source)
    assert len(result) == 1
    assert isinstance(result[0], ExtensionStatement)
    return result[0]


def test_a_e03_distinct_projection():
    result = parse_distinct("SELECT DISTINCT name,age FROM t;")
    assert result.feature == "distinct" and result.version == 1
    assert set(result.payload) == {"query"}
    query = result.payload["query"]
    assert query["kind"] == "SelectStmt"
    assert set(query["fields"]) == {"table", "columns", "where"}
    assert [c["fields"]["name"] for c in query["fields"]["columns"]] == ["name", "age"]


def test_a_e03_distinct_star():
    result = parse_distinct("SELECT DISTINCT * FROM t;")
    assert result.payload["query"]["fields"]["columns"] is None
    assert result.payload["query"]["fields"]["table"]["fields"]["name"] == "t"
    assert json.loads(json.dumps(result.payload)) == result.payload


def test_a_e03_double_distinct():
    source = "SELECT DISTINCT DISTINCT id FROM t;"
    with pytest.raises(SyntaxError) as caught:
        parse_distinct(source)
    assert caught.value.context["lexeme"] == "DISTINCT"
    assert set(caught.value.context["expected"]) == {"IDENTIFIER", "STAR"}
    assert caught.value.span.start == position_at(source, source.rindex("DISTINCT"))


@pytest.mark.parametrize("flags", [{"distinct"}, {"update", "order_limit", "distinct"}])
def test_a_e03_core_unchanged(flags):
    source = "CREATE TABLE t(id INT);INSERT INTO t VALUES(1);SELECT id FROM t;DELETE FROM t"
    result = Frontend(enabled_extensions=flags).parse(source)
    assert result == Frontend().parse(source)
    assert isinstance(result[2], SelectStmt)
    assert format_ast(result) == format_ast(Frontend().parse(source))


def test_a_e03_duplicate_projection_not_deduplicated():
    result = parse_distinct("SELECT DISTINCT name,name,age FROM t WHERE id=1")
    fields = result.payload["query"]["fields"]
    assert [c["fields"]["name"] for c in fields["columns"]] == ["name", "name", "age"]
    assert fields["where"]["fields"]["op"] == "="
    assert fields["where"]["fields"]["left"]["fields"]["name"] == "id"


def test_a_e03_case_and_source_positions():
    source = "-- demo\r\nSeLeCt /*x*/ dIsTiNcT NaMe FROM T WHERE name='DISTINCT';"
    result = parse_distinct(source)
    query = result.payload["query"]
    assert result.span.start == position_at(source, source.index("SeLeCt"))
    assert result.span.end.offset == len(source) - 1
    column = query["fields"]["columns"][0]
    assert column["fields"]["name"] == "name"
    assert column["span"]["start"]["offset"] == source.index("NaMe")
    assert query["fields"]["where"]["fields"]["right"]["fields"]["value"] == "DISTINCT"


def test_a_e03_disabled_and_independent_flags():
    for flags in ((), {"update"}, {"order_limit"}):
        with pytest.raises(SyntaxError) as caught:
            Frontend(enabled_extensions=flags).parse("SELECT DISTINCT * FROM t")
        assert caught.value.code == "EXTENSION_DISABLED"
        assert caught.value.context["lexeme"] == "DISTINCT"


@pytest.mark.parametrize("source,unexpected", [
    ("SELECT DISTINCT FROM t", "FROM"), ("SELECT DISTINCT", ""),
    ("SELECT DISTINCT *,id FROM t", ","), ("SELECT DISTINCT name, FROM t", "FROM"),
    ("SELECT name DISTINCT FROM t", "DISTINCT"),
    ("SELECT name FROM t DISTINCT", "DISTINCT"),
])
def test_a_e03_invalid_placement_or_projection(source, unexpected):
    with pytest.raises(SyntaxError) as caught:
        parse_distinct(source)
    assert caught.value.context["lexeme"] == unexpected


@pytest.mark.parametrize("suffix", ["ORDER BY id", "LIMIT 2", "ORDER BY id LIMIT 2"])
def test_a_e03_combinations_explicitly_rejected(suffix):
    with pytest.raises(SyntaxError) as caught:
        Frontend(enabled_extensions={"distinct", "order_limit"}).parse("SELECT DISTINCT id FROM t " + suffix)
    assert caught.value.code == "UNSUPPORTED_COMBINATION"
    assert caught.value.context["lexeme"] in ("ORDER", "LIMIT")


def test_a_e03_mixed_batch_trace():
    events = []
    frontend = Frontend(events.append, enabled_extensions={"update", "order_limit", "distinct"})
    result = frontend.parse("UPDATE t SET id=2;SELECT id FROM t LIMIT 1;SELECT DISTINCT id FROM t;SELECT * FROM t")
    assert [r.feature for r in result[:3]] == ["update", "order_limit", "distinct"]
    assert isinstance(result[3], SelectStmt)
    assert [e.stage for e in events] == ["TOKEN", "AST"]
    assert json.loads(events[-1].detail) == ast_to_data(result)


def test_a_e03_snapshot_examples():
    root = Path(__file__).resolve().parents[2]
    snapshot = json.loads((root / "docs/extensions/frontend_distinct_v1.json").read_text(encoding="utf-8"))
    for case in snapshot["valid_examples"]:
        assert ast_to_data(parse_distinct(case["sql"])) == case["expected"]
    assert len(snapshot["invalid_examples"]) >= 2
    for case in snapshot["invalid_examples"]:
        with pytest.raises(SyntaxError) as caught:
            parse_distinct(case["sql"])
        assert caught.value.context["lexeme"] == case["unexpected"]


def test_a_e03_demo_real_process_and_default_disabled():
    root = Path(__file__).resolve().parents[2]
    command = [sys.executable, "-m", "demo.frontend_demo", "--file", "demo/extensions.sql"]
    env = dict(os.environ, PYTHONUTF8="1")
    enabled = subprocess.run(command + ["--enable", "update", "--enable", "order_limit", "--enable", "distinct"],
                             cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert enabled.returncode == 0, enabled.stderr
    assert "3 条语句" in enabled.stdout
    data = json.loads(enabled.stdout.split("\nAST\n", 1)[1])
    assert [node["fields"]["feature"] for node in data] == ["update", "order_limit", "distinct"]
    disabled = subprocess.run(command, cwd=root, env=env, capture_output=True,
                              text=True, encoding="utf-8", timeout=20)
    assert disabled.returncode == 1
    assert "EXTENSION_DISABLED" in disabled.stderr
    assert disabled.stdout == ""
