"""Night report: one PDF with an overview table and one section per observation."""

from __future__ import annotations

import logging
import math
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from astropy.io import fits

from drpm4.analysis.pdeepstack import deepstack
from drpm4.analysis.pseeing import seeing
from drpm4.analysis.ptemperature import temperature
from drpm4.analysis.ptracking import TrackingQuality, ptracking
from drpm4.ommobj import ommobj, ommobjs
from drpm4.pestoHeader import fits_files
from drpm4.tools.configfile import load_config

log = logging.getLogger(__name__)

NA = "N/A"


def tex(text: object) -> str:
    """Escape ``text`` for LaTeX."""
    out = str(text)
    for char, repl in (("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"), ("$", r"\$"), ("#", r"\#"),
                       ("_", r"\_"), ("{", r"\{"), ("}", r"\}"), ("~", r"\textasciitilde{}"), ("^", r"\^{}")):
        out = out.replace(char, repl)
    return out


def _num(value: float | None, fmt: str = ".2f") -> str:
    return NA if value is None or (isinstance(value, float) and math.isnan(value)) else format(value, fmt)


def mode_text(o: ommobj) -> str:
    """Readout mode as a short text."""
    m = o.mode
    return f"amp {m['amp_type']}, {m['h_freq'] / 1e6:g} MHz, gain {m['ana_gain']}, bin {m['binning'][0]}x{m['binning'][1]}"


def redux_status(o: ommobj) -> tuple[str, int, int]:
    """Return (level text, files in redux/, files with astrometry).

    The level is "-" if level 0 did not process every file, "0" if no file has astrometry,
    "1" if all have it and "1 (n/N)" if only n of N have it.
    """
    redux = o.path / "redux"
    files = fits_files(redux) if redux.is_dir() else []
    astrom = sum(1 for f in files if fits.getheader(f).get("DRPCODE") == "level-1")
    if len(files) < o.number_of_file:
        return "-", len(files), astrom
    if astrom == 0:
        return "0", len(files), 0
    return ("1" if astrom == len(files) else f"1 ({astrom}/{len(files)})"), len(files), astrom


@dataclass
class ObservationResult:
    """Measurements of one observation, ``None`` when they could not be made."""

    seeing: float | None = None
    tracking: TrackingQuality | None = None
    t_mirror: float | None = None
    t_exterior: float | None = None
    figures: dict[str, Path] = field(default_factory=dict)


class NightReport:
    """Build the report of a night.

    Parameters
    ----------
    objs : ommobjs
        Parsed night (``ommobjs.parse`` already called).
    config : configparser.ConfigParser, optional
        Configuration (loaded from ``pesto.cfg`` when omitted).
    """

    def __init__(self, objs: ommobjs, config=None) -> None:
        self.objs = objs
        self.config = config or load_config()
        self.results: dict[int, ObservationResult] = {}

    # ------------------------------------------------------------------
    def analyse(self, figures_dir: Path, nb: int | None = None) -> None:
        """Run the analyses on every observation and save the figures in ``figures_dir``.

        An analysis that fails is logged and shown as N/A, it never stops the report.
        """
        nb = nb or self.config.getint("report", "nb_frames")
        figures_dir.mkdir(parents=True, exist_ok=True)
        for k, o in enumerate(self.objs):
            res = self.results[k] = ObservationResult()
            stem = f"{k:02d}_{o.object}_{o.filter}".replace("/", "_").replace(" ", "_")
            log.info("analysing %s (%s, %d files)", o.object, o.filter, o.number_of_file)

            def attempt(name: str, func) -> None:
                try:
                    func()
                except Exception as exc:  # noqa: BLE001
                    log.error("%s: %s failed: %s", o.object, name, exc)

            def do_seeing() -> None:
                s = seeing(o, nb)
                res.figures["seeing"] = s.create_seeing_png(figures_dir / f"{stem}_seeing.png")
                avg = s.avg_seeing()
                res.seeing = None if math.isnan(avg) else avg

            def do_tracking() -> None:
                t = ptracking(o, nb)
                res.figures["tracking"] = t.create_tracking_png(figures_dir / f"{stem}_tracking.png")
                res.tracking = t.t_quality

            def do_stack() -> None:
                res.figures["deepstack"] = deepstack(o, nb).create_deepstack_png(figures_dir / f"{stem}_deepstack.png")

            def do_temperature() -> None:
                t = temperature(o, nb)
                res.figures["ccd"] = t.create_ccd_png(figures_dir / f"{stem}_ccd.png")
                res.t_mirror, res.t_exterior = t.avg_mirror(), t.avg_exterior()

            attempt("seeing", do_seeing)
            attempt("tracking", do_tracking)
            attempt("deepstack", do_stack)
            attempt("temperature", do_temperature)

    # ------------------------------------------------------------------
    def table_rows(self) -> list[list[str]]:
        """Return the overview table, one list of strings per observation."""
        rows = []
        for k, o in enumerate(self.objs):
            res = self.results.get(k, ObservationResult())
            h = o.H(0)
            level, _, _ = redux_status(o)
            rows.append([
                o.object, h["PROGRAM"], h["RATXT"], h["DECTXT"], _num(o.foyer, ".0f"), level,
                f"{len(o.flat)}/{len(o.dark)}", _num(res.seeing), _num(res.tracking.rms if res.tracking else None),
                _num(res.t_mirror, ".1f"), _num(res.t_exterior, ".1f"), str(o.number_of_file), mode_text(o), NA,
                f"{o.texp / 1000:g}", o.filter,
            ])
        return rows

    HEADER = ["Object", "Program", "RA", "DEC", "Focus", "Level", "Flat/Dark", "Seeing (\")", "Track rms (\")",
              "T mir (C)", "T ext (C)", "N", "Readout mode", "Zero point", "Exp (s)", "Filter"]

    def to_tex(self, figures_dir: Path) -> str:
        """Return the LaTeX source of the report. Figure paths are relative to the .tex file."""
        objs = self.objs
        first = objs[0] if objs.nb_objects else None
        h0 = first.H(0) if first else None
        night = objs.night
        date = first.date.strftime("%Y-%m-%d") if first and first.date else NA
        n = objs.nb_objects
        out = [
            r"\documentclass[10pt]{article}",
            r"\usepackage[T1]{fontenc}\usepackage[utf8]{inputenc}\usepackage{lmodern}",
            r"\usepackage{graphicx}\usepackage{longtable}\usepackage{booktabs}\usepackage{float}",
            r"\usepackage[landscape,margin=0.5in]{geometry}",
            r"\begin{document}",
            rf"\begin{{center}}{{\Large PESTO night report}}\\[2pt]{{\large {tex(night)} ({tex(date)})}}\end{{center}}",
            r"\section*{Overview}",
            rf"During the night of {tex(date)}, {n} observation{'s' if n != 1 else ''} "
            rf"{'were' if n != 1 else 'was'} made. Observations with the same object name are listed separately "
            r"when the filter, the rotation or the position (more than 4 arcmin) differ.\par",
        ]
        if h0 is not None:
            out.append(
                r"\medskip\noindent "
                + r"\quad ".join(f"{label}: {tex(h0[key])}" for label, key in
                                 (("Instrument", "INSTRUME"), ("Detector", "DET_TYPE"), ("Serial", "SERIAL"),
                                  ("Observer", "OBSERVER"), ("Operator", "OPERATOR")))
                + r"\par"
            )
        cols = "l" * 4 + "r" * 12
        out += [
            r"\bigskip{\scriptsize\setlength{\tabcolsep}{3pt}",
            rf"\begin{{longtable}}{{{cols}}}",
            r"\toprule " + " & ".join(tex(c) for c in self.HEADER) + r" \\ \midrule \endhead",
        ]
        out += [" & ".join(tex(c) for c in row) + r" \\" for row in self.table_rows()]
        out += [r"\bottomrule\end{longtable}}"]
        for k, o in enumerate(objs):
            res = self.results.get(k, ObservationResult())
            q = res.tracking
            out += [
                r"\clearpage",
                rf"\section*{{{tex(o.object)} -- {tex(o.filter)}, rotation {o.rotation:g}}}",
                rf"{o.number_of_file} images (increments {o.start}--{o.stop}), exposure {o.texp / 1000:g} s, "
                rf"{len(o.flat)} flat and {len(o.dark)} dark file(s) found. "
                rf"Mean seeing {_num(res.seeing)}\,\", tracking rms {_num(q.rms if q else None)}\,\"" + (
                    rf" (largest offset {q.max_offset:.2f}\,\", drift {q.drift_per_hour:.2f}\,\"/h)" if q else "") + r".\par",
                r"\medskip",
            ]
            for i, name in enumerate(("tracking", "seeing", "deepstack", "ccd")):
                fig = res.figures.get(name)
                body = (rf"\includegraphics[width=0.47\textwidth]{{{(figures_dir / fig.name).as_posix()}}}" if fig
                        else rf"\fbox{{\parbox[c][2in][c]{{0.45\textwidth}}{{\centering {name}: not available}}}}")
                out.append(body + (r"\hfill" if i % 2 == 0 else r"\\[6pt]"))
        out.append(r"\end{document}")
        return "\n".join(out) + "\n"

    # ------------------------------------------------------------------
    def build(self, outdir: Path | str | None = None, nb: int | None = None) -> Path | None:
        """Analyse the night and write the report.

        Parameters
        ----------
        outdir : Path, optional
            Output folder (default ``<night folder>/report``).
        nb : int, optional
            Frames sampled per observation.

        Returns
        -------
        Path or None
            The PDF, or None if ``pdflatex`` is missing (the .tex file is still written).
        """
        if not self.objs.nb_objects:
            log.warning("no observation found, no report")
            return None
        outdir = Path(outdir) if outdir else self.objs.night_dir / "report"
        outdir.mkdir(parents=True, exist_ok=True)
        figures = outdir / "figures"
        self.analyse(figures, nb)
        name = f"{self.objs.night}_report"
        tex_path = outdir / f"{name}.tex"
        tex_path.write_text(self.to_tex(Path("figures")), encoding="utf-8")
        if not shutil.which("pdflatex"):
            log.error("pdflatex not found: wrote %s only", tex_path)
            return None
        for _ in range(2):  # longtable needs two passes
            run = subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", tex_path.name],
                cwd=outdir, capture_output=True, text=True, check=False,
            )
        if run.returncode != 0:
            log.error("pdflatex failed, see %s\n%s", outdir / f"{name}.log", run.stdout[-1500:])
            return None
        for ext in (".aux", ".log"):
            (outdir / f"{name}{ext}").unlink(missing_ok=True)
        log.info("report written: %s", outdir / f"{name}.pdf")
        return outdir / f"{name}.pdf"
