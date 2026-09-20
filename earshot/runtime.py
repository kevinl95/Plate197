"""Whichever TFLite interpreter this Pi happens to have.

``tflite-runtime`` is the right one: 2.3MB wheel, arm64, no TensorFlow.
It has wheels up to cp311, which covers Raspberry Pi OS Bookworm (3.11).
On a newer image with Python 3.13 there is no such wheel, so we fall back
to ``ai-edge-litert`` — the same interpreter, Google's renamed package,
14MB instead of 2.3. Both expose the identical API, so nothing else in
here cares which one loaded.
"""

from __future__ import annotations

import importlib
import logging

log = logging.getLogger(__name__)

_CANDIDATES = (
    ("tflite_runtime.interpreter", "Interpreter"),
    ("ai_edge_litert.interpreter", "Interpreter"),
    ("tensorflow.lite", "Interpreter"),
)

_chosen: tuple[str, type] | None = None


def interpreter_class() -> tuple[str, type]:
    """(module name, Interpreter class). Raises if none are installed."""
    global _chosen
    if _chosen is not None:
        return _chosen
    tried = []
    for module_name, attr in _CANDIDATES:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:          # ImportError, and litert's own noise
            tried.append(f"{module_name}: {exc.__class__.__name__}")
            continue
        _chosen = (module_name, getattr(module, attr))
        return _chosen
    raise RuntimeError(
        "No TFLite interpreter available. Install tflite-runtime "
        "(Python <= 3.11) or ai-edge-litert. Tried: " + "; ".join(tried)
    )


def load(model_path, num_threads: int = 2):
    """An allocated interpreter for a .tflite file."""
    module_name, Interpreter = interpreter_class()
    interp = Interpreter(model_path=str(model_path), num_threads=num_threads)
    interp.allocate_tensors()
    log.debug("loaded %s via %s", model_path, module_name)
    return interp


def output_for(interp, size: int):
    """The output tensor with `size` values — BirdNET's models have several.

    Matching on shape rather than hardcoding an index, because the index
    differs between the audio model, the meta-model and any future build.
    """
    details = interp.get_output_details()
    for detail in details:
        if int(detail["shape"][-1]) == size:
            return detail
    raise ValueError(
        f"no output tensor of size {size}; have "
        + ", ".join(str(list(d["shape"])) for d in details)
    )
