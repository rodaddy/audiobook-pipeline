"""Stage registry -- maps Stage enum values to run functions.

Pipeline order: validate -> concat -> convert -> asin -> metadata -> organize -> cleanup

Stages:
    validate -- Verify source directory of audio files and prepare conversion
                metadata. Discovers audio files (excludes .m4b since we convert
                TO m4b), validates with ffprobe, natural-sorts by filename,
                checks disk space, detects target bitrate from first file,
                sums total duration, creates work_dir, writes audio_files.txt
                with absolute paths. Updates manifest with target_bitrate,
                file_count, total_duration. Supports dry_run.
    concat -- Generate ffmpeg input files from validated audio list. Creates
              files.txt (concat demuxer format with escaped paths) and
              metadata.txt (FFMETADATA1 chapter markers with cumulative
              timestamps). For single-file books, writes metadata header only.
              Updates manifest with chapter_count. Handles path escaping for
              single quotes in filenames. Uses ffprobe to get duration for
              each audio file.
    convert -- Wraps ffmpeg MP3-to-M4B conversion as subprocess. Auto-detects
               aac_at (Apple AudioToolbox) encoder, falls back to aac. Validates
               output codec, format, and chapter count (for multi-file books).
               Supports dry-run mode and thread control via kwargs. Updates
               manifest with output_file, codec, and bitrate.
    asin -- Resolve audiobook metadata via Audible catalog search, AI
            disambiguation, embedded tags, and path parsing. Searches Audible
            with multiple query strategies, scores with fuzzy matching, uses
            AI when scores are low or sources conflict. Writes parsed_author,
            parsed_title, parsed_series, parsed_position, parsed_asin, and
            cover_url to manifest. Runs before metadata tagging so files are
            tagged before landing in the library. Can run in dry-run mode.
    metadata -- Tag M4B files with artist, album_artist, album, title, genre,
                ASIN, and cover art via ffmpeg -c copy (no re-encode). Preserves
                chapters with -map_chapters 0. Downloads cover art from Audible
                and embeds as attached_pic. Reads parsed metadata from manifest
                (set by ASIN stage). Reads output file from convert stage
                (work_dir). Album is "Series, Book N" when series exists,
                otherwise title. Writes to temp file with atomic replace.
                Cover download failure is non-fatal. Supports dry-run mode.
    organize -- Pure file-mover. Reads pre-resolved metadata from manifest
                (set by ASIN stage) and tagged file from metadata stage output.
                Builds Plex-compatible destination path, copies or moves file
                to NFS library. Accepts optional LibraryIndex for O(1) batch
                lookups and reorganize flag for in-place library cleanup.
                Handles cross-source dedup. Supports batch and single-file modes.
    cleanup -- Remove temporary work directory after conversion.
"""

from ..models import Stage


def get_stage_runner(stage: Stage):
    """Return the run function for a given stage.

    Raises NotImplementedError for stages not yet implemented.
    """
    if stage == Stage.VALIDATE:
        from .validate import run as validate_run

        return validate_run

    if stage == Stage.CONCAT:
        from .concat import run as concat_run

        return concat_run

    if stage == Stage.CONVERT:
        from .convert import run as convert_run

        return convert_run

    if stage == Stage.ASIN:
        from .asin import run as asin_run

        return asin_run

    if stage == Stage.METADATA:
        from .metadata import run as metadata_run

        return metadata_run

    if stage == Stage.ORGANIZE:
        from .organize import run as organize_run

        return organize_run

    if stage == Stage.CLEANUP:
        from .cleanup import run as cleanup_run

        return cleanup_run

    # Raise clear error for unimplemented stages
    raise NotImplementedError(
        f"Stage '{stage.value}' is not yet implemented. "
        f"Only 'validate', 'concat', 'convert', 'organize', and 'cleanup' modes are currently supported."
    )
