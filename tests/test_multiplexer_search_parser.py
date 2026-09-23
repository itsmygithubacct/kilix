"""Deterministic controls for the reported-search parser.

`search_roots` records what the compiler reported it searched. That is a fact
about the past, so these controls fix what the parser must do independently of
what the filesystem looks like when the parse happens, and fix the boundary of
the search block itself so unrelated indented diagnostics never enter the
population.

The direct controls need no compiler. One control at the end runs the real
system compiler and removes a listed directory before parsing its trace; it is
an actual-GCC observation with no race, because the trace is captured first and
parsed afterwards under this test's own control.
"""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'search_parser_guards', ROOT / 'config/multiplexer_build_guards.py')
guards = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guards)

COMPILER = Path('/usr/bin/cc')


def trace(*lines):
    return ('\n'.join(lines) + '\n').encode()


class SearchListParsingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='kmx-parser-', dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # The snapshot's parent is the private generation: roots under it are
        # the private ones the default call filters out.
        self.generation = self.root / 'generation'
        self.snapshot = self.generation / 'source'
        self.snapshot.mkdir(parents=True)
        self.present = self.root / 'present'
        self.present.mkdir()
        self.absent = self.root / 'absent'

    def listing(self, *entries, before=(), after=()):
        return trace(*before,
                     '#include "..." search starts here:',
                     '#include <...> search starts here:',
                     *(' ' + str(entry) for entry in entries),
                     'End of search list.',
                     *after)

    def test_reported_directory_removed_before_parsing_stays_in_roots(self):
        # The whole point: the record of a past search must not depend on the
        # filesystem state at parse time.
        self.assertFalse(self.absent.exists())
        roots = guards.search_roots(self.listing(self.present, self.absent), self.snapshot)
        self.assertEqual(roots, sorted([str(self.present), str(self.absent)]))

    def test_present_directory_is_admitted_exactly_as_before(self):
        roots = guards.search_roots(self.listing(self.present), self.snapshot)
        self.assertEqual(roots, [str(self.present)])

    def test_indented_diagnostics_outside_the_search_block_are_not_roots(self):
        # GCC indents ordinary diagnostic context. None of it is a search entry,
        # and none of it may enter the watched population -- including a line
        # that happens to name a directory that exists.
        listed = self.root / 'listed'
        listed.mkdir()
        noise = ('  from /usr/include/features.h:1,',
                 '    2 | #include <guard.h>',
                 '      |          ^~~~~~~~~',
                 ' ' + str(self.present))
        tail = (' ' + str(self.present),
                '  note: each undeclared identifier is reported only once')
        roots = guards.search_roots(
            self.listing(listed, before=noise, after=tail), self.snapshot)
        self.assertEqual(roots, [str(listed)])

    def test_ignored_directory_lines_are_still_recorded(self):
        missing = self.root / 'nonexistent'
        duplicate = self.root / 'duplicate'
        roots = guards.search_roots(
            self.listing(self.present,
                         before=('ignoring nonexistent directory "%s"' % missing,
                                 'ignoring duplicate directory "%s"' % duplicate)),
            self.snapshot)
        self.assertEqual(roots, sorted([str(self.present), str(missing), str(duplicate)]))

    def test_relative_entries_normalize_against_the_snapshot(self):
        roots = guards.search_roots(self.listing('include', '.'), self.snapshot,
                                    include_private=True)
        self.assertEqual(roots, sorted([str(self.snapshot / 'include'), str(self.snapshot)]))

    def test_private_roots_are_filtered_unless_requested(self):
        private = self.generation / 'tmp'
        listing = self.listing(self.present, private)
        self.assertEqual(guards.search_roots(listing, self.snapshot), [str(self.present)])
        self.assertEqual(guards.search_roots(listing, self.snapshot, include_private=True),
                         sorted([str(self.present), str(private)]))

    def test_a_trace_without_a_reported_search_is_refused(self):
        for missing in (('#include <...> search starts here:', ' ' + str(self.present)),
                        (' ' + str(self.present), 'End of search list.')):
            with self.subTest(missing=missing[0]):
                with self.assertRaises(ValueError):
                    guards.search_roots(trace(*missing), self.snapshot)

    def test_a_second_block_continues_the_same_population(self):
        # The aggregated build trace carries one block per translation unit.
        other = self.root / 'other'
        first = self.listing(self.present).decode()
        second = self.listing(other).decode()
        roots = guards.search_roots((first + second).encode(), self.snapshot)
        self.assertEqual(roots, sorted([str(self.present), str(other)]))

    @unittest.skipUnless(COMPILER.exists(), 'the system compiler is required')
    def test_actual_compiler_listing_survives_removal_before_parsing(self):
        selected = self.root / 'selected'
        selected.mkdir()
        (selected / 'late.h').write_text('#define LATE 1\n')
        source = self.root / 'probe.c'
        source.write_text('int main(void){return 0;}\n')
        # Capture the real search list first; parse it only afterwards, so this
        # is an ordinary observation and not a race.
        result = subprocess.run(
            [str(COMPILER), '-Wp,-v', '-E', '-o', '/dev/null', '-I', str(selected), str(source)],
            capture_output=True, check=False,
            env=dict(PATH='/usr/bin:/bin', LC_ALL='C', HOME=str(self.root)))
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
        reported = result.stderr
        self.assertIn(b'\n ' + os.fsencode(selected) + b'\n', reported)
        shutil.rmtree(selected)
        self.assertFalse(selected.exists())
        roots = guards.search_roots(reported, self.snapshot, include_private=True)
        self.assertIn(str(selected), roots)
        # Everything else the compiler actually reported is still there too.
        self.assertIn('/usr/include', roots)


if __name__ == '__main__':
    unittest.main()
