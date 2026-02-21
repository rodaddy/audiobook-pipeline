#!/usr/bin/env bash
# lib/sanitize.sh -- Filename sanitization and book hash generation
# Sourced by bin/audiobook-convert; do not execute directly.

# Sanitize a filename component (not a full path)
# Replaces unsafe chars with underscores, removes leading dots,
# collapses repeated underscores, truncates to 255 bytes preserving extension
sanitize_filename() {
  local filename="$1"

  local sanitized
  sanitized=$(echo "$filename" | sed -E '
    s/[\/\\:"*?<>|;]+/_/g;
    s/^[._]+//;
    s/[._]+$//;
    s/__+/_/g;
  ')

  # Truncate to 255 bytes while preserving extension
  if [[ ${#sanitized} -gt 255 ]]; then
    local ext="${sanitized##*.}"
    local base="${sanitized%.*}"

    if [[ "$ext" != "$sanitized" ]]; then
      local max_base_len=$((255 - ${#ext} - 1))
      sanitized="${base:0:$max_base_len}.$ext"
    else
      sanitized="${sanitized:0:255}"
    fi
  fi

  echo "$sanitized"
}

# Sanitize a chapter title (more permissive -- uses spaces instead of underscores)
sanitize_chapter_title() {
  local title="$1"

  echo "$title" | sed -E '
    s/[\/\\:"*?<>|;]+/ /g;
    s/  +/ /g;
    s/^ +//;
    s/ +$//;
  '
}

# Generate a 16-char hex hash for idempotency
# For directories: hash source path + sorted audio file list
# For files (M4B): hash file path + file size
generate_book_hash() {
  local source_path="$1"

  if [[ -f "$source_path" ]]; then
    # Single file input (M4B enrichment)
    local file_size
    file_size=$(stat -f%z "$source_path" 2>/dev/null || stat -c%s "$source_path" 2>/dev/null)
    [[ -z "$file_size" ]] && { echo "Cannot stat file: $source_path" >&2; return 1; }
    printf '%s\n%s\n' "$source_path" "$file_size" | shasum -a 256 | cut -d' ' -f1 | cut -c1-16
  else
    # Directory input (conversion)
    {
      echo "$source_path"
      find "$source_path" -type f \( -iname "*.mp3" -o -iname "*.flac" -o -iname "*.ogg" -o -iname "*.m4a" -o -iname "*.wma" \) | sort -V
    } | shasum -a 256 | cut -d' ' -f1 | cut -c1-16
  fi
}
