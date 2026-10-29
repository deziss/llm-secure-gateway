"""Model-group normalisation and OBS_MODEL_GROUPS override parsing."""
import pytest

from llm_gateway.observability.collect.groups import (
    group_key, normalize, parse_overrides, validate_overrides,
)


@pytest.mark.parametrize("raw,expected", [
    ("qwen2.5:7b", "qwen2.5-7b"),
    ("Qwen/Qwen2.5-7B-Instruct", "qwen2.5-7b-instruct"),
    ("llama3:latest", "llama3"),
    ("Llama-3.1-8B.gguf", "llama-3.1-8b"),
    ("meta_llama 3 8b", "meta-llama-3-8b"),
    ("library/llama3:8b-instruct-q4_K_M", "llama3-8b-instruct-q4-k-m"),
    ("  Mistral  ", "mistral"),
    ("", ""),
    (None, ""),
])
def test_normalize_table(raw, expected):
    assert normalize(raw) == expected


def test_same_model_different_spellings_share_a_group():
    names = ["qwen2.5:7b", "Qwen/Qwen2.5-7B", "qwen2.5_7b"]
    assert len({group_key(n) for n in names}) == 1


def test_overrides_win_and_match_raw_tail_or_normalised_name():
    ov = {"coder": ["*coder*"], "qwen-7b": ["qwen2.5-7b*", "Llama-3*"]}
    assert group_key("Qwen/Qwen2.5-Coder-7B", ov) == "coder"           # raw / tail glob
    assert group_key("qwen2.5:7b", ov) == "qwen-7b"                    # normalised name glob
    assert group_key("llama-3-8b", ov) == "qwen-7b"                    # case-insensitive pattern
    assert group_key("mistral:7b", ov) == "mistral-7b"                 # no match -> normal key


def test_first_matching_group_in_dict_order_wins():
    ov = {"first": ["llama*"], "second": ["llama3*"]}
    assert group_key("llama3:8b", ov) == "first"


def test_parse_overrides_is_tolerant():
    assert parse_overrides("") == {}
    assert parse_overrides("not json") == {}
    assert parse_overrides("[1,2]") == {}
    assert parse_overrides('{"g": "single*", "": ["x"], "h": [" ", "y*"]}') == {"g": ["single*"], "h": ["y*"]}


def test_validate_overrides_rejects_bad_shapes():
    assert validate_overrides({" g ": [" a* "]}) == {"g": ["a*"]}
    for bad in ({"": ["a"]}, {"g": "a"}, {"g": []}, {"g": [1]}, {"x" * 65: ["a"]}):
        with pytest.raises(ValueError):
            validate_overrides(bad)
