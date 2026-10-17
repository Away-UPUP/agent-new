"""Real launcher/stop processes, isolated fake report processor, no model API calls."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from scripts.batch_runner import configuration, identity

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT/'scripts/batch_runner.py'

FAKE_PIPELINE = '''import json,os,subprocess,sys,time
from pathlib import Path
def process(source,root,output,work,concurrency):
    source=Path(source);root=Path(root);output=Path(output);work=Path(work)
    assert concurrency==1
    work.mkdir(parents=True,exist_ok=True)
    checkpoint=work/(source.stem+'.checkpoint');checkpoint.write_text('preserved')
    dest=output/(source.stem+'.docx')
    if dest.exists():return {'status':'succeeded','output':str(dest),'resumed':True}
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(300)'],start_new_session=True)
    (work/(source.stem+'.child')).write_text(str(child.pid))
    try:
        while not (root/'release').exists():time.sleep(.05)
        if source.stem=='fail':raise ValueError('controlled report failure')
        dest.write_text('fixture report')
        return {'status':'succeeded','output':str(dest),'resumed':False}
    finally:
        child.terminate();child.wait()
'''


class BatchRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='batch-runner-test-')
        self.root = Path(self.tmp.name)
        (self.root/'输入 文件').mkdir()
        (self.root/'bridge_agent').mkdir()
        (self.root/'bridge_agent/__init__.py').write_text('')
        (self.root/'bridge_agent/pipeline.py').write_text(FAKE_PIPELINE)
        (self.root/'.env').write_text('INSPECTION_API_BASE_URL=fixture\nINSPECTION_API_KEY=fixture\nINSPECTION_MODEL=fixture\nBATCH_INPUT_DIR="输入 文件"\nBATCH_OUTPUT_DIR="输出 文件"\nBATCH_WORK_DIR="work/检查点"\nBATCH_WORKERS=4\nBATCH_REQUEST_CONCURRENCY=1\n')
        self.children = []

    def command(self, command, *args):
        return subprocess.run([sys.executable, str(RUNNER), command, '--root', str(self.root), *args], cwd='/tmp', capture_output=True, text=True, timeout=25)

    def wait_state(self, predicate):
        deadline = time.monotonic()+15
        while time.monotonic()<deadline:
            p=self.root/'work/batch_runner/active.json'
            if p.exists():
                state=json.loads(p.read_text())
                if predicate(state):return state
            time.sleep(.05)
        self.fail('Batch state deadline exceeded')

    def tearDown(self):
        try:
            self.command('stop')
        finally:
            for p in (self.root/'work/检查点').glob('*.child'):
                try:os.kill(int(p.read_text()),signal.SIGKILL)
                except ProcessLookupError:pass
            for child in self.children:
                if child.poll() is None:child.terminate()
                child.wait()
            self.tmp.cleanup()

    def test_four_workers_duplicate_stop_descendants_and_resume(self):
        for i in range(6):(self.root/'输入 文件'/f'桥 {i}.docx').write_text('fixture')
        (self.root/'输入 文件/~$临时.docx').write_text('ignore')
        check=self.command('start','--dry-run');self.assertEqual(check.returncode,0,check.stderr)
        self.assertEqual(len(json.loads(check.stdout)['files']),6)
        start=self.command('start');self.assertEqual(start.returncode,0,start.stderr)
        state=self.wait_state(lambda s:sum(x['status']=='running' for x in s['reports'])==4)
        deadline=time.monotonic()+10
        while len(list((self.root/'work/检查点').glob('*.child')))<4 and time.monotonic()<deadline:time.sleep(.05)
        self.assertEqual(len(list((self.root/'work/检查点').glob('*.child'))),4)
        workers=[x['process']['pid'] for x in state['reports'] if x['status']=='running']
        descendants=[int(p.read_text()) for p in (self.root/'work/检查点').glob('*.child')]
        unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(300)']);self.children.append(unrelated)
        duplicate=self.command('start');self.assertNotEqual(duplicate.returncode,0)
        self.assertIn('already running',duplicate.stderr)
        status=self.command('start','--status');self.assertIn('running 4',status.stdout)
        stopped=self.command('stop');self.assertEqual(stopped.returncode,0,stopped.stderr)
        state=self.wait_state(lambda s:s['status']=='stopped')
        self.assertEqual(sum(x['status']=='interrupted' for x in state['reports']),4)
        self.assertTrue(all(identity(pid) is None for pid in workers+descendants))
        self.assertIsNone(unrelated.poll())
        self.assertEqual(len(list((self.root/'work/检查点').glob('*.checkpoint'))),4)
        self.assertEqual(self.command('stop').returncode,0)
        (self.root/'release').touch()
        self.assertEqual(self.command('start').returncode,0)
        self.wait_state(lambda s:s['status']=='succeeded')
        self.assertEqual(len(list((self.root/'输出 文件').glob('*.docx'))),6)
        # A new batch reuses the existing processor's compatible completed state.
        self.assertEqual(self.command('start').returncode,0)
        state=self.wait_state(lambda s:s['status']=='succeeded')
        self.assertTrue(all(x['resumed'] for x in state['reports']))
        self.assertEqual(len(list((self.root/'work/batch_runner').glob('*/batch.log'))),3)

    def test_failure_is_recorded_and_other_reports_finish(self):
        for name in ['fail.doc','good.docx']:(self.root/'输入 文件'/name).write_text('fixture')
        (self.root/'release').touch()
        self.assertEqual(self.command('start').returncode,0)
        state=self.wait_state(lambda s:s['status']=='failed')
        self.assertEqual(sorted(x['status'] for x in state['reports']),['failed','succeeded'])
        self.assertIn('controlled report failure',next(x['error'] for x in state['reports'] if x['status']=='failed'))
        self.assertTrue((self.root/'输出 文件/good.docx').exists())

    def test_duplicate_names_and_input_output_nesting_are_rejected(self):
        (self.root/'输入 文件/a.doc').touch();(self.root/'输入 文件/a.docx').touch()
        with self.assertRaisesRegex(ValueError,'Duplicate'):configuration(self.root)
        (self.root/'输入 文件/a.docx').unlink()
        with (self.root/'.env').open('a') as f:f.write('BATCH_OUTPUT_DIR="输入 文件/输出"\n')
        with self.assertRaisesRegex(ValueError,'outside'):configuration(self.root)

    def test_stale_or_unrelated_pid_is_never_signalled(self):
        unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(300)']);self.children.append(unrelated)
        target=identity(unrelated.pid);stale={**target,'start_ticks':'0'}
        control=self.root/'work/batch_runner';control.mkdir(parents=True)
        state={'status':'running','controller':stale,'reports':[{'process':target,'status':'running'}],
               'log':str(control/'old/batch.log'),'config':{'output':str(self.root/'输出 文件')}}
        (control/'active.json').write_text(json.dumps(state))
        self.assertEqual(self.command('stop').returncode,0)
        self.assertIsNone(unrelated.poll())
        stopped=json.loads((control/'active.json').read_text())
        self.assertEqual(stopped['status'],'stopped');self.assertEqual(stopped['reports'][0]['status'],'interrupted')


if __name__=='__main__':unittest.main()
