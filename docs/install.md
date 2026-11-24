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

