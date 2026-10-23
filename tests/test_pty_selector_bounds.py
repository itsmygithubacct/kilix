"""Syntax-only boundary fixtures; no request execution or broker access."""
import json
import unittest

import kilix_pty_selector as kilix


class StartupWordBounds(unittest.TestCase):
    def validators(self):
        return ((kilix.validate_selector, kilix.legacy.Refusal),)

    def test_actual_joined_bytes_below_at_and_above_boundary(self):
        for size in (4095, 4096, 4097):
            for words in (["a" * size], ["a", "b" * (size - 2)],
                          ["é" * 512, "b" * (size - 1025)],
                          ["🚀" * 128, "b" * (size - 513)]):
                selector = {"startup_words": words, "attached": False}
                self.assertEqual(len(" ".join(words).encode("utf-8")), size)
                self.assertLess(len(json.dumps(selector, ensure_ascii=True,
                                               separators=(",", ":")).encode()), 8192)
                for validate, error in self.validators():
                    with self.subTest(size=size, words=len(words), validate=validate.__name__):
                        if size <= 4096:
                            try:
                                result = validate(selector)
                            except error:
                                self.fail("a valid joined-word boundary was refused")
                            self.assertIs(result, selector)
                        else:
                            with self.assertRaises(error):
                                validate(selector)

    def test_word_count_controls_and_unicode_control_refusals(self):
        for validate, error in self.validators():
            self.assertEqual(validate({"startup_words": ["x"] * 64})["startup_words"], ["x"] * 64)
            for words in ([], ["x"] * 65, [""], ["a b"], ["a\tb"], ["\x00"], ["\x1b"], ["\x7f"],
                          ["\ud800"], [1], "x"):
                with self.subTest(validate=validate.__name__, words=repr(words)), self.assertRaises(error):
                    validate({"startup_words": words})

    def test_encoded_limit_is_independent_of_joined_word_limit(self):
        # ASCII JSON escapes add four bytes per é beyond its two-byte UTF-8 spelling.
        for validate, error in self.validators():
            for limit in (8191, 8192, 8193):
                prefix = "é" * 1360
                overhead = len(json.dumps({"startup_words": [prefix]}, ensure_ascii=True,
                                          separators=(",", ":")).encode())
                selector = {"startup_words": [prefix + "a" * (limit - overhead)]}
                encoded = len(json.dumps(selector, ensure_ascii=True, separators=(",", ":")).encode())
                self.assertEqual(encoded, limit)
                self.assertLess(len(selector["startup_words"][0].encode("utf-8")), 4096)
                with self.subTest(validate=validate.__name__, encoded=encoded):
                    if encoded <= 8192:
                        self.assertIs(validate(selector), selector)
                    else:
                        with self.assertRaises(error):
                            validate(selector)

    def test_opaque_ids_and_predicates_remain_literal(self):
        for validate, error in self.validators():
            for selector in ({"id": "-Ab_9"}, {"id_prefix": "."}, {"id_prefix": "..", "attached": True},
                             {"startup_words": ["É", "'x'", "0042"]}):
                self.assertIs(validate(selector), selector)
            for selector in ({"id": ".."}, {"id": "../x"}, {"id": "x", "attached": 0},
                             {"id": "x", "id_prefix": "x"}, {"startup_words": ["x"], "force": True}):
                with self.subTest(selector=selector), self.assertRaises(error):
                    validate(selector)
