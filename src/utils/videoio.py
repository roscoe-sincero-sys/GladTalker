import shutil
import subprocess
import sys
import uuid

import os

import cv2

from src.utils.safe_io import resolve_ffmpeg

# Don't flash a console window per FFmpeg call when running under a windowed (Tauri) sidecar.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

def load_video_to_cv2(input_path):
    video_stream = cv2.VideoCapture(input_path)
    fps = video_stream.get(cv2.CAP_PROP_FPS)
    full_frames = [] 
    while 1:
        still_reading, frame = video_stream.read()
        if not still_reading:
            video_stream.release()
            break 
        full_frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    return full_frames

def _run_ffmpeg(args):
    """Run FFmpeg (GLADTALKER_FFMPEG or PATH) with an argument list; raise on failure."""
    proc = subprocess.run(
        [resolve_ffmpeg(), '-y', '-hide_banner', '-loglevel', 'error', *args],
        capture_output=True, text=True, creationflags=_NO_WINDOW,
    )
    if proc.returncode != 0:
        tail = ' '.join((proc.stderr or '').strip().splitlines()[-6:])
        raise RuntimeError('FFmpeg failed (exit %d): %s' % (proc.returncode, tail))


def save_video_with_watermark(video, audio, save_path, watermark=False):
    # Keep the temp file beside the output (not the working directory) so an interrupted run
    # never litters the repository root.
    temp_file = os.path.join(os.path.dirname(os.path.abspath(save_path)), str(uuid.uuid4()) + '.mp4')
    try:
        _run_ffmpeg(['-i', video, '-i', audio, '-vcodec', 'copy', temp_file])
    except BaseException:
        if os.path.exists(temp_file):
            os.remove(temp_file)
        raise

    if watermark is False:
        shutil.move(temp_file, save_path)
    else:
        # watermark
        try:
            ##### check if stable-diffusion-webui
            import webui
            from modules import paths
            watarmark_path = paths.script_path+"/extensions/SadTalker/docs/sadtalker_logo.png"
        except:
            # get the root path of sadtalker.
            dir_path = os.path.dirname(os.path.realpath(__file__))
            watarmark_path = dir_path+"/../../docs/sadtalker_logo.png"

        try:
            _run_ffmpeg(['-i', temp_file, '-i', watarmark_path, '-filter_complex',
                         '[1]scale=100:-1[wm];[0][wm]overlay=(main_w-overlay_w)-10:10', save_path])
        finally:
            os.remove(temp_file)