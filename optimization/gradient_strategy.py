"""
Prescription (handoff §8.2, C32): the gradient picks the axes, the stage heads pick how
far each one moves, starting from the level the Rewriter is shown.

    g        = sum_s (1 - p_s) * d logit_s / dz      (query embedding and head weights fixed)
    level_i  = the axis's current level (continuous axes: three bins, edges from the schema)
    movable  = axes whose level can still move the way g_i points; lowering only on axes
               with `direction: both`, not under their `lower_floor` (schema v0.10, C37)
    share_i  = g_i / |g|  over all axes; axes with |share_i| >= tau are candidates,
               largest first, at most k_max
    target_i = the allowed level in g_i's direction (any number of steps) that maximizes
               log p_final = sum_s log p_s, given the targets already chosen;
               the axis is dropped if no level raises it (k = 0 is allowed)

The random prescription (§11.1) keeps everything but the choice of axes: the same k, drawn
at random among the movable axes whose move raises log p_final, targets as above.
"""
import numpy as np
import torch

BINS = [0.0, 0.5, 1.0]


def axis_levels(axis: dict) -> list[float]:
    if axis["levels"] == "continuous":
        return BINS
    if axis["levels"] in ("count5", "reverse_count5"):  # sentence-count axes: five levels (the reverse only flips the rubric)
        return [0.0, 0.25, 0.5, 0.75, 1.0]
    return [float(v) for v in axis["levels"]]


def nearest(levels, value) -> float:
    return min(levels, key=lambda v: abs(v - value))


def level_of(axis: dict, value: float) -> float:
    """Current level of a z value. Continuous axes with `bin_edges` [lo, hi] in the schema use
    them (a value on an edge takes the lower level); the others take the nearest level."""
    edges = axis.get("bin_edges")
    if edges:
        return 0.0 if value <= edges[0] else 0.5 if value <= edges[1] else 1.0
    return nearest(axis_levels(axis), value)


def gradient(heads, x: np.ndarray, z_dim: int) -> np.ndarray:
    """heads: stage heads already in eval mode; x = [z | PCA(query)]. Gradient w.r.t. z only."""
    xt = torch.tensor(x, dtype=torch.float32)
    z = xt[:z_dim].clone().requires_grad_(True)
    inp = torch.cat([z, xt[z_dim:]])
    total = 0.0
    for head in heads:
        logit = head(inp.unsqueeze(0))[0]
        total = total + (1 - torch.sigmoid(logit)).detach() * logit
    total.backward()
    return z.grad.numpy()


def log_p_final(heads, query_part: np.ndarray):
    """z -> sum_s log p_s for one query (the query part of the head input fixed)."""
    q = torch.tensor(query_part, dtype=torch.float32)

    def f(z: np.ndarray) -> float:
        inp = torch.cat([torch.tensor(z, dtype=torch.float32), q]).unsqueeze(0)
        with torch.no_grad():
            return float(sum(torch.nn.functional.logsigmoid(head(inp))[0] for head in heads))
    return f


def allowed_levels(axis: dict, cur: float, g_i: float) -> list[float]:
    """Levels the axis may move to the way g_i points (schema v0.10, C37): any level above for g_i > 0;
    below only for `direction: both` axes and not under their `lower_floor`."""
    levels = axis_levels(axis)
    if g_i > 0:
        return [v for v in levels if v > cur]
    if g_i < 0 and axis.get("direction") == "both":
        return [v for v in levels if axis.get("lower_floor", 0.0) <= v < cur]
    return []


def movable(g, z, axes) -> np.ndarray:
    """1 where the axis may still move the way its gradient points, 0 where it is at the end or that way is not allowed."""
    return np.array([1.0 if allowed_levels(axis, level_of(axis, z[i]), g[i]) else 0.0 for i, axis in enumerate(axes)])


def target_level(i, g, z, zp, base, axes, objective, eps):
    """(current level, best allowed level along g_i or None, its log p_final) with the other axes at zp."""
    cur = level_of(axes[i], z[i])
    best, best_lp = None, base
    for v in sorted(allowed_levels(axes[i], cur, g[i]), key=lambda v: abs(v - cur)):
        zc = zp.copy()
        zc[i] = v
        lp = objective(zc)
        if lp > best_lp + eps:
            best, best_lp = v, lp
    return cur, best, best_lp


def prescribe(g, z, axes, objective, tau=0.25, k_max=5, eps=1e-6):
    """Returns [(axis index, current level, target level, gain in log p_final)], in the order chosen."""
    m = movable(g, z, axes)
    share = m * g / (np.linalg.norm(g) + 1e-8)
    plan, zp = [], np.array(z, dtype=np.float64)
    base = objective(zp)
    for i in np.argsort(-np.abs(share)):
        if abs(share[i]) < tau or len(plan) >= k_max:
            break
        cur, best, best_lp = target_level(i, g, z, zp, base, axes, objective, eps)
        if best is not None:
            plan.append((int(i), cur, best, best_lp - base))
            zp[i], base = best, best_lp
    return plan


def random_prescription(k, g, z, axes, objective, rng, eps=1e-6):
    """prescribe with the axes drawn in random order instead of by |share| (no tau): the first k axes
    whose allowed move along g raises log p_final, each with its target level chosen as in prescribe."""
    m = movable(g, z, axes)
    plan, zp = [], np.array(z, dtype=np.float64)
    base = objective(zp)
    for i in rng.permutation(len(axes)):
        if len(plan) >= k:
            break
        if not m[i]:
            continue
        cur, best, best_lp = target_level(i, g, z, zp, base, axes, objective, eps)
        if best is not None:
            plan.append((int(i), cur, best, best_lp - base))
            zp[i], base = best, best_lp
    return plan
