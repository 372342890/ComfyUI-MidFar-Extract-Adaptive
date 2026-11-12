"""Ten-pair diagnostics, including mirror and textured partial containment."""
from itertools import combinations

import cv2
import numpy as np

try:
    from .visible_batch import _any_asset, _white_asset, _normalized_asset, _variant_signature
except ImportError:
    from visible_batch import _any_asset, _white_asset, _normalized_asset, _variant_signature


def _containment(a, b):
    """SIFT/RANSAC evidence is a review signal, never proof of source identity."""
    detector = cv2.SIFT_create(nfeatures=1000)
    prepared = []
    for image in (a, b):
        pixels = image[0]
        mask = pixels[..., 3] > .5
        ys, xs = np.where(mask)
        crop = pixels[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        ratio = min(1., 640 / max(crop.shape[:2]))
        crop = cv2.resize(crop, None, fx=ratio, fy=ratio)
        gray = cv2.cvtColor(np.round(crop[..., :3] * 255).astype(np.uint8),
                            cv2.COLOR_RGB2GRAY)
        alpha = (crop[..., 3] > .5).astype(np.uint8) * 255
        points, desc = detector.detectAndCompute(gray, alpha)
        prepared.append((points, desc, gray.shape))
    (ka, da, shape_a), (kb, db, shape_b) = prepared
    if da is None or db is None or min(len(ka), len(kb)) < 12:
        return {"status": "insufficient_detail", "inliers": 0}
    matches = cv2.BFMatcher().knnMatch(da, db, k=2)
    good = [m for pair in matches if len(pair) == 2
            for m, n in [pair] if m.distance < .65 * n.distance]
    if len(good) < 12:
        return {"status": "no_strong_match", "inliers": 0, "matches": len(good)}
    pa = np.float32([ka[m.queryIdx].pt for m in good])
    pb = np.float32([kb[m.trainIdx].pt for m in good])
    _, mask = cv2.findHomography(pa, pb, cv2.RANSAC, 3.)
    if mask is None:
        return {"status": "no_consistent_transform", "inliers": 0}
    selected = mask.ravel().astype(bool)
    inliers = int(selected.sum())
    coverage = []
    for points, shape in ((pa, shape_a), (pb, shape_b)):
        area = cv2.contourArea(cv2.convexHull(points[selected])) if inliers >= 3 else 0
        coverage.append(float(area / (shape[0] * shape[1])))
    suspected = (inliers >= 12 and inliers / len(good) >= .6 and max(coverage) >= .18)
    return {"status": "review_required" if suspected else "no_strong_match",
            "inliers": inliers, "matches": len(good), "coverage": coverage}


def audit_pairs(images, background="white", branches=None):
    if not 1 <= len(images) <= 5:
        raise ValueError("需要1–5张真实提取图。")
    branches = list(range(1, len(images) + 1)) if branches is None else list(branches)
    if len(branches) != len(images) or len(set(branches)) != len(branches):
        raise ValueError("实际路编号必须唯一且与图片对应。")
    assets = [(_white_asset(image) if background == "white" else _any_asset(image))[1]
              for image in images]
    signatures = [_normalized_asset(a) for a in assets]
    variants = [_variant_signature(a, "#FFFFFF" if background == "white" else "any")
                for a in assets]
    pairs = []
    for a, b in combinations(range(len(images)), 2):
        color_a, mask_a = signatures[a]
        orientations = []
        for mirror in (False, True):
            color_b, mask_b = signatures[b]
            if mirror:
                color_b, mask_b = color_b[:, ::-1], mask_b[:, ::-1]
            union = mask_a | mask_b
            overlap = float((mask_a & mask_b).sum()) / max(1, int(union.sum()))
            error = float(np.abs(color_a[union] - color_b[union]).mean())
            orientations.append({"mirror": mirror, "silhouette_iou": overlap,
                                 "color_error": error})
        same = any(o["silhouette_iou"] >= .995 and o["color_error"] <= .008
                   for o in orientations)
        upper = None
        if variants[a] is not None and variants[b] is not None:
            va, vb = variants[a], variants[b]
            union = va[0] | vb[0]
            upper = {"silhouette_iou": float((va[0] & vb[0]).sum()) / max(1, int(union.sum())),
                     "hash_distance": int(np.count_nonzero(va[1] != vb[1])),
                     "color_distance": float(cv2.compareHist(va[2], vb[2],
                                                             cv2.HISTCMP_BHATTACHARYYA))}
        containment = _containment(assets[a], assets[b])
        mirror_containment = _containment(assets[a], assets[b][:, :, ::-1].copy())
        status = ("duplicate" if same else
                  "review_required" if containment["status"] == "review_required" or
                  mirror_containment["status"] == "review_required" else "no_duplicate_evidence")
        pairs.append({"branches": [branches[a], branches[b]], "status": status,
                      "orientations": orientations, "upper_structure": upper,
                      "partial_containment": containment,
                      "mirror_partial_containment": mirror_containment})
    return {"pair_count": len(pairs), "pairs": pairs,
            "status": "duplicate" if any(p["status"] == "duplicate" for p in pairs) else
            "review_required" if any(p["status"] == "review_required" for p in pairs) else
            "no_duplicate_evidence",
            "limitation": "相似度是成图证据；源目标身份和同组拆分仍需对照原图人工复核。"}
