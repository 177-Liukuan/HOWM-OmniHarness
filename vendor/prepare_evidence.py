#!/usr/bin/env python3
"""Prepare deterministic, traceable contact sheets from JSON-referenced media.

No predictions, labels, or unreferenced state videos are read. Each video is
decoded once; the first decoded frame at or after 5%, 50%, and 95% of its JSON
duration is retained. Actual presentation timestamps are saved in the manifest.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

from PIL import Image, ImageDraw, ImageFont, ImageOps


VERSION = 1
ROOT = Path('/storage1/HOWM-LAB-Project')
DEFAULT_DATA = ROOT / 'datasets/HomeHWM/extracted/HomeHWM_preliminary_questions_5000_schema_3_1'
DEFAULT_OUTPUT = ROOT / 'outputs/codex-gpt6-factor-agent-100-20260929/evidence'
FRACTIONS = (0.05, 0.50, 0.95)
FRAME_SIZE = (320, 180)
STATE_SIZE = (640, 360)
PAGE_NAMES = [f'options_{n:02d}_{n + 9:02d}.jpg' for n in (1, 11, 21)]
IMAGE_NAMES = ['states.jpg', *PAGE_NAMES]
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
CONFIG = {
    'version': VERSION,
    'fractions': list(FRACTIONS),
    'selection': 'first decoded frame at or after requested relative timestamp',
    'candidate_frame_size': list(FRAME_SIZE),
    'state_frame_size': list(STATE_SIZE),
    'jpeg_quality': 93,
    'resize': 'preserve aspect ratio; black padding',
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(path: Path) -> dict:
    stat = path.stat()
    if not path.is_file() or stat.st_size <= 0:
        raise ValueError(f'Missing or empty input: {path}')
    return {'path': str(path), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def referenced_file(question_dir: Path, relative: str) -> Path:
    path = (question_dir / relative).resolve()
    if not path.is_relative_to(question_dir.resolve()):
        raise ValueError(f'Media path escapes question directory: {relative}')
    return path


def atomic_json(path: Path, content: dict) -> None:
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    temporary.write_text(json.dumps(content, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def save_image(path: Path, image: Image.Image) -> dict:
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    image.save(temporary, format='JPEG', quality=CONFIG['jpeg_quality'], subsampling=0)
    temporary.replace(path)
    return {'file': path.name, 'width': image.width, 'height': image.height,
            'size': path.stat().st_size, 'sha256': sha256(path)}


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT, size)


def make_states(question: dict, question_dir: Path) -> Image.Image:
    sheet = Image.new('RGB', (1280, 400), (245, 245, 245))
    draw = ImageDraw.Draw(sheet)
    for index, (key, label) in enumerate((('init', 'INITIAL'), ('final', 'FINAL'))):
        with Image.open(referenced_file(question_dir, question[key])) as source:
            tile = ImageOps.pad(ImageOps.exif_transpose(source).convert('RGB'), STATE_SIZE,
                                method=Image.Resampling.LANCZOS, color=(0, 0, 0))
        x = index * STATE_SIZE[0]
        draw.text((x + 12, 7), f'Question {question["question_id"]} | {label}',
                  font=font(22), fill=(0, 0, 0))
        sheet.paste(tile, (x, 40))
    return sheet


def extract_frames(path: Path, duration: float) -> tuple[list[Image.Image], list[float], list[float]]:
    if duration <= 0:
        raise ValueError(f'Invalid duration for {path}: {duration}')
    requested = [round(duration * fraction, 6) for fraction in FRACTIONS]
    selection = '+'.join(f'gte(t,{timestamp:.6f})*eq(selected_n,{index})'
                         for index, timestamp in enumerate(requested))
    filters = (
        f"setpts=PTS-STARTPTS,select='{selection}',"
        'scale=320:180:force_original_aspect_ratio=decrease:flags=lanczos,'
        'pad=320:180:(ow-iw)/2:(oh-ih)/2:black,showinfo'
    )
    command = [
        'ffmpeg', '-hide_banner', '-nostdin', '-loglevel', 'info',
        '-threads', '1', '-filter_threads', '1', '-filter_complex_threads', '1',
        '-i', str(path), '-an', '-sn', '-dn', '-vf', filters,
        '-vsync', '0', '-frames:v', '3', '-threads', '1',
        '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1',
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    stderr = result.stderr.decode('utf-8', errors='replace')
    frame_bytes = FRAME_SIZE[0] * FRAME_SIZE[1] * 3
    if result.returncode or len(result.stdout) != frame_bytes * 3:
        raise RuntimeError(f'Frame extraction failed for {path}: returncode={result.returncode}, '
                           f'bytes={len(result.stdout)}, expected={frame_bytes * 3}\n{stderr[-5000:]}')
    timestamps = [float(value) for value in re.findall(r'\bn:\s*\d+\s+pts:.*?\bpts_time:([\d.eE+-]+)', stderr)]
    if len(timestamps) != 3:
        raise RuntimeError(f'Expected three actual timestamps for {path}, got {timestamps}')
    frames = [Image.frombytes('RGB', FRAME_SIZE, result.stdout[i * frame_bytes:(i + 1) * frame_bytes])
              for i in range(3)]
    return frames, requested, timestamps


def valid_existing(output: Path, source_files: list[dict], question_hash: str) -> bool:
    try:
        record = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
        if (record.get('complete') is not True or record.get('config') != CONFIG
                or record.get('source_files') != source_files
                or record.get('question_sha256') != question_hash
                or len(record.get('options', [])) != 30):
            return False
        images = record['images']
        if [entry['file'] for entry in images] != IMAGE_NAMES:
            return False
        for entry in images:
            image_path = output / entry['file']
            if image_path.stat().st_size != entry['size'] or sha256(image_path) != entry['sha256']:
                return False
            with Image.open(image_path) as image:
                if image.size != (entry['width'], entry['height']):
                    return False
                image.verify()
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def prepare_question(number: int, data: Path, base_output: Path, tool_version: str) -> dict:
    started = time.monotonic()
    question_dir = data / f'{number:03d}'
    question_path = question_dir / 'question.json'
    question = json.loads(question_path.read_text(encoding='utf-8'))
    if question['question_id'] != f'{number:03d}':
        raise ValueError(f'Unexpected question_id in {question_path}')
    options = question['options']
    if len(options) != 30 or [str(option['option_id']) for option in options] != list(map(str, range(1, 31))):
        raise ValueError(f'Expected ordered option IDs 1..30 in {question_path}')
    paths = [question_path, *(referenced_file(question_dir, question[key]) for key in ('init', 'final')),
             *(referenced_file(question_dir, option['video']) for option in options)]
    sources = [fingerprint(path) for path in paths]
    question_hash = sha256(question_path)
    output = base_output / question['question_id']
    if valid_existing(output, sources, question_hash):
        return {'question_id': question['question_id'], 'status': 'skipped_verified'}
    output.mkdir(parents=True, exist_ok=True)
    images = [save_image(output / 'states.jpg', make_states(question, question_dir))]
    option_records = []
    for offset, page_name in zip((0, 10, 20), PAGE_NAMES):
        sheet = Image.new('RGB', (960, 2090), (245, 245, 245))
        draw = ImageDraw.Draw(sheet)
        draw.text((8, 7), f'Question {question["question_id"]} | Options {offset + 1:02d}-{offset + 10:02d} | Early / Middle / Late',
                  font=font(19), fill=(0, 0, 0))
        for row, option in enumerate(options[offset:offset + 10]):
            path = referenced_file(question_dir, option['video'])
            frames, requested, actual = extract_frames(path, float(option['duration_seconds']))
            y = 40 + row * 205
            for column, (frame, timestamp) in enumerate(zip(frames, actual)):
                x = column * FRAME_SIZE[0]
                draw.text((x + 5, y), f'Option {option["option_id"]} | t={timestamp:.3f}s',
                          font=font(17), fill=(0, 0, 0))
                sheet.paste(frame, (x, y + 22))
            option_records.append({
                'option_id': str(option['option_id']), 'video': option['video'],
                'source_path': str(path), 'source_sha256': sha256(path),
                'duration_seconds_json': option['duration_seconds'],
                'requested_timestamps_seconds': requested,
                'actual_timestamps_seconds': actual,
                'page': page_name, 'row': row + 1,
            })
        images.append(save_image(output / page_name, sheet))
    manifest = {
        'complete': True, 'question_id': question['question_id'],
        'instruction': question['instruction'], 'question_path': str(question_path),
        'question_sha256': question_hash, 'config': CONFIG,
        'ffmpeg_version': tool_version, 'pillow_version': Image.__version__,
        'source_files': sources, 'state_files': {
            key: {'path': str(referenced_file(question_dir, question[key])),
                  'sha256': sha256(referenced_file(question_dir, question[key]))}
            for key in ('init', 'final')
        },
        'options': option_records, 'images': images,
    }
    atomic_json(output / 'manifest.json', manifest)
    if not valid_existing(output, sources, question_hash):
        raise RuntimeError(f'Post-write evidence validation failed: {output}')
    return {'question_id': question['question_id'], 'status': 'prepared',
            'seconds': round(time.monotonic() - started, 2),
            'images': len(images), 'candidate_frames': 90}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', type=int, default=1)
    parser.add_argument('--count', type=int, default=100)
    parser.add_argument('--data', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()
    if args.start < 1 or args.count < 1 or not 1 <= args.workers <= 8:
        parser.error('start/count must be positive; workers must be in 1..8')
    version = subprocess.check_output(['ffmpeg', '-version'], text=True).splitlines()[0]
    started = time.monotonic()
    statuses = []
    failures = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(prepare_question, number, args.data.resolve(), args.output.resolve(), version): number
                   for number in range(args.start, args.start + args.count)}
        for future in as_completed(futures):
            number = futures[future]
            try:
                result = future.result()
                statuses.append(result)
            except Exception as error:
                result = {'question_id': f'{number:03d}', 'status': 'failed', 'error': str(error)}
                failures.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    print(json.dumps({'summary': True, 'requested': args.count, 'complete': len(statuses),
                      'failed': len(failures), 'seconds': round(time.monotonic() - started, 2)},
                     ensure_ascii=False), flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
