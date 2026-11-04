#!/usr/bin/env bash
# Create the drpm4 runtime directory tree (default: /opt/drpm4).
#
#   ./install.sh                  # create /opt/drpm4 (uses sudo if needed)
#   DRPM4_HOME=/data/drpm4 ./install.sh   # use another location
#
# Safe to run more than once: it only creates missing directories and never
# deletes or overwrites anything. This script does not install the python
# package, see README.md.
set -euo pipefail

DRPM4_HOME="${DRPM4_HOME:-/opt/drpm4}"
SUBDIRS=(config db references documentation log)

# Prerequisites (Ubuntu 24.04). Missing ones are reported, never installed.
check_prerequisites() {
    local missing=0
    if ! python3 -c 'import sys, venv, ensurepip; assert sys.version_info >= (3, 12)' 2>/dev/null; then
        echo "warning: python3 >= 3.12 with venv is needed:  sudo apt install python3 python3-venv" >&2; missing=1
    fi
    if ! command -v pdflatex >/dev/null; then
        echo "warning: pdflatex is missing, the PDF night report cannot be built:" >&2
        echo "         sudo apt install texlive-latex-base texlive-latex-recommended texlive-fonts-recommended" >&2; missing=1
    else
        local sty
        for sty in geometry graphicx longtable booktabs float lmodern; do
            if ! kpsewhich "$sty.sty" >/dev/null 2>&1; then
                echo "warning: LaTeX package '$sty' not found:  sudo apt install texlive-latex-recommended texlive-fonts-recommended" >&2
                missing=1
            fi
        done
    fi
    return 0
}
check_prerequisites

# The tree belongs to the person who ran the script, even through sudo.
OWNER="${SUDO_USER:-$(id -un)}"
GROUP="$(id -gn "$OWNER")"

SUDO=""
parent="$(dirname "$DRPM4_HOME")"
if [[ ! -w "$parent" && ! -d "$DRPM4_HOME" ]] || [[ -d "$DRPM4_HOME" && ! -w "$DRPM4_HOME" ]]; then
    if [[ $EUID -ne 0 ]]; then
        command -v sudo >/dev/null || { echo "error: cannot write to $parent and sudo is not available" >&2; exit 1; }
        SUDO="sudo"
    fi
fi

echo "Installing drpm4 directory tree in $DRPM4_HOME (owner: $OWNER:$GROUP)"
for d in "${SUBDIRS[@]}"; do
    $SUDO mkdir -p "$DRPM4_HOME/$d"
done
$SUDO chown "$OWNER:$GROUP" "$DRPM4_HOME" "${SUBDIRS[@]/#/$DRPM4_HOME/}"
$SUDO chmod 755 "$DRPM4_HOME" "${SUBDIRS[@]/#/$DRPM4_HOME/}"

# Seed files. Existing copies are never replaced: header.csv and pesto.cfg are
# yours to edit once installed.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
seed() {  # seed <source> <destination>
    if [[ -f "$1" && ! -e "$2" ]]; then
        $SUDO cp "$1" "$2"
        $SUDO chown "$OWNER:$GROUP" "$2" 2>/dev/null || true
        echo "installed $2"
    fi
}
seed "$here/drpm4/resources/header.csv" "$DRPM4_HOME/references/header.csv"
seed "$here/drpm4/resources/pesto.cfg"  "$DRPM4_HOME/config/pesto.cfg"
seed "$here/spec.md"                    "$DRPM4_HOME/documentation/spec.md"

echo "Done:"
ls -ld "$DRPM4_HOME" "${SUBDIRS[@]/#/$DRPM4_HOME/}"
