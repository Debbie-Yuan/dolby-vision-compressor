#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 3 ]]; then
  echo "Usage: $0 <input.mov> [max_bitrate_mbps=12] [preset=slow]" >&2
  exit 1
fi

INPUT="$(readlink -f "$1")"
MAX_BITRATE_MBPS="${2:-12}"
PRESET="${3:-slow}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STACK_ROOT="${STACK_ROOT:-$BUNDLE_ROOT/toolchain/install}"
STACK_ENV="$STACK_ROOT/env.sh"
FFMPEG="$STACK_ROOT/prefix/bin/ffmpeg"
FFPROBE="$STACK_ROOT/prefix/bin/ffprobe"
DOVI_TOOL="$STACK_ROOT/prefix/bin/dovi_tool"

if [[ ! -f "$INPUT" ]]; then
  echo "Input file not found: $INPUT" >&2
  exit 1
fi

for tool in "$STACK_ENV" "$FFMPEG" "$FFPROBE" "$DOVI_TOOL"; do
  if [[ ! -e "$tool" ]]; then
    echo "Required tool or file missing: $tool" >&2
    exit 1
  fi
done

for cmd in MP4Box python3 mediainfo; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Required command missing: $cmd" >&2
    exit 1
  fi
done

if ! [[ "$MAX_BITRATE_MBPS" =~ ^[0-9]+$ ]]; then
  echo "max_bitrate_mbps must be an integer, got: $MAX_BITRATE_MBPS" >&2
  exit 1
fi

source "$STACK_ENV"

BASE_NAME="$(basename "$INPUT")"
BASE_STEM="${BASE_NAME%.*}"
RUN_ROOT="${OUTPUT_ROOT:-$BUNDLE_ROOT/output}/runs/${BASE_STEM}_1080p_test_$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$RUN_ROOT/verify"

SOURCE_HEVC="$RUN_ROOT/source_video.hevc"
SOURCE_RPU="$RUN_ROOT/source_video.rpu.bin"
ENCODED_1080_HEVC="$RUN_ROOT/encoded_1080p_${MAX_BITRATE_MBPS}m.hevc"
ENCODED_1080_DV_HEVC="$RUN_ROOT/encoded_1080p_${MAX_BITRATE_MBPS}m_dv.hevc"
MOV_1080_HLG="$RUN_ROOT/${BASE_STEM}_1080p_hlg_${MAX_BITRATE_MBPS}m_${PRESET}.mov"
MOV_1080_DV_REBUILT="$RUN_ROOT/${BASE_STEM}_1080p_dv_rebuilt_${MAX_BITRATE_MBPS}m_${PRESET}.mov"
MOV_1080_DV_PATCHED="$RUN_ROOT/${BASE_STEM}_1080p_dv_patched_${MAX_BITRATE_MBPS}m_${PRESET}.mov"

MAXRATE_MBPS=$(( MAX_BITRATE_MBPS * 125 / 100 ))
BUFSIZE_MBPS=$(( MAX_BITRATE_MBPS * 250 / 100 ))

TRACK_COUNT="$(
python3 - "$INPUT" <<'PY'
from pathlib import Path
import sys
data = Path(sys.argv[1]).read_bytes()
def parse(start, end):
    out = []
    off = start
    while off + 8 <= end:
        size = int.from_bytes(data[off:off+4], "big")
        typ = data[off+4:off+8]
        hdr = 8
        if size == 1:
            size = int.from_bytes(data[off+8:off+16], "big")
            hdr = 16
        elif size == 0:
            size = end - off
        if size < 8 or off + size > end:
            break
        out.append((off, size, typ, hdr))
        off += size
    return out
moov = next(b for b in parse(0, len(data)) if b[2] == b"moov")
traks = [b for b in parse(moov[0] + 8, moov[0] + moov[1]) if b[2] == b"trak"]
print(len(traks))
PY
)"

echo "Input: $INPUT"
echo "Workdir: $RUN_ROOT"
echo "Tracks detected: $TRACK_COUNT"
echo "Target bitrate: ${MAX_BITRATE_MBPS} Mbps"
echo "Preset: $PRESET"
echo "Mode: 1080p experimental investigation"

echo "[1/8] Extracting source HEVC elementary stream"
"$FFMPEG" -y -hide_banner -i "$INPUT" -map 0:v:0 -c:v copy -bsf:v hevc_mp4toannexb "$SOURCE_HEVC"

echo "[2/8] Extracting source Dolby Vision RPU"
"$DOVI_TOOL" extract-rpu -i "$SOURCE_HEVC" -o "$SOURCE_RPU"

echo "[3/8] Encoding 1080p HEVC"
"$FFMPEG" -y -hide_banner -vsync 0 -i "$INPUT" \
  -map 0:v:0 -an -sn -dn -fps_mode passthrough \
  -vf "scale=1920:1080:flags=lanczos" \
  -pix_fmt yuv420p10le \
  -c:v libx265 -preset "$PRESET" \
  -b:v "${MAX_BITRATE_MBPS}M" -maxrate "${MAXRATE_MBPS}M" -bufsize "${BUFSIZE_MBPS}M" \
  -x265-params "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc:repeat-headers=1:aud=1:no-info=1" \
  -f hevc "$ENCODED_1080_HEVC"

echo "[4/8] Creating 1080p HLG-only MOV"
MP4BOX_HLG_ARGS=( -new "$MOV_1080_HLG" -add "${ENCODED_1080_HEVC}:fps=60:ID=1:colr=nclx,BT2020,STDB67,BT2020,no" )
for (( track = 2; track <= TRACK_COUNT; track++ )); do
  MP4BOX_HLG_ARGS+=( -add "${INPUT}#${track}:ID=${track}:keep_refs" )
done
MP4Box "${MP4BOX_HLG_ARGS[@]}"

echo "[5/8] Injecting original RPU into 1080p HEVC"
"$DOVI_TOOL" inject-rpu -i "$ENCODED_1080_HEVC" --rpu-in "$SOURCE_RPU" -o "$ENCODED_1080_DV_HEVC"

echo "[6/8] Creating 1080p DV experimental MOV"
MP4BOX_DV_ARGS=( -new "$MOV_1080_DV_REBUILT" -add "${ENCODED_1080_DV_HEVC}:fps=60:ID=1:colr=nclx,BT2020,STDB67,BT2020,no" )
for (( track = 2; track <= TRACK_COUNT; track++ )); do
  MP4BOX_DV_ARGS+=( -add "${INPUT}#${track}:ID=${track}:keep_refs" )
done
MP4Box "${MP4BOX_DV_ARGS[@]}"

echo "[7/8] Restoring original DV config box into 1080p DV MOV"
python3 - "$INPUT" "$MOV_1080_DV_REBUILT" "$MOV_1080_DV_PATCHED" <<'PY'
from pathlib import Path
import shutil
import sys

src = Path(sys.argv[1]).read_bytes()
rebuilt = Path(sys.argv[2]).read_bytes()
out_path = Path(sys.argv[3])
shutil.copyfile(sys.argv[2], sys.argv[3])

src_off = src.find(b"dvvC")
if src_off < 4:
    raise SystemExit("source dvvC box not found")
src_box_size = int.from_bytes(src[src_off-4:src_off], "big")
src_box = src[src_off-4:src_off-4+src_box_size]

dst_off = rebuilt.find(b"dvcC")
if dst_off < 4:
    dst_off = rebuilt.find(b"dvvC")
if dst_off < 4:
    raise SystemExit("rebuilt DV config box not found")
dst_box_size = int.from_bytes(rebuilt[dst_off-4:dst_off], "big")
if dst_box_size != src_box_size:
    raise SystemExit(f"DV box size mismatch: source={src_box_size}, rebuilt={dst_box_size}")

with out_path.open("r+b") as f:
    f.seek(dst_off - 4)
    f.write(src_box)
PY

echo "[8/8] Writing comparison reports"
mediainfo "$MOV_1080_HLG" > "$RUN_ROOT/verify/mediainfo_1080p_hlg.txt"
mediainfo "$MOV_1080_DV_PATCHED" > "$RUN_ROOT/verify/mediainfo_1080p_dv.txt"
"$FFPROBE" -v error -show_streams -of json "$MOV_1080_HLG" > "$RUN_ROOT/verify/ffprobe_1080p_hlg.json"
"$FFPROBE" -v error -show_streams -of json "$MOV_1080_DV_PATCHED" > "$RUN_ROOT/verify/ffprobe_1080p_dv.json"

echo
echo "Experimental outputs:"
echo "  HLG only: $MOV_1080_HLG"
echo "  DV injected: $MOV_1080_DV_PATCHED"
echo
echo "Verification reports:"
echo "  $RUN_ROOT/verify"
echo
echo "Important:"
echo "  This script is for investigation only."
echo "  It does not yet build a geometry-aware Apple hybrid MOV for 1080p."
echo "  Compare these outputs on Apple devices before treating 1080p DV as valid."
