# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, report security vulnerabilities via email to:

**rico@rodaddy.live**

You should receive an acknowledgment within 48 hours. We will follow up with a timeline for a fix after investigating the issue.

## What Counts as a Security Issue

Security issues in this project typically involve:

- **Credential exposure** -- API keys, tokens, or credentials leaked through logs or error messages
- **Command injection** -- Unsafe handling of filenames or metadata that could allow arbitrary command execution
- **Path traversal** -- Improper validation of input paths allowing access outside intended directories
- **Unsafe temporary file handling** -- Race conditions or predictable temporary file paths

## What Is NOT a Security Issue

The following should be reported as regular bugs or feature requests:

- Feature requests
- Bugs that do not have security implications (crashes, incorrect output, etc.)
- Performance issues
- Compatibility issues with specific tools or platforms

## What to Expect

1. **Acknowledgment** within 48 hours of your report
2. **Initial assessment** within 1 week
3. **Fix timeline** provided based on severity and complexity
4. **Credit** in the security advisory if you wish (please let us know your preference)

We take security seriously and appreciate responsible disclosure. Thank you for helping keep the audiobook-pipeline and its users safe.
