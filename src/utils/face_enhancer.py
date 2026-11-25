import os
import sys
import warnings

import cv2
import numpy as np
import torch
from tqdm import tqdm

from src.utils.device import get_device, is_gpu_device
from src.utils.videoio import load_video_to_cv2  # noqa: F401 - kept for callers importing it from here


class SpandrelFaceRestorer:
    """Drop-in replacement for GFPGANer backed by spandrel + facexlib.

    Works on Python 3.12-3.14 and PyTorch 2.x without requiring basicsr.
    """

    def __init__(self, model_path, upscale=2, bg_upsampler=None, device=None, model_rootpath='gfpgan/weights'):
        import spandrel
        from facexlib.utils import load_file_from_url
        from facexlib.utils.face_restoration_helper import FaceRestoreHelper

        self.upscale = upscale
        self.bg_upsampler = bg_upsampler
        self.device = get_device(device_arg=device) if device is not None else get_device()

        if model_path.startswith('https://') or model_path.startswith('http://'):
            model_path = load_file_from_url(
                url=model_path,
                model_dir=model_rootpath,
                progress=True,
                file_name=None,
            )

        # Load on CPU first; keep GFPGAN StyleGAN2 weights on CPU for DirectML because
        # torch-directml has a modulated-conv color-shift bug in GFPGANv1Clean.
        self.model_device = torch.device('cpu') if self.device.type == 'privateuseone' else self.device
        self.model = spandrel.ModelLoader(device=torch.device('cpu')).load_from_file(model_path)
        self.model.to(self.model_device)
        self.model.eval()

        self.face_helper = FaceRestoreHelper(
            upscale_factor=upscale,
            face_size=512,
            crop_ratio=(1, 1),
            det_model='retinaface_resnet50',
            save_ext='png',
            use_parse=True,
            device=self.device,
            model_rootpath=model_rootpath,
        )

    @torch.no_grad()
    def enhance(self, img, has_aligned=False, only_center_face=False, paste_back=True):
        self.face_helper.clean_all()

        if has_aligned:
            img = cv2.resize(img, (512, 512), interpolation=cv2.INTER_LINEAR)
            self.face_helper.cropped_faces = [img]
        else:
            self.face_helper.read_image(img)
            self.face_helper.get_face_landmarks_5(only_center_face=only_center_face, eye_dist_threshold=5)
            self.face_helper.align_warp_face()

        if not self.face_helper.cropped_faces:
            return [], [], img

        for cropped_face in self.face_helper.cropped_faces:
            cropped_rgb = cv2.cvtColor(cropped_face, cv2.COLOR_BGR2RGB)
            cropped_t = torch.from_numpy(cropped_rgb).float().permute(2, 0, 1).unsqueeze(0) / 255.0
            output = self.model(cropped_t.to(self.model_device))
            restored_rgb = (
                output.squeeze(0).clamp(0, 1).permute(1, 2, 0).cpu().numpy() * 255.0
            ).round().astype(np.uint8)
            restored_bgr = cv2.cvtColor(restored_rgb, cv2.COLOR_RGB2BGR)
            self.face_helper.add_restored_face(restored_bgr)

        if not has_aligned and paste_back:
            bg_img = None
            if self.bg_upsampler is not None:
                bg_img = self.bg_upsampler.enhance(img, outscale=self.upscale)[0]
            self.face_helper.get_inverse_affine(None)
            restored_img = self.face_helper.paste_faces_to_input_image(upsample_img=bg_img)
            return self.face_helper.cropped_faces, self.face_helper.restored_faces, restored_img

        return self.face_helper.cropped_faces, self.face_helper.restored_faces, None


def _import_gfpgan():
    # basicsr 1.4.2 imports torchvision.transforms.functional_tensor, removed in torchvision 0.17+
    if 'torchvision.transforms.functional_tensor' not in sys.modules:
        try:
            import torchvision.transforms.functional_tensor  # noqa: F401
        except ImportError:
            import torchvision.transforms.functional as _tv_functional
            sys.modules['torchvision.transforms.functional_tensor'] = _tv_functional
    from gfpgan import GFPGANer
    return GFPGANer


class GeneratorWithLen(object):
    """ From https://stackoverflow.com/a/7460929 """

    def __init__(self, gen, length):
        self.gen = gen
        self.length = length

    def __len__(self):
        return self.length

    def __iter__(self):
        return self.gen


def enhancer_list(images, method='gfpgan', bg_upsampler='realesrgan'):
    gen = enhancer_generator_no_len(images, method=method, bg_upsampler=bg_upsampler)
    return list(gen)


def _is_video_path(images):
    return not isinstance(images, (list, tuple)) and isinstance(images, (str, os.PathLike)) and os.path.isfile(images)


def video_frame_count(path):
    cap = cv2.VideoCapture(path)
    try:
        return int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        cap.release()


def iter_video_frames_rgb(path):
    """Yield a video's frames as RGB one at a time (load_video_to_cv2 holds them all in RAM)."""
    cap = cv2.VideoCapture(path)
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                return
            yield cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    finally:
        cap.release()


def enhancer_generator_with_len(images, method='gfpgan', bg_upsampler='realesrgan'):
    """ Provide a generator with a __len__ method so that it can passed to functions that
    call len(). Video paths are read lazily, so frames are never all held in memory."""

    length = video_frame_count(images) if _is_video_path(images) else len(images)
    gen = enhancer_generator_no_len(images, method=method, bg_upsampler=bg_upsampler)
    gen_with_len = GeneratorWithLen(gen, length)
    return gen_with_len


def enhancer_generator_no_len(images, method='gfpgan', bg_upsampler='realesrgan'):
    """ Provide a generator function so that all of the enhanced images don't need
    to be stored in memory at the same time. This can save tons of RAM compared to
    the enhancer function. """

    print('face enhancer....')
    if _is_video_path(images):  # stream video frames instead of decoding them all up front
        total = video_frame_count(images)
        frames = iter_video_frames_rgb(images)
    else:
        total = len(images)
        frames = iter(images)

    # ------------------------ set up GFPGAN restorer ------------------------
    if method == 'gfpgan':
        arch = 'clean'
        channel_multiplier = 2
        model_name = 'GFPGANv1.4'
        url = 'https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth'
    elif method == 'RestoreFormer':
        arch = 'RestoreFormer'
        channel_multiplier = 2
        model_name = 'RestoreFormer'
        url = 'https://github.com/TencentARC/GFPGAN/releases/download/v1.3.4/RestoreFormer.pth'
    elif method == 'codeformer':  # TODO:
        arch = 'CodeFormer'
        channel_multiplier = 2
        model_name = 'CodeFormer'
        url = 'https://github.com/sczhou/CodeFormer/releases/download/v0.1.0/codeformer.pth'
    else:
        raise ValueError(f'Wrong model version {method}.')

    # ------------------------ set up background upsampler ------------------------
    if bg_upsampler == 'realesrgan':
        if not torch.cuda.is_available():  # CPU
            warnings.warn(
                'The unoptimized RealESRGAN is slow on CPU. We do not use it. '
                'If you really want to use it, please modify the corresponding codes.'
            )
            bg_upsampler = None
        else:
            try:
                from basicsr.archs.rrdbnet_arch import RRDBNet
                from realesrgan import RealESRGANer
                model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=2)
                bg_upsampler = RealESRGANer(
                    scale=2,
                    model_path='https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth',
                    model=model,
                    tile=400,
                    tile_pad=10,
                    pre_pad=0,
                    half=True,
                )
            except ImportError:
                warnings.warn('RealESRGAN / basicsr not installed; skipping background upsampler.')
                bg_upsampler = None
    else:
        bg_upsampler = None

    # determine model paths
    model_path = os.path.join('gfpgan/weights', model_name + '.pth')

    if not os.path.isfile(model_path):
        model_path = os.path.join('checkpoints', model_name + '.pth')

    if not os.path.isfile(model_path):
        # download pre-trained models from url
        model_path = url

    try:
        restorer = SpandrelFaceRestorer(
            model_path=model_path,
            upscale=2,
            bg_upsampler=bg_upsampler,
        )
    except ImportError:
        GFPGANer = _import_gfpgan()
        restorer = GFPGANer(
            model_path=model_path,
            upscale=2,
            arch=arch,
            channel_multiplier=channel_multiplier,
            bg_upsampler=bg_upsampler,
        )

    # ------------------------ restore ------------------------
    for frame in tqdm(frames, 'Face Enhancer:', total=total):
        img = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

        # restore faces and background if necessary
        cropped_faces, restored_faces, r_img = restorer.enhance(
            img,
            has_aligned=False,
            only_center_face=False,
            paste_back=True,
        )

        r_img = cv2.cvtColor(r_img, cv2.COLOR_BGR2RGB)
        yield r_img
