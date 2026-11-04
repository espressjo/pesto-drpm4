#!/usr/bin/env bash
# Download the astrometry.net index files drpm4 needs for level 1 (astrometry).
#
# Based on get.sh. Series 5200 "LITE" (Tycho-2 + Gaia DR2), 48 sky tiles per scale.
# PESTO's field is 7.8' x 7.8'; astrometry.net wants quads between 10 % and 100 %
# of the field, which is what scales 0 to 3 provide:
#
#   file          quads        total size (48 tiles)
#   index-5200-*  2.0 - 2.8'   17.2 GB
#   index-5201-*  2.8 - 4.0'    9.5 GB
#   index-5202-*  4.0 - 5.6'    4.9 GB
#   index-5203-*  5.6 - 8.0'    2.5 GB
#   index-5204-*  8.0 - 11 '    1.2 GB   (wider than the field, optional)
#   index-5205-*, 5206-*        smaller files, wider quads: not useful for PESTO
#
# Usage:
#   ./download_indexes.sh                         # scales 0-3 into the configured index_dir
#   ./download_indexes.sh --scales 2-4 --dry-run  # show what would be downloaded
#   ./download_indexes.sh --dir /data/astrometry -y
#
# Options:
#   --dir DIR       destination (default: $ASTROMETRY_INDEX_DIR, else index_dir of
#                   $DRPM4_HOME/config/pesto.cfg, else /usr/local/astrometry/data)
#   --scales LIST   scales to download, e.g. 0-3 or 2,3,4 (default 0-3)
#   --tiles LIST    sky tiles 0-47, e.g. 0-47 (default). A subset is only useful for
#                   tests: PESTO observes the whole sky, so a real install needs all of them.
#   --dry-run       only print the plan and the size
#   -y, --yes       do not ask for confirmation
#   -h, --help      this text
#
# Safe to re-run: complete files are skipped, partial files are resumed.
set -euo pipefail

BASE_URL="https://portal.nersc.gov/project/cosmo/temp/dstn/index-5200/LITE"
# Size in MB of the 48 tiles of each scale (measured on the server).
declare -A TOTAL_MB=([0]=17200 [1]=9490 [2]=4860 [3]=2450 [4]=1220 [5]=610 [6]=310)
NTILES_TOTAL=48

DIR=""
SCALES="0-3"
TILES="0-47"
DRY_RUN=0
ASSUME_YES=0

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }
die() { echo "error: $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir)     DIR="${2:?--dir needs a value}"; shift 2 ;;
        --scales)  SCALES="${2:?--scales needs a value}"; shift 2 ;;
        --tiles)   TILES="${2:?--tiles needs a value}"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        -y|--yes)  ASSUME_YES=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option: $1 (see --help)" ;;
    esac
done

# "0-3,7" -> "0 1 2 3 7"
expand_list() {
    local list="$1" max="$2" part out=()
    IFS=',' read -ra parts <<< "$list"
    for part in "${parts[@]}"; do
        if [[ "$part" =~ ^([0-9]+)-([0-9]+)$ ]]; then
            mapfile -t -O "${#out[@]}" out < <(seq "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}")
        elif [[ "$part" =~ ^[0-9]+$ ]]; then
            out+=("$part")
        else
            die "bad list '$list' (use e.g. 0-3 or 2,3,4)"
        fi
    done
    local n
    for n in "${out[@]}"; do
        (( n <= max )) || die "'$n' is out of range (0-$max) in '$list'"
    done
    echo "${out[@]}"
}

default_dir() {
    if [[ -n "${ASTROMETRY_INDEX_DIR:-}" ]]; then echo "$ASTROMETRY_INDEX_DIR"; return; fi
    local cfg="${DRPM4_HOME:-/opt/drpm4}/config/pesto.cfg" d=""
    if [[ -f "$cfg" ]]; then
        d="$(sed -n '/^\[astrometry\]/,/^\[/{s/^index_dir[[:space:]]*=[[:space:]]*//p}' "$cfg" | head -1 \
             | sed 's/[[:space:]]*#.*//; s/[[:space:]]*$//')"
    fi
    echo "${d:-/usr/local/astrometry/data}"
}

[[ -n "$DIR" ]] || DIR="$(default_dir)"
scale_str="$(expand_list "$SCALES" 6)" || exit 1  # die() inside $(...) only leaves the subshell
tile_str="$(expand_list "$TILES" $((NTILES_TOTAL - 1)))" || exit 1
read -ra SCALE_LIST <<< "$scale_str"
read -ra TILE_LIST <<< "$tile_str"

# ---- downloader -------------------------------------------------------------
if command -v wget >/dev/null; then
    DOWNLOADER=wget
elif command -v curl >/dev/null; then
    DOWNLOADER=curl
else
    die "wget or curl is needed:  sudo apt install wget"
fi

remote_size() {  # remote_size <url>: size in bytes, empty if unknown
    if command -v curl >/dev/null; then
        curl -sIL --max-time 30 "$1" | tr -d '\r' | awk 'tolower($1)=="content-length:" {s=$2} END {print s}'
    else
        wget --spider -S -T 30 "$1" 2>&1 | tr -d '\r' | awk 'tolower($1)=="content-length:" {s=$2} END {print s}'
    fi
}

fetch() {  # fetch <url> <file>: resumes a partial file
    if [[ "$DOWNLOADER" == wget ]]; then
        wget -q --show-progress -c -T 60 -t 5 -O "$2" "$1"
    else
        curl -fL --retry 5 -C - -o "$2" "$1"
    fi
}

# ---- plan -------------------------------------------------------------------
est_mb=0
for s in "${SCALE_LIST[@]}"; do
    est_mb=$(( est_mb + TOTAL_MB[$s] * ${#TILE_LIST[@]} / NTILES_TOTAL ))
done
n_files=$(( ${#SCALE_LIST[@]} * ${#TILE_LIST[@]} ))

echo "astrometry.net index files, series 5200 (LITE)"
echo "  destination : $DIR"
echo "  scales      : ${SCALE_LIST[*]}   (index-52<scale>-<tile>.fits)"
echo "  tiles       : ${TILE_LIST[0]}..${TILE_LIST[-1]} (${#TILE_LIST[@]} tiles)"
echo "  files       : $n_files, about $(( est_mb / 1000 )).$(( est_mb % 1000 / 100 )) GB"
if [[ ${#TILE_LIST[@]} -ne $NTILES_TOTAL ]]; then
    echo "  note        : not all tiles: the solver will fail on fields outside them."
fi

if [[ -d "$DIR" ]]; then
    free_mb=$(( $(df -B1 --output=avail "$DIR" | tail -1) / 1000000 ))
    echo "  free space  : $(( free_mb / 1000 )).$(( free_mb % 1000 / 100 )) GB"
    if (( free_mb < est_mb * 105 / 100 )); then
        echo "warning: this may not fit in the free space (files already present are not counted)." >&2
    fi
fi
(( DRY_RUN )) && exit 0
if (( ! ASSUME_YES )); then
    read -r -p "Download? [y/N] " answer
    [[ "$answer" =~ ^[yY] ]] || { echo "cancelled"; exit 0; }
fi

# ---- destination (the folder may belong to root, as /usr/local/astrometry/data does) --
if [[ ! -d "$DIR" ]]; then
    mkdir -p "$DIR" 2>/dev/null || {
        command -v sudo >/dev/null || die "cannot create $DIR and sudo is not available"
        echo "creating $DIR with sudo and giving it to $(id -un)"
        sudo mkdir -p "$DIR" && sudo chown "$(id -un)" "$DIR"
    }
fi
if [[ ! -w "$DIR" ]]; then
    command -v sudo >/dev/null || die "$DIR is not writable and sudo is not available"
    echo "$DIR is not writable: giving it to $(id -un) with sudo"
    sudo chown "$(id -un)" "$DIR"
fi

# ---- download ---------------------------------------------------------------
failed=()
k=0
for s in "${SCALE_LIST[@]}"; do
    for t in "${TILE_LIST[@]}"; do
        k=$((k + 1))
        name="$(printf 'index-52%02d-%02d.fits' "$s" "$t")"
        url="$BASE_URL/$name"
        dest="$DIR/$name"
        want="$(remote_size "$url" || true)"
        if [[ -n "$want" && -f "$dest" && "$(stat -c %s "$dest")" == "$want" ]]; then
            echo "[$k/$n_files] $name: already complete"
            continue
        fi
        echo "[$k/$n_files] $name${want:+ ($(( want / 1000000 )) MB)}"
        if ! fetch "$url" "$dest"; then
            echo "  download failed" >&2; failed+=("$name"); continue
        fi
        if [[ -n "$want" && "$(stat -c %s "$dest")" != "$want" ]]; then
            echo "  size is $(stat -c %s "$dest"), expected $want" >&2; failed+=("$name"); continue
        fi
        if [[ "$(head -c 6 "$dest")" != "SIMPLE" ]]; then
            echo "  not a FITS file" >&2; failed+=("$name"); continue
        fi
    done
done

echo
if (( ${#failed[@]} )); then
    echo "${#failed[@]} file(s) failed: ${failed[*]}" >&2
    echo "Run the same command again to resume." >&2
    exit 1
fi
echo "Done: $n_files file(s) in $DIR"
if [[ "$DIR" != "$(default_dir)" ]]; then
    echo "Set  index_dir = $DIR  in the [astrometry] section of ${DRPM4_HOME:-/opt/drpm4}/config/pesto.cfg"
fi
