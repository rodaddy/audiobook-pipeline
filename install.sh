#!/usr/bin/env bash
# install.sh -- Audiobook Pipeline Dependency Installer
# Checks for and optionally installs all required dependencies.
# Works on macOS (Homebrew) and Linux (apt).

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Resolve script location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/config.env"
CONFIG_EXAMPLE="${SCRIPT_DIR}/config.env.example"

# Banner
print_banner() {
  echo -e "${BLUE}${BOLD}"
  cat <<'BANNER'
╔═══════════════════════════════════════════════════════╗
║     Audiobook Pipeline - Dependency Installer         ║
╚═══════════════════════════════════════════════════════╝
BANNER
  echo -e "${NC}"
}

# Detect OS
detect_os() {
  if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
    PKG_MANAGER="brew"
  elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    if command -v apt-get &>/dev/null; then
      OS="linux"
      PKG_MANAGER="apt"
    else
      echo -e "${RED}Error: Only Debian/Ubuntu Linux is supported (apt required)${NC}"
      exit 1
    fi
  else
    echo -e "${RED}Error: Unsupported OS: $OSTYPE${NC}"
    exit 1
  fi
  echo -e "${BOLD}Detected OS:${NC} $OS ($PKG_MANAGER)\n"
}

# Check if a command exists
command_exists() {
  command -v "$1" &>/dev/null
}

# Version check helpers
get_version() {
  local cmd="$1"
  case "$cmd" in
    ffmpeg)
      ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}'
      ;;
    jq)
      jq --version 2>/dev/null | sed 's/jq-//'
      ;;
    curl)
      curl --version 2>/dev/null | head -1 | awk '{print $2}'
      ;;
    bc)
      bc --version 2>/dev/null | head -1 | awk '{print $2}'
      ;;
    xxd)
      xxd -v 2>&1 | head -1 | awk '{print $NF}'
      ;;
    tone)
      tone --version 2>/dev/null || echo "unknown"
      ;;
    *)
      echo "unknown"
      ;;
  esac
}

# Check ffmpeg encoders
check_ffmpeg_encoders() {
  if ! command_exists ffmpeg; then
    return 1
  fi

  local has_aac=false
  local has_aac_at=false

  if ffmpeg -encoders 2>/dev/null | grep -q "aac"; then
    has_aac=true
  fi

  if [[ "$OS" == "macos" ]] && ffmpeg -encoders 2>/dev/null | grep -q "aac_at"; then
    has_aac_at=true
  fi

  if [[ "$has_aac" == "false" ]]; then
    echo -e "  ${YELLOW}Warning: ffmpeg missing AAC encoder${NC}"
    return 1
  fi

  if [[ "$OS" == "macos" && "$has_aac_at" == "true" ]]; then
    echo -e "  ${GREEN}✓${NC} ffmpeg has hardware AAC encoder (aac_at)"
  elif [[ "$OS" == "macos" ]]; then
    echo -e "  ${YELLOW}Note: ffmpeg missing hardware AAC encoder (aac_at), will use software${NC}"
  fi

  return 0
}

# Install tone from GitHub releases
install_tone() {
  echo -e "\n${BOLD}Installing tone...${NC}"

  # Detect architecture
  local arch
  case "$(uname -m)" in
    x86_64)
      arch="x64"
      ;;
    arm64|aarch64)
      arch="arm64"
      ;;
    *)
      echo -e "${RED}Unsupported architecture: $(uname -m)${NC}"
      return 1
      ;;
  esac

  local platform
  if [[ "$OS" == "macos" ]]; then
    platform="osx-${arch}"
  else
    platform="linux-${arch}"
  fi

  echo "  Fetching latest tone release for ${platform}..."

  # Get latest release info
  local release_json
  release_json=$(curl -fsSL https://api.github.com/repos/sandreas/tone/releases/latest)

  # Find download URL for our platform
  local download_url
  download_url=$(echo "$release_json" | jq -r ".assets[] | select(.name | contains(\"${platform}\")) | .browser_download_url" | head -1)

  if [[ -z "$download_url" || "$download_url" == "null" ]]; then
    echo -e "${RED}Error: Could not find tone binary for ${platform}${NC}"
    return 1
  fi

  echo "  Downloading from: $download_url"

  # Create temp directory
  local temp_dir
  temp_dir=$(mktemp -d)
  trap 'rm -rf "$temp_dir"' EXIT

  # Download and extract
  local archive="${temp_dir}/tone.tar.gz"
  if ! curl -fsSL -o "$archive" "$download_url"; then
    echo -e "${RED}Error: Failed to download tone${NC}"
    return 1
  fi

  tar -xzf "$archive" -C "$temp_dir"

  # Find the tone binary in extracted files
  local tone_binary
  tone_binary=$(find "$temp_dir" -name "tone" -type f | head -1)

  if [[ -z "$tone_binary" ]]; then
    echo -e "${RED}Error: tone binary not found in archive${NC}"
    return 1
  fi

  # Install to /usr/local/bin
  echo "  Installing to /usr/local/bin/tone..."
  if [[ -w /usr/local/bin ]]; then
    cp "$tone_binary" /usr/local/bin/tone
    chmod +x /usr/local/bin/tone
  else
    sudo cp "$tone_binary" /usr/local/bin/tone
    sudo chmod +x /usr/local/bin/tone
  fi

  echo -e "${GREEN}✓ tone installed successfully${NC}"
  return 0
}

# Main dependency check
MISSING_DEPS=()
MISSING_PACKAGES=()

check_dependencies() {
  echo -e "${BOLD}Checking dependencies...${NC}\n"

  # Standard tools
  declare -A deps=(
    [ffmpeg]="Audio conversion (required)"
    [jq]="JSON processing (required)"
    [curl]="API requests (required)"
    [bc]="Duration math (required)"
    [xxd]="JPEG validation (required)"
    [tone]="M4B chapter/metadata tagging (required)"
  )

  for cmd in "${!deps[@]}"; do
    local desc="${deps[$cmd]}"
    if command_exists "$cmd"; then
      local version
      version=$(get_version "$cmd")
      echo -e "${GREEN}✓${NC} ${BOLD}$cmd${NC} -- $desc"
      echo -e "  Version: $version"

      # Extra checks for ffmpeg
      if [[ "$cmd" == "ffmpeg" ]]; then
        check_ffmpeg_encoders || true
      fi
    else
      echo -e "${RED}✗${NC} ${BOLD}$cmd${NC} -- $desc"
      echo -e "  ${RED}NOT FOUND${NC}"
      MISSING_DEPS+=("$cmd")

      # Map to package names
      if [[ "$cmd" != "tone" ]]; then
        MISSING_PACKAGES+=("$cmd")
      fi
    fi
    echo
  done
}

# Install missing dependencies
install_dependencies() {
  if [[ ${#MISSING_DEPS[@]} -eq 0 ]]; then
    echo -e "${GREEN}${BOLD}✓ All dependencies satisfied!${NC}\n"
    return 0
  fi

  echo -e "${YELLOW}${BOLD}Missing dependencies:${NC}"
  for dep in "${MISSING_DEPS[@]}"; do
    echo -e "  - $dep"
  done
  echo

  # Ask user
  echo -e "${BOLD}Install missing dependencies automatically? (y/n)${NC} "
  read -r response

  if [[ ! "$response" =~ ^[Yy]$ ]]; then
    echo
    print_manual_instructions
    exit 1
  fi

  echo

  # Install standard packages
  if [[ ${#MISSING_PACKAGES[@]} -gt 0 ]]; then
    echo -e "${BOLD}Installing packages via ${PKG_MANAGER}...${NC}"

    if [[ "$PKG_MANAGER" == "brew" ]]; then
      brew install "${MISSING_PACKAGES[@]}"
    elif [[ "$PKG_MANAGER" == "apt" ]]; then
      sudo apt-get update
      sudo apt-get install -y "${MISSING_PACKAGES[@]}"
    fi
  fi

  # Install tone if missing
  if [[ " ${MISSING_DEPS[*]} " =~ " tone " ]]; then
    install_tone || {
      echo -e "${RED}Failed to install tone${NC}"
      exit 1
    }
  fi

  echo
  verify_installation
}

# Verify all dependencies after installation
verify_installation() {
  echo -e "${BOLD}Verifying installation...${NC}\n"

  local all_good=true

  for dep in "${MISSING_DEPS[@]}"; do
    if command_exists "$dep"; then
      local version
      version=$(get_version "$dep")
      echo -e "${GREEN}✓${NC} $dep installed successfully (version: $version)"
    else
      echo -e "${RED}✗${NC} $dep still missing"
      all_good=false
    fi
  done

  echo

  if [[ "$all_good" == "true" ]]; then
    echo -e "${GREEN}${BOLD}✓ All dependencies installed successfully!${NC}\n"
  else
    echo -e "${RED}${BOLD}Some dependencies failed to install${NC}"
    print_manual_instructions
    exit 1
  fi
}

# Print manual installation instructions
print_manual_instructions() {
  echo -e "${BOLD}Manual Installation Instructions:${NC}\n"

  if [[ "$OS" == "macos" ]]; then
    cat <<'INSTRUCTIONS'
macOS (Homebrew):
  brew install ffmpeg jq curl

  tone (manual):
    1. Visit: https://github.com/sandreas/tone/releases
    2. Download the latest osx-x64 or osx-arm64 release
    3. Extract and move to /usr/local/bin:
       tar -xzf tone-*.tar.gz
       cp tone /usr/local/bin/
       chmod +x /usr/local/bin/tone
INSTRUCTIONS
  else
    cat <<'INSTRUCTIONS'
Linux (Debian/Ubuntu):
  sudo apt-get update
  sudo apt-get install -y ffmpeg jq curl bc xxd

  tone (manual):
    1. Visit: https://github.com/sandreas/tone/releases
    2. Download the latest linux-x64 or linux-arm64 release
    3. Extract and move to /usr/local/bin:
       tar -xzf tone-*.tar.gz
       sudo cp tone /usr/local/bin/
       sudo chmod +x /usr/local/bin/tone
INSTRUCTIONS
  fi
  echo
}

# Configure the pipeline
configure_pipeline() {
  if [[ -f "$CONFIG_FILE" ]]; then
    echo -e "${GREEN}✓ Configuration file exists:${NC} config.env"
  else
    if [[ -f "$CONFIG_EXAMPLE" ]]; then
      echo -e "${YELLOW}Configuration file not found${NC}"
      echo -e "${BOLD}Copy config.env.example to config.env? (y/n)${NC} "
      read -r response

      if [[ "$response" =~ ^[Yy]$ ]]; then
        cp "$CONFIG_EXAMPLE" "$CONFIG_FILE"
        echo -e "${GREEN}✓ Created config.env from example${NC}"
        echo -e "  ${YELLOW}Edit config.env to customize paths and settings${NC}"
      else
        echo -e "  ${YELLOW}Skipped. Create config.env manually before running the pipeline.${NC}"
      fi
    else
      echo -e "${RED}✗ config.env.example not found${NC}"
    fi
  fi
  echo
}

# Print next steps
print_next_steps() {
  echo -e "${BOLD}${GREEN}Installation complete!${NC}\n"

  cat <<'NEXT_STEPS'
Next steps:

1. Configure the pipeline:
   Edit config.env and adjust paths for your system

2. Create required directories:
   sudo mkdir -p /var/lib/audiobook-pipeline/{work,manifests,output,archive,locks}
   sudo mkdir -p /var/log/audiobook-pipeline
   sudo chown -R $USER:$USER /var/lib/audiobook-pipeline /var/log/audiobook-pipeline

3. Test the pipeline:
   ./bin/audiobook-convert --help
   ./bin/audiobook-convert --dry-run /path/to/audiobook/

4. Set up automation (optional):
   See docs/automation.md for cron and webhook setup

For more information, see README.md
NEXT_STEPS
}

# Main
main() {
  print_banner
  detect_os
  check_dependencies
  install_dependencies
  configure_pipeline
  print_next_steps
}

main
