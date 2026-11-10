import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description='Verify incremental CLI output against its atomic JSON export.')
    parser.add_argument('input', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--start', type=float, default=0)
    parser.add_argument('--duration', type=float, default=65)
    parser.add_argument('--language')
    parser.add_argument('--bun', default='bun')
    parser.add_argument('--timeout', type=float, default=300)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source_hashes = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ("src/cli.ts", "src/transcriber.ts", "src/process.ts", "src/format.ts", "python/worker.py", "model-pins.json")
    }
    args.out.mkdir(parents=True, exist_ok=True)
    output = args.out / 'transcript.json'
    command = [args.bun, 'src/cli.ts', 'run', str(args.input.resolve()), '--start', str(args.start),
               '--duration', str(args.duration), '--timeout', str(args.timeout), '--stream',
               '--format', 'json', '--output', str(output.resolve())]
    if args.language:
        command += ['--language', args.language]
    trace = []
    segments = []
    complete = None
    first_while_alive = False
    start = time.monotonic()
    with (args.out / 'stderr.log').open('w') as stderr:
        child = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=stderr, text=True, start_new_session=True)
        try:
            for line in child.stdout:
                record = json.loads(line)
                elapsed = time.monotonic() - start
                trace.append({'elapsed_seconds': elapsed, 'record': record})
                if complete is not None:
                    raise AssertionError('received data after completion')
                if record['type'] == 'segment':
                    if not segments:
                        first_while_alive = child.poll() is None
                        assert not output.exists(), 'export published before the first streamed segment'
                    segments.append(record['segment'])
                elif record['type'] == 'complete':
                    complete = record
                else:
                    raise AssertionError(f'unexpected public record: {record}')
            code = child.wait()
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
            child.stdout.close()
            (args.out / 'trace.json').write_text(json.dumps(trace, ensure_ascii=False, indent=2) + '\n')
    assert code == 0, f'CLI failed with exit {code}; see stderr.log'
    assert complete is not None, 'missing completion record'
    assert segments, 'choose an excerpt containing speech'
    assert first_while_alive, 'no segment observed before process exit'
    exported = json.loads(output.read_text())
    assert exported['segments'] == segments, 'stream and exported segments differ'
    assert complete['count'] == len(segments), 'incorrect terminal segment count'
    assert complete['transcript'] == {key: value for key, value in exported.items() if key != 'segments'}
    first = next(entry['elapsed_seconds'] for entry in trace if entry['record']['type'] == 'segment')
    final = trace[-1]['elapsed_seconds']
    assert first < final, 'no segment arrived before completion'
    report = {'exit_code': code, 'segment_count': len(segments), 'first_segment_seconds': first,
              'completion_seconds': final, 'observed_before_exit': first_while_alive,
              'stream_matches_export': True, 'source_sha256': source_hashes}
    (args.out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
