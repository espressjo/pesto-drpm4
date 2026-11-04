# drpm4

Data reduction pipeline for PESTO (Observatoire du Mont Mégantic). Current scope: level 0 reduction (header standardization) and the night report. No database interaction and no astrometry yet. See `spec.md` for the design.

## Installation

Requires Python >= 3.12, and `pdflatex` (TeX Live) for the PDF night report.

### 1. Runtime directories

```bash
./install.sh
```

This creates the following tree (it asks for `sudo` only if `/opt` is not writable by you) and gives it to your user:

```
/opt/drpm4/
├── config/         # pesto.cfg (timezone, site, ...)
├── db/             # database (not used yet)
├── references/     # header.csv, the standard header definition
├── documentation/  # copy of spec.md
└── log/            # drpm4-YYYYMMDD.log, one per day
```

To use another location: `DRPM4_HOME=/some/path ./install.sh`. The script is safe to re-run, it never deletes or overwrites anything. `references/header.csv` and `config/pesto.cfg` are copied from the repository once and are yours to edit afterwards; to start over, delete the file and re-run `./install.sh`.

### 2. Python package

On Ubuntu 24.04 `pip` refuses to install into the system Python ("externally-managed-environment"), so always use a virtual environment (or conda). If `python3 -m venv` fails, run `sudo apt install python3-venv`. The PDF report needs LaTeX: `sudo apt install texlive-latex-base texlive-latex-recommended texlive-fonts-recommended`. `./install.sh` checks these and prints what is missing.

```bash
python3 -m venv ~/.venvs/drpm4
source ~/.venvs/drpm4/bin/activate
pip install --upgrade pip
pip install .            # regular install, use this on the observatory machine
# or, for development (edits take effect immediately):
pip install -e .
```

Dependencies (numpy, matplotlib, astropy, scipy, photutils) are installed automatically. `pip install -e .` always prints a setuptools notice ("Editable installation ... be careful with folders in your working directory with the same name as your package"); it is informational. Run `pip install` from outside the repository folder if you want to avoid it. With a regular install, run `pip install .` again after each code change. Check the install with:

```bash
drpm4 --version
```

### 3. Astrometry index files (level 1)

Level 1 needs the astrometry.net index files. On a fresh machine, download them with:

```bash
./download_indexes.sh --dry-run     # plan and size, downloads nothing
./download_indexes.sh               # scales 0-3 into the configured index_dir, asks first
```

It fetches the series 5200 "LITE" files (Tycho-2 + Gaia DR2, 48 sky tiles per scale) from the astrometry.net mirror, based on `get.sh`. PESTO's field is 7.8' wide and astrometry.net wants quads between 10 % and 100 % of the field, so the default is scales 0 to 3:

| Files | Quads | Size (48 tiles) |
|---|---|---|
| `index-5200-*` | 2.0 - 2.8' | 17.2 GB |
| `index-5201-*` | 2.8 - 4.0' | 9.5 GB |
| `index-5202-*` | 4.0 - 5.6' | 4.9 GB |
| `index-5203-*` | 5.6 - 8.0' | 2.5 GB |
| `index-5204-*` | 8.0 - 11' | 1.2 GB (optional) |

The default is about 34 GB. For less, use `--scales 2-4` (8.5 GB) or `--scales 3-4` (3.7 GB), with fewer small-scale quads to match on a sparse field. The destination is `index_dir` of `/opt/drpm4/config/pesto.cfg` (default `/usr/local/astrometry/data`, created with `sudo` if needed); use `--dir PATH` for another place and set `index_dir` to it. The script needs `wget` or `curl`, checks the free space, and is safe to re-run: complete files are skipped and partial ones are resumed. `--tiles 8-11` limits the download to some sky tiles, which is only useful for tests because PESTO observes the whole sky.

### Troubleshooting

- `AttributeError: module 'numpy' has no attribute 'in1d'` when starting `drpm4`: the installed astropy is too old for the installed numpy (astropy older than 7.2 with numpy 2.5). Upgrade it with `pip install -U "astropy>=7.2"`, or, in a conda environment, `conda install -c conda-forge "astropy>=7.2"`. Re-running `pip install .` (or `pip install -e .`) from the repository does the same, since `pyproject.toml` now requires `astropy>=7.2`.

## Usage

```bash
cd /path/to/260817        # night folder, or a Target/<object> folder
drpm4 --list              # objects, filters, rotations, increments
drpm4                     # level 0 (results in <folder>/redux/), level 1 (astrometry), then the night report in <night>/report/
drpm4 --level 0           # level 0 only, no astrometry
drpm4 --no-report         # level 0 only
drpm4 --report-only       # report only (level 0 already done)
drpm4 --dir /path/to/night --skip-existing -v
drpm4 --archive           # same, then archive the night (see below)
```

Level 1 needs the astrometry.net index files in `/usr/local/astrometry/data` (the 52xx series is enough for PESTO's 7.8' field). To use another folder, set `index_dir` in the `[astrometry]` section of `/opt/drpm4/config/pesto.cfg`; the other `[astrometry]` settings have defaults and can be left out.

### Archiving

Archiving is never automatic, only when you ask for it:

```bash
drpm4 --archive                  # after level 0/1 and the report: empty Video/, gzip every FITS file
drpm4 --archive-only --dry-run   # show what would happen on an already processed night
drpm4 --archive-only             # archive without running anything else
```

- **Video/** is emptied (the folder stays). This cannot be undone.
- **Every `.fits`** under the night folder (`Dark`, `DomeFlat`, `SkyFlat`, `Target/*`, and their `redux/`) becomes a `.fits.gz`, one file each. A file is replaced only after its compressed copy was read back and has exactly the same size, so an interrupted run loses nothing and can simply be run again. `drpm4 --list`, level 0/1 and the report all read `.fits.gz` files.
- With `--archive` nothing is archived if a previous step reported errors (check the log, fix, then `--archive-only`). With `--dir .../Target/<object>` only that folder's files are gzipped and `Video/` is left alone.

Originals are never modified by the reduction. Logs go to the console and `/opt/drpm4/log/drpm4-YYYYMMDD.log` (one file per day).

## Tests

```bash
pip install -e ".[test]"
pytest                    # one test: pytest tests/test_level0.py::test_cli_single_object_folder_and_bad_file
```
