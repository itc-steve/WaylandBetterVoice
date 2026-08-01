# python-pywhispercpp-cuda (Blackwell / sm_120)

WaylandBetterVoice needs `python-pywhispercpp`. This PKGBUILD builds it with CUDA kernels
for compute capability **12.0 (`sm_120`)**, for an RTX PRO 6000 Blackwell.

Upstream: <https://github.com/Absadiki/pywhispercpp>

## Build

```bash
cd packaging/python-pywhispercpp-cuda
makepkg --cleanbuild --force
sudo pacman -U python-pywhispercpp-cuda-*.pkg.tar.zst
```

## Different GPU

Change `local cuda_archs="120"` in `PKGBUILD` to your GPU's compute capability, then
rebuild. Find it with:

```bash
nvidia-smi --query-gpu=name,compute_cap --format=csv
```
