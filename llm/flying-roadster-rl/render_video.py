"""Render episode JSONs to an MP4 with the Three.js viewer (headless Chromium).

Frame-exact: we step the viewer with ``renderAt(t)`` and screenshot each
frame, so the video is smooth regardless of how slow software WebGL is.

Default output is horizontal 1920x1080: one episode fills the frame, several
episodes are laid out side by side (two -> 960x1080 panels).

    # one episode (1920x1080)
    python render_video.py out.mp4 ep.json --label "Hand-tuned baseline"
    # before / after, side by side in one 1920x1080 frame
    python render_video.py cmp.mp4 naive.json tuned.json \
        --label "Naive" --label "Hand-tuned baseline" --motion-blur 3
    # opt-in vertical 1080x1920 social cut (episodes stacked top / bottom),
    # with intro + outro title cards
    python render_video.py social.mp4 a.json b.json --vertical \
        --intro "Kickflip a Roadster?" --outro "Simulated on SkyPilot"

The bottom-right brand line is label-driven: --brand "..." (default
"Simulated on SkyPilot"; --brand "" hides it). Only claim RL
training in it for clips that actually come from an RL-trained policy.

Requires: pip install playwright imageio-ffmpeg && playwright install chromium
(--motion-blur additionally needs numpy + pillow)
"""
import argparse
import io
import json
import pathlib
import shutil
import subprocess
import tempfile

import imageio_ffmpeg
from playwright.sync_api import sync_playwright

VIEWER = pathlib.Path(__file__).resolve().parent / 'viewer' / 'index.html'
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
ENC = [
    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', '-preset', 'medium',
    '-movflags', '+faststart'
]


class FrameSink:
    """Pipes frames into ffmpeg; averages ``blur`` sub-frames per output frame."""

    def __init__(self, out, w, h, fps, blur=1):
        self.blur, self.acc, self.n = blur, None, 0
        if blur > 1:
            src = ['-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{w}x{h}']
        else:
            src = ['-f', 'image2pipe']
        self.ff = subprocess.Popen([
            FFMPEG, '-y', '-loglevel', 'error', *src, '-framerate',
            str(fps), '-i', '-', '-vf', f'scale={w}:{h}', *ENC,
            str(out)
        ],
                                   stdin=subprocess.PIPE)

    def add(self, jpeg: bytes):
        if self.blur <= 1:
            self.ff.stdin.write(jpeg)
            return
        import numpy as np
        from PIL import Image
        img = np.asarray(Image.open(io.BytesIO(jpeg)).convert('RGB'),
                         dtype=np.float32)
        self.acc = img if self.acc is None else self.acc + img
        self.n += 1
        if self.n == self.blur:
            frame = np.clip(self.acc / self.n + .5, 0, 255).astype(np.uint8)
            self.ff.stdin.write(frame.tobytes())
            self.acc, self.n = None, 0

    def close(self):
        self.ff.stdin.close()
        self.ff.wait()


def subframe_times(i, fps, blur, shutter=.5):
    """Sample times for output frame i: ``blur`` samples over a 180-degree shutter."""
    if blur <= 1:
        return [i / fps]
    return [
        max(0.0, (i + shutter * ((j + .5) / blur - .5)) / fps)
        for j in range(blur)
    ]


def shoot(page):
    return page.screenshot(type='jpeg', quality=94)


def render_one(page,
               ep: dict,
               out: pathlib.Path,
               fps: int,
               opts: dict,
               duration: float = None,
               blur: int = 1):
    dur = page.evaluate('([ep, opts]) => window.loadEpisode(ep, opts)',
                        [ep, opts])
    dur = duration or dur
    n = int(dur * fps)
    w, h = page.viewport_size['width'], page.viewport_size['height']
    sink = FrameSink(out, w, h, fps, blur)
    for i in range(n):
        for t in subframe_times(i, fps, blur):
            # 2nd arg pins the DOM HUD to the frame time (no ghosted digits).
            page.evaluate(f'window.renderAt({t}, {i / fps})')
            sink.add(shoot(page))
    sink.close()
    return dur


def render_card(page, ep: dict, out: pathlib.Path, fps: int, card: dict):
    """Intro / outro title card over a slow orbit of the car."""
    page.evaluate('([ep, o]) => window.loadEpisode(ep, o)',
                  [ep, card.get('opts', {})])
    w, h = page.viewport_size['width'], page.viewport_size['height']
    sink = FrameSink(out, w, h, fps)
    for i in range(int(card['duration'] * fps)):
        page.evaluate('([t, c]) => window.renderCard(t, c)', [i / fps, card])
        sink.add(shoot(page))
    sink.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out')
    ap.add_argument('episodes',
                    nargs='+',
                    help='episode JSON files (results with a recorded traj)')
    ap.add_argument('--label', action='append', default=[])
    ap.add_argument('--tag', action='append', default=[])
    ap.add_argument('--camera',
                    default=None,
                    help='auto (per-task, default)|chase|rear|side|top')
    ap.add_argument('--layout', default='hstack', choices=['hstack', 'vstack'])
    ap.add_argument('--width',
                    type=int,
                    default=None,
                    help='per-panel width (default: 1920 / n side by side)')
    ap.add_argument(
        '--height',
        type=int,
        default=None,
        help='per-panel height (default: 1080, or 1080 / n for vstack)')
    ap.add_argument(
        '--brand',
        default=None,
        help=
        'bottom-right brand line (default "Simulated on SkyPilot"; "" hides it)'
    )
    ap.add_argument('--fps', type=int, default=30)
    ap.add_argument(
        '--vertical',
        action='store_true',
        help='opt-in 1080x1920 social layout (several episodes are stacked '
        'top to bottom); overrides --width/--height/--layout')
    ap.add_argument(
        '--trails',
        action='store_true',
        help='draw light trails from the rear light bar while airborne')
    ap.add_argument(
        '--motion-blur',
        type=int,
        default=1,
        metavar='N',
        help='average N sub-frames per frame (180-degree shutter); N x slower')
    ap.add_argument('--intro',
                    default=None,
                    help='intro title card text ("\\n" for a line break)')
    ap.add_argument('--intro-sub', default='')
    ap.add_argument('--intro-kicker', default='')
    ap.add_argument('--outro', default=None, help='outro title card text')
    ap.add_argument('--outro-sub', default='')
    ap.add_argument('--outro-kicker', default='')
    ap.add_argument('--card-seconds', type=float, default=2.5)
    args = ap.parse_args()

    eps = [json.loads(pathlib.Path(p).read_text()) for p in args.episodes]
    n = len(eps)
    if args.vertical:
        args.width, args.layout = 1080, 'vstack'
        args.height = 1920 // n
    else:
        if args.width is None:
            args.width = 1920 // n if args.layout == 'hstack' else 1920
        if args.height is None:
            args.height = 1080 if args.layout == 'hstack' else 1080 // n
    args.width -= args.width % 2
    args.height -= args.height % 2
    W = args.width * (len(eps) if args.layout == 'hstack' else 1)
    H = args.height * (len(eps) if args.layout == 'vstack' else 1)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=[
            '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
            '--ignore-gpu-blocklist'
        ])
        page = browser.new_page(viewport={
            'width': args.width,
            'height': args.height
        })
        page.on('console',
                lambda m: m.type == 'error' and print('[viewer]', m.text))
        page.on('pageerror', lambda e: print('[viewer error]', e))
        page.goto(VIEWER.as_uri() + '?record=1')
        page.wait_for_function('window.viewerReady === true', timeout=60000)
        page.evaluate('document.fonts.ready.then(() => true)')
        tmp = pathlib.Path(tempfile.mkdtemp())
        parts = []
        # Same length for every clip so a side-by-side stays in sync.
        dur = max(
            page.evaluate('([ep]) => window.loadEpisode(ep, {})', [e])
            for e in eps)
        for i, ep in enumerate(eps):
            opts = {
                'model': args.label[i] if i < len(args.label) else ep.get(
                    'label', '')
            }
            if i < len(args.tag):
                opts['tag'] = args.tag[i]
            if args.camera:
                opts['camera'] = args.camera
            if args.trails:
                opts['trails'] = True
            if args.brand is not None:
                opts['brand'] = args.brand
            part = tmp / f'part{i}.mp4'
            render_one(page,
                       ep,
                       part,
                       args.fps,
                       opts,
                       duration=dur,
                       blur=max(1, args.motion_blur))
            parts.append(part)
            print(f'rendered {part} ({dur:.1f}s)')

        cards = {}
        for kind, title, sub, kicker in (('intro', args.intro, args.intro_sub,
                                          args.intro_kicker),
                                         ('outro', args.outro, args.outro_sub,
                                          args.outro_kicker)):
            if title is None:
                continue
            page.set_viewport_size({'width': W, 'height': H})
            page.evaluate('window.dispatchEvent(new Event("resize"))')
            card = {
                'kind': kind,
                'title': title.replace('\\n', '\n'),
                'subtitle': sub,
                'kicker': kicker,
                'duration': args.card_seconds,
                'opts': {} if args.brand is None else {
                    'brand': args.brand
                }
            }
            cards[kind] = tmp / f'{kind}.mp4'
            render_card(page, eps[-1], cards[kind], args.fps, card)
            print(f'rendered {kind} card')
        browser.close()

    main_clip = tmp / 'main.mp4'
    if len(parts) == 1:
        shutil.move(str(parts[0]), str(main_clip))
    else:
        inputs = sum((['-i', str(p)] for p in parts), [])
        subprocess.run([
            FFMPEG, '-y', '-loglevel', 'error', *inputs, '-filter_complex',
            f'{args.layout}=inputs={len(parts)}', *ENC,
            str(main_clip)
        ],
                       check=True)
    seq = [c for c in (cards.get('intro'), main_clip, cards.get('outro')) if c]
    if len(seq) == 1:
        shutil.move(str(main_clip), args.out)
    else:
        inputs = sum((['-i', str(p)] for p in seq), [])
        streams = ''.join(f'[{i}:v]' for i in range(len(seq)))
        subprocess.run([
            FFMPEG, '-y', '-loglevel', 'error', *inputs, '-filter_complex',
            f'{streams}concat=n={len(seq)}:v=1:a=0[v]', '-map', '[v]', *ENC,
            args.out
        ],
                       check=True)
    print('wrote', args.out)


if __name__ == '__main__':
    main()
