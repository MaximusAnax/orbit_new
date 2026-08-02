"""FR-8 — rep segmentation, plus the signal conditioning the measurements use.

The pipeline is deliberately explicit about what the docs specify and what is
implementation choice:

* **Specified by FR-8.** The segmentation signal is the exercise's
  ``primary_signal``; it is smoothed by a *centred moving average* over
  ``max(3, round(fps * smoothing_window_frac))`` frames; reps are the extrema
  whose topographic prominence is at least ``prominence_frac`` of the smoothed
  signal's range and which are at least ``min_rep_duration_s`` apart, with the
  window and separation derived from the clip's own fps.
* **Implementation choice, documented here.** A 3-point *median* pre-filter
  runs before that moving average.  Real pose estimators emit occasional
  single-frame teleports at high reported confidence; a median of three is the
  standard impulse rejector and does not move a peak.  Separately, keypoint
  coordinates used for *measurement* are smoothed with a quadratic
  Savitzky-Golay filter over the same window — unlike a moving average it
  reproduces a parabola exactly, so it removes jitter at the turnaround frame
  without flattening the extremum the depth and angle features are read at.
"""

from __future__ import annotations

from collections.abc import Sequence

from formcoach.engine.geometry import signal_value
from formcoach.models import (
    FormProfile,
    Keypoint,
    PoseFrame,
    PoseSequence,
    RepBoundary,
    RepDirection,
    round_half_up,
)

# ------------------------------------------------------------------------ filters


def smoothing_window(fps: float, frac: float) -> int:
    """FR-8 window length in frames, forced odd so the average is truly centred."""
    window = max(3, round_half_up(fps * frac))
    if window % 2 == 0:
        window += 1
    return window


def _clamped(values: Sequence[float], index: int) -> float:
    if index < 0:
        return values[0]
    if index >= len(values):
        return values[-1]
    return values[index]


def median3(values: Sequence[float]) -> list[float]:
    """3-point median filter — removes single-frame teleports, keeps edges."""
    n = len(values)
    if n < 3:
        return list(values)
    out = [0.0] * n
    for i in range(n):
        window = sorted((_clamped(values, i - 1), values[i], _clamped(values, i + 1)))
        out[i] = window[1]
    return out


def moving_average(values: Sequence[float], window: int) -> list[float]:
    """Centred moving average with edge replication."""
    n = len(values)
    if n == 0:
        return []
    half = window // 2
    out = [0.0] * n
    for i in range(n):
        total = 0.0
        for k in range(-half, half + 1):
            total += _clamped(values, i + k)
        out[i] = total / (2 * half + 1)
    return out


def savgol_coefficients(window: int) -> list[float]:
    """Quadratic Savitzky-Golay smoothing weights for a centred window.

    Closed form for the centre point of a quadratic (equivalently cubic) least
    squares fit over ``window = 2m + 1`` samples:
    ``c_i = 3 * (3m^2 + 3m - 1 - 5 i^2) / ((2m + 3)(2m + 1)(2m - 1))``.
    """
    m = window // 2
    if m < 2:
        return [1.0 / window] * window
    denom = (2 * m + 3) * (2 * m + 1) * (2 * m - 1)
    return [3.0 * (3 * m * m + 3 * m - 1 - 5 * i * i) / denom for i in range(-m, m + 1)]


def savgol(values: Sequence[float], window: int) -> list[float]:
    """Quadratic Savitzky-Golay smoothing with edge replication."""
    n = len(values)
    if n == 0:
        return []
    coeffs = savgol_coefficients(window)
    half = window // 2
    out = [0.0] * n
    for i in range(n):
        total = 0.0
        for k, c in enumerate(coeffs):
            total += c * _clamped(values, i + k - half)
        out[i] = total
    return out


# ------------------------------------------------------------------- signal handling


def extract_signal(sequence: PoseSequence, expression: str) -> list[float] | None:
    """Evaluate ``primary_signal`` per frame, linearly filling short gaps."""
    raw: list[float | None] = [signal_value(f, expression) for f in sequence.frames]
    known = [i for i, v in enumerate(raw) if v is not None]
    if not known:
        return None
    filled: list[float] = []
    for i, value in enumerate(raw):
        if value is not None:
            filled.append(value)
            continue
        before = [k for k in known if k < i]
        after = [k for k in known if k > i]
        if before and after:
            lo, hi = before[-1], after[0]
            t = (i - lo) / (hi - lo)
            filled.append(raw[lo] + t * (raw[hi] - raw[lo]))  # type: ignore[operator]
        elif before:
            filled.append(raw[before[-1]])  # type: ignore[arg-type]
        else:
            filled.append(raw[after[0]])  # type: ignore[arg-type]
    return filled


def smooth_sequence(sequence: PoseSequence, window: int) -> PoseSequence:
    """Return a copy whose keypoint coordinates are de-spiked and SG-smoothed.

    Confidences are copied untouched: smoothing changes where we think a joint
    is, never how sure the estimator was, and every visibility decision keys off
    the reported confidence.  Keypoints that are not present in every frame are
    passed through unchanged.
    """
    frames = sequence.frames
    if len(frames) < 3:
        return sequence
    common = set(frames[0].keypoints)
    for frame in frames[1:]:
        common &= set(frame.keypoints)
    smoothed: dict[str, tuple[list[float], list[float]]] = {}
    for name in sorted(common):
        xs = median3([f.keypoints[name].x for f in frames])
        ys = median3([f.keypoints[name].y for f in frames])
        smoothed[name] = (savgol(xs, window), savgol(ys, window))

    new_frames: list[PoseFrame] = []
    for i, frame in enumerate(frames):
        kps: dict[str, Keypoint] = {}
        for name, kp in frame.keypoints.items():
            if name in smoothed:
                xs, ys = smoothed[name]
                kps[name] = Keypoint(x=xs[i], y=ys[i], conf=kp.conf)
            else:
                kps[name] = kp
        new_frames.append(PoseFrame(t_ms=frame.t_ms, keypoints=kps))
    return sequence.model_copy(update={"frames": new_frames})


# ------------------------------------------------------------------ peak detection


def local_maxima(values: Sequence[float]) -> list[int]:
    """Interior indices that are >= both neighbours and > at least one."""
    out: list[int] = []
    for i in range(1, len(values) - 1):
        left, here, right = values[i - 1], values[i], values[i + 1]
        if here >= left and here >= right and (here > left or here > right):
            out.append(i)
    return out


def peak_prominence(values: Sequence[float], index: int) -> float:
    """Topographic prominence of the peak at ``index``."""
    peak = values[index]
    left_min = peak
    j = index - 1
    while j >= 0 and values[j] <= peak:
        left_min = min(left_min, values[j])
        j -= 1
    right_min = peak
    k = index + 1
    while k < len(values) and values[k] <= peak:
        right_min = min(right_min, values[k])
        k += 1
    return peak - max(left_min, right_min)


def _select_extrema(values: Sequence[float], min_prominence: float, min_sep: int) -> list[int]:
    """Prominence-filtered peaks, thinned so none are closer than ``min_sep``."""
    candidates = [(peak_prominence(values, i), i) for i in local_maxima(values)]
    candidates = [(p, i) for p, i in candidates if p >= min_prominence]
    candidates.sort(key=lambda pi: (-pi[0], pi[1]))
    accepted: list[int] = []
    for _, index in candidates:
        if all(abs(index - kept) >= min_sep for kept in accepted):
            accepted.append(index)
    return sorted(accepted)


def _argmin(values: Sequence[float], lo: int, hi: int) -> int:
    """First index of the minimum of ``values[lo:hi + 1]``."""
    best = lo
    for i in range(lo, hi + 1):
        if values[i] < values[best]:
            best = i
    return best


def segment_reps(sequence: PoseSequence, profile: FormProfile) -> list[RepBoundary]:
    """FR-8 — segment a clip into ``(start, extremum, end)`` rep boundaries."""
    raw = extract_signal(sequence, profile.primary_signal)
    if raw is None or len(raw) < 3:
        return []

    window = smoothing_window(sequence.fps, profile.smoothing_window_frac)
    smoothed = moving_average(median3(raw), window)

    # A rep's extremum is the deepest point for descend-first movements and the
    # lockout for ascend-first ones; working on the negated signal for the
    # latter lets one peak-finder serve both.
    work = smoothed if profile.direction is RepDirection.DOWN_UP else [-value for value in smoothed]

    span = max(work) - min(work)
    if span <= 1e-9:
        return []
    min_sep = max(1, round_half_up(profile.min_rep_duration_s * sequence.fps))
    extrema = _select_extrema(work, profile.prominence_frac * span, min_sep)
    if not extrema:
        return []

    n = len(work)
    turns: list[int] = [_argmin(work, 0, extrema[0])]
    for i in range(1, len(extrema)):
        turns.append(_argmin(work, extrema[i - 1], extrema[i]))
    turns.append(_argmin(work, extrema[-1], n - 1))

    reps: list[RepBoundary] = []
    for i, extremum in enumerate(extrema):
        start = min(turns[i], extremum - 1) if extremum > 0 else extremum
        end = max(turns[i + 1], extremum + 1) if extremum < n - 1 else extremum
        start = max(0, start)
        end = min(n - 1, end)
        if not (start < extremum < end):
            continue
        reps.append(
            RepBoundary(
                rep_index=len(reps),
                start_frame=start,
                extremum_frame=extremum,
                end_frame=end,
            )
        )
    return reps
