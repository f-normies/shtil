"""Строгое чтение YAML: повтор ключа — ошибка, якоря и `<<:` работают."""

import pytest
import yaml

from clin_compass_knowledge.loader import load_yaml


def test_duplicate_key_is_an_error() -> None:
    with pytest.raises(yaml.YAMLError, match="повторяется ключ title"):
        load_yaml("title: a\ntitle: b\n")


def test_duplicate_key_in_nested_mapping_is_an_error() -> None:
    with pytest.raises(yaml.YAMLError, match="повторяется ключ x"):
        load_yaml("items:\n  - {x: 1, x: 2}\n")


def test_anchors_and_merge_keys_work() -> None:
    text = "base: &b {x: 1, y: 2}\nchild:\n  <<: *b\n  x: 3\ncopy: *b\n"

    assert load_yaml(text) == {
        "base": {"x": 1, "y": 2},
        "child": {"x": 3, "y": 2},
        "copy": {"x": 1, "y": 2},
    }


def test_empty_text_is_none() -> None:
    assert load_yaml("# только комментарий\n") is None


def test_map_tag_on_scalar_is_a_yaml_error() -> None:
    with pytest.raises(yaml.YAMLError):
        load_yaml("x: !!map foo\n")
