#!/usr/bin/env bash
# kilix — measure a live A/V stream as the VIEWER receives it, to quantify
# streaming efficiency: delivered video codec/resolution/fps + bitrate, audio
# codec/bitrate, and download throughput. Works on any HLS (.m3u8) URL (append
# ?t=<token> for a token-gated kilix bridge).
#
# Usage:  scripts/stream-stats.sh <hls-url> [seconds]
set -u
URL="${1:?usage: stream-stats.sh <hls-url> [seconds]}"
SECS="${2:-8}"

echo "=== kilix stream-stats — $URL  (sampling ${SECS}s) ==="

echo "--- declared streams (ffprobe) ---"
ffprobe -v error -show_entries \
  stream=codec_type,codec_name,width,height,avg_frame_rate,bit_rate,sample_rate,channels \
  -of default=noprint_wrappers=1 "$URL" 2>&1 | sed 's/^/  /'

echo "--- delivered over ${SECS}s (ffmpeg decode) ---"
raw=$(ffmpeg -hide_banner -i "$URL" -t "$SECS" -f null - 2>&1 | tr '\r' '\n' | grep -E "frame=" | tail -1)
echo "  ${raw:-<no encoder stats>}"
frames=$(printf '%s' "$raw" | grep -oE "frame=[[:space:]]*[0-9]+" | grep -oE "[0-9]+" | tail -1)
if [ -n "${frames:-}" ]; then
  awk "BEGIN{printf \"  measured video: %.1f fps  (%d frames / %ds)\n\", $frames/$SECS, $frames, $SECS}"
fi
printf '%s\n' "$raw" | grep -oE "bitrate=[[:space:]]*[0-9.]+kbits/s" | sed 's/^/  measured /'

echo "--- audio ---"
acodec=$(ffprobe -v error -select_streams a:0 -show_entries stream=codec_name -of csv=p=0 "$URL" 2>/dev/null)
if [ -n "${acodec:-}" ]; then
  echo "  present: $(ffprobe -v error -select_streams a:0 \
      -show_entries stream=codec_name,sample_rate,channels,bit_rate \
      -of default=noprint_wrappers=1 "$URL" 2>/dev/null | tr '\n' ' ')"
else
  echo "  NONE (video-only stream)"
fi

echo "--- download throughput (newest segment, via curl timing) ---"
base="${URL%/*}"; q=""; case "$URL" in *\?*) q="?${URL#*\?}";; esac
seg=$(curl -fsS "$URL" 2>/dev/null | grep -vE '^#' | tail -1)
if [ -n "${seg:-}" ]; then
  read -r ttime tspeed tsize < <(curl -fsS -o /dev/null \
      -w '%{time_total} %{speed_download} %{size_download}\n' "$base/${seg%\?*}$q" 2>/dev/null)
  [ -n "${tsize:-}" ] && awk "BEGIN{printf \"  segment %s: %d KB, %.0f kbit/s (%.2fs)\n\", \"${seg%\?*}\", $tsize/1024, $tspeed*8/1000, $ttime}"
else
  echo "  (could not read playlist)"
fi
