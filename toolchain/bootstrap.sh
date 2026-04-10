#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLCHAIN_ROOT="$(cd "$SCRIPT_DIR" && pwd)"
INSTALL_ROOT="${INSTALL_ROOT:-$TOOLCHAIN_ROOT/install}"
SRC_ROOT="${SRC_ROOT:-$TOOLCHAIN_ROOT/src}"
BUILD_ROOT="${BUILD_ROOT:-$TOOLCHAIN_ROOT/build}"

source "$TOOLCHAIN_ROOT/versions.env"

mkdir -p "$INSTALL_ROOT/prefix" "$SRC_ROOT" "$BUILD_ROOT"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

for cmd in git cmake make pkg-config python3 cargo rustc; do
  require_cmd "$cmd"
done

clone_or_update() {
  local repo="$1"
  local ref="$2"
  local dir="$3"
  if [[ ! -d "$dir/.git" ]]; then
    git clone "$repo" "$dir"
  fi
  git -C "$dir" fetch --tags --force
  git -C "$dir" checkout "$ref"
}

clone_or_update "$X265_REPO" "$X265_REF" "$SRC_ROOT/x265_git"
clone_or_update "$FFMPEG_REPO" "$FFMPEG_REF" "$SRC_ROOT/FFmpeg"
clone_or_update "$DOVI_TOOL_REPO" "$DOVI_TOOL_REF" "$SRC_ROOT/dovi_tool"

echo "Building x265"
cmake -S "$SRC_ROOT/x265_git/source" \
  -B "$BUILD_ROOT/x265-10bit" \
  -G "Unix Makefiles" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$INSTALL_ROOT/prefix" \
  -DENABLE_SHARED=ON \
  -DENABLE_CLI=ON \
  -DHIGH_BIT_DEPTH=ON \
  -DMAIN12=OFF \
  -DEXPORT_C_API=ON
cmake --build "$BUILD_ROOT/x265-10bit" -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
cmake --install "$BUILD_ROOT/x265-10bit"

echo "Building dovi_tool"
cargo build --manifest-path "$SRC_ROOT/dovi_tool/Cargo.toml" --release
install -Dm755 "$SRC_ROOT/dovi_tool/target/release/dovi_tool" "$INSTALL_ROOT/prefix/bin/dovi_tool"

echo "Building ffmpeg"
pushd "$SRC_ROOT/FFmpeg" >/dev/null
make distclean >/dev/null 2>&1 || true
export PATH="$INSTALL_ROOT/prefix/bin:$PATH"
export LD_LIBRARY_PATH="$INSTALL_ROOT/prefix/lib:${LD_LIBRARY_PATH:-}"
export DYLD_LIBRARY_PATH="$INSTALL_ROOT/prefix/lib:${DYLD_LIBRARY_PATH:-}"
export PKG_CONFIG_PATH="$INSTALL_ROOT/prefix/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
./configure \
  --prefix="$INSTALL_ROOT/prefix" \
  --bindir="$INSTALL_ROOT/prefix/bin" \
  --libdir="$INSTALL_ROOT/prefix/lib" \
  --extra-cflags="-I$INSTALL_ROOT/prefix/include" \
  --extra-ldflags="-L$INSTALL_ROOT/prefix/lib -Wl,-rpath,$INSTALL_ROOT/prefix/lib" \
  --enable-gpl \
  --enable-shared \
  --disable-static \
  --disable-debug \
  --disable-doc \
  --enable-libx265
make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
make install
popd >/dev/null

cat > "$INSTALL_ROOT/env.sh" <<EOF
#!/usr/bin/env bash
export TOOLCHAIN_ROOT="$INSTALL_ROOT"
export PATH="$INSTALL_ROOT/prefix/bin:\$PATH"
export LD_LIBRARY_PATH="$INSTALL_ROOT/prefix/lib:\${LD_LIBRARY_PATH:-}"
export DYLD_LIBRARY_PATH="$INSTALL_ROOT/prefix/lib:\${DYLD_LIBRARY_PATH:-}"
export PKG_CONFIG_PATH="$INSTALL_ROOT/prefix/lib/pkgconfig:\${PKG_CONFIG_PATH:-}"
EOF
chmod +x "$INSTALL_ROOT/env.sh"

echo
echo "Toolchain installed under:"
echo "  $INSTALL_ROOT"
echo
echo "Note:"
echo "  MP4Box/GPAC and mediainfo are not built by this bootstrap script."
echo "  Install them separately on the host, or extend CI/bootstrap to build them too."
