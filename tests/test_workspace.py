import os
import json
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from PIL import Image
from thumbwork.storage import config_root, projects_root
from thumbwork.workspace import create_run, load_checkpoint, file_lock
from thumbwork.context_manager import load_task_prompt_arg

class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve(); self.projects=self.root/'projects'
        self.prompt=self.projects/'collect'/'collect.md'
        self.prompt.parent.mkdir(parents=True); Image.new('RGB',(12,12)).save(self.prompt.parent/'ref.png')
        self.prompt.write_text('Find this: ![ref](ref.png)')
    def create(self, **kw):
        return create_run(self.prompt, model='test', projects_dir=self.projects, **kw)
    def test_separate_roots_and_precedence(self):
        with patch.dict(os.environ, {'THUMBWORK_CONFIG_DIR':str(self.root/'hidden'), 'THUMBWORK_PROJECTS_DIR':str(self.projects)}):
            self.assertEqual(config_root(), self.root/'hidden')
            self.assertEqual(projects_root(), self.projects)
            self.assertEqual(config_root(self.root/'override'), self.root/'override')
            self.assertEqual(projects_root(self.root/'override2'), self.root/'override2')
    def test_snapshot_is_independent_and_each_run_unique(self):
        run=self.create(); other=self.create()
        self.assertNotEqual(run,other)
        self.prompt.unlink(); (self.prompt.parent/'ref.png').unlink()
        parts=load_task_prompt_arg('',str(run/'input/task.md'))
        self.assertTrue(any('image' in p for p in parts))
        self.assertFalse((run/'debug').exists())
        self.assertEqual(load_checkpoint(run)['next_step'],0)
        self.assertFalse((self.projects/'config.json').exists())
        state=load_checkpoint(run)
        started=datetime.fromisoformat(state['started_at'])
        self.assertEqual(state['task_name'],'collect')
        self.assertTrue(run.name.startswith(started.strftime('%Y-%m-%d_%H-%M-%S_%f%z')))
    def test_name_collision_and_debug(self):
        run=self.create(debug=True); self.assertTrue((run/'debug').is_dir())
        other=self.projects/'other'/'collect.md'; other.parent.mkdir(); other.write_text('Another task')
        with self.assertRaisesRegex(ValueError,'Duplicate task prompt name'):
            self.create()
        other.rename(other.with_name('other.md'))
        create_run('other',model='test',projects_dir=self.projects)
    def test_same_run_lock_is_exclusive(self):
        path=self.root/'lock'
        with file_lock(path):
            with self.assertRaises(RuntimeError):
                with file_lock(path): pass
        with file_lock(path): pass
    def test_invalid_prompt_does_not_create_run(self):
        self.prompt.write_text('![missing](missing.png)')
        with self.assertRaises(ValueError): self.create()
        self.assertFalse((self.prompt.parent/'runs').exists())

    def test_prompt_filename_with_spaces_preserves_task_name(self):
        prompt=self.projects/'My collection'/'My collection.md';prompt.parent.mkdir();prompt.write_text('Collect records')
        run=create_run(prompt,model='test',projects_dir=self.projects)
        self.assertEqual(run.parent.parent.name,'My collection')
        with self.assertRaises(ValueError):
            create_run('../outside',model='test',projects_dir=self.projects)

    def test_task_name_and_filename_resolve_to_same_task(self):
        runs=[create_run(value,model='test',projects_dir=self.projects) for value in ('collect','collect.md',self.prompt)]
        self.assertTrue(all(run.parent==self.prompt.parent/'runs' for run in runs))
        self.assertEqual(len(set(runs)),3)
        self.assertEqual(json.loads((self.prompt.parent/'task.json').read_text())['source_path'],str(self.prompt))

    def test_duplicate_names_ignore_case_but_exclude_run_snapshots(self):
        archive=self.prompt.parent/'runs'/'old'/'input'/'collect.md'
        archive.parent.mkdir(parents=True);archive.write_text('Old prompt')
        self.create()
        for folder in ('other', '.hidden'):
            duplicate=self.projects/folder/'COLLECT.MD'
            duplicate.parent.mkdir();duplicate.write_text('Conflicting prompt')
            with self.assertRaisesRegex(ValueError,'Duplicate task prompt name'):
                self.create()
            duplicate.unlink()

    def test_external_and_misplaced_prompts_are_rejected(self):
        for prompt in (self.root/'outside.md',self.projects/'wrong.md'):
            prompt.write_text('Collect records')
            with self.assertRaisesRegex(ValueError,'Move the task prompt'):
                create_run(prompt,model='test',projects_dir=self.projects)
        self.assertFalse((self.projects/'outside').exists())
        self.assertFalse((self.projects/'wrong').exists())

    def test_missing_task_does_not_create_folder(self):
        with self.assertRaisesRegex(ValueError,'Task prompt not found'):
            create_run('missing',model='test',projects_dir=self.projects)
        self.assertFalse((self.projects/'missing').exists())

    def test_terminal_prompt_snapshots_images_and_creates_unique_runs(self):
        with patch('thumbwork.workspace.Path.cwd', return_value=self.prompt.parent):
            runs = [create_run(prompt_text=self.prompt.read_text(), model='test', projects_dir=self.projects) for _ in range(2)]
        self.assertNotEqual(*runs)
        (self.prompt.parent/'ref.png').unlink()
        for run in runs:
            self.assertEqual(run.parent, self.projects/'runs')
            self.assertEqual(load_checkpoint(run)['task_name'], 'inline')
            parts = load_task_prompt_arg('', str(run/'input/task.md'))
            self.assertTrue(any('image' in part for part in parts))

    def test_invalid_terminal_prompt_does_not_create_output(self):
        root = self.root/'terminal-output'
        for text in ('', ' \n ', '![missing](missing.png)'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                create_run(prompt_text=text, model='test', projects_dir=root)
            self.assertFalse(root.exists())
        with self.assertRaisesRegex(ValueError, 'either'):
            create_run(self.prompt, prompt_text='Open Google', model='test', projects_dir=root)
        self.assertFalse(root.exists())

    def test_malformed_checkpoint_fails_clearly(self):
        import json
        run=self.create()
        path=run/'checkpoint.json';state=json.loads(path.read_text())
        state['max_steps']=None;path.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError,'Invalid checkpoint'):load_checkpoint(run)
