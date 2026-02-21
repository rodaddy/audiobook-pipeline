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

# Generate a 16-char hex hash from source path + sorted MP3 file list
# This is the idempotency key for a book
generate_book_hash() {
  local source_path="$1"

  {
    echo "$source_path"
    find "$source_path" -type f -name "*.mp3" | sort
  } | shasum -a 256 | cut -d' ' -f1 | cut -c1-16
}
