#!/usr/bin/env bash
# lib/metadata.sh -- tone CLI wrapper functions for M4B metadata tagging
# Sourced by stages/06-metadata.sh; do not execute directly.
# Requires: lib/core.sh sourced first (for logging), lib/ffmpeg.sh (get_duration)
# Requires: tone, jq

# Tag an M4B file with metadata, cover art, and chapters using tone CLI
# Args: $1 = m4b_file, $2 = work_dir, $3 = book_json (string), $4 = chapters_file (optional)
# Returns: 0 on success, non-zero on tone failure
tag_m4b() {
  local m4b_file="$1"
  local work_dir="$2"
  local book_json="$3"
  local chapters_file="${4:-}"

  # Extract metadata fields from book JSON
  local title author narrator genre description release_date
  local series_name series_position asin

  title=$(echo "$book_json" | jq -r '.title // empty')
  author=$(echo "$book_json" | jq -r '[.authors[]?.name] | join(", ") // empty')
  narrator=$(echo "$book_json" | jq -r '[.narrators[]?.name // .narrators[]?] | join(", ") // empty')
  genre=$(echo "$book_json" | jq -r '.genres[0]?.name // empty')
  description=$(echo "$book_json" | jq -r '.description // .summary // empty')
  series_name=$(echo "$book_json" | jq -r '.seriesPrimary?.name // empty')
  series_position=$(echo "$book_json" | jq -r '.seriesPrimary?.position // empty')
  asin=$(echo "$book_json" | jq -r '.asin // empty')

  # Extract recording date -- must be ISO 8601 YYYY-MM-DD format
  release_date=$(echo "$book_json" | jq -r '
    if .releaseDate != null and .releaseDate != "" then
      if (.releaseDate | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}")) then
        .releaseDate[:10]
      elif (.releaseDate | test("^[0-9]{4}$")) then
        .releaseDate + "-01-01"
      else
        empty
      end
    elif .copyright != null and .copyright != "" then
      (.copyright | capture("(?<y>[0-9]{4})").y // empty) + "-01-01"
    else
      empty
    end
  ')

  # Build tone tag argument array conditionally
  local tone_args=()

  [[ -n "$title" ]] && tone_args+=("--meta-title" "$title")
  [[ -n "$author" ]] && tone_args+=("--meta-artist" "$author")
  [[ -n "$narrator" ]] && tone_args+=("--meta-narrator" "$narrator")
  [[ -n "$genre" ]] && tone_args+=("--meta-genre" "$genre")
  [[ -n "$release_date" ]] && tone_args+=("--meta-recording-date" "$release_date")
  [[ -n "$description" ]] && tone_args+=("--meta-description" "$description")
  [[ -n "$series_name" ]] && tone_args+=("--meta-album" "$series_name")
  [[ -n "$series_position" ]] && tone_args+=("--meta-part" "$series_position")

  # Cover art -- only if file exists
  if [[ -f "$work_dir/cover.jpg" ]]; then
    tone_args+=("--meta-cover-file" "$work_dir/cover.jpg")
  fi

  # Chapters -- only if file provided and exists
  if [[ -n "$chapters_file" && -f "$chapters_file" ]]; then
    tone_args+=("--meta-chapters-file" "$chapters_file")
  fi

  # Store ASIN in custom field for future re-runs
  if [[ -n "$asin" ]]; then
    tone_args+=("--meta-additional-field=----:com.pilabor.tone:AUDIBLE_ASIN=$asin")
  fi

  if [[ ${#tone_args[@]} -eq 0 ]]; then
    log_warn "No metadata fields to tag -- skipping tone"
    return 0
  fi

  log_info "Tagging M4B with tone: ${#tone_args[@]} arguments"

  # Single invocation -- never tag on NFS (file should be in work_dir)
  if ! run tone tag "$m4b_file" "${tone_args[@]}"; then
    log_error "tone tag failed for $m4b_file"
    return 1
  fi

  log_info "M4B tagged successfully"
  return 0
}

# Verify metadata was written correctly using tone dump
# Args: $1 = m4b_file, $2 = expected_title
# Returns: 0 on match, 1 on mismatch (non-fatal)
verify_metadata() {
  local m4b_file="$1"
  local expected_title="$2"

  # Use --include-property to avoid binary data from embedded pictures
  local dump_output
  if ! dump_output=$(tone dump "$m4b_file" --format json \
    --include-property title \
    --include-property artist \
    --include-property narrator \
    --include-property chapters 2>/dev/null); then
    log_warn "tone dump failed -- skipping verification"
    return 1
  fi

  local actual_title chapter_count
  actual_title=$(echo "$dump_output" | jq -r '.meta.title // empty')
  chapter_count=$(echo "$dump_output" | jq -r '.meta.chapters | length // 0')

  if [[ -n "$expected_title" && "$actual_title" != "$expected_title" ]]; then
    log_warn "Title mismatch after tagging: expected='$expected_title', got='$actual_title'"
    return 1
  fi

  log_info "Metadata verified: title='$actual_title', chapters=$chapter_count"
  return 0
}

# Convert Audnexus chapters JSON to tone chapter file format
# Args: $1 = chapters_json (string), $2 = output_path (directory for chapters.txt)
# Returns: 0 on success, 1 on failure
convert_chapters_to_tone() {
  local chapters_json="$1"
  local output_path="$2"
  local chapters_file="$output_path/chapters.txt"

  local result
  result=$(echo "$chapters_json" | jq -r '
    .chapters[] |
    .startOffsetMs as $ms |
    ($ms / 3600000 | floor) as $h |
    (($ms % 3600000) / 60000 | floor) as $m |
    (($ms % 60000) / 1000 | floor) as $s |
    ($ms % 1000) as $frac |
    (.title | gsub("[\\p{Cc}]"; "") | ltrimstr(" ") | rtrimstr(" ")) as $title |
    "\($h | tostring | if length < 2 then "0" + . else . end):\($m | tostring | if length < 2 then "0" + . else . end):\($s | tostring | if length < 2 then "0" + . else . end).\($frac | tostring | if length < 3 then ("000" + .)[-3:] else . end) \($title)"
  ' 2>/dev/null)

  if [[ -z "$result" ]]; then
    log_warn "Failed to convert Audnexus chapters to tone format"
    return 1
  fi

  echo "$result" > "$chapters_file"
  local chapter_count
  chapter_count=$(wc -l < "$chapters_file" | tr -d ' ')
  log_info "Converted $chapter_count Audnexus chapters to tone format"
  echo "$chapters_file"
  return 0
}

# Generate companion text files alongside M4B
# Args: $1 = work_dir, $2 = book_json (string), $3 = narrator
generate_companions() {
  local work_dir="$1"
  local book_json="$2"
  local narrator="$3"

  # desc.txt -- description with HTML tags stripped, UTF-8 without BOM
  local description
  description=$(echo "$book_json" | jq -r '.description // .summary // empty')
  if [[ -n "$description" ]]; then
    echo "$description" | sed 's/<[^>]*>//g' > "$work_dir/desc.txt"
    log_info "Companion file created: desc.txt"
  else
    log_debug "No description available for desc.txt"
  fi

  # reader.txt -- narrator name, UTF-8 without BOM
  if [[ -n "$narrator" ]]; then
    echo "$narrator" > "$work_dir/reader.txt"
    log_info "Companion file created: reader.txt"
  else
    log_debug "No narrator available for reader.txt"
  fi
}
