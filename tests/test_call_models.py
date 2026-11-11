"""Playalong and PDF install models only on the call that needs them."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
import call_models  # noqa: E402


class PlayalongCallTests(unittest.TestCase):
    def models(self, *arguments, declined=None, unreadable=False):
        return call_models.models_for_call(
            "playalong", arguments, declined=declined, unreadable=unreadable)

    def test_create_and_resume_install_the_defaults(self):
        for command in ("create", "resume"):
            with self.subTest(command=command):
                self.assertEqual(self.models(command), call_models.playalong_models.DEFAULT_MODELS)

    def test_read_only_verbs_install_nothing(self):
        for arguments in (
            (), ("help",), ("-h",), ("--help",), ("list",), ("doctor",),
            ("show",), ("options",), ("models",), ("models", "install"),
            ("serve",), ("songbook",), ("--", "list"),
        ):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.models(*arguments), ())

    def test_a_declined_default_set_is_not_installed(self):
        self.assertEqual(self.models("create", declined="page no"), ())

    def test_an_unreadable_answer_installs_nothing(self):
        self.assertEqual(
            self.models("create", "--language", "fr", unreadable=True), ())

    def test_a_non_english_lyric_call_adds_large_v3(self):
        large = call_models.playalong_models.OPTIONAL_MODELS[0]
        self.assertEqual(self.models("create", "--language", "fr")[-1], large)
        self.assertEqual(self.models("resume", "--language=de")[-1], large)
        self.assertNotIn(large, self.models("create", "--language", "en"))
        self.assertNotIn(large, self.models("create", "--language", "english"))
        self.assertNotIn(large, self.models("create", "--language=auto"))
        self.assertEqual(self.models("create", "--model=large-v3")[-1], large)
        self.assertEqual(self.models("resume", "--whisper", "large-v3")[-1], large)

    def test_large_v3_still_installs_when_the_defaults_were_declined(self):
        large = call_models.playalong_models.OPTIONAL_MODELS[0]
        self.assertEqual(
            self.models("create", "--language", "fr", declined="page no"), (large,))

    def test_a_read_only_verb_does_not_install_large_v3(self):
        self.assertEqual(self.models("list", "--language", "fr"), ())
        self.assertEqual(self.models("serve", "--model=large-v3"), ())


class PdfCallTests(unittest.TestCase):
    def models(self, *arguments):
        return call_models.models_for_call("kilix-pdf-conversion", arguments)

    def test_convert_and_batch_install_the_text_models(self):
        for command in ("convert", "batch"):
            with self.subTest(command=command):
                self.assertEqual(self.models(command), call_models.PDF_TEXT)

    def test_inspect_and_plan_install_nothing(self):
        for command in ("", "inspect", "plan", "doctor", "recommend",
                        "validate", "interactive", "help"):
            with self.subTest(command=command):
                self.assertEqual(self.models(command, "granite-hybrid", "--chart-extraction"), ())

    def test_granite_and_charts_are_added_only_when_named(self):
        self.assertEqual(self.models("convert", "granite-hybrid")[-1], call_models.PDF_GRANITE)
        self.assertEqual(self.models("batch", "granite-pure-vlm")[-1], call_models.PDF_GRANITE)
        self.assertNotIn(call_models.PDF_GRANITE, self.models("convert", "notes.pdf"))
        self.assertEqual(
            self.models("convert", "--chart-extraction")[-1], call_models.PDF_CHART)
        self.assertNotIn(call_models.PDF_CHART, self.models("convert"))

    def test_an_action_flag_selects_the_command(self):
        self.assertEqual(
            self.models("--action", "convert"), call_models.PDF_TEXT)
        self.assertEqual(self.models("--action", "inspect"), ())


class OtherCallsInstallNothing(unittest.TestCase):
    def test_an_unrelated_application_has_no_models(self):
        self.assertEqual(call_models.models_for_call("kilix-find", ["run"]), ())


if __name__ == "__main__":
    unittest.main()
