#!/usr/bin/env sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"
APP_NAME="codex-session-toolkit"
PACKAGE_NAME="codex_session_toolkit"
VENV_DIR="${VENV_DIR:-$PROJECT_ROOT/.venv}"
EDITABLE=0
FORCE=0
PYTHON_BIN="${PYTHON_BIN:-}"

usage() {
  cat <<'EOF'
Usage: ./install.sh [--editable] [--force] [--python <python-bin>]

Create or refresh an isolated local virtual environment under ./.venv.
The installer keeps package changes inside the project and does not modify
your base Python environment.

Options:
  --editable         Install in editable mode for local development
  --force            Recreate the local .venv before installing
  --python <bin>     Use a specific Python executable
  --help             Show this help text
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --editable)
      EDITABLE=1
      shift
      ;;
    --force)
      FORCE=1
      shift
      ;;
    --python)
      if [ "$#" -lt 2 ]; then
        echo "Error: --python requires a value." >&2
        exit 2
      fi
      PYTHON_BIN="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

resolve_python() {
  if [ -n "$PYTHON_BIN" ]; then
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  else
    echo "Error: python3/python not found in PATH." >&2
    exit 127
  fi
}

venv_uses_system_site_packages() {
  cfg_file="$VENV_DIR/pyvenv.cfg"
  [ -f "$cfg_file" ] || return 1
  grep -Eiq '^[[:space:]]*include-system-site-packages[[:space:]]*=[[:space:]]*true[[:space:]]*$' "$cfg_file"
}

site_packages_dir() {
  "$VENV_PYTHON" -c 'import sysconfig; print(sysconfig.get_path("purelib"))'
}

install_local_package() {
  SITE_PACKAGES="$(site_packages_dir)"
  PTH_FILE="$SITE_PACKAGES/${PACKAGE_NAME}-local.pth"
  INSTALLED_PACKAGE_DIR="$SITE_PACKAGES/$PACKAGE_NAME"

  mkdir -p "$SITE_PACKAGES"
  rm -f "$PTH_FILE"
  rm -rf "$INSTALLED_PACKAGE_DIR"

  if [ "$EDITABLE" -eq 1 ]; then
    printf '%s\n' "$PROJECT_ROOT/src" > "$PTH_FILE"
  else
    cp -R "$PROJECT_ROOT/src/$PACKAGE_NAME" "$INSTALLED_PACKAGE_DIR"
  fi
}

install_console_wrapper() {
  WRAPPER="$VENV_DIR/bin/$APP_NAME"
  cat > "$WRAPPER" <<EOF
#!/usr/bin/env sh
exec "$VENV_PYTHON" -m $PACKAGE_NAME "\$@"
EOF
  chmod +x "$WRAPPER"
}

resolve_python

if [ "$FORCE" -eq 1 ] && [ -d "$VENV_DIR" ]; then
  rm -rf "$VENV_DIR"
fi

if [ -d "$VENV_DIR" ] && venv_uses_system_site_packages; then
  echo "Existing venv is not isolated (system site packages are enabled)." >&2
  echo "Recreating $VENV_DIR as an isolated local environment..." >&2
  rm -rf "$VENV_DIR"
fi

echo "============================================="
echo " Codex Session Toolkit - Installer (Unix)"
echo "============================================="
echo "Project:   $PROJECT_ROOT"
echo "Python:    $PYTHON_BIN"
echo "Venv:      $VENV_DIR"
echo "Isolation: enabled"
if [ "$EDITABLE" -eq 1 ]; then
  echo "Mode:      editable"
else
  echo "Mode:      standard"
fi

"$PYTHON_BIN" -m venv "$VENV_DIR"
VENV_PYTHON="$VENV_DIR/bin/python"

install_local_package
install_console_wrapper

for chmod_target in \
  "$PROJECT_ROOT/codex-session-toolkit" \
  "$PROJECT_ROOT/codex-session-toolkit.command" \
  "$PROJECT_ROOT/install.sh" \
  "$PROJECT_ROOT/install.command" \
  "$PROJECT_ROOT/release.sh"
do
  if [ -e "$chmod_target" ]; then
    chmod +x "$chmod_target"
  fi
done

echo ""
echo "Install complete."
echo "Run now:"
echo "  ./codex-session-toolkit"
echo "Version:"
echo "  $VENV_DIR/bin/$APP_NAME --version"
