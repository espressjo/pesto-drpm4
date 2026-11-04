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

## Usage

```bash
cd /path/to/260817        # night folder, or a Target/<object> folder
drpm4 --list              # objects, filters, rotations, increments
drpm4                     # level 0 (results in <folder>/redux/), level 1 (astrometry), then the night report in <night>/report/
drpm4 --level 0           # level 0 only, no astrometry
drpm4 --no-report         # level 0 only
drpm4 --report-only       # report only (level 0 already done)
drpm4 --dir /path/to/night --skip-existing -v
```

Level 1 needs the astrometry.net index files in `/usr/local/astrometry/data` (the 52xx series is enough for PESTO's 7.8' field). To use another folder, set `index_dir` in the `[astrometry]` section of `/opt/drpm4/config/pesto.cfg`; the other `[astrometry]` settings have defaults and can be left out.

Originals are never modified. Logs go to the console and `/opt/drpm4/log/drpm4-YYYYMMDD.log` (one file per day).

## Tests

```bash
pip install -e ".[test]"
pytest                    # one test: pytest tests/test_level0.py::test_cli_single_object_folder_and_bad_file
```
