import base64
import shlex
import subprocess
import unittest
from unittest.mock import Mock, patch

from thumbwork.agent_io import AdbTools, TextInputError, execute_action


class TextInputTests(unittest.TestCase):
    def setUp(self):
        self.adb = object.__new__(AdbTools)
        self.adb.adb_path = '/path with spaces/adb'
        self.adb.device = 'test-device'
        self.commands = []
        self.ime = 'com.android.adbkeyboard/.AdbIME'
        self.original = self.ime
        self.focus = 'mInputEditorInfo:\n  inputType=0x24001\nmStartedInputConnection=InputConnectionWrapper'
        self.responses = {}
        self.sleep = patch('thumbwork.agent_io.time.sleep').start()
        self.addCleanup(patch.stopall)
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
            return self.result(self.original)
        return self.result()

    def test_unicode_spaces_and_punctuation_are_transmitted_losslessly(self):
        text = "Li Auto 理想 'quoted' & $HOME; `id` %s\n第二行 😀"
        self.adb.type(text)
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
                with self.assertRaises((TextInputError, subprocess.TimeoutExpired, KeyboardInterrupt)):
                    self.adb.type('Li Auto')
                self.assertEqual(self.commands[-1], ['shell', 'ime', 'set', self.original])
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
        execute_action({'action': 'type', 'text': 'Li Auto'}, adb)
        adb.type.assert_called_once_with('Li Auto')
        adb._run.assert_not_called()
        execute_action({'action': 'system_button', 'button': 'Enter'}, adb)
        adb._run.assert_called_once_with('shell input keyevent 66')
