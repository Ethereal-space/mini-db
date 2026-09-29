from demo.frontend_gui import analyze_sql


def test_gui_analysis_formats_core_tokens_and_ast():
    result = analyze_sql("SELECT name FROM student WHERE age >= 18;")

    assert result.ok
    assert result.status == "解析成功：共 1 条 SQL 语句。"
    assert '"kind": "SELECT"' in result.token_text
    assert '"kind": "SelectStmt"' in result.ast_text
    assert result.error_text == ""


def test_gui_analysis_keeps_window_friendly_error_details():
    result = analyze_sql("SELECT name FROM student WHERE age > ;")

    assert not result.ok
    assert result.error_line == 1
    assert result.error_column == 38
    assert "SYNTAX" in result.error_text
    assert result.token_text


def test_gui_analysis_uses_selected_extensions():
    disabled = analyze_sql("SELECT DISTINCT age FROM student;")
    enabled = analyze_sql("SELECT DISTINCT age FROM student;", {"distinct"})

    assert not disabled.ok
    assert enabled.ok
    assert '"feature": "distinct"' in enabled.ast_text


def test_gui_analysis_rejects_empty_input_without_crashing():
    result = analyze_sql("  \n")

    assert not result.ok
    assert result.error_text == "SQL 输入为空。"
    assert result.error_line is None
