# Adaptive MidFar Targets Implementation Plan

> **For agentic workers:** Use the executing-plans workflow inline. Steps use checkbox syntax; user has already authorized copying and modifying this node in a new repository.

**Goal:** Support one to three real mountains without fabricating targets, generating missing branches, or failing three-image output validation.

**Architecture:** Keep the splitter's eight-slot contract with empty trailing strings. Add distinct adaptive ComfyUI nodes for prompt blocking and lazy image collection, and build a standalone extraction-only graph using those node keys.

**Tech Stack:** Python 3.12, NumPy, OpenCV, Pillow, unittest, ComfyUI node protocol, GitHub CLI/API.

---

## Task 1: Split actual targets

**Files:** Modify `visible_pipeline.py`; create `test_adaptive_split.py`.

- [ ] Write failing tests using `test_visible_pe_split.recognition()` sliced to one and two objects; construct PE entries for only the retained ids. Assert eight outputs, missing prompts/path slots empty, `target_count` correct and no invented recognized objects.

```python
raw = recognition()
raw["objects"] = raw["objects"][:2]
pe = {"objects": [{"id": t["id"], "prompt": "独立提取 " + t["id"]}
                  for t in raw["objects"]]}
out = split_prompts(json.dumps(raw), json.dumps(pe), image(), "adaptive/test", "any")
self.assertEqual(len(out), 8)
self.assertEqual(out[2], "")
self.assertEqual(out[6], "")
self.assertEqual(json.loads(out[-1])["target_count"], 2)
```

- [ ] Also test zero/four targets rejected, mismatched PE rejected with two targets, case-disguised unit duplication rejected, and `connected_parts=["各石块独立，无岩土连接"]` rejected.
- [ ] Run `/tmp/byteartist-midfar-venv/bin/python -m unittest test_adaptive_split -v`; observe expected failures for the current exactly-three gate.
- [ ] Change list counts to `1 <= len(targets) <= 3`, require PE count to equal actual targets, retain identity validation, and reject explicit disconnected facts in connected_parts.
- [ ] Pad only result-slot strings:

```python
count = len(targets)
record.update(target_count=count, active_branches=list(range(1, count + 1)),
              skipped_branches=list(range(count + 1, 4)))
prompts += [""] * (3 - count)
prefixes += [""] * (3 - count)
return (*prompts, key, *prefixes, json.dumps(record, ensure_ascii=False))
```

- [ ] Re-run new tests and `test_visible_pe_split`.

## Task 2: Process actual image batches

**Files:** Modify `visible_batch.py`; create `test_adaptive_batch.py`.

- [ ] Write failing tests for `process_images([image()], "any")` and two images preserving original pixels, duplicate two images rejected, mirrored `image(2)` rejected, zero/four batches rejected, and existing `process_three` still requiring exactly three.
- [ ] Assert the new function exists before invoking it so missing behavior is an assertion failure. Run the new suite and observe RED.
- [ ] Extract the processing body into `process_images`; validate one to three arrays and iterate pairs over `len(images)`. Keep `process_three` as a strict-three wrapper.
- [ ] Compare each normalized pair in normal and horizontal-mirror orientation with the existing strong exact-duplicate overlap/color thresholds; retain all original detailed variant checks.
- [ ] Re-run new tests plus `test_visible_batch`, `test_visible_any_background`, and white-background tests.

## Task 3: Block absent models and collect only present images

**Files:** Create `adaptive_nodes.py`, `test_adaptive_nodes.py`; modify `__init__.py`.

- [ ] Load the new node module in tests with the existing fake Tensor host adapter, temporary folder_paths output directory and an explicit test ExecutionBlocker class.
- [ ] Before implementation, tests assert new classes exist. Then test a nonempty prompt is unchanged; an empty prompt returns an ExecutionBlocker rather than any image or placeholder string.
- [ ] Test lazy collection for two nonempty prefixes requests only `image_1` and `image_2`; a missing second active input is requested, third inactive input never is. Test one/two actual saves produce exactly one/two PNG files and inactive return positions are blockers. Duplicate active prefixes or noncontiguous active slots must fail.
- [ ] Implement `BAMidFarAdaptivePE` as a distinct subclass of the visible PE node using the updated splitter.
- [ ] Implement `BAMidFarActivePrompt`:

```python
@classmethod
def INPUT_TYPES(cls):
    return {"required": {"prompt": ("STRING", {"forceInput": True})}}

RETURN_TYPES = ("STRING",)
RETURN_NAMES = ("prompt",)
FUNCTION = "run"
CATEGORY = "MidFar Extract/Adaptive"

def run(self, prompt):
    if not isinstance(prompt, str):
        raise ValueError("Prompt must be text.")
    if not prompt.strip():
        from comfy_execution.graph import ExecutionBlocker
        return (ExecutionBlocker(None),)
    return (prompt,)
```

- [ ] Implement adaptive output by reusing the old node's four return slots. Mark the three IMAGE inputs lazy; validate active prefixes are a unique nonempty leading sequence, call `process_images` only on corresponding tensors, save only actual outputs, return blockers in unused IMAGE slots, and write actual output/skipped counts in the quality JSON.
- [ ] Register only the new adaptive keys in addition to existing exports; keep legacy names intact.
- [ ] Run node regressions and legacy preview tests. Close each PIL Image with a context manager in the existing test to remove its observed inherited ResourceWarning.

## Task 4: Export an extraction-only graph with faithful prompts

**Files:** Create `adaptive_workflow.py`, adaptive prompt text resources, `test_adaptive_workflow.py`, `midfar-adaptive.byteartist.json`; update `README.md`.

- [ ] Test graph has one LoadImage, two native text stages, three native image stages, three active-prompt gates and one adaptive collector; no 3D, face-count or GLB classes. Every generation prompt edge must originate at a gate, each gate at its corresponding adaptive splitter slot.
- [ ] Test current image still connects directly to both text stages and every native image stage; output type/edge types match; recognition/PE prompts do not force exactly three real objects.
- [ ] Implement by wrapping the old `build_workflow`, replacing only the splitter/collector types and text instructions, rerouting three prompt edges through newly allocated gate ids, and preserving original native templates and output slots.
- [ ] Bundle the user's v3 recognition/PE rules and extraction continuity/contour rules. Add image-truth precedence: preserve the same natural unit and ids, but do not reshape a pointed original into a round hill merely because prior text was wrong. Use no scene-independent fabricated anchors.
- [ ] Export `midfar-adaptive.byteartist.json` from the tested builder for input `026.png`, background `any`. Document active-prefix semantics, explicit skip behavior, new node names, source MIT attribution and extraction-only execution.
- [ ] Run all new/related tests, then full unittest discovery. Fix failures before publishing.

## Task 5: Publish the independent copy and verify live execution

- [ ] Create `372342890/ComfyUI-MidFar-Extract-Adaptive` as a private repository after code validation.
- [ ] Use `/tmp/byteartist-gh-cli/gh_2.85.0_macOS_arm64/bin/gh` for GitHub operations. System git and Homebrew are blocked by an unaccepted Xcode license; do not accept it or change git config.
- [ ] Initialize via GitHub contents API for LICENSE, then upload all text files in one Git tree with inline content, create a new commit with source attribution and update main. Keep intermediate API JSON scripts outside the workspace, and exclude __pycache__, local test outputs, credentials and temporary artifacts.
- [ ] Verify remote file hashes/content and repository visibility. Package the tested plugin and workflow as user-facing artifacts.
- [ ] Inspect the actual ByteArtist installation UI for new-repository or local-package support; install within already-authorized node modification scope when supported. Do not expose authentication tokens in repository URLs. If private-source access is genuinely blocked, finish all local/GitHub work and report that specific runtime constraint.
- [ ] Install/use the uniquely named adaptive node keys, retain current source/PE identity, run only extraction subgraph and inspect real image continuity and distinct macro outlines. Do not run face/3D outputs.
- [ ] Update execution record with actual tests, repository/commit URL, deployment result and actual generation status. Do not claim cloud save or image generation without UI evidence.
