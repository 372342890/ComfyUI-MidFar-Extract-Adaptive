# Adaptive 1–3 target validation

Source copied under MIT from `372342890/ComfyUI-MidFar-Extract`, main commit
`17ac4195daa2e81af141744e929aa493e34746e0`. The source repository is unchanged.

Local environment: Python 3.12.16, NumPy 2.5.4, OpenCV 5.0.0.93, Pillow 13.0.0,
PyTorch 2.15.0. No real image model was invoked by unit tests.

## Verified on 2026-11-11

- RED: new tests reproduced the fixed-three split failure and missing adaptive
  processor, gates, lazy collector and workflow.
- GREEN: `python -m unittest discover -q` passed all **305 tests**.
- One/two real targets retain eight split slots; inactive trailing prompt/prefix
  strings are empty and records include actual active/skipped branches.
- PE ids and counts must match recognition; duplicate natural units and explicit
  disconnected-stone facts are rejected.
- Empty prompt returns the host's `ExecutionBlocker(None)`; the collector's
  `check_lazy_status` requests only missing active IMAGE inputs. These host
  contracts were tested with an explicit adapter, not a live cloud scheduler.
- Exactly one/two real images are saved and returned; unused IMAGE slots are
  blockers. Missing active images, repeated output paths and gaps fail.
- Actual image pixels are preserved in `any` mode. Exact and mirrored duplicate
  assets are rejected, and existing detailed duplicate checks are retained.
- The exported graph has 11 nodes and 22 edges: one input, two native text
  stages, three native image stages, three gates, one splitter and one collector.
  Every model prompt is gated; every model receives the current original image.
  No face-count or 3D stage is present.
- Native size parameters and prompt instructions agree on `2016x1344`.
  The old workflow builders and strict-three batch entry point remain available.

## Runtime boundary

The cloud host must install the new repository, expose the unique Adaptive node
keys, support ComfyUI ExecutionBlocker and lazy inputs, and reload the plugin.
Local test success does not establish cloud installation or visual quality.
The generated JSON references input `026.png`; the current source image must be
selected after importing.

Live acceptance still requires two actual generated mountain images, a check
against the original contour and connections, cross-image duplicate review,
and evidence of cloud persistence. Follow only the extraction graph.
