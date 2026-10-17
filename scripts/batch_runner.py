"""Detached, bounded report workers with persisted progress and a verified stop target."""
import argparse
from collections import Counter
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid

from dotenv import dotenv_values

SCRIPT = Path(__file__).resolve()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def identity(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':
            return None
        return {'pid': pid, 'start_ticks': fields[19],
                'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
    except (OSError, IndexError):
        return None


def alive(target):
    return bool(target) and identity(target['pid']) == target


def verified(target, mode):
    if not alive(target):
        return False
    try:
        args = Path(f"/proc/{target['pid']}/cmdline").read_bytes().split(b'\0')
        return str(SCRIPT).encode() in args and mode.encode() in args
    except OSError:
        return False


def terminate_workers(targets):
    # Include converters that create their own session, as well as worker process groups.
    tracked = {t['pid']: t for t in targets if verified(t, 'worker')}
    for pid in tracked:
        try:
            os.kill(pid, signal.SIGSTOP)
        except ProcessLookupError:
            pass
    changed = True
    while changed:
        changed = False
        for p in Path('/proc').iterdir():
            if not p.name.isdigit() or int(p.name) in tracked:
                continue
            try:
                parent = int((p/'stat').read_text().rsplit(')', 1)[1].split()[1])
                target = identity(int(p.name))
                if parent in tracked and target:
                    os.kill(target['pid'], signal.SIGSTOP)
                    tracked[target['pid']] = target
                    changed = True
            except (OSError, ValueError, IndexError):
                continue
    for target in tracked.values():
        if alive(target):
            try:
                os.kill(target['pid'], signal.SIGTERM)
                os.kill(target['pid'], signal.SIGCONT)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(alive(t) for t in tracked.values()):
        time.sleep(0.1)
    for target in tracked.values():
        if alive(target):
            try:
                os.kill(target['pid'], signal.SIGKILL)
            except ProcessLookupError:
                pass


def configuration(root):
    values = {**dotenv_values(root/'.env'), **os.environ}
    def directory(key, default):
        p = Path(values.get(key) or default).expanduser()
        return (root/p).resolve() if not p.is_absolute() else p.resolve()
    config = {'root': str(root), 'input': str(directory('BATCH_INPUT_DIR', '阶段1测试集')),
              'output': str(directory('BATCH_OUTPUT_DIR', 'result/阶段1')),
              'work': str(directory('BATCH_WORK_DIR', 'work/阶段1')),
              'workers': int(values.get('BATCH_WORKERS', '4')),
              'request_concurrency': int(values.get('BATCH_REQUEST_CONCURRENCY', '1'))}
    if not 1 <= config['workers'] <= 4 or config['request_concurrency'] not in (1, 2, 4):
        raise ValueError('BATCH_WORKERS must be 1..4; BATCH_REQUEST_CONCURRENCY must be 1, 2 or 4')
    for key in ['INSPECTION_API_BASE_URL', 'INSPECTION_API_KEY', 'INSPECTION_MODEL']:
        if not values.get(key):
            raise ValueError('Missing model configuration: ' + key)
    source = Path(config['input'])
    if not source.is_dir():
        raise ValueError('Input directory does not exist: ' + str(source))
    if any(Path(config[k]).is_relative_to(source) for k in ['output', 'work']):
        raise ValueError('Output/work directories must be outside the input directory')
    files = sorted(p for p in source.rglob('*') if p.is_file() and
                   p.suffix.lower() in ('.doc', '.docx') and not p.name.startswith('~$'))
    if not files:
        raise ValueError('No Word files found: ' + str(source))
    stems = Counter(p.stem.casefold() for p in files)
    if any(n > 1 for n in stems.values()):
        raise ValueError('Duplicate report stems would overwrite output; rename duplicates first')
    config['files'] = [str(p) for p in files]
    return config


def summary(state):
    counts = Counter(x['status'] for x in state['reports'])
    print(f"批次状态：{state['status']}；总数 {len(state['reports'])}；" +
          '；'.join(f'{k} {v}' for k, v in sorted(counts.items())), flush=True)
    print('输出目录：' + state['config']['output'])
    print('日志：' + state['log'])
    for item in state['reports']:
        if item['status'] in ('running', 'failed'):
            print(item['status'] + '：' + Path(item['source']).name + ' ' + item.get('error', ''))


def worker(root, source, output, work, concurrency, result):
    sys.path.insert(0, str(root))
    from bridge_agent.pipeline import process
    try:
        data = process(source, root, output, work, concurrency)
        save(result, data)
        return 0
    except Exception as exc:
        save(result, {'source': source, 'status': 'failed', 'error': str(exc)})
        print('FAILED ' + Path(source).name + ': ' + str(exc), flush=True)
        return 1


def run(root, request):
    state = read(request)
    control = root/'work/batch_runner'
    with (control/'run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another batch controller is already running')
        stopped = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
        signal.signal(signal.SIGINT, lambda *_: stopped.set())
        state.update(status='running', controller=identity(os.getpid()))
        config = state['config']
        active = {}
        def persist():
            state['updated_at'] = time.time()
            save(request.parent/'status.json', state)
            save(control/'active.json', state)
            save(Path(config['output'])/'batch_manifest.json', state['reports'])
        persist()
        print('START ' + str(len(state['reports'])) + ' reports; workers=' + str(config['workers']) +
              '; requests_per_report=' + str(config['request_concurrency']), flush=True)
        try:
            while True:
                for pid, (proc, item, result) in list(active.items()):
                    if proc.poll() is None:
                        continue
                    data = read(result) if result.exists() else {'status': 'failed', 'error': 'Worker exited without a result'}
                    item.update(data)
                    item['source'] = config['files'][item['index']]
                    item['returncode'] = proc.returncode
                    if proc.returncode and item['status'] == 'succeeded':
                        item.update(status='failed', error='Worker exited with an error after saving a result')
                    print(item['status'].upper() + ' ' + Path(item['source']).name, flush=True)
                    del active[pid]
                    persist()
                if stopped.is_set():
                    break
                pending = [x for x in state['reports'] if x['status'] == 'pending']
                for item in pending[:max(0, config['workers']-len(active))]:
                    if stopped.is_set():
                        break
                    result = request.parent/f"{item['index']:04d}.json"
                    cmd = [sys.executable, '-u', str(SCRIPT), 'worker', '--root', str(root),
                           '--source', item['source'], '--output', config['output'], '--work', config['work'],
                           '--concurrency', str(config['request_concurrency']), '--result', str(result)]
                    proc = subprocess.Popen(cmd, cwd=root, start_new_session=True)
                    item.update(status='running', process=identity(proc.pid))
                    active[proc.pid] = (proc, item, result)
                    print('RUNNING ' + Path(item['source']).name, flush=True)
                    persist()
                if not active and not any(x['status'] == 'pending' for x in state['reports']):
                    break
                stopped.wait(0.25)
        finally:
            terminate_workers([item['process'] for _, item, _ in active.values()])
            for proc, item, result in active.values():
                proc.wait()
                if result.exists():
                    item.update(read(result))
                else:
                    item['status'] = 'interrupted'
            state['status'] = 'stopped' if stopped.is_set() else ('failed' if any(x['status'] != 'succeeded' for x in state['reports']) else 'succeeded')
            persist()
            summary(state)
        return 0 if state['status'] == 'succeeded' else 1


def main():
    p = argparse.ArgumentParser(description='阶段测试集后台批处理：启动、状态、停止；读取项目 .env')
    p.add_argument('command', choices=['start', 'stop', 'run', 'worker'])
    p.add_argument('--root', type=Path, default=SCRIPT.parents[1])
    p.add_argument('--status', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--request', type=Path)
    for name in ['source', 'output', 'work', 'result']:
        p.add_argument('--'+name)
    p.add_argument('--concurrency', type=int)
    args = p.parse_args()
    root = args.root.resolve()
    if args.command == 'worker':
        return worker(root, args.source, args.output, args.work, args.concurrency, args.result)
    if args.command == 'run':
        return run(root, args.request)
    control = root/'work/batch_runner'
    current = control/'active.json'
    if args.status:
        if current.exists():
            state = read(current)
            if state['status'] == 'running' and not verified(state.get('controller'), 'run'):
                state['status'] = 'controller_missing'
            summary(state)
        else:
            print('尚未启动批次。')
        return 0
    if args.command == 'stop':
        if not current.exists():
            print('没有正在运行的批次。')
            return 0
        state = read(current)
        if verified(state.get('controller'), 'run'):
            os.kill(state['controller']['pid'], signal.SIGTERM)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and verified(state['controller'], 'run'):
                time.sleep(0.1)
            if verified(state['controller'], 'run'):
                raise RuntimeError('Stop is taking longer than expected; inspect ' + state['log'])
        else:
            terminate_workers([x.get('process') for x in state['reports'] if x.get('process')])
            if state['status'] in ('starting', 'running'):
                for item in state['reports']:
                    if item['status'] == 'running':
                        item['status'] = 'interrupted'
                state['status'] = 'stopped'
                save(current, state)
                save(Path(state['log']).parent/'status.json', state)
                save(Path(state['config']['output'])/'batch_manifest.json', state['reports'])
        print('批次已停止；保留已生成报告、分块检查点和日志，再次启动可复用兼容进度。')
        return 0
    config = configuration(root)
    if args.dry_run:
        print(json.dumps(config, ensure_ascii=False, indent=2))
        return 0
    control.mkdir(parents=True, exist_ok=True)
    with (control/'launch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if current.exists():
            old = read(current)
            if verified(old.get('controller'), 'run') or any(verified(x.get('process'), 'worker') for x in old['reports']):
                raise RuntimeError('A batch is already running; use bash run_batch.sh --status or bash stop_batch.sh')
        batch = control/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
        batch.mkdir()
        Path(config['output']).mkdir(parents=True, exist_ok=True)
        request = batch/'request.json'
        state = {'status': 'starting', 'config': config, 'log': str(batch/'batch.log'),
                 'reports': [{'index': i, 'source': s, 'status': 'pending'} for i, s in enumerate(config['files'])]}
        save(request, state)
        with (batch/'batch.log').open('ab') as log:
            proc = subprocess.Popen([sys.executable, '-u', str(SCRIPT), 'run', '--root', str(root), '--request', str(request)], cwd=root, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if current.exists() and read(current).get('controller', {}).get('pid') == proc.pid:
                summary(read(current))
                print('后台已启动。查看进度：bash run_batch.sh --status；停止：bash stop_batch.sh')
                return 0
            if proc.poll() is not None:
                raise RuntimeError('Controller startup failed; inspect ' + state['log'])
            time.sleep(0.05)
        raise RuntimeError('Controller startup did not confirm; inspect ' + state['log'])


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
