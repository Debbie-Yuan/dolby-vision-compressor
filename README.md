# MOV Hybrid Bundle

Portable bundle for Apple-camera MOV compression with Apple-style hybrid container repair.

## What It Contains

- `scripts/mov_hybrid_compress.sh`
  - 4K same-resolution hybrid workflow
- `scripts/mov_hybrid_compress_1080p.sh`
  - 1080p geometry-aware hybrid workflow
- `scripts/mov_hybrid_compress_1080p_experimental.sh`
  - earlier investigation workflow kept for comparison
- `python/build_hybrid_mov.py`
  - hybrid MOV builder
- `toolchain/bootstrap.sh`
  - source-based bootstrap for `x265`, `ffmpeg`, and `dovi_tool`
- `.github/workflows/build-toolchain.yml`
  - GitHub Actions bundle/build workflow for source, `linux-amd64`, and OpenWrt SDK input

## Why The Hybrid Method Exists

Apple playback behavior depended on more than just valid HEVC/Dolby Vision bitstreams.

The working approach preserves:

- original Apple metadata tree
- original audio sample entries, including `apac`
- Apple-private video boxes where possible

and only replaces:

- encoded media payload
- sample tables that must match the new payload
- geometry fields when doing 1080p downscale

## Local Bootstrap

Bootstrap the source toolchain:

```bash
cd toolchain
chmod +x bootstrap.sh
./bootstrap.sh
```

This installs a local toolchain under:

```bash
toolchain/install
```

Current bootstrap builds:

- `x265`
- `ffmpeg`
- `ffprobe`
- `dovi_tool`

It does **not** currently build:

- `MP4Box` / GPAC
- `mediainfo`

Those should be installed on the host separately, or added to bootstrap later.

The current `bootstrap.sh` is a host build script.
It is appropriate for native `linux-amd64`.
It is not, by itself, an OpenWrt cross-compilation recipe.

## Entrypoints

4K same-resolution workflow:

```bash
scripts/mov_hybrid_compress.sh <input.mov> [max_bitrate_mbps=20] [preset=slow]
```

1080p hybrid workflow:

```bash
scripts/mov_hybrid_compress_1080p.sh <input.mov> [max_bitrate_mbps=12] [preset=slow]
```

Both scripts support:

- `STACK_ROOT`
  - override toolchain install location
- `OUTPUT_ROOT`
  - override output directory

Example:

```bash
OUTPUT_ROOT="$PWD/output" scripts/mov_hybrid_compress_1080p.sh /path/to/input.mov 12 slow
```

## GitHub CI

Yes, GitHub CI is a reasonable way to support the targets you actually want.

This bundle includes:

- `.github/workflows/build-toolchain.yml`

It now produces:

- `mov-hybrid-source-bundle`
  - source-only bundle artifact
- `mov-hybrid-toolchain-linux-amd64`
  - native Linux AMD64 built toolchain artifact
- `mov-hybrid-openwrt-aarch64-sdk-input`
  - uploaded only when `OPENWRT_SDK_URL` is configured in GitHub repository variables

The OpenWrt artifact is intentionally SDK-oriented, not a fake native host build.
OpenWrt should be treated as a cross-compilation target, usually via an OpenWrt SDK/toolchain, not as a normal desktop Linux target.

For a generic OpenWrt ARM64 target on OpenWrt 24.10.x, a good default is:

```text
https://downloads.openwrt.org/releases/24.10.0/targets/armsr/armv8/openwrt-sdk-24.10.0-armsr-armv8_gcc-13.3.0_musl.Linux-x86_64.tar.zst
```

Set that as the GitHub repository variable:

```text
OPENWRT_SDK_URL
```

If your device uses a board-specific ARM64 target such as `rockchip/armv8`, prefer that target's SDK instead of the generic `armsr/armv8` SDK.

That gives you a practical split:

- keep source in the repo for portability and transparency
- publish a ready-to-use `linux-amd64` toolchain artifact
- keep an OpenWrt SDK-driven path for later cross-build integration

## Recommended Distribution Model

If you want this usable by other users:

1. keep the repo source-first
2. ship scripts + Python builder + bootstrap logic
3. use GitHub Actions to publish:
   - a source bundle
   - a `linux-amd64` toolchain artifact
   - an OpenWrt SDK input artifact or a later OpenWrt cross-built artifact
4. keep the entry scripts stable so users only call one command

That avoids hardcoding your current machine while still making onboarding simple.
