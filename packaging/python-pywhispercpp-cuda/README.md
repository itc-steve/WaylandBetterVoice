# python-pywhispercpp-cuda (Blackwell / sm_120)

WaylandBetterVoice depends on `python-pywhispercpp`, and this is the build that provides
it on this machine: whisper.cpp compiled with CUDA kernels for compute capability
**12.0 (`sm_120`)** only — an RTX PRO 6000 Blackwell.

This PKGBUILD is vendored here because it is the *only* copy. It originally lived in a
separate local `vocalinux-blackwell-packages` repository, which had no git remote; when
vocalinux was uninstalled that repository went away, and losing this file would have made
the CUDA whisper package unrebuildable.

Original author: goodroot (hyprwhspr). Upstream source:
<https://github.com/Absadiki/pywhispercpp>

## When you need this

- Reinstalling on a fresh system.
- Upgrading whisper.cpp.
- **Porting to a different GPU** — change `cuda_archs` (line ~79) to your card's compute
  capability. Anything pre-Blackwell needs this; `sm_120` binaries will not run on
  a 40-series or older card.

Find your compute capability with:

```bash
nvidia-smi --query-gpu=name,compute_cap --format=csv
```

## Build

```bash
cd packaging/python-pywhispercpp-cuda
makepkg --cleanbuild --force
sudo pacman -U python-pywhispercpp-cuda-*.pkg.tar.zst
```

Takes a while — it compiles CUDA kernels.

## Notes

- `provides=python-pywhispercpp` and `conflicts` with the cpu/rocm variants, so it
  satisfies the WaylandBetterVoice dependency directly.
- It is pinned in `/etc/pacman.conf` under `IgnorePkg` so routine upgrades cannot
  silently replace it with the CPU build:

  ```
  IgnorePkg = python-pywhispercpp-cuda
  ```

- The `package()` step strips bundled `libcuda*` and repoints `DT_NEEDED` entries at the
  system `libcuda.so.1`, which is what makes it work against the installed driver.
