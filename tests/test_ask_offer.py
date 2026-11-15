"""The LFM2 question `kilix ask` asks once, and what each answer saves."""
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
import kilix_ask_offer as offer  # noqa: E402

FACTS = {"label": "LFM2", "version": "v10", "download_bytes": 1_346_876_805,
         "installed_bytes": 1_432_537_967, "reuse": None}


class OfferTests(unittest.TestCase):
    def setUp(self):
        self.saved, self.recorded, self.installs = [], [], []
        self.save_ok = True
        for name, value in (
                ("facts", lambda: self.facts), ("wizard_answer", lambda: self.wizard),
                ("wizard_record", self.recorded.append),
                ("save", lambda needle, backend: self.saved.append(backend) or self.save_ok)):
            patcher = patch.object(offer, name, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.facts, self.wizard = dict(FACTS), None

    def ask(self, typed, status=0):
        output = io.StringIO()
        result = offer.offer("/needle", source=io.StringIO(typed), output=output,
                             install=lambda: self.installs.append(1) or status)
        return result, output.getvalue()

    def test_the_question_states_size_locality_and_that_terms_come_first(self):
        _result, text = self.ask("n\n")
        self.assertIn("built-in grammar", text)
        self.assertIn("1.35 GB download", text)
        self.assertIn("1.43 GB of disk", text)
        self.assertIn("runs on this computer", text)
        self.assertIn("licence terms are shown before anything is installed", text)
        self.assertIn("Install the LFM2 command model now? [y/N] ", text)

    def test_an_earlier_delivery_is_offered_without_a_download(self):
        self.facts["reuse"] = "/apps/assets/lfm2-kilix-commands--old"
        _result, text = self.ask("n\n")
        self.assertIn("nothing is downloaded", text)
        self.assertNotIn("GB download", text)

    def test_unreadable_catalog_facts_still_ask_without_numbers(self):
        self.facts = None
        result, text = self.ask("n\n")
        self.assertEqual(result, "declined")
        self.assertNotIn("GB", text)

    def test_no_and_a_bare_enter_save_grammar_and_decline_the_wizard_page(self):
        for typed in ("n\n", "\n", "no\n", "maybe\n"):
            with self.subTest(typed=typed):
                self.saved.clear(); self.recorded.clear()
                result, text = self.ask(typed)
                self.assertEqual((result, self.saved, self.recorded, self.installs),
                                 ("declined", ["grammar"], ["no"], []))
                self.assertIn("will not ask again", text)
                self.assertIn("kilix-needle workflows setup --model lfm2", text)

    def test_a_no_that_could_not_be_saved_does_not_claim_it_was(self):
        self.save_ok = False
        _result, text = self.ask("n\n")
        self.assertNotIn("will not ask again", text)

    def test_yes_installs_then_saves_lfm2_and_answers_the_wizard_page(self):
        result, _text = self.ask("y\n")
        self.assertEqual((result, self.installs, self.saved, self.recorded),
                         ("installed", [1], ["lfm2"], ["yes"]))

    def test_a_declined_licence_or_failed_install_saves_nothing(self):
        result, text = self.ask("yes\n", status=1)
        self.assertEqual((result, self.saved, self.recorded), ("failed", [], []))
        self.assertIn("will offer the model again", text)

    def test_end_of_input_saves_nothing(self):
        result, _text = self.ask("")
        self.assertEqual((result, self.saved, self.recorded, self.installs), ("unanswered", [], [], []))

    def test_a_no_on_the_wizard_page_is_the_answer_and_nothing_is_asked(self):
        self.wizard = "no"
        result, text = self.ask("y\n")
        self.assertEqual((result, text, self.saved, self.installs), ("declined", "", ["grammar"], []))

    def test_main_without_a_terminal_asks_nothing(self):
        with patch.object(offer, "offer", side_effect=AssertionError("asked")):
            self.assertEqual(offer.main(["/needle"]), 0)
        self.assertEqual(offer.main([]), 2)


class WizardRecordTests(unittest.TestCase):
    """The real wizard state: only an unanswered Kilix Ask page takes Ask's answer."""

    def setUp(self):
        import tempfile
        import model_wizard
        self.wizard = model_wizard
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.object(model_wizard, "state_root", return_value=Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_and_yes_answer_an_unanswered_page(self):
        self.assertIsNone(offer.wizard_answer())
        offer.wizard_record("no")
        self.assertEqual(offer.wizard_answer(), "no")
        entry = self.wizard.answers()["workflows"]
        self.assertEqual((entry["models"], entry["scope"], entry["phase"]), ([], "ask", "complete"))
        self.assertEqual(self.wizard.legacy_state("workflows"), {"answer": "no", "scope": "ask"})

    def test_yes_records_lfm2_as_complete_so_finish_has_nothing_pending(self):
        offer.wizard_record("yes")
        self.assertEqual(self.wizard.answers()["workflows"]["models"], ["lfm2-kilix-commands"])
        self.assertEqual(self.wizard.pending(), {})

    def test_an_answered_or_planned_page_is_left_alone(self):
        self.wizard.record("workflows", "yes", ["needle3-kilix-commands"], "planned")
        before = self.wizard.answers()
        offer.wizard_record("no")
        offer.wizard_record("yes")
        self.assertEqual(self.wizard.answers(), before)

    def test_unusable_wizard_state_never_stops_ask(self):
        with patch.object(self.wizard, "answers", side_effect=ValueError("bad state")):
            self.assertIsNone(offer.wizard_answer())
            offer.wizard_record("no")


if __name__ == "__main__":
    unittest.main()
