# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-08-04

### Added

- Python 3.13 command-line applications for conversion, audit, library diff,
  and watch-folder processing.
- Resumable SQLite pipeline state, bounded batch scheduling, durable library
  reservations, and retry-safe metadata stages.
- Audible catalogue matching, duration-bounded Audnexus chapters, constrained
  optional AI candidate resolution, and franchise author override markers.
- Read-only library audits and source-to-library comparison across supported
  audio formats.

### Changed

- Replaced the legacy shell pipeline with typed Pydantic models, strict mypy,
  Ruff enforcement, Loguru logging, and generated package documentation.
- Routed watch claims, conversion stages, metadata, and organization through
  the same resumable pipeline contracts.

### Fixed

- Preserved embedded and multidisc chapter boundaries without accepting remote
  chapter data that contradicts local audio duration.
- Prevented weak catalogue matches, duplicate destination claims, stale state
  updates, and unsafe quarantine path moves.
- Corrected author parsing for `Author/Series/Book` trees and made critical
  audit findings and missing-library diffs return a nonzero status.
- Excluded local uv environments from source distributions so package builds
  cannot capture machine-specific Python symlinks.

[Unreleased]: https://github.com/rodaddy/audiobook-pipeline/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/rodaddy/audiobook-pipeline/compare/v0.5.0...v1.0.0
