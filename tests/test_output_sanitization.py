from piagent.utils import is_error_tool_result, sanitize_final_text


def test_sanitize_final_text_removes_closed_reasoning_block():
    text = "<reasoning>private chain of thought</reasoning>\n사용자에게 보일 답변"

    assert sanitize_final_text(text) == "사용자에게 보일 답변"


def test_sanitize_final_text_fails_closed_for_unterminated_reasoning():
    text = "<reasoning>private chain of thought without a closing tag"

    assert sanitize_final_text(text) == (
        "The model returned no user-visible answer after internal reasoning was removed."
    )


def test_sanitize_final_text_removes_provider_final_message_prefix():
    assert sanitize_final_text("<final<|message|>**완료**") == "**완료**"
    assert sanitize_final_text("<|assistant|><|final|>answer") == "answer"


def test_is_error_tool_result_detects_string_encoded_tool_failures():
    assert is_error_tool_result("Error: query is empty.") is True
    assert is_error_tool_result("Error fetching URL 'https://example.test': HTTP Error 404") is True
    assert is_error_tool_result("url=https://example.test\nchar_count=10") is False
