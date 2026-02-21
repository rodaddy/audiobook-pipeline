---
name: Bug Report
about: Report a bug or unexpected behavior
title: "[Bug] "
labels: bug
assignees: ''
---

## Description

A clear description of what the bug is.

## Steps to Reproduce

1. Run `bin/audiobook-convert ...`
2. ...
3. See error

## Expected Behavior

What you expected to happen.

## Actual Behavior

What actually happened. Include error messages or unexpected output.

## Environment

- **OS:** (e.g., macOS 15.3, Ubuntu 24.04)
- **Bash version:** (`bash --version`)
- **ffmpeg version:** (`ffmpeg -version | head -1`)
- **tone version:** (`tone --version`)
- **jq version:** (`jq --version`)
- **Pipeline version:** (`cat VERSION`)

## Configuration

- **METADATA_SOURCE:** (audible / audnexus)
- **AUDIBLE_REGION:** (com / co.uk / etc.)
- **Pipeline mode:** (convert / enrich / metadata / organize)

## Logs

Run with `--verbose` and paste relevant log output:

```
bin/audiobook-convert --verbose /path/to/input/
```

<details>
<summary>Full log output</summary>

```
Paste verbose log here
```

</details>
