import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from PIL import Image
from thumbwork.cli import main
from thumbwork.models import ModelRegistry
from thumbwork.workspace import load_checkpoint
from test_llm_client import response_with, content_event
from test_smoke_runner import _response


def completion(text):
    return response_with(content_event(text)+'data: [DONE]\n\n')


class ArgumentErrorTests(unittest.TestCase):
    def invoke(self, args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), patch('thumbwork.cli.ModelRegistry') as registry:
            code = main(args)
        registry.assert_not_called()
        return code, out.getvalue(), err.getvalue()

    def test_command_errors_show_full_help_directly(self):
        cases = [
            ([], [], 'command'),
            (['unknown'], [], 'invalid choice'),
            (['--unknown'], [], 'command'),
            (['--config-dir'], [], 'command'),
            (['--config-dir', '/tmp/config', 'models', 'list'], [], 'invalid choice'),
            (['models'], ['models'], 'model_command'),
            (['config', 'path'], [], 'invalid choice'),
            (['models', 'show'], ['models', 'show'], 'name'),
            (['models', 'list', '--unknown'], [], '--unknown'),
            (['run', 'task.md', '--max-steps', 'many'], ['run'], 'invalid int value'),
            (['smoke', '--mode', 'invalid'], ['smoke'], 'invalid choice'),
        ]
        for prefix in (['smoke'], ['run', 'task.md']):
            help_args = prefix[:1]
            cases.append((prefix + ['--projects-dir'], help_args, 'expected one argument'))
        for args, help_args, reason in cases:
            with self.subTest(args=args):
                _, expected_help, _ = self.invoke(help_args + ['--help'])
                code, out, err = self.invoke(args)
                self.assertEqual(code, 2)
                self.assertEqual(out, '')
                self.assertEqual(err, expected_help)
                self.assertIn('usage:', err)
                self.assertIn('options:', err)
                self.assertNotIn('"status": "error"', err)
                self.assertNotIn('"hint"', err)

                code, out, err = self.invoke(['--json'] + args)
                result = json.loads(out)
                self.assertEqual(code, 2)
                self.assertEqual(result['status'], 'error')
                self.assertIn(reason, result['reason'])
                self.assertEqual(result['help'], expected_help)
                self.assertNotIn('hint', result)
                self.assertEqual(err, '')

    def test_projects_directory_is_only_available_for_new_task_output(self):
        for command in ([], ['models'], ['models', 'path'], ['models', 'list'], ['resume']):
            with self.subTest(command=command):
                _, help_text, _ = self.invoke(command + ['--help'])
                self.assertNotIn('--projects-dir', help_text)
                args = command + (['saved-run'] if command == ['resume'] else [])
                code, out, err = self.invoke(args + ['--projects-dir', '/tmp/output'])
                self.assertEqual(code, 2)
                self.assertEqual(out, '')
                self.assertIn('usage:', err)
        for command in ('run', 'smoke'):
            _, help_text, _ = self.invoke([command, '--help'])
            self.assertIn('--projects-dir PATH', help_text)

    def test_help_and_version_still_exit_successfully(self):
        for args in (['--help'], ['models', 'list', '--help'], ['models', 'path', '--help'], ['--version']):
            with self.subTest(args=args):
                code, out, err = self.invoke(args)
                self.assertEqual(code, 0)
                self.assertTrue(out.strip())
                self.assertEqual(err, '')
                self.assertNotIn('"status": "error"', out)
                if '--help' in args:
                    self.assertNotIn('--config-dir', out)


class TaskListingTests(unittest.TestCase):
    def invoke(self, args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), patch('thumbwork.cli.ModelRegistry') as registry, patch('thumbwork.runtime.run_command') as run:
            code = main(args)
        registry.assert_not_called()
        run.assert_not_called()
        self.assertEqual(err.getvalue(), '')
        self.assertEqual(code, 0)
        return out.getvalue()

    def test_run_lists_current_tasks_without_starting_a_run(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            for name in ('zebra', 'alpha', '中文任务'):
                prompt = root / name / (name + '.md')
                prompt.parent.mkdir(); prompt.write_text('Collect records')
            for path in ('old/runs/previous/input/task.md', '_smoke/_smoke.md', 'notes/README.md', 'loose.md'):
                candidate = root / path
                candidate.parent.mkdir(parents=True, exist_ok=True); candidate.write_text('Not a task')
            before = set(root.rglob('*'))
            with patch.dict(os.environ, {'THUMBWORK_PROJECTS_DIR': str(root)}):
                out = self.invoke(['run'])
                self.assertEqual(out, f'Available tasks in {root}:\n  alpha\n  zebra\n  中文任务\n\nRun a task: thumbwork run NAME\n')
                for args in (['run', '--json'], ['--json', 'run']):
                    data = json.loads(self.invoke(args))
                    self.assertEqual(data, {'projects_dir': str(root), 'tasks': [
                        {'name': name, 'prompt_path': str(root/name/(name+'.md'))}
                        for name in ('alpha', 'zebra', '中文任务')
                    ]})
            self.assertEqual(set(root.rglob('*')), before)

    def test_listing_uses_default_folder_and_explicit_override(self):
        with TemporaryDirectory() as temp:
            home = Path(temp).resolve()
            default = home / 'thumbwork_projects'
            override = home / 'alternate'
            for root, name in ((default, 'first'), (override, 'second')):
                prompt = root/name/(name+'.md')
                prompt.parent.mkdir(parents=True); prompt.write_text('Collect records')
            with patch.dict(os.environ, {'THUMBWORK_PROJECTS_DIR': ''}), patch('thumbwork.storage.Path.home', return_value=home):
                self.assertIn('  first\n', self.invoke(['run']))
            with patch.dict(os.environ, {'THUMBWORK_PROJECTS_DIR': str(default)}):
                data = json.loads(self.invoke(['run', '--projects-dir', str(override), '--json']))
                self.assertEqual(data['projects_dir'], str(override))
                self.assertEqual([task['name'] for task in data['tasks']], ['second'])

    def test_missing_or_empty_folder_has_clear_message_without_creating_it(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve() / 'missing'
            for exists in (False, True):
                if exists:
                    root.mkdir()
                args = ['run', '--projects-dir', str(root)]
                out = self.invoke(args)
                self.assertIn(f'No tasks found in {root}.', out)
                self.assertIn(f'{root}/NAME/NAME.md', out)
                self.assertEqual(json.loads(self.invoke(args + ['--json'])), {'projects_dir': str(root), 'tasks': []})
                self.assertEqual(root.exists(), exists)


class ModelPathTests(unittest.TestCase):
    def test_path_resolves_default_and_environment_without_creating_files(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            for override, expected in (('', root / '.thumbwork'), (str(root / 'custom config'), root / 'custom config')):
                for args in (['models', 'path'], ['--json', 'models', 'path'], ['models', '--json', 'path'], ['models', 'path', '--json']):
                    with self.subTest(override=override, args=args):
                        out, err = io.StringIO(), io.StringIO()
                        with patch.dict(os.environ, {'THUMBWORK_CONFIG_DIR': override}), patch('thumbwork.storage.Path.home', return_value=root), patch('thumbwork.cli.ModelRegistry') as registry, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                            code = main(args)
                        self.assertEqual(code, 0)
                        self.assertEqual(err.getvalue(), '')
                        if '--json' in args:
                            self.assertEqual(json.loads(out.getvalue()), {'config_dir': str(expected)})
                        else:
                            self.assertEqual(out.getvalue(), str(expected) + '\n')
                        registry.assert_not_called()
                        self.assertFalse(expected.exists())


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.config=self.root/'hidden';self.projects=self.root/'projects'
        self.prompt=self.projects/'collect'/'collect.md'
        self.prompt.parent.mkdir(parents=True);self.prompt.write_text('Collect one note then finish.')
        self.common=['--json']
        config_env=patch.dict(os.environ,{'THUMBWORK_CONFIG_DIR':str(self.config),'THUMBWORK_PROJECTS_DIR':str(self.projects)})
        config_env.start();self.addCleanup(config_env.stop)
        self.registry=ModelRegistry(self.config)
        self.registry.save('test',{'base_url':'https://example.invalid/v1','model_id':'test-model','api_key':'secret-test-key','context_window':32768})
        self.adb=Mock();self.adb.get_display_size.return_value=(100,200)
        def capture(path):Image.new('RGB',(100,200)).save(path);return True
        self.adb.get_screenshot.side_effect=capture
    def invoke(self, args):
        out=io.StringIO();err=io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
            code=main(self.common+args)
        self.assertNotIn('secret-test-key',out.getvalue()+err.getvalue())
        return code,json.loads(out.getvalue()),err.getvalue()
    def test_complete_agent_workflow_and_secret_redaction(self):
        extract=_response({'action':'extract','data':{'note_title':'One','author_name':'A','other':'secret-test-key'}})
        pause=_response({'action':'interact','text':'Please log in'})
        done=_response({'action':'terminate','status':'success'})
        with patch('thumbwork.llm_client.requests.post',side_effect=[completion(extract),completion(pause),completion(done)]),patch('thumbwork.runtime.AdbTools',return_value=self.adb):
            output_root=self.root/'custom-output'
            custom_prompt=output_root/'collect'/'collect.md'
            custom_prompt.parent.mkdir(parents=True);custom_prompt.write_text(self.prompt.read_text())
            code,first,_=self.invoke(['run','collect','--model','test','--debug','--projects-dir',str(output_root)])
            self.assertEqual(code,3);self.assertEqual(first['extraction_count'],1)
            self.assertTrue(Path(first['run_directory']).is_relative_to(output_root))
            self.assertFalse((self.prompt.parent/'runs').exists())
            run=Path(first['run_directory']);custom_prompt.unlink()
            self.adb.go_home_default_page.reset_mock()
            code,last,_=self.invoke(['resume',str(run)])
            self.assertEqual(code,0);self.assertEqual(last['step_count'],3)
            self.adb.go_home_default_page.assert_not_called()
        for path in run.rglob('*'):
            if path.is_file() and path.suffix in {'.json','.jsonl','.log'}:
                self.assertNotIn('secret-test-key',path.read_text())
        self.assertTrue((run/'debug/llm-tracer').is_dir())
        self.assertEqual(json.loads((run/'result.json').read_text()),last)
    def test_noninteractive_setup_uses_config_environment(self):
        metadata=Mock(status_code=200,ok=True)
        metadata.json.return_value={'data':[{'id':'vision','max_model_len':32768}]}
        with patch.dict(os.environ,{'TEST_THUMBWORK_KEY':'secret-test-key'}),patch('thumbwork.models.requests.get',return_value=metadata),patch('thumbwork.models.secrets.SystemRandom.sample',return_value=['red','blue']),patch('thumbwork.llm_client.requests.post',side_effect=[completion('red blue'),completion('blue red')]):
            code,result,_=self.invoke(['models','add','office','--base-url','https://example.invalid/v1','--model-id','vision','--api-key-env','TEST_THUMBWORK_KEY','--thinking','default'])
        self.assertEqual(code,0);self.assertEqual(result['context_source'],'server_reported')
        self.assertEqual(self.registry.get('office')['api_key'],'secret-test-key')
        self.assertFalse((self.prompt.parent/'runs').exists())
    def test_missing_model_and_arguments_return_json_error(self):
        code,result,_=self.invoke(['run',str(self.prompt),'--model','missing'])
        self.assertEqual(code,2);self.assertEqual(result['status'],'error')
        self.assertFalse((self.prompt.parent/'runs').exists())
        code,result,_=self.invoke(['run',str(self.prompt)])
        self.assertEqual(code,2);self.assertEqual(result['status'],'error')
    def test_step_limit_resume_requires_increased_total(self):
        with patch('thumbwork.llm_client.requests.post',return_value=completion('invalid response')),patch('thumbwork.runtime.AdbTools',return_value=self.adb):
            code,result,_=self.invoke(['run',str(self.prompt),'--model','test','--max-steps','1'])
        self.assertEqual(code,1)
        run=Path(result['run_directory'])
        code,_,_=self.invoke(['resume',str(run)])
        self.assertEqual(code,2)
        self.assertEqual(load_checkpoint(run)['status'],'incomplete')
        with patch('thumbwork.llm_client.requests.post',return_value=completion(_response({'action':'terminate','status':'success'}))),patch('thumbwork.runtime.AdbTools',return_value=self.adb):
            code,result,_=self.invoke(['resume',str(run),'--max-steps','2'])
        self.assertEqual(code,0);self.assertEqual(result['step_count'],2)

    def test_default_command_and_listing(self):
        code,result,_=self.invoke(['models','default'])
        self.assertEqual(code,0);self.assertIsNone(result['default_model'])
        code,result,_=self.invoke(['models','default','test'])
        self.assertEqual(code,0);self.assertEqual(result,{'default_model':'test'})
        code,result,_=self.invoke(['models','default'])
        self.assertEqual(code,0);self.assertEqual(result['default_model'],'test')
        code,result,_=self.invoke(['models','list'])
        self.assertEqual(code,0);self.assertEqual(result['default_model'],'test')
        self.assertIn('test',result['models'])
        code,result,_=self.invoke(['models','default','missing'])
        self.assertEqual(code,2);self.assertIn('Unknown model profile',result['reason'])
        self.assertEqual(self.registry.resolve_name(),'test')

    def test_no_default_fails_before_model_or_device_use(self):
        with patch('thumbwork.cli.smoke_suite') as smoke,patch('thumbwork.runtime.run_command') as run:
            for command in (['smoke'],['run',str(self.prompt)]):
                code,result,err=self.invoke(command)
                self.assertEqual(code,2)
                self.assertIn('models default NAME',result['reason'])
                self.assertNotIn('usage:',err)
            smoke.assert_not_called();run.assert_not_called()
        self.assertFalse((self.prompt.parent/'runs').exists())

    def test_smoke_uses_default_and_explicit_override_in_report(self):
        self.registry.save('other',dict(self.registry.get('test'),model_id='other-model'))
        self.registry.set_default('other')
        for extra,expected in (([],'other'),(['--model','test'],'test')):
            with self.subTest(expected=expected),patch('thumbwork.llm_client.requests.post',return_value=completion(_response({'action':'open','text':'com.sina.weibo'}))) as post:
                output_root=self.root/'custom-smoke-output'
                code,result,_=self.invoke(['smoke','--task','open_weibo','--projects-dir',str(output_root)]+extra)
                self.assertEqual(code,0);self.assertEqual(result['model'],expected)
                self.assertTrue(Path(result['run_directory']).is_relative_to(output_root))
                self.assertFalse((self.prompt.parent/'runs').exists())
                self.assertEqual(post.call_args.kwargs['json']['model'],self.registry.get(expected)['model_id'])
                self.assertEqual(json.loads((Path(result['run_directory'])/'result.json').read_text())['model'],expected)

    def test_default_run_resume_keeps_original_model_after_default_changes(self):
        self.registry.set_default('test')
        actions=[{'action':'interact','text':'Log in'},{'action':'terminate','status':'success'}]
        with patch('thumbwork.llm_client.requests.post',side_effect=[completion(_response(a)) for a in actions]) as post,patch('thumbwork.runtime.AdbTools',return_value=self.adb):
            code,result,_=self.invoke(['run',str(self.prompt)])
            self.assertEqual(code,3)
            run=Path(result['run_directory'])
            self.assertEqual(load_checkpoint(run)['model'],'test')
            self.registry.save('other',dict(self.registry.get('test'),model_id='other-model'))
            self.registry.set_default('other')
            code,result,_=self.invoke(['resume',str(run)])
            self.assertEqual(code,0)
            self.assertEqual(load_checkpoint(run)['model'],'test')
            self.assertTrue(all(call.kwargs['json']['model']=='test-model' for call in post.call_args_list))

    def test_installed_smoke_command_uses_named_profile_and_three_fixtures(self):
        profile=self.registry.get('test');profile['context_window']=131072
        self.registry.save('test',profile,replace=True)
        actions=[
            {'action':'open','text':'com.sina.weibo'},
            {'action':'extract','data':{'title':'Earth','description':'Third planet from the Sun'}},
            {'action':'system_button','button':'Back'},
            {'action':'system_button','button':'Back'},
            {'action':'click','coordinate':[40,80]},
            {'action':'interact','text':'Please check the phone'},
        ]
        with patch('thumbwork.llm_client.requests.post',side_effect=[completion(_response(a)) for a in actions]),patch('thumbwork.runtime.AdbTools') as adb:
            code,result,_=self.invoke(['smoke','--model','test','--task','all'])
        self.assertEqual(code,0);self.assertTrue(result['passed'])
        self.assertEqual(len(result['task_verdicts']),3)
        adb.assert_not_called()
        self.assertTrue((Path(result['run_directory'])/'result.json').exists())
        self.assertFalse((Path(result['run_directory'])/'debug').exists())

    def test_changed_endpoint_does_not_destroy_handoff_checkpoint(self):
        with patch('thumbwork.llm_client.requests.post',return_value=completion(_response({'action':'interact','text':'Log in'}))),patch('thumbwork.runtime.AdbTools',return_value=self.adb):
            _,result,_=self.invoke(['run',str(self.prompt),'--model','test'])
        run=Path(result['run_directory'])
        profile=self.registry.get('test');profile['model_id']='different'
        self.registry.save('test',profile,replace=True)
        code,error,_=self.invoke(['resume',str(run)])
        self.assertEqual(code,2);self.assertIn('changed',error['reason'])
        self.assertEqual(load_checkpoint(run)['status'],'needs_input')
