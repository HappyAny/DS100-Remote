"""Compose the recorded R11 application, click overlays, narration and subtitles."""
# SPDX-License-Identifier: GPL-3.0-only

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import wave

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
W, H, FPS = 1920, 1080, 30
FONT = ROOT / 'assets/fonts/NotoSansCJKsc-Regular.otf'
BG, TEXT, MUTED, GREEN, ORANGE = '#101418', '#e8edf2', '#9aa8b4', '#79dfb5', '#ffb465'


def font(size):
    return ImageFont.truetype(str(FONT), size)


def wrap(text, size, width):
    result, line = [], ''
    f = font(size)
    for char in text:
        if f.getlength(line + char) > width and line:
            result.append(line)
            line = char
        else:
            line += char
    return result + ([line] if line else [])


def text(draw, xy, value, size, color=TEXT):
    draw.text(xy, value, font=font(size), fill=color)


def stamp(seconds, vtt=False):
    ms = round(seconds * 1000)
    hours, ms = divmod(ms, 3600000)
    minutes, ms = divmod(ms, 60000)
    seconds, ms = divmod(ms, 1000)
    return f'{hours:02}:{minutes:02}:{seconds:02}' + ('.' if vtt else ',') + f'{ms:03}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/public-release')
    args = parser.parse_args()
    args.capture = args.capture.resolve()
    if not args.capture.is_relative_to(ROOT):
        parser.error('Use a project capture directory.')
    output = args.output.resolve()
    if not output.is_relative_to(ROOT):
        parser.error('Use a project output directory.')
    output.mkdir(parents=True, exist_ok=True)
    plan = json.loads((ROOT / 'media/tutorial-plan.json').read_text(encoding='utf-8'))
    captured = json.loads((args.capture / 'receipt.json').read_text(encoding='utf-8'))
    if not captured['success'] or not captured['physical_device'] or not captured['settings_restored']:
        raise ValueError('The physical recording did not pass its checks.')
    live = {s['id']: s for s in captured['scenes']}
    audio_dir = ROOT / 'build/tutorial-audio'
    chunks = json.loads((audio_dir / 'chunks.json').read_text(encoding='utf-8-sig'))
    work = ROOT / 'build/tutorial-render'
    work.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which('ffmpeg') or 'ffmpeg'
    cues, scene_reports, parts = [], [], []
    elapsed = 0.0
    for number, scene in enumerate(plan['scenes'], 1):
        name = scene['id']
        audio = []
        local_cues = []
        position = 0.8
        params = None
        for chunk in [c for c in chunks['chunks'] if c['scene'] == name]:
            with wave.open(str(audio_dir / chunk['file']), 'rb') as source:
                current = (source.getnchannels(), source.getsampwidth(), source.getframerate())
                if params is not None and current != params:
                    raise ValueError('The narration audio formats differ.')
                params = current
                if not audio:
                    audio.append(b'\0' * round(position * params[2]) * params[0] * params[1])
                duration = source.getnframes() / source.getframerate()
                audio.append(source.readframes(source.getnframes()))
                local_cues.append((position, position + duration, chunk['text']))
                position += duration + .2
                audio.append(b'\0' * round(.2 * params[2]) * params[0] * params[1])
        duration = max(position + .6, live[name]['seconds'] if scene['kind'] == 'live' else scene['seconds'])
        duration = math.ceil(duration * FPS) / FPS
        audio_path = work / (name + '.wav')
        with wave.open(str(audio_path), 'wb') as target:
            target.setnchannels(params[0]); target.setsampwidth(params[1]); target.setframerate(params[2])
            target.writeframes(b''.join(audio))
        for start, end, words in local_cues:
            cues.append((elapsed + start, elapsed + end, words))
        background = Image.new('RGB', (W, H), BG)
        draw = ImageDraw.Draw(background)
        text(draw, (64, 25), 'DS100 REMOTE  /  R11 + ' + captured['client'], 24, GREEN)
        text(draw, (64, 65), scene['title'], 46)
        text(draw, (1570, 30), f'{number:02} / {len(plan["scenes"]):02}', 25, MUTED)
        if scene['kind'] == 'warning':
            draw.rounded_rectangle((60, 138, 1860, 925), radius=28, fill='#241c17', outline=ORANGE, width=3)
            draw.polygon([(960, 215), (885, 350), (1035, 350)], fill=ORANGE)
            text(draw, (944, 247), '!', 66, BG)
            text(draw, (780, 382), 'Attention', 76, ORANGE)
            for index, line in enumerate(scene['lines']):
                f = font(62)
                text(draw, ((W - f.getlength(line)) / 2, 520 + index * 110), line, 62)
            text(draw, (460, 790), '第三方实验固件 · 请先确认硬件版本并备份文件', 36, MUTED)
        elif scene['kind'] == 'card':
            draw.rounded_rectangle((60, 156, 1860, 920), radius=20, fill='#171d23', outline='#2c3641', width=2)
            for index, line in enumerate(scene['lines']):
                text(draw, (112, 240 + index * 114), f'{index + 1:02}', 30, GREEN)
                text(draw, (195, 229 + index * 114), line, 38)
            text(draw, (114, 848), 'github.com/HappyAny/DS100-Remote', 30, MUTED)
        else:
            draw.rounded_rectangle((40, 145, 1295, 946), radius=12, fill='#090d10', outline='#2c3641', width=2)
            draw.rounded_rectangle((1330, 145, 1860, 946), radius=15, fill='#171d23', outline='#2c3641', width=2)
            text(draw, (1360, 172), '真实设备 · USB', 32, GREEN)
            y = 240
            for line in scene['lines']:
                for row in wrap(line, 29, 465):
                    text(draw, (1360, y), row, 29)
                    y += 42
                y += 18
            text(draw, (1360, 500), '鼠标点击 / 按住提示', 28, MUTED)
            text(draw, (1360, 882), '原速操作 · 刷新跟随硬件', 24, MUTED)
        text(draw, (66, 1042), plan['voice_disclosure'], 19, MUTED)
        text(draw, (1370, 1042), 'GPL-3.0 · 项目代码与补丁', 19, MUTED)
        base = cv2.cvtColor(np.asarray(background), cv2.COLOR_RGB2BGR)
        cap = cv2.VideoCapture(str(args.capture / (name + '.mp4'))) if scene['kind'] == 'live' else None
        events = live[name]['events'] if cap is not None else []
        event_index, last_event, last_down, pressed = 0, None, -100.0, False
        cursor = (25, 20)
        part = work / (f'{number:02}-{name}.mp4')
        parts.append(part)
        log = (work / (name + '-ffmpeg.log')).open('wb')
        encoder = subprocess.Popen([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'rawvideo',
            '-pix_fmt', 'bgr24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', 'pipe:0', '-i', str(audio_path),
            '-map', '0:v', '-map', '1:a', '-af', 'apad', '-t', str(duration), '-c:v', 'libx264',
            '-preset', 'veryfast', '-threads', '4', '-crf', '21', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-b:a', '160k', '-movflags', '+faststart', str(part)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
        log.close()
        subtitle_cache = {}
        last_frame = None
        try:
            for frame_number in range(round(duration * FPS)):
                t = frame_number / FPS
                canvas = base.copy()
                if cap is not None:
                    ok, source = cap.read()
                    if ok:
                        last_frame = source
                    if last_frame is None:
                        raise ValueError('The captured video has no frames: ' + name)
                    image = last_frame.copy()
                    while event_index < len(events) and events[event_index]['time'] <= t:
                        event = events[event_index]
                        cursor = (round(event['x']), round(event['y']))
                        if event['kind'] != 'move':
                            last_event = event
                        if event['kind'] == 'down':
                            last_down, pressed = event['time'], True
                        elif event['kind'] == 'up':
                            pressed = False
                        event_index += 1
                    x, y = cursor
                    age = t - last_down
                    if pressed or 0 <= age < .7:
                        radius = 17 if pressed else round(17 + 32 * age / .7)
                        cv2.circle(image, (x, y), radius, (90, 185, 255), 3, cv2.LINE_AA)
                    polygon = np.array([(x, y), (x + 1, y + 28), (x + 8, y + 21), (x + 15, y + 33),
                                        (x + 20, y + 30), (x + 13, y + 18), (x + 24, y + 17)], np.int32)
                    cv2.fillPoly(image, [polygon], (248, 248, 248), cv2.LINE_AA)
                    cv2.polylines(image, [polygon], True, (12, 16, 20), 2, cv2.LINE_AA)
                    resized = cv2.resize(image, (1066, 800), interpolation=cv2.INTER_NEAREST)
                    canvas[145:945, 134:1200] = resized
                    left = max(0, min(image.shape[1] - 250, x - 125))
                    top = max(0, min(image.shape[0] - 150, y - 75))
                    zoom = cv2.resize(image[top:top + 150, left:left + 250], (460, 276), interpolation=cv2.INTER_NEAREST)
                    canvas[580:856, 1360:1820] = zoom
                    if last_event is not None:
                        label = last_event['label']
                        for old, new in [('time_up', '时基 ＋'), ('time_down', '时基 −'), ('range_up', '量程 ＋'), ('range_down', '量程 −')]:
                            label = label.replace(old, new)
                        p = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
                        text(ImageDraw.Draw(p), (1360, 540), label, 23, GREEN)
                        if not ok:
                            text(ImageDraw.Draw(p), (1360, 912), '片段结束 · 定格讲解', 18, MUTED)
                        canvas = cv2.cvtColor(np.asarray(p), cv2.COLOR_RGB2BGR)
                words = next((words for start, end, words in local_cues if start <= t < end), '')
                if words:
                    if words not in subtitle_cache:
                        tile = Image.new('RGB', (1790, 88), BG)
                        d = ImageDraw.Draw(tile)
                        rows = wrap(words, 31, 1740)
                        for index, row in enumerate(rows):
                            d.text(((1790 - font(31).getlength(row)) / 2, index * 41), row, font=font(31), fill=TEXT)
                        subtitle_cache[words] = cv2.cvtColor(np.asarray(tile), cv2.COLOR_RGB2BGR)
                    canvas[956:1044, 65:1855] = subtitle_cache[words]
                total_progress = (number - 1 + t / duration) / len(plan['scenes'])
                canvas[1073:1080, :round(W * total_progress)] = (181, 223, 121)
                encoder.stdin.write(canvas.tobytes())
                if name == 'menu' and frame_number == 150:
                    cv2.imwrite(str(output / 'DS100-Remote-R11-Usage-poster.jpg'), canvas)
                if frame_number == min(150, round(duration * FPS) - 1):
                    cv2.imwrite(str(work / (name + '-preview.jpg')), canvas)
        finally:
            if cap is not None:
                cap.release()
            encoder.stdin.close()
            if encoder.wait(timeout=60) != 0:
                raise RuntimeError('The scene encoder failed: ' + name)
        scene_reports.append({'id': name, 'start': elapsed, 'seconds': duration, 'physical_capture': cap is not None,
                              'warning_text': plan['warning'] if scene['kind'] == 'warning' else None})
        elapsed += duration
        print(name, 'rendered', duration, flush=True)
    concat = work / 'concat.txt'
    concat.write_text(''.join("file '" + p.name + "'\n" for p in parts), encoding='utf-8')
    video = output / 'DS100-Remote-R11-Usage-1080p.mp4'
    concatenated = work / 'concatenated.mp4'
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0',
                    '-i', str(concat), '-c', 'copy', str(concatenated)], check=True)
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(concatenated),
                    '-c:v', 'copy', '-af', 'loudnorm=I=-16:TP=-1.5:LRA=9', '-ar', '48000',
                    '-c:a', 'aac', '-b:a', '160k', '-movflags', '+faststart', str(video)], check=True)
    srt = '\n\n'.join(str(i) + '\n' + stamp(a) + ' --> ' + stamp(b) + '\n' + t for i, (a, b, t) in enumerate(cues, 1)) + '\n'
    (output / 'DS100-Remote-R11-Usage-zh.srt').write_text(srt, encoding='utf-8-sig')
    vtt = 'WEBVTT\n\n' + '\n\n'.join(stamp(a, True) + ' --> ' + stamp(b, True) + '\n' + t for a, b, t in cues) + '\n'
    (output / 'DS100-Remote-R11-Usage-zh.vtt').write_text(vtt, encoding='utf-8')
    report = {'created_utc': datetime.now(timezone.utc).isoformat(), 'success': True,
              'video': video.name, 'video_sha256': hashlib.sha256(video.read_bytes()).hexdigest(),
              'size_bytes': video.stat().st_size, 'seconds': elapsed, 'width': W, 'height': H, 'file_fps': FPS,
              'native_fps_claimed': False, 'physical_capture_receipt': str(args.capture.relative_to(ROOT) / 'receipt.json'),
              'automated_qt_mouse_events': True, 'cursor_and_click_overlays': True, 'synthetic_narration': True,
              'warning_at_start_and_end': True, 'warning_text': plan['warning'], 'scenes': scene_reports,
              'recorded_resyncs': captured['resyncs'], 'settings_restored': captured['settings_restored']}
    (output / 'video-manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('video', 'seconds', 'size_bytes', 'video_sha256')}, indent=2))


if __name__ == '__main__':
    main()
