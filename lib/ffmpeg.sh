#!/usr/bin/env bash
# lib/ffmpeg.sh -- FFprobe wrapper functions for audio file inspection
# Sourced by bin/audiobook-convert; do not execute directly.
# Requires: lib/core.sh sourced first (for log_error, log_debug)

# Get duration in seconds (float)
get_duration() {
  local file="$1"
  ffprobe -v error -show_entries format=duration \
    -of default=noprint_wrappers=1:nokey=1 "$file"
}

# Get bitrate in bits/sec (integer)
get_bitrate() {
  local file="$1"
  ffprobe -v error -show_entries format=bit_rate \
    -of default=noprint_wrappers=1:nokey=1 "$file"
}

# Get codec name (e.g. mp3, aac)
get_codec() {
  local file="$1"
  ffprobe -v error -select_streams a:0 \
    -show_entries stream=codec_name \
    -of default=noprint_wrappers=1:nokey=1 "$file"
}

# Get channel count (1=mono, 2=stereo)
get_channels() {
  local file="$1"
  ffprobe -v error -select_streams a:0 \
    -show_entries stream=channels \
    -of default=noprint_wrappers=1:nokey=1 "$file"
}

# Get sample rate in Hz
get_sample_rate() {
  local file="$1"
  ffprobe -v error -select_streams a:0 \
    -show_entries stream=sample_rate \
    -of default=noprint_wrappers=1:nokey=1 "$file"
}

# Validate that a file is a readable audio file with at least one audio stream
# Returns 0 on success, 1 on failure
validate_audio_file() {
  local file="$1"

  if [[ ! -f "$file" ]]; then
    log_error "Not found: $file"
    return 1
  fi

  if ! ffprobe -v error "$file" >/dev/null 2>&1; then
    log_error "Invalid audio file: $file"
    return 1
  fi

  local codec
  codec=$(get_codec "$file")
  if [[ -z "$codec" ]]; then
    log_error "No audio stream found: $file"
    return 1
  fi

  log_debug "Valid audio: $file (codec=$codec)"
  return 0
}

# Convert float seconds to HH:MM:SS timestamp
duration_to_timestamp() {
  local sec="$1"
  local h m s
  h=$(echo "$sec / 3600" | bc)
  m=$(echo "($sec % 3600) / 60" | bc)
  s=$(echo "$sec % 60" | bc)
  printf "%02d:%02d:%02d\n" "$h" "$m" "$s"
}

# Count embedded chapters in an audio file
# Returns 0 if no chapters found
count_chapters() {
  local file="$1"
  local count
  count=$(ffprobe -v error -show_chapters "$file" 2>/dev/null | grep -c "^\[CHAPTER\]" || true)
  echo "${count:-0}"
}
