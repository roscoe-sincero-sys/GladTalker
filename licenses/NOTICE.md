# SadTalker notices for GladTalker

GladTalker derives from OpenTalker/SadTalker commit `cd4c046`. The
upstream project puts its Tencent copyright notice and Apache 2.0 terms in
`LICENSE` and its attribution and media notices in `README.md`.
[Upstream revision](https://github.com/OpenTalker/SadTalker/tree/cd4c0465ae0b54a6f85af57f5c65fec9fe23e7f8)

## License copies

- [LICENSE](LICENSE) copies GladTalker's root license. Its Git content matches
  SadTalker's Apache 2.0 license at the fork point, including Tencent's notice.
  [Upstream license](https://github.com/OpenTalker/SadTalker/blob/cd4c0465ae0b54a6f85af57f5c65fec9fe23e7f8/LICENSE)
- [SadTalker-v0.0.2-MIT.txt](SadTalker-v0.0.2-MIT.txt) preserves the earlier MIT
  license and Tencent AI Lab copyright from the `v0.0.2` release.
  [Tagged release license](https://github.com/OpenTalker/SadTalker/blob/35dd94f443e5cd542ee3db0b627815d79478196e/LICENSE)
- [David-Sandberg-verification-MIT.txt](David-Sandberg-verification-MIT.txt)
  copies the MIT notice from the inherited face-verification code.
  [Source notice](https://github.com/OpenTalker/SadTalker/blob/cd4c0465ae0b54a6f85af57f5c65fec9fe23e7f8/src/face3d/models/arcface_torch/eval/verification.py#L4-L24)

## Project attribution

```bibtex
@article{zhang2022sadtalker,
  title={SadTalker: Learning Realistic 3D Motion Coefficients for Stylized Audio-Driven Single Image Talking Face Animation},
  author={Zhang, Wenxuan and Cun, Xiaodong and Wang, Xuan and Zhang, Yong and Shen, Xi and Guo, Yu and Shan, Ying and Wang, Fei},
  journal={arXiv preprint arXiv:2211.12194},
  year={2022}
}
```

SadTalker credits the face-vid2vid reproduction by zhanglonghao1992 and PIRender
for face-rendering code, and Deep3DFaceReconstruction and Wav2Lip for models used
during training. It also credits facexlib, GFPGAN, and Real-ESRGAN.
[Upstream acknowledgements](https://github.com/OpenTalker/SadTalker/blob/cd4c0465ae0b54a6f85af57f5c65fec9fe23e7f8/README.md#acknowledgements)

The upstream README credits @fakerybakery for the September 12, 2023 spelling
edits and identifies those edits as Apache 2.0. It also credits Montserrat
Alternates as the logo font and includes a statement about Tencent names and
logos. [Upstream README notices](https://github.com/OpenTalker/SadTalker/blob/cd4c0465ae0b54a6f85af57f5c65fec9fe23e7f8/README.md#disclaimer)

## Demo media and ArcFace models

SadTalker's README contains this copyright notice for its demo media.

> All the copyrights of the demo images and audio are from community users or the generation from stable diffusion.

[Upstream demo media notice](https://github.com/OpenTalker/SadTalker/blob/cd4c0465ae0b54a6f85af57f5c65fec9fe23e7f8/README.md#disclaimer)

The inherited ArcFace README gives the following terms for the models in its
Model Zoo.

> The models are available for non-commercial research purposes only.

[ArcFace Model Zoo notice](https://github.com/OpenTalker/SadTalker/blob/cd4c0465ae0b54a6f85af57f5c65fec9fe23e7f8/src/face3d/models/arcface_torch/README.md#model-zoo)

## Redistribution

Give recipients of SadTalker-derived distributions a copy of the Apache 2.0
license. Retain applicable copyright, patent, trademark, and attribution
notices in distributed source, and give modified files prominent change
notices as required by Section 4.
[Apache License 2.0, Section 4](https://www.apache.org/licenses/LICENSE-2.0#redistribution)
