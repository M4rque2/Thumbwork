import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from PIL import Image
from thumbwork.runtime import run_loop
from thumbwork.workspace import create_run, load_checkpoint
from test_smoke_runner import _response, FakeVlm

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name).resolve(); prompt=root/'projects'/'collect'/'collect.md'
        prompt.parent.mkdir(parents=True);prompt.write_text('Collect two notes.')
        self.run=create_run(prompt,model='test',projects_dir=root/'projects',max_steps=6)
        self.adb=Mock()
        def screenshot(path): Image.new('RGB',(100,200)).save(path); return True
        self.adb.get_screenshot.side_effect=screenshot
        self.adb.get_display_size.return_value=(100,200)
    def execute(self, outputs, resume=False):
        with patch('thumbwork.runtime.time.sleep'), patch('builtins.print'):
            return run_loop(self.run,load_checkpoint(self.run),self.adb,FakeVlm(outputs),resume=resume)
    def test_extract_pause_and_resume_without_home_or_duplicate(self):
        first=self.execute([_response({'action':'extract','data':{'note_title':'One','author_name':'A'}}),_response({'action':'interact','text':'Please log in'})])
        self.assertEqual(first.exit_code,3); self.assertEqual(first.extraction_count,1)
        self.assertEqual(first.required_action,'Please log in')
        self.adb.reset_mock()
        second=self.execute([_response({'action':'terminate','status':'success'})],resume=True)
        self.assertEqual(second.exit_code,0); self.assertEqual(second.extraction_count,1)
        self.assertEqual(second.step_count,3)
        self.adb.go_home_default_page.assert_not_called()
        self.adb.get_screenshot.assert_called_once()
    def test_failure_and_budget_are_not_success(self):
        result=self.execute([_response({'action':'terminate','status':'failure'})])
        self.assertEqual(result.exit_code,1)
        state=load_checkpoint(self.run);state['next_step']=0;state['max_steps']=1
        with patch('builtins.print'):
            result=run_loop(self.run,state,self.adb,FakeVlm(['malformed']))
        self.assertEqual(result.status,'incomplete')
    def test_screenshot_failure_and_interrupt_preserve_result(self):
        self.adb.get_screenshot.side_effect=KeyboardInterrupt()
        result=self.execute([])
        self.assertEqual(result.exit_code,130)
        self.assertTrue((self.run/'result.json').is_file())
        self.adb.get_screenshot.side_effect=None;self.adb.get_screenshot.return_value=False
        result=self.execute([])
        self.assertEqual(result.exit_code,2)
    def test_no_debug_traces_by_default(self):
        self.execute([_response({'action':'terminate','status':'success'})])
        self.assertFalse((self.run/'debug').exists())

    def test_overflow_retry_compacts_once_and_resume_retains_memory(self):
        from thumbwork.llm_client import ContextOverflowError, LLMResult
        from thumbwork.storage import atomic_json
        (self.run/'input/system.md').write_text('You control the phone.')
        old=self.run/'state/screen-old.png';Image.new('RGB',(100,200)).save(old)
        state=load_checkpoint(self.run)
        state['history']=[{'image':'state/screen-old.png','output':'Collected note A. '+'x'*1000,'previous_expectation':'A visible'}]
        state['previous_expectation']='B visible'
        state['next_step']=1
        atomic_json(self.run/'checkpoint.json',state)
        class OverflowModel:
            def __init__(self):self.actions=0;self.summaries=0;self.messages=[]
            def invoke(self,messages,**kwargs):
                self.messages.append(messages)
                if kwargs:
                    self.summaries+=1
                    return LLMResult('Collected note A. Pending note B.')
                self.actions+=1
                if self.actions==1:raise ContextOverflowError('context too large')
                return LLMResult(_response({'action':'interact','text':'Log in'}))
        model=OverflowModel()
        with patch('builtins.print'),patch('thumbwork.runtime.execute_action') as execute:
            result=run_loop(self.run,state,self.adb,model,context_window=8000)
        self.assertEqual(result.status,'needs_input');self.assertEqual(model.actions,2)
        self.assertEqual(model.summaries,1);execute.assert_not_called()
        checkpoint=load_checkpoint(self.run)
        self.assertIn('note A',checkpoint['summary'])
        self.assertFalse(old.exists())
        self.adb.go_home_default_page.reset_mock()
        fake=FakeVlm([_response({'action':'terminate','status':'success'})])
        with patch('builtins.print'):
            result=run_loop(self.run,checkpoint,self.adb,fake,resume=True,context_window=8000)
        self.assertEqual(result.status,'success')
        self.assertIn('note A',str(fake.messages[0]))
        self.adb.go_home_default_page.assert_not_called()

    def test_repeated_overflow_stops_without_action(self):
        from thumbwork.llm_client import ContextOverflowError, LLMResult
        (self.run/'input/system.md').write_text('Control phone.')
        old=self.run/'state/screen-old.png';Image.new('RGB',(10,10)).save(old)
        state=load_checkpoint(self.run)
        state['history']=[{'image':'state/screen-old.png','output':'x'*1000}]
        class AlwaysOverflow:
            calls=0
            def invoke(self,messages,**kwargs):
                if kwargs:return LLMResult('Old work summarized.')
                self.calls+=1
                raise ContextOverflowError('too large')
        model=AlwaysOverflow()
        with patch('builtins.print'),patch('thumbwork.runtime.execute_action') as execute:
            result=run_loop(self.run,state,self.adb,model,context_window=8000)
        self.assertEqual(result.status,'error');self.assertEqual(model.calls,2)
        execute.assert_not_called()

    def test_adb_action_errors_propagate_without_host_shell(self):
        from thumbwork.agent_io import AdbTools
        adb=object.__new__(AdbTools);adb.adb_path='/path with spaces/adb';adb.device=None
        result=Mock(returncode=1,stderr='device offline',stdout='')
        with patch('thumbwork.agent_io.subprocess.run',return_value=result) as run:
            with self.assertRaisesRegex(RuntimeError,'device offline'):adb.click(10,20)
        self.assertEqual(run.call_args.args[0],['/path with spaces/adb','shell','input','tap','10','20'])
        self.assertNotIn('shell',run.call_args.kwargs)

    def test_typing_feedback_uses_fresh_screenshot_and_allows_recovery(self):
        from thumbwork.agent_io import TextInputError
        self.adb.type.side_effect = [TextInputError('No editable text field is focused'), None]
        model = FakeVlm([
            _response({'action':'type', 'text':'Li Auto'}),
            _response({'action':'click', 'coordinate':[250, 68]}),
            _response({'action':'type', 'text':'Li Auto'}),
            _response({'action':'interact', 'text':'Check the field'})])
        with patch('thumbwork.runtime.time.sleep'), patch('builtins.print'):
            result = run_loop(self.run, load_checkpoint(self.run), self.adb, model)
        self.assertEqual(result.status, 'needs_input')
        self.assertEqual(self.adb.type.call_count, 2)
        self.adb.click.assert_called_once()
        self.assertEqual(self.adb.get_screenshot.call_count, 4)
        self.assertIn('No editable text field is focused', str(model.messages[1]))
        self.assertIn('Enter was not pressed', str(model.messages[3]))
        self.adb._run.assert_not_called()

    def test_unsupported_text_input_pauses_and_can_resume_after_setup(self):
        from thumbwork.agent_io import TextInputError, TextInputResult
        self.adb.type.side_effect = TextInputError('Enable ADB Keyboard, then resume.', requires_human=True)
        first = self.execute([_response({'action':'type', 'text':'理想L6'})])
        self.assertEqual(first.status, 'needs_input')
        self.assertEqual(first.required_action, 'Enable ADB Keyboard, then resume.')
        self.assertEqual(first.step_count, 1)
        self.adb.type.side_effect = None
        self.adb.type.return_value = TextInputResult('adb_keyboard')
        model = FakeVlm([_response({'action':'type', 'text':'理想L6'}),
                         _response({'action':'terminate', 'status':'success'})])
        with patch('thumbwork.runtime.time.sleep'), patch('builtins.print'):
            result = run_loop(self.run, load_checkpoint(self.run), self.adb, model, resume=True)
        self.assertEqual(result.status, 'success')
        self.assertIn('via adb_keyboard', str(model.messages[1]))
        self.assertIn('not yet verified', str(model.messages[1]))
        self.assertEqual(self.adb.get_screenshot.call_count, 3)

    def test_open_settings_searches_system_apps(self):
        from thumbwork.agent_io import handle_open_action
        adb=Mock()
        adb.get_package_name.side_effect=lambda all_packages=False: ['com.android.settings'] if all_packages else []
        self.assertTrue(handle_open_action({'action':'open','text':'Settings'},adb))
        adb.get_package_name.assert_called_once_with(all_packages=True)
        adb.open_app.assert_called_once_with('com.android.settings')

    def test_generic_extraction_history_matches_saved_data(self):
        import json
        from thumbwork.agent_io import parse_turn_response
        data={'app':'Settings','visible_settings':['WLAN','Display','Sound'],'screen_verified':True}
        model=FakeVlm([_response({'action':'extract','data':data}),_response({'action':'terminate','status':'success'})])
        with patch('builtins.print'):
            result=run_loop(self.run,load_checkpoint(self.run),self.adb,model)
        self.assertEqual(result.status,'success')
        self.assertEqual(result.extraction_count,1)
        historical=[m for m in model.messages[1] if m['role']=='assistant'][0]
        remembered=parse_turn_response(historical['content'][0]['text'])['tool_call']['arguments']['data']
        saved=json.loads((self.run/'output.jsonl').read_text())['data']
        self.assertEqual(remembered,saved)
        self.assertEqual(saved,data)

    def test_note_comments_and_replies_survive_pause_and_resume(self):
        import json
        from thumbwork.context_manager import build_collection_memory
        common = {'task': 'xhs_note_crawler', 'app': 'XHS',
                  'search_keyword': '理想i9', 'note_key': 'note_0001'}
        note = {**common, 'record_type': 'note',
                'note_detail': {'note_title': '理想i9体验', 'author_name': '作者'}}
        comments = {**common, 'record_type': 'comment_batch', 'comments': [
            {'comment_key': 'comment_0001', 'parent_comment_key': None,
             'author_name': '读者', 'comment_text': '期待试驾', 'is_reply': False}]}
        replies = {**common, 'record_type': 'comment_batch', 'comments': [
            {'comment_key': 'comment_0002', 'parent_comment_key': 'comment_0001',
             'author_name': '作者', 'comment_text': '我也是', 'is_reply': True}]}
        complete = {**common, 'record_type': 'note_complete',
                    'comments_status': 'complete', 'comments_saved': 2,
                    'end_evidence': '没有更多了', 'limitations': []}
        first = self.execute([
            _response({'action': 'extract', 'data': note}),
            _response({'action': 'extract', 'data': comments}),
            _response({'action': 'interact', 'text': 'Complete login to load replies'})])
        self.assertEqual(first.status, 'needs_input')
        output = self.run / 'output.jsonl'
        self.assertEqual([json.loads(line)['data'] for line in output.read_text().splitlines()],
                         [note, comments])
        second = self.execute([
            _response({'action': 'extract', 'data': replies}),
            _response({'action': 'extract', 'data': complete}),
            _response({'action': 'terminate', 'status': 'success'})], resume=True)
        self.assertEqual(second.status, 'success')
        self.assertEqual(second.extraction_count, 4)
        self.assertEqual([json.loads(line)['data'] for line in output.read_text().splitlines()],
                         [note, comments, replies, complete])
        memory = build_collection_memory(str(output))
        self.assertIn('Collected items so far: 1.', memory)
        self.assertIn('理想i9体验 - 作者', memory)
        self.assertNotIn('(missing title)', memory)
        self.assertIn('finish the task\'s pending content, comments, and replies', memory)

    def test_note_extraction_history_preserves_actual_schema(self):
        from thumbwork.agent_io import parse_turn_response,format_turn_response
        for data in ({'note_title':'A','author_name':'B','note_text':'Full text'},
                     {'notes':[{'note_title':'A','likes':42}], 'source':'feed'},
                     {'note_detail':{'note_title':'A'},'notes_collected':1}):
            with self.subTest(data=data):
                parsed=parse_turn_response(_response({'action':'extract','data':data}))
                roundtrip=parse_turn_response(format_turn_response(parsed))
                self.assertEqual(roundtrip['tool_call']['arguments']['data'],data)

    def test_duplicate_comment_feedback_and_aliases_survive_resume(self):
        import json
        batch = {'record_type': 'comment_batch', 'note_key': 'n1', 'comments': [
            {'comment_key': 'c1', 'author_name': 'A',
             'comment_text': 'A distinctive comment about this vehicle.',
             'publish_time': '昨天', 'location': '北京'}]}
        first = self.execute([_response({'action': 'extract', 'data': batch}),
                              _response({'action': 'interact', 'text': 'Pause'})])
        self.assertEqual(first.status, 'needs_input')
        changed = {**batch, 'comments': [{**batch['comments'][0], 'comment_key': 'new-id'}]}
        model = FakeVlm([_response({'action': 'extract', 'data': changed}),
                        _response({'action': 'terminate', 'status': 'success'})])
        with patch('builtins.print'):
            result = run_loop(self.run, load_checkpoint(self.run), self.adb, model, resume=True)
        self.assertEqual(result.extraction_count, 1)
        saved = json.loads((self.run / 'output.jsonl').read_text())['data']['comments']
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]['comment_key'], 'c1')
        self.assertEqual(saved[0]['source_comment_keys'], ['new-id'])
        self.assertIn('skipped 1 duplicates', str(model.messages[1]))
        self.assertIn('Canonical keys', str(model.messages[1]))

    def test_localized_settings_alias(self):
        from thumbwork.app_name_to_package import resolve_package_ids
        self.assertEqual(resolve_package_ids('设置'),['com.android.settings'])
        self.assertEqual(resolve_package_ids('設定'),['com.android.settings'])
