# M8 — real-clip fixtures (reported, never gated)

This directory is empty on purpose. `evals/run.py` prints M8 as `NOT AVAILABLE`
until it is populated, so the suite stays zero-config.

To populate it, extract keypoints **once** with the live MediaPipe adapter
(`formcoach[pose]`), commit the resulting `*.keypoints.json` sidecars here, and
add a hand-annotated `labels.json`:

```json
{
  "clips": [
    {
      "file": "squat_side_real_01.keypoints.json",
      "exercise_id": "barbell-back-squat",
      "form_profile_id": "squat_v1",
      "declared_view": "side_left",
      "rep_count": 5,
      "faults": [[0, "insufficient_depth"], [3, "excessive_trunk_lean"]]
    }
  ]
}
```

`faults` is the hand-annotated set of `(rep_index, fault_id)` pairs; `run.py`
reports rep-count agreement and the Jaccard overlap of the flagged set. Eight
hand-labelled clips cannot support a threshold, so M8 is never gated — a
regression here is a review conversation, not a red build. Hermeticity is
preserved because the keypoints are committed and MediaPipe never runs in CI.
