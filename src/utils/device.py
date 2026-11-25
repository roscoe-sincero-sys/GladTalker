"""Unified compute device selection for CUDA, AMD ROCm, AMD/Intel DirectML (Windows), MPS, and CPU."""

import os
import torch

_dml_compat_installed = False


def enable_directml_compat() -> None:
    """Install lightweight CPU-fallback shims for ops unsupported on DirectML (e.g. 5D conv3d)."""
    global _dml_compat_installed
    if _dml_compat_installed:
        return
    import torch.nn.functional as F

    _orig_conv3d = F.conv3d

    def _dml_safe_conv3d(input, weight, bias=None, stride=1, padding=0, dilation=1, groups=1):
        if input.device.type == "privateuseone":
            out = _orig_conv3d(
                input.cpu(),
                weight.cpu(),
                bias.cpu() if bias is not None else None,
                stride=stride,
                padding=padding,
                dilation=dilation,
                groups=groups,
            )
            return out.to(input.device)
        return _orig_conv3d(input, weight, bias, stride=stride, padding=padding, dilation=dilation, groups=groups)

    F.conv3d = _dml_safe_conv3d
    _dml_compat_installed = True


def _import_directml():
    try:
        import torch_directml
        return torch_directml
    except ImportError:
        return None


def is_directml_available() -> bool:
    dml = _import_directml()
    return bool(dml is not None and dml.is_available())


def _pick_directml_index(dml) -> int:
    env_idx = os.environ.get("DML_DEVICE_INDEX")
    if env_idx is not None and env_idx.strip() != "":
        return int(env_idx)

    count = dml.device_count()
    if count <= 1:
        return dml.default_device()

    # On dual-GPU Windows laptops (e.g. integrated Radeon Graphics + discrete RX 6550M),
    # prefer a discrete GPU over generic integrated graphics.
    integrated_markers = ("microsoft basic", "uhd graphics", "iris", "radeon(tm) graphics", "vega")
    for idx in range(count):
        name = (dml.device_name(idx) or "").lower()
        if name and not any(m in name for m in integrated_markers):
            return idx
    return dml.default_device()


def get_device(prefer_cpu: bool = False, device_arg=None) -> torch.device:
    """Resolve the best available torch.device.

    Args:
        prefer_cpu: If True, force CPU regardless of available accelerators.
        device_arg: Explicit device spec ('auto', 'cpu', 'cuda', 'cuda:0',
            'dml', 'dml:1', 'directml', 'mps') or an existing torch.device.
    """
    if isinstance(device_arg, torch.device):
        return device_arg

    if prefer_cpu:
        return torch.device("cpu")

    spec = (device_arg or os.environ.get("GLADTALKER_DEVICE") or "auto").strip().lower()

    if spec == "cpu":
        return torch.device("cpu")

    if spec.startswith("cuda"):
        if not torch.cuda.is_available():
            raise RuntimeError(f"Requested device '{spec}', but CUDA/ROCm is not available.")
        return torch.device(spec)

    if spec in ("dml", "directml") or spec.startswith("dml:") or spec.startswith("directml:"):
        dml = _import_directml()
        if dml is None or not dml.is_available():
            raise RuntimeError(
                f"Requested DirectML device '{spec}', but torch-directml is not installed or unavailable. "
                "Install it in a Python 3.10-3.12 environment with: pip install torch-directml"
            )
        if ":" in spec:
            idx = int(spec.split(":", 1)[1])
        else:
            idx = _pick_directml_index(dml)
        enable_directml_compat()
        return dml.device(idx)

    if spec == "mps":
        if not (getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()):
            raise RuntimeError("Requested device 'mps', but Apple MPS is not available.")
        return torch.device("mps")

    if spec != "auto":
        return torch.device(spec)

    # Auto-detection priority: CUDA/ROCm -> DirectML (AMD/Intel on Windows) -> MPS -> CPU
    if torch.cuda.is_available():
        return torch.device("cuda")

    dml = _import_directml()
    if dml is not None and dml.is_available():
        enable_directml_compat()
        return dml.device(_pick_directml_index(dml))

    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return torch.device("mps")

    return torch.device("cpu")


def is_gpu_device(device) -> bool:
    dev = device if isinstance(device, torch.device) else torch.device(device)
    return dev.type != "cpu"


def empty_cache(device=None) -> None:
    if device is None:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
        return

    dev = device if isinstance(device, torch.device) else torch.device(device)
    if dev.type == "cuda" and torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
    elif dev.type == "mps" and hasattr(torch, "mps") and hasattr(torch.mps, "empty_cache"):
        torch.mps.empty_cache()


def describe_device(device) -> str:
    dev = device if isinstance(device, torch.device) else torch.device(device)
    if dev.type == "cuda" and torch.cuda.is_available():
        idx = dev.index if dev.index is not None else 0
        backend = "ROCm" if getattr(torch.version, "hip", None) else "CUDA"
        return f"{backend} ({torch.cuda.get_device_name(idx)})"
    if dev.type == "privateuseone":
        dml = _import_directml()
        if dml is not None:
            idx = dev.index if dev.index is not None else 0
            name = (dml.device_name(idx) or "").replace("\x00", "").strip()
            return f"DirectML:{idx} ({name})"
        return f"DirectML ({dev})"
    if dev.type == "mps":
        return "Apple MPS"
    return "CPU"
