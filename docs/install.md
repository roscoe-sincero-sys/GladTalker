### macOS

This method has been tested on a M1 Mac (13.3)

```bash
git clone https://github.com/OpenTalker/SadTalker.git
cd SadTalker 
python3 -m venv venv
source venv/bin/activate
pip install torch torchvision torchaudio
brew install ffmpeg
pip install -r requirements.txt
```

### Windows Native

- Make sure you have `ffmpeg` in the `%PATH%` as suggested in [#54](https://github.com/Winfredy/SadTalker/issues/54), following [this](https://www.geeksforgeeks.org/how-to-install-ffmpeg-on-windows/) tutorial to install `ffmpeg` or using scoop.

### Windows AMD GPU (DirectML / ROCm)

GladTalker automatically detects available accelerators in order: **CUDA / ROCm → DirectML → MPS → CPU** (override with `--device auto|cpu|cuda|dml|dml:N|mps` or `GLADTALKER_DEVICE`).

- **Option 1 — DirectML (`torch-directml`, works on Radeon RX 6000 / 7000 / integrated Radeon GPUs):**
  > **Note:** `torch-directml` currently publishes wheels for **Python 3.10–3.12** (not Python 3.14 yet). Create a Python 3.12 virtual environment to use DirectML:
  ```powershell
  py -3.12 -m venv venv-dml
  .\venv-dml\Scripts\activate
  pip install torch-directml
  pip install -r requirements.txt
  python inference.py --device dml --driven_audio <audio.wav> --source_image <image.png>
  ```
  On dual-GPU laptops (e.g., integrated Radeon Graphics + discrete Radeon RX 6550M), GladTalker automatically prefers the discrete GPU; you can also pick an adapter explicitly with `--device dml:1` or `DML_DEVICE_INDEX=1`.

- **Option 2 — AMD ROCm on Windows (Radeon RX 7000 / 9000 series):**
  Install AMD's PyTorch ROCm Windows preview build (Python 3.12). PyTorch ROCm exposes the GPU through `torch.cuda`, so `--device auto` or `--device cuda` uses it automatically.


### Windows WSL


- Make sure the environment: `export LD_LIBRARY_PATH=/usr/lib/wsl/lib:$LD_LIBRARY_PATH`


### Docker Installation

A community Docker image by [@thegenerativegeneration](https://github.com/thegenerativegeneration) is available on the [Docker hub](https://hub.docker.com/repository/docker/wawa9000/sadtalker), which can be used directly:
```bash
docker run --gpus "all" --rm -v $(pwd):/host_dir wawa9000/sadtalker \
    --driven_audio /host_dir/deyu.wav \
    --source_image /host_dir/image.jpg \
    --expression_scale 1.0 \
    --still \
    --result_dir /host_dir
```

