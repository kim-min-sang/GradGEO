"""
Per-query loss for the R and K heads (handoff §6, C24):

    L_pair = sum_{i,j: r_i < r_j} w_ij * softplus(-(s_i - s_j)),  w_ij = |log r_i - log r_j|
    L      = L_pair + lambda * BCE(p, y)

All pairs of a query are used (no sampling). Pairs whose engine scores are equal carry
no order and are dropped. L_pair is divided by the query's total pair weight so that
every query counts the same; queries with only positives or only negatives get BCE only.
The G head's soft-label version is still open (handoff §17).
"""
import torch
import torch.nn.functional as F


def ranked_pair_bce_loss(logits, ranks, scores, labels, mask, lam=1.0):
    """Batched over queries. All tensors (B, N); mask marks real entries (padding = False)."""
    rank_i, rank_j = ranks.unsqueeze(2), ranks.unsqueeze(1)
    pair = (rank_i < rank_j) & (scores.unsqueeze(2) != scores.unsqueeze(1))
    pair &= mask.unsqueeze(2) & mask.unsqueeze(1)
    w = (torch.log(rank_i.float()) - torch.log(rank_j.float())).abs() * pair
    diff = logits.unsqueeze(2) - logits.unsqueeze(1)
    pos = (labels * mask).sum(1)
    has_both = (pos > 0) & (pos < mask.sum(1))
    per_query = (w * F.softplus(-diff)).sum((1, 2)) / w.sum((1, 2)).clamp_min(1e-12)
    l_pair = (per_query * has_both).sum() / has_both.sum().clamp_min(1)
    bce = F.binary_cross_entropy_with_logits(logits, labels.float(), reduction="none")
    l_bce = (bce * mask).sum() / mask.sum()
    return l_pair + lam * l_bce, l_pair.detach(), l_bce.detach()
