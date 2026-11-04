# drpm4

Data reduction pipeline for PESTO (Observatoire du Mont Mégantic). Current scope: level 0 reduction (header standardization) and the night report. No database interaction and no astrometry yet. See `spec.md` for the design.

## Installation

Requires Python >= 3.12.

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

Use a virtual environment so the package does not touch the system Python:

```bash
python3 -m venv ~/.venvs/drpm4
source ~/.venvs/drpm4/bin/activate
pip install .            # regular install
# or, for development (edits take effect immediately):
pip install -e .
```

Dependencies (numpy, matplotlib, astropy) are installed automatically. Check the install with:

```bash
python -c "import drpm4; print(drpm4.__version__)"
```

## Usage

```bash
cd /path/to/260817        # night folder, or a Target/<object> folder
drpm4 --list              # objects, filters, rotations, increments
drpm4                     # level 0 on calibrations and targets, results in <folder>/redux/
drpm4 --dir /path/to/night --skip-existing -v
```

Originals are never modified. Logs go to the console and `/opt/drpm4/log/drpm4-YYYYMMDD.log` (one file per day).

## Tests

```bash
pip install -e ".[test]"
pytest                    # one test: pytest tests/test_level0.py::test_cli_single_object_folder_and_bad_file
```
