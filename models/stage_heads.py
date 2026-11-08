"""
Stage head (handoff §6): x_s = [z | PCA(query embedding)] -> logit_s. One head per
stage with its own weights; a small MLP, or a linear head for the ablation.
"""
import torch
from torch import nn


class StageHead(nn.Module):
    def __init__(self, in_dim: int, hidden: int = 64, linear: bool = False, dropout: float = 0.1):
        super().__init__()
        if linear:
            self.net = nn.Linear(in_dim, 1)
        else:
            self.net = nn.Sequential(
                nn.Linear(in_dim, hidden), nn.SiLU(), nn.Dropout(dropout),
                nn.Linear(hidden, hidden), nn.SiLU(), nn.Dropout(dropout),
                nn.Linear(hidden, 1),
            )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).squeeze(-1)
