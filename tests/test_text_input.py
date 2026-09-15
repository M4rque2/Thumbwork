import base64
import shlex
import subprocess
import unittest
from unittest.mock import Mock, patch

from thumbwork.agent_io import AdbTools, TextInputError, TextInputResult, execute_action


class TextInputTests(unittest.TestCase):
    def setUp(self):
        self.adb = object.__new__(AdbTools)
        self.adb.adb_path = '/path with spaces/adb'
        self.adb.device = 'test-device'
        self.commands = []
        self.ime = 'com.android.adbkeyboard/.AdbIME'
        self.original = self.ime
        self.selected = None
        self.focus = 'mInputEditorInfo:\n  inputType=0x24001\nmStartedInputConnection=InputConnectionWrapper'
        self.responses = {}
        self.sleep = patch('thumbwork.agent_io.time.sleep').start()
        self.addCleanup(patch.stopall)
        self.print = patch('builtins.print').start()
        self.adb._run_args = Mock(side_effect=self.respond)

    @staticmethod
    def result(stdout='', returncode=0, stderr=''):
        return subprocess.CompletedProcess([], returncode, stdout, stderr)

    def respond(self, args):
        self.commands.append(args)
        override = self.responses.get(tuple(args))
        if isinstance(override, BaseException):
            raise override
        if override is not None:
            return override
        if args[1:3] == ['dumpsys', 'input_method']:
            return self.result(self.focus)
        if args[1:3] == ['ime', 'list']:
            return self.result(self.ime)
        if args[1:3] == ['settings', 'get']:
            return self.result(self.selected or self.original)
        if args[1:3] == ['ime', 'set']:
            self.selected = args[-1]
        if args == ['shell', 'input']:
            return self.result('Commands:\n  text <string>\n  keycombination <key codes>\n')
        if args == ['shell', 'cmd', 'clipboard', 'help']:
            return self.result('Clipboard commands:\n  set text TEXT\n  get\n')
        return self.result()

    def test_unicode_spaces_and_punctuation_are_transmitted_losslessly(self):
        text = "Li Auto 理想 'quoted' & $HOME; `id` %s\n第二行 😀"
        result = self.adb.type(text)
        self.assertEqual(result.backend, 'adb_keyboard')
        self.assertIn('not yet verified', result.feedback())
        broadcast = next(cmd for cmd in self.commands if 'ADB_INPUT_B64' in cmd)
        self.assertEqual(base64.b64decode(broadcast[-1]).decode('utf-8'), text)
        self.assertIn('com.android.adbkeyboard', broadcast)
        self.assertTrue(any('ADB_CLEAR_TEXT' in cmd for cmd in self.commands))
        self.assertFalse(any('66' in cmd or 'KEYCODE_ENTER' in cmd for cmd in self.commands))

    def test_empty_text_clears_without_input_broadcast(self):
        self.adb.type('')
        self.assertTrue(any('ADB_CLEAR_TEXT' in cmd for cmd in self.commands))
        self.assertFalse(any('ADB_INPUT_B64' in cmd for cmd in self.commands))

    def test_unfocused_editor_stops_before_mutation_despite_historical_focus(self):
        self.focus = 'StartInput #1:\n inputType=0x24001\nmInputEditorInfo:\n inputType=0x0\nmStartedInputConnection=null'
        with self.assertRaisesRegex(TextInputError, 'No editable text field'):
            self.adb.type('Li Auto')
        self.assertEqual(self.commands, [['shell', 'dumpsys', 'input_method']])

    def test_historical_unfocused_editor_does_not_block_current_focus(self):
        self.focus = 'StartInput #1:\n inputType=0x0\n' + self.focus
        self.adb.type('Li Auto')
        self.assertTrue(any('ADB_INPUT_B64' in cmd for cmd in self.commands))

    def test_keyboard_restored_on_broadcast_error_timeout_and_interrupt(self):
        self.original = 'original/.Keyboard'
        clear = ('shell', 'am', 'broadcast', '-p', 'com.android.adbkeyboard', '-a', 'ADB_CLEAR_TEXT')
        for failure in (self.result('SecurityException: denied'), subprocess.TimeoutExpired('adb', 30), KeyboardInterrupt()):
            with self.subTest(failure=failure):
                self.commands.clear()
                self.responses[clear] = failure
                with self.assertRaises((TextInputError, KeyboardInterrupt)):
                    self.adb.type('Li Auto')
                self.assertEqual(self.commands[-2], ['shell', 'ime', 'set', self.original])
                self.assertEqual(self.selected, self.original)
                self.assertFalse(any('ADB_INPUT_B64' in cmd for cmd in self.commands))

    def test_clipboard_must_match_before_paste(self):
        self.responses[('shell', 'cmd', 'clipboard', 'get')] = self.result('old clipboard\n')
        self.assertFalse(self.adb._type_with_clipboard('Li Auto'))
        self.assertFalse(any('KEYCODE_PASTE' in cmd or 'KEYCODE_DEL' in cmd for cmd in self.commands))

    def test_ascii_fallback_clears_and_reports_input_failure(self):
        self.adb._type_with_adb_keyboard = Mock(return_value=False)
        self.adb._type_with_clipboard = Mock(return_value=False)
        self.responses[('shell', 'input', 'text', 'Li%sAuto')] = self.result(returncode=1)
        with self.assertRaises(TextInputError), patch('builtins.print'):
            self.adb.type('Li Auto')
        self.assertEqual(self.commands[-3:], [
            ['shell', 'input', 'keycombination', 'KEYCODE_CTRL_LEFT', 'KEYCODE_A'],
            ['shell', 'input', 'keyevent', 'KEYCODE_DEL'],
            ['shell', 'input', 'text', 'Li%sAuto']])

    def test_unsupported_fallback_does_not_corrupt_or_clear_text(self):
        self.adb._type_with_adb_keyboard = Mock(return_value=False)
        self.adb._type_with_clipboard = Mock(return_value=False)
        for text in ('Li Auto 理想', 'café', 'literal%s', 'two\nlines'):
            with self.subTest(text=text), patch('builtins.print'):
                self.commands.clear()
                with self.assertRaisesRegex(TextInputError, 'Exact text input requires'):
                    self.adb.type(text)
                self.assertFalse(any('KEYCODE_DEL' in cmd for cmd in self.commands))

    def test_android_shell_receives_each_argument_intact_without_host_shell(self):
        text = "Li Auto '理想' & $HOME; `id`\nsecond line"
        args = ['shell', 'cmd', 'clipboard', 'set', 'text', text]
        with patch('thumbwork.agent_io.subprocess.run', return_value=self.result()) as run:
            AdbTools._run_args(self.adb, args)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[:4], [self.adb.adb_path, '-s', 'test-device', 'shell'])
        self.assertEqual(shlex.split(' '.join(cmd[4:])), args[1:])
        self.assertNotIn('shell', run.call_args.kwargs)

    def test_type_does_not_submit_but_explicit_enter_does(self):
        adb = Mock()
        adb.type.return_value = TextInputResult('adb_keyboard')
        result = execute_action({'action': 'type', 'text': 'Li Auto'}, adb)
        self.assertEqual(result.backend, 'adb_keyboard')
        adb.type.assert_called_once_with('Li Auto')
        adb._run.assert_not_called()
        execute_action({'action': 'system_button', 'button': 'Enter'}, adb)
        adb._run.assert_called_once_with('shell input keyevent 66')

    def test_android_10_fallback_stops_before_clipboard_or_field_mutation(self):
        self.ime = ''
        self.responses[('shell', 'cmd', 'clipboard', 'help')] = self.result(stderr='No shell command implementation.')
        self.responses[('shell', 'input')] = self.result('Commands:\n text <string>\n keyevent <key codes>\n')
        for _ in range(2):
            with self.assertRaisesRegex(TextInputError, 'select-all') as raised:
                self.adb.type('Li Auto')
            self.assertTrue(raised.exception.requires_human)
        self.assertEqual(self.commands.count(['shell', 'cmd', 'clipboard', 'help']), 1)
        self.assertEqual(self.commands.count(['shell', 'input']), 1)
        self.assertFalse(any('keyevent' in cmd or 'keycombination' in cmd or 'set' in cmd for cmd in self.commands))

    def test_clipboard_is_not_overwritten_when_replacement_is_unsupported(self):
        self.responses[('shell', 'input')] = self.result('Commands:\n text <string>\n')
        with self.assertRaises(TextInputError):
            self.adb._type_with_clipboard('理想L6')
        self.assertFalse(any('set' in cmd or 'keyevent' in cmd for cmd in self.commands))

    def test_unknown_clipboard_syntax_is_not_guessed(self):
        self.responses[('shell', 'cmd', 'clipboard', 'help')] = self.result('Commands:\n set CLIP\n get\n')
        self.assertFalse(self.adb._type_with_clipboard('理想L6'))
        self.assertEqual(self.commands, [['shell', 'cmd', 'clipboard', 'help']])

    def test_clipboard_diagnostic_words_and_trailing_newlines_remain_data(self):
        for text in ('Error: file not found', '理想L6\n'):
            for ending in ('', '\n'):
                with self.subTest(text=text, ending=ending):
                    self.responses[('shell', 'cmd', 'clipboard', 'get')] = self.result(text + ending)
                    result = self.adb._type_with_clipboard(text)
                    self.assertEqual(result.backend, 'clipboard')
        self.assertEqual(self.commands.count(['shell', 'cmd', 'clipboard', 'help']), 1)

    def test_keyboard_readiness_polls_for_selected_and_bound_method(self):
        self.adb._current_ime = Mock(side_effect=['old/.IME', self.ime, self.ime])
        self.adb._input_method_state = Mock(side_effect=[
            (True, ['old/.IME']), (True, ['old/.IME']), (True, [self.ime])])
        self.adb._wait_for_keyboard(self.ime)
        self.assertEqual(self.adb._current_ime.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)

    def test_unready_keyboard_never_clears_or_sends_text(self):
        self.focus += '\nmCurMethodId=other/.IME'
        with self.assertRaisesRegex(TextInputError, 'did not become ready'):
            self.adb.type('理想L6')
        self.assertFalse(any('broadcast' in cmd for cmd in self.commands))

    def test_restore_failure_is_reported_without_retyping_or_masking_error(self):
        self.original = 'original/.Keyboard'
        self.responses[('shell', 'ime', 'set', self.original)] = self.result(returncode=1)
        result = self.adb.type('理想L6')
        self.assertIn('restore', result.feedback())
        self.assertEqual(sum('ADB_INPUT_B64' in cmd for cmd in self.commands), 1)

    def test_timeout_requests_human_help_without_guessing_field_state(self):
        broadcast = ('shell', 'am', 'broadcast', '-p', 'com.android.adbkeyboard', '-a', 'ADB_INPUT_B64', '--es', 'msg', 'TGkgQXV0bw==')
        self.responses[broadcast] = subprocess.TimeoutExpired('adb', 30)
        with self.assertRaisesRegex(TextInputError, 'partially') as raised:
            self.adb.type('Li Auto')
        self.assertTrue(raised.exception.requires_human)
        self.assertFalse(any('clipboard' in cmd or 'keyevent' in cmd for cmd in self.commands))

    def test_nul_text_is_rejected_before_device_commands(self):
        with self.assertRaises(TextInputError):
            self.adb.type('Li\0Auto')
        self.assertEqual(self.commands, [])

    def test_disconnected_device_is_not_cached_as_unsupported_clipboard(self):
        command = ('shell', 'cmd', 'clipboard', 'help')
        self.responses[command] = self.result(returncode=1, stderr='error: device not found')
        with self.assertRaises(TextInputError) as raised:
            self.adb._supports_clipboard_commands()
        self.assertTrue(raised.exception.requires_human)
        del self.responses[command]
        self.assertTrue(self.adb._supports_clipboard_commands())
        self.assertEqual(self.commands.count(list(command)), 2)

    def test_restore_readback_error_does_not_mask_broadcast_error(self):
        self.adb._current_ime = Mock(side_effect=['original/.Keyboard', self.ime, TextInputError('readback failed')])
        clear = ('shell', 'am', 'broadcast', '-p', 'com.android.adbkeyboard', '-a', 'ADB_CLEAR_TEXT')
        self.responses[clear] = self.result(returncode=1)
        with self.assertRaisesRegex(TextInputError, 'Clear text failed'):
            self.adb.type('Li Auto')
        self.assertFalse(any('ADB_INPUT_B64' in cmd for cmd in self.commands))
