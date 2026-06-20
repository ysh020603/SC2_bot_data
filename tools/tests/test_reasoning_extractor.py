from API_Tools.reasoning_extractor import extract_reasoning


def test_extracts_reasoning_content_field():
    completion = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "reasoning_content": "compare decimals",
                    "content": "9.8",
                }
            }
        ]
    }

    result = extract_reasoning(completion, mode="field_reasoning_content")

    assert result["reasoning"] == "compare decimals"
    assert result["final_content"] == "9.8"
    assert result["source"] == "message.reasoning_content"


def test_extracts_think_tag_block_from_content():
    completion = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "<think>compare decimals</think>\n\n9.8",
                }
            }
        ]
    }

    result = extract_reasoning(completion, mode="content_think_tags")

    assert result["reasoning"] == "compare decimals"
    assert result["final_content"] == "9.8"
    assert result["source"] == "content.think"


def test_extracts_dangling_think_tag_as_reasoning_only():
    completion = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "<think>compare decimals",
                }
            }
        ]
    }

    result = extract_reasoning(completion, mode="content_think_tags")

    assert result["reasoning"] == "compare decimals"
    assert result["final_content"] == ""
    assert result["source"] == "content.think.dangling"


def test_non_reasoning_mode_returns_empty_reasoning():
    completion = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "plain answer",
                }
            }
        ]
    }

    result = extract_reasoning(completion, mode="none")

    assert result["reasoning"] == ""
    assert result["final_content"] == "plain answer"
    assert result["source"] == "none"
