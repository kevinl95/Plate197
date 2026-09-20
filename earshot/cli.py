"""earshot's front door.

    python -m earshot              start the service
    python -m earshot devices      what the mic is called, from here
    python -m earshot models       download BirdNET v2.4 (~120MB, once)
    python -m earshot bench        one window, honestly timed
    python -m earshot check        everything that has to be true before it runs
    python -m earshot species      BirdNET's name for a bird, and its plate file
    python -m earshot plates       check plates/ sizes; --fix to shrink oversized ones
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load


def cmd_devices(args, cfg) -> int:
    from . import audio
    audio.rescan()
    devices = audio.input_devices()
    if not devices:
        print("no input devices at all — is the mic plugged in?")
        return 1
    match = cfg.device_match.lower()
    print(f"matching on device_match = {cfg.device_match!r}\n")
    for device in devices:
        hit = "->" if match in device.name.lower() else "  "
        print(f"{hit} {device.name}  "
              f"({device.channels} ch, {device.default_samplerate:.0f} Hz default)")
    chosen = [d for d in devices if match in d.name.lower()]
    print()
    if not chosen:
        print("nothing matches; set device_match to part of one of the names above")
        return 1
    print(f"would use: {chosen[0]}")
    if len(chosen) > 1:
        print(f"note: {len(chosen)} devices match; the first wins")
    return 0


def cmd_models(args, cfg) -> int:
    from . import models
    if models.have(cfg.model_dir, cfg.model_precision) and not args.force:
        print(f"already present in {cfg.model_dir}:")
        for name, size in models.describe(cfg.model_dir).items():
            print(f"  {size:>12,}  {name}")
        return 0

    last = [0]

    def progress(read: int, total: int) -> None:
        percent = int(100 * read / total) if total else 0
        if percent >= last[0] + 10:
            last[0] = percent
            print(f"  {percent}%", flush=True)

    models.fetch(cfg.model_dir, cfg.model_precision, on_progress=progress)
    print(f"models in {cfg.model_dir}")
    for name, size in models.describe(cfg.model_dir).items():
        print(f"  {size:>12,}  {name}")
    return 0


def cmd_bench(args, cfg) -> int:
    from . import bench
    return bench.run(cfg, windows=args.windows, threads=args.threads,
                     wav=Path(args.wav) if args.wav else None)


def cmd_species(args, cfg) -> int:
    """What BirdNET calls a bird, and what to name its picture.

    Audubon's plate names are not BirdNET's names and never will be, so
    this is the lookup that sits between a plate and a filename.
    """
    import logging

    from . import api, plates
    from .classify import Labels, RangeFilter

    # A listing, not a service: the species-count line belongs in the
    # header below, not in the middle of a table.
    logging.getLogger("earshot.classify").setLevel(logging.ERROR)

    if not (cfg.model_dir / "labels.txt").is_file():
        print(f"no labels in {cfg.model_dir}; run `earshot models` first")
        return 1
    labels = Labels(cfg.model_dir / "labels.txt")

    web = api.find_web_dir(cfg.web_dir)
    have = plates.Plates((web / "plates") if web else None)

    # Default to what could actually turn up at this feeder. The full
    # 6522 is a worldwide list and not a to-do list.
    indices = range(len(labels))
    scope = "every species BirdNET knows"
    if not args.all:
        meta = cfg.model_dir / "meta-model.tflite"
        if not meta.is_file():
            print(f"no range model in {cfg.model_dir}; showing all species")
        else:
            range_filter = RangeFilter(meta, labels, cfg.species_cache_path,
                                       cfg.latitude, cfg.longitude,
                                       cfg.location_filter_threshold)
            range_filter.refresh()
            indices = [int(i) for i in (range_filter.mask.nonzero()[0])]
            scope = (f"plausible at {cfg.latitude}, {cfg.longitude} "
                     f"in week {range_filter.week} of 48")

    query = (args.query or "").strip().lower()
    rows = []
    for i in indices:
        common, sci = labels.common[i], labels.sci[i]
        if not labels.is_bird[i]:
            continue
        if query and query not in common.lower() and query not in sci.lower():
            continue
        got = have.has(common)
        if args.missing and got:
            continue
        rows.append((common, sci, plates.filename_for(common), got))

    if not rows:
        print("nothing matches" + (f" {args.query!r}" if args.query else ""))
        return 1

    print(f"{scope}\n")
    width = max(len(r[0]) for r in rows)
    sci_width = max(len(r[1]) for r in rows)
    print(f"{'BirdNET name'.ljust(width)}  {'scientific'.ljust(sci_width)}  plate file")
    print("-" * (width + sci_width + 30))
    for common, sci, filename, got in sorted(rows)[:args.limit]:
        mark = "have" if got else "--"
        print(f"{common.ljust(width)}  {sci.ljust(sci_width)}  {filename}  {mark}")
    if len(rows) > args.limit:
        print(f"\n...and {len(rows) - args.limit} more; --limit to see them")

    print(f"\n{sum(1 for r in rows if r[3])} of {len(rows)} have a picture.")
    if web is None:
        print("No display directory found yet — plates go beside index-7inch.html.")
    else:
        print(f"Put new ones in {web / 'plates'}/ — any spelling of the name works.")
    stray = plates.unmatched(have, labels.common)
    if stray:
        print(f"\nNo species matches: {', '.join(stray)}")
    return 0


def cmd_check(args, cfg) -> int:
    """Everything that has to be true, each reported separately."""
    from . import api, models, pi
    ok = True

    def line(label: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and good
        print(f"  [{'ok' if good else '--'}] {label}{': ' + detail if detail else ''}")

    print("earshot check")
    try:
        from . import runtime
        line("tflite runtime", True, runtime.interpreter_class()[0])
    except RuntimeError as exc:
        line("tflite runtime", False, str(exc))

    present = models.have(cfg.model_dir, cfg.model_precision)
    line("BirdNET v2.4 models", present,
         str(cfg.model_dir) if present else f"missing from {cfg.model_dir}")

    try:
        from . import audio
        audio.rescan()
        device = audio.resolve(cfg.device_match)
        line("microphone", True, str(device))
    except Exception as exc:
        line("microphone", False, str(exc))

    try:
        cfg.data_path.mkdir(parents=True, exist_ok=True)
        probe = cfg.data_path / ".writable"
        probe.write_text("x")
        probe.unlink()
        line("data directory", True, str(cfg.data_path))
    except OSError as exc:
        line("data directory", False, str(exc))

    disk = pi.disk_free(cfg.data_path)
    line("disk space", disk.get("available") and not disk.get("low"),
         f"{disk.get('free_mb')} MB free" if disk.get("available") else "unknown")

    web = api.find_web_dir(cfg.web_dir)
    line("display page", web is not None,
         str(web / api.PAGE) if web else "index-7inch.html not found")

    if web is not None:
        from . import plates as plates_module
        have = plates_module.Plates(web / "plates")
        detail = f"{len(have)} in {web / 'plates'}"
        stray = []
        if (cfg.model_dir / "labels.txt").is_file():
            from .classify import Labels
            stray = plates_module.unmatched(have, Labels(cfg.model_dir / "labels.txt").common)
        oversized = [r["name"] for r in plates_module.describe(have) if r["oversized"]]
        problems = []
        if stray:
            problems.append(f"no species matches {', '.join(stray)}")
        if oversized:
            problems.append(f"{len(oversized)} larger than "
                            f"{plates_module.MAX_SIDE}px on a side "
                            f"({', '.join(oversized)}) — run `earshot plates --fix`")
        # Not having plates yet is fine — the page draws silhouettes. A
        # plate named after something BirdNET has never heard of, or big
        # enough to threaten a Pi 3's memory once Chromium decodes it, is.
        line("artwork", not problems, detail + ("; " + "; ".join(problems) if problems else ""))

    power = pi.throttled()
    if power.get("available"):
        line("power", power["power_ok"],
             ", ".join(power["flags"]) or "no undervoltage recorded")

    temp = pi.soc_temp_c()
    if temp is not None:
        line("soc temperature", temp < 80, f"{temp} C")

    print()
    return 0 if ok else 1


def cmd_plates(args, cfg) -> int:
    """Whether the pictures in plates/ are a size Chromium can safely hold.

    Not a crop tool — deciding which bird in a group plate is the right
    one needs a person, and that judgment stays entirely in your image
    editor. This only catches the mechanical mistake: an export at scan
    resolution that would ask a 1GB Pi to decode a multi-megapixel bitmap
    for every visible bird.
    """
    from . import api, plates

    web = api.find_web_dir(cfg.web_dir)
    if web is None:
        print("no display directory found; plates go beside index-7inch.html")
        return 1
    have = plates.Plates(web / "plates")
    if not len(have):
        print(f"no plates yet in {web / 'plates'}")
        return 0

    rows = plates.describe(have)
    width = max(len(r["name"]) for r in rows)
    oversized = [r for r in rows if r["oversized"]]
    for r in sorted(rows, key=lambda r: r["name"]):
        if r["error"]:
            print(f"  {r['name'].ljust(width)}  could not read: {r['error']}")
            continue
        dims = f"{r['width']}x{r['height']}" if r["width"] else "unknown size"
        flag = "  <- over " + str(plates.MAX_SIDE) + "px" if r["oversized"] else ""
        print(f"  {r['name'].ljust(width)}  {dims:<11} {r['bytes']:>9,} bytes{flag}")

    if not oversized:
        print(f"\nall {len(rows)} plates are {plates.MAX_SIDE}px or under. nothing to do.")
        return 0

    print(f"\n{len(oversized)} of {len(rows)} plates are over {plates.MAX_SIDE}px on a "
         f"side — decoded in memory, each one can be tens of MB regardless "
         f"of its file size on disk.")
    if not args.fix:
        print("run `earshot plates --fix` to shrink them in place (needs Pillow).")
        return 1

    print()
    try:
        changed = plates.optimize(have)
    except RuntimeError as exc:
        print(str(exc))
        return 1
    for line in changed:
        print(f"  {line}")
    print(f"\nfixed {len(changed)}.")
    return 0


def cmd_run(args, cfg) -> int:
    from .service import main as run_service
    return run_service(cfg)


def build_parser() -> argparse.ArgumentParser:
    # --config is accepted on either side of the subcommand; SUPPRESS keeps
    # the subparser's copy from overwriting one given before it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="path to earshot.toml",
                        default=argparse.SUPPRESS)

    parser = argparse.ArgumentParser(prog="earshot", description=__doc__,
                                     parents=[common])
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("run", parents=[common], help="start the service (default)")
    sub.add_parser("devices", parents=[common],
                   help="list input devices and show which one matches")

    models_cmd = sub.add_parser("models", parents=[common],
                                help="download the BirdNET v2.4 files")
    models_cmd.add_argument("--force", action="store_true")

    bench_cmd = sub.add_parser("bench", parents=[common],
                               help="time one 3-second window")
    bench_cmd.add_argument("--windows", type=int, default=20)
    bench_cmd.add_argument("--threads", type=int, default=None)
    bench_cmd.add_argument("--wav", help="48kHz mono wav to use instead of noise")

    sub.add_parser("check", parents=[common], help="preflight everything")

    species_cmd = sub.add_parser(
        "species", parents=[common],
        help="what BirdNET calls a bird, and what to name its plate")
    species_cmd.add_argument("query", nargs="?",
                             help="part of a common or scientific name")
    species_cmd.add_argument("--all", action="store_true",
                             help="every species, not just the ones plausible here")
    species_cmd.add_argument("--missing", action="store_true",
                             help="only the ones with no picture yet")
    species_cmd.add_argument("--limit", type=int, default=60)

    plates_cmd = sub.add_parser(
        "plates", parents=[common],
        help="check plates/ for anything too large for a Pi 3 to decode")
    plates_cmd.add_argument("--fix", action="store_true",
                            help="shrink oversized plates in place (needs Pillow)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    cfg = load(getattr(args, "config", None))
    handlers = {
        None: cmd_run, "run": cmd_run, "devices": cmd_devices,
        "models": cmd_models, "bench": cmd_bench, "check": cmd_check,
        "species": cmd_species, "plates": cmd_plates,
    }
    return handlers[args.command](args, cfg)
