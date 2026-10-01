"""Frozen-task source data. Evaluators and controls never enter agent checkouts."""
from textwrap import dedent, indent


def code(text):
    return dedent(text).lstrip("\n")


def tests(body):
    return ("import os, sys, unittest, subprocess\nfrom pathlib import Path\n"
            "ROOT = Path(os.environ.get('JEVTO_EVAL_WORKSPACE', Path(__file__).resolve().parent)).resolve()\n"
            "sys.path.insert(0, str(ROOT))\n\nclass Checks(unittest.TestCase):\n"
            + indent(code(body), "    ")
            + "\nif __name__ == '__main__':\n    unittest.main()\n")


def task(identifier, prompt, reference, starter, mutant, visible, hidden, language="python"):
    if language == "rust":
        manifest = '[package]\nname = "jevto-offline-wrapper"\nversion = "0.0.0"\nedition = "2021"\n\n[[bin]]\nname = "wrapper"\npath = "main.rs"\n'
        for files in (reference, starter, mutant):
            files["Cargo.toml"] = manifest
    return dict(id=identifier, prompt=prompt, reference=reference, files=starter,
                mutants=[starter, mutant], visible=tests(visible), hidden=tests(hidden), language=language)


CONFIG = code('''
def resolve(defaults, file_values, env_values, cli_values):
    result = {}
    for layer in (defaults, file_values, env_values, cli_values):
        result.update(layer)
    port = result['port']
    if isinstance(port, str) and port.isascii() and port.isdigit():
        port = int(port)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('port')
    debug = result['debug']
    if debug in ('true', 'false'):
        debug = debug == 'true'
    if type(debug) is not bool:
        raise ValueError('debug')
    return {'port': port, 'debug': debug}
''')

PAGINATION = code('''
def collect_pages(fetch, start=None, max_pages=100):
    if type(max_pages) is not int or max_pages < 1:
        raise ValueError('max_pages')
    result, seen, cursor = [], set(), start
    for _ in range(max_pages):
        if cursor in seen:
            raise ValueError('cursor cycle')
        seen.add(cursor)
        page = fetch(cursor)
        result.extend(page['items'])
        cursor = page['next_cursor']
        if cursor is None:
            return result
    raise ValueError('page limit')
''')

STATE = code('''
def apply_batch(state, operations):
    candidate = dict(state)
    for key, delta in operations:
        if type(delta) is not int:
            raise ValueError('delta')
        value = candidate.get(key, 0) + delta
        if value < 0:
            raise ValueError('negative balance')
        candidate[key] = value
    state.clear()
    state.update(candidate)
''')

LOGS = code('''
import json
def select_logs(lines, level, component):
    result = []
    for raw in lines:
        try:
            row = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(row, dict):
            continue
        item_level = row.get('level')
        if isinstance(item_level, str) and item_level.upper() == level.upper() and row.get('component') == component:
            result.append(raw)
    return result
''')

CATALOG = code('''
def lookup(items, identifier, *, case_sensitive=False):
    key = identifier if case_sensitive else identifier.casefold()
    matches = [item for item in items if (item['id'] if case_sensitive else item['id'].casefold()) == key]
    if len(matches) > 1:
        raise ValueError('ambiguous identifier')
    return matches[0]['value'] if matches else None
''')
SERVICE = "from catalog import lookup\ndef find_item(items, identifier):\n    return lookup(items, identifier, case_sensitive=False)\n"

CLI = code('''
use std::{env, process::{Command, exit}};
fn main() {
    let mut args = env::args_os().skip(1);
    let Some(program) = args.next() else { exit(2) };
    let code = match Command::new(program).args(args).status() {
        Ok(status) => status.code().unwrap_or(1),
        Err(_) => 2,
    };
    exit(code);
}
''')

TASKS = [
    task("config-precedence", "Fix app.resolve(defaults, file_values, env_values, cli_values). Later layers override earlier ones by key presence, including False. Return only port and debug. Port is an int (not bool) or ASCII digit string in 1..65535; debug is bool or exactly 'true'/'false'. Invalid values raise ValueError. Do not mutate inputs.",
         {"app.py": CONFIG}, {"app.py": CONFIG.replace("result.update(layer)", "result.update({k: v for k, v in layer.items() if v})")},
         {"app.py": CONFIG.replace("<= 65535", "<= 65536")},
         '''
def test_layers(self):
    from app import resolve
    self.assertEqual(resolve({'port': 80, 'debug': False}, {'port': 90}, {'debug': 'true'}, {'port': '100'}), {'port': 100, 'debug': True})
''', '''
def test_false_override(self):
    from app import resolve
    defaults = {'port': 80, 'debug': True}
    self.assertEqual(resolve(defaults, {}, {}, {'debug': False}), {'port': 80, 'debug': False})
    self.assertEqual(defaults, {'port': 80, 'debug': True})
def test_bounds_and_types(self):
    from app import resolve
    for bad in (True, 0, 65536, ' 80', '12x', None):
        with self.subTest(port=bad), self.assertRaises(ValueError):
            resolve({'port': bad, 'debug': False}, {}, {}, {})
    for port in (1, 65535, '42'):
        self.assertEqual(resolve({'port': port, 'debug': 'false'}, {}, {}, {})['port'], int(port))
'''),
    task("empty-page-pagination", "Fix app.collect_pages(fetch, start=None, max_pages=100). fetch(cursor) returns items and next_cursor. Concatenate items in order; only next_cursor=None ends traversal, even for empty pages. Reject repeated cursors before fetching again. A positive integer max_pages bounds fetch calls; cycles/limit/invalid limits raise ValueError. Do not mutate returned pages.",
         {"app.py": PAGINATION}, {"app.py": PAGINATION.replace("if cursor is None:", "if cursor is None or not page['items']:")},
         {"app.py": PAGINATION.replace("if cursor in seen:", "if False:")},
         '''
def test_two_pages(self):
    from app import collect_pages
    pages = {None: {'items': [1], 'next_cursor': 'a'}, 'a': {'items': [2], 'next_cursor': None}}
    self.assertEqual(collect_pages(pages.__getitem__), [1, 2])
''', '''
def test_empty_middle_page(self):
    from app import collect_pages
    pages = {None: {'items': [1], 'next_cursor': 'a'}, 'a': {'items': [], 'next_cursor': 'b'}, 'b': {'items': [2], 'next_cursor': None}}
    self.assertEqual(collect_pages(pages.__getitem__), [1, 2])
    self.assertEqual(pages['a']['items'], [])
def test_cycle_before_refetch(self):
    from app import collect_pages
    calls = []
    def fetch(cursor):
        calls.append(cursor)
        return {'items': [1], 'next_cursor': 'a'}
    with self.assertRaises(ValueError): collect_pages(fetch, start='a', max_pages=3)
    self.assertEqual(calls, ['a'])
def test_limit_and_bad_limits(self):
    from app import collect_pages
    calls = []
    def fetch(cursor):
        calls.append(cursor)
        return {'items': [1], 'next_cursor': 'next'}
    with self.assertRaises(ValueError): collect_pages(fetch, max_pages=1)
    self.assertEqual(len(calls), 1)
    for limit in (0, -1, True):
        with self.assertRaises(ValueError): collect_pages(fetch, max_pages=limit)
'''),
    task("atomic-state-batch", "Fix app.apply_batch(state, operations). Starting state has nonnegative integer balances. Each (key, delta) uses an integer delta, not bool; absent keys start at zero. Every intermediate balance must be nonnegative. On any invalid operation raise ValueError and leave state unchanged. On success update the same state object atomically and return None.",
         {"app.py": STATE}, {"app.py": STATE.replace("candidate = dict(state)", "candidate = state").replace("    state.clear()\n", "")},
         {"app.py": STATE.replace("if value < 0:", "if False:")},
         '''
def test_success(self):
    from app import apply_batch
    state = {'a': 10}
    self.assertIsNone(apply_batch(state, [('a', -2), ('b', 3)]))
    self.assertEqual(state, {'a': 8, 'b': 3})
''', '''
def test_rollback(self):
    from app import apply_batch
    state = {'a': 10}
    with self.assertRaises(ValueError): apply_batch(state, [('a', -2), ('b', -1)])
    self.assertEqual(state, {'a': 10})
def test_intermediate_negative_and_boolean(self):
    from app import apply_batch
    for operations in [[('a', -11), ('a', 11)], [('a', True)], [('a', 1.5)]]:
        state = {'a': 10}
        with self.assertRaises(ValueError): apply_batch(state, operations)
        self.assertEqual(state, {'a': 10})
'''),
    task("structured-log-selection", "Fix app.select_logs(lines, level, component). Parse JSON lines; ignore malformed JSON and non-object values. Match string level case-insensitively and component by exact equality, not substring. Return original matching lines in original order, preserving whitespace/newlines. Missing/nonstring level does not match.",
         {"app.py": LOGS}, {"app.py": LOGS.replace("row.get('component') == component", "component in str(row.get('component'))")},
         {"app.py": LOGS.replace("item_level.upper() == level.upper()", "item_level == level")},
         '''
def test_basic(self):
    from app import select_logs
    line = '{"level":"WARN","component":"api","message":"slow"}' + chr(10)
    self.assertEqual(select_logs([line], 'WARN', 'api'), [line])
''', '''
def test_exact_component_and_case(self):
    from app import select_logs
    good = '  {"level":"warn","component":"api","message":"slow"}  ' + chr(10)
    other = '{"level":"WARN","component":"api-admin"}' + chr(10)
    self.assertEqual(select_logs([other, good, other, good], 'WARN', 'api'), [good, good])
def test_ignore_malformed_and_nonobjects(self):
    from app import select_logs
    self.assertEqual(select_logs(['bad', 'null', '[]', '{"level":3,"component":"api"}', '{"component":"api"}'], 'WARN', 'api'), [])
'''),
    task("child-exit-propagation", "Fix this Rust command wrapper. First argument is the child executable; pass remaining arguments unchanged and inherit its stdout/stderr. Return its exit code. No executable or spawn failure returns 2; a terminated child without an exit code returns 1. Preserve success=0. Use only the Rust standard library.",
         {"main.rs": CLI}, {"main.rs": CLI.replace("Ok(status) => status.code().unwrap_or(1)", "Ok(_status) => 0")},
         {"main.rs": CLI.replace("Err(_) => 2", "Err(_) => 0")},
         '''
def test_success(self):
    exe = ROOT / 'target' / 'debug' / ('wrapper.exe' if sys.platform == 'win32' else 'wrapper')
    run = subprocess.run([str(exe), sys.executable, '-c', "print('ok')"], capture_output=True)
    self.assertEqual(run.returncode, 0)
    self.assertEqual(run.stdout.strip(), b'ok')
''', '''
def test_exit_and_streams(self):
    exe = ROOT / 'target' / 'debug' / ('wrapper.exe' if sys.platform == 'win32' else 'wrapper')
    run = subprocess.run([str(exe), sys.executable, '-c', "import sys;print('out');print('err',file=sys.stderr);sys.exit(7)"], capture_output=True)
    self.assertEqual(run.returncode, 7)
    self.assertEqual(run.stdout.strip(), b'out')
    self.assertEqual(run.stderr.strip(), b'err')
def test_spawn_error_and_no_args(self):
    exe = ROOT / 'target' / 'debug' / ('wrapper.exe' if sys.platform == 'win32' else 'wrapper')
    self.assertEqual(subprocess.run([str(exe)], capture_output=True).returncode, 2)
    self.assertEqual(subprocess.run([str(exe), str(ROOT / 'missing-command')], capture_output=True).returncode, 2)

def test_argument_preservation(self):
    exe = ROOT / 'target' / 'debug' / ('wrapper.exe' if sys.platform == 'win32' else 'wrapper')
    argument = 'two words "quoted"'
    run = subprocess.run([str(exe), sys.executable, '-c', "import sys;print(sys.argv[1])", argument], capture_output=True)
    self.assertEqual(run.returncode, 0)
    self.assertEqual(run.stdout.decode().strip(), argument)

@unittest.skipIf(sys.platform == 'win32', 'Unix native byte arguments only')
def test_non_utf8_argument_preservation(self):
    exe = ROOT / 'target' / 'debug' / 'wrapper'
    argument = bytes([255, 254])
    run = subprocess.run([os.fsencode(exe), os.fsencode(sys.executable), b'-c', b'import os,sys;sys.stdout.buffer.write(os.fsencode(sys.argv[1]))', argument], capture_output=True)
    self.assertEqual(run.returncode, 0)
    self.assertEqual(run.stdout, argument)
''', language="rust"),
    task("catalog-api-migration", "Update catalog.lookup(items, identifier, *, case_sensitive=False) and service.find_item. Items contain string id and value. Default matching uses Unicode casefold; explicit case_sensitive=True uses exact comparison. Return matching value or None; multiple matching items raise ValueError. service.find_item must use the new case-insensitive behavior. Preserve inputs.",
         {"catalog.py": CATALOG, "service.py": SERVICE},
         {"catalog.py": CATALOG.replace("case_sensitive=False", "case_sensitive=True"), "service.py": SERVICE.replace("case_sensitive=False", "case_sensitive=True")},
         {"catalog.py": CATALOG, "service.py": SERVICE.replace("case_sensitive=False", "case_sensitive=True")},
         '''
def test_lookup(self):
    from catalog import lookup
    from service import find_item
    items = [{'id': 'A', 'value': 3}]
    self.assertEqual(lookup(items, 'A'), 3)
    self.assertEqual(find_item(items, 'A'), 3)
''', '''
def test_unicode_and_service(self):
    from catalog import lookup
    from service import find_item
    items = [{'id': 'Straße', 'value': 3}]
    self.assertEqual(lookup(items, 'STRASSE'), 3)
    self.assertEqual(find_item(items, 'STRASSE'), 3)
    self.assertIsNone(lookup(items, 'STRASSE', case_sensitive=True))
    self.assertEqual(items, [{'id': 'Straße', 'value': 3}])
def test_ambiguity_and_absence(self):
    from catalog import lookup
    from service import find_item
    items = [{'id': 'A', 'value': 1}, {'id': 'a', 'value': 2}]
    for fn in (lookup, find_item):
        with self.assertRaises(ValueError): fn(items, 'a')
        self.assertIsNone(fn(items, 'missing'))
    self.assertEqual(lookup(items, 'a', case_sensitive=True), 2)
'''),
]
