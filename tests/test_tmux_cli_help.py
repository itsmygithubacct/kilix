import contextlib
import io
import unittest
from kilix_tmux.__main__ import main


class HelpContract(unittest.TestCase):
    def help(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as exit:
            main([*args, '--help'])
        self.assertEqual(exit.exception.code, 0)
        return out.getvalue()

    def test_top_level_names_the_enter_difference(self):
        text = self.help()
        self.assertIn('without pressing Enter', text)
        self.assertIn('press Enter (submit)', text)

    def test_subcommand_help_matches_submission(self):
        self.assertIn('no Enter is sent', self.help('send'))
        self.assertIn('Enter is appended to submit', self.help('type'))
        self.assertNotIn('does not append Enter', self.help('type'))
