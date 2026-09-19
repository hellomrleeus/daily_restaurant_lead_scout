#!/usr/bin/env bash
# ==============================================================================
# Daily Restaurant Lead Scout - Clean Installation Script
# Standardizes installation path to .agents/skills/ and strips Git metadata.
# ==============================================================================

set -e

REPO_URL="https://github.com/hellomrleeus/daily_restaurant_lead_scout.git"
SKILL_NAME="daily-restaurant-lead-scout"

TARGET_MODE="project"
TARGET_DIR=""

for arg in "$@"; do
  case $arg in
    --global|-g)
      TARGET_MODE="global"
      shift
      ;;
    --project|-p)
      TARGET_MODE="project"
      shift
      ;;
    --help|-h)
      echo "Usage: ./install.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --project, -p   Install cleanly into current project (./.agents/skills/$SKILL_NAME) [Default]"
      echo "  --global, -g    Install globally into user profile (~/.gemini/config/skills/$SKILL_NAME)"
      echo "  --help, -h      Show this help message"
      exit 0
      ;;
  esac
done

if [ "$TARGET_MODE" = "global" ]; then
  DEST_BASE="$HOME/.gemini/config/skills"
  DEST_DIR="$DEST_BASE/$SKILL_NAME"
  echo "[Install] Target: Global Skill Directory ($DEST_DIR)"
else
  DEST_BASE="./.agents/skills"
  DEST_DIR="$DEST_BASE/$SKILL_NAME"
  echo "[Install] Target: Project Workspace Standard Directory ($DEST_DIR)"
fi

# Clean up stray singular .agent directory if it was an empty or accidental artifact
if [ -d "./.agent" ] && [ ! -d "./.agent/skills/$SKILL_NAME" ]; then
  echo "[Clean] Removing stray empty .agent directory to maintain single .agents standard..."
  rm -rf "./.agent" 2>/dev/null || true
fi

# Prepare destination
mkdir -p "$DEST_BASE"

# Check if installing from local repo or remote URL
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$SCRIPT_DIR/SKILL.md" ] && [ -d "$SCRIPT_DIR/scripts" ]; then
  echo "[Install] Copying clean assets from current local repository..."
  rm -rf "$DEST_DIR"
  mkdir -p "$DEST_DIR"
  
  # Copy files cleanly excluding .git and temporary files
  rsync -a --exclude='.git' --exclude='__pycache__' --exclude='.cache' --exclude='output' "$SCRIPT_DIR/" "$DEST_DIR/"
else
  echo "[Install] Downloading clean release archive from GitHub (Zero Git footprints)..."
  rm -rf "$DEST_DIR"
  mkdir -p "$DEST_DIR"
  curl -sL "https://github.com/hellomrleeus/daily_restaurant_lead_scout/archive/refs/heads/main.tar.gz" | \
    tar -xz -C "$DEST_DIR" --strip-components=1
fi

# Ensure any stray .git is removed just in case
if [ -d "$DEST_DIR/.git" ]; then
  rm -rf "$DEST_DIR/.git"
fi

# Ensure executable permissions
chmod +x "$DEST_DIR/scripts/main.py" 2>/dev/null || true
if [ -f "$DEST_DIR/bin/restaurant-scout" ]; then
  chmod +x "$DEST_DIR/bin/restaurant-scout" 2>/dev/null || true
fi

# If project mode and .gitignore exists, ensure .agents/ is ignored to prevent dirty repo status
if [ "$TARGET_MODE" = "project" ] && [ -f ".gitignore" ]; then
  if ! grep -q "^\.agents" .gitignore 2>/dev/null; then
    echo "" >> .gitignore
    echo "# Antigravity and Agent Workspace Skills" >> .gitignore
    echo ".agents/" >> .gitignore
    echo "[Config] Added .agents/ to host project's .gitignore"
  fi
fi

echo "[Success] Daily Restaurant Lead Scout installed cleanly into:"
echo "          $DEST_DIR"
echo ""
echo "[Verification] Running configuration check..."
python3 "$DEST_DIR/scripts/main.py" --check-config
