# pylint: disable=C, R, not-callable, no-member, arguments-differ
from functools import partial

import torch

from se3cnn.non_linearities.gated_block_parity import GatedBlockParity
from se3cnn.point.operations import Convolution
from se3cnn.non_linearities.rescaled_act import relu, sigmoid, tanh
from se3cnn.point.kernel import Kernel
from se3cnn.point.radial import CosineBasisModel
from se3cnn.SO3 import rand_rot, formatRs


def get_dataset():
    tetris = [[(0, 0, 0), (0, 0, 1), (1, 0, 0), (1, 1, 0)],  # chiral_shape_1
              [(0, 0, 0), (0, 0, 1), (1, 0, 0), (1, -1, 0)], # chiral_shape_2
              [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)],  # square
              [(0, 0, 0), (0, 0, 1), (0, 0, 2), (0, 0, 3)],  # line
              [(0, 0, 0), (0, 0, 1), (0, 1, 0), (1, 0, 0)],  # corner
              [(0, 0, 0), (0, 0, 1), (0, 0, 2), (0, 1, 0)],  # T
              [(0, 0, 0), (0, 0, 1), (0, 0, 2), (0, 1, 1)],  # zigzag
              [(0, 0, 0), (1, 0, 0), (1, 1, 0), (2, 1, 0)]]  # L
    tetris = torch.tensor(tetris, dtype=torch.get_default_dtype())
    labels = torch.arange(len(tetris))

    # apply random rotation
    tetris = torch.stack([torch.einsum("ij,nj->ni", (rand_rot(), x)) for x in tetris])

    return tetris, labels


class AvgSpacial(torch.nn.Module):
    def forward(self, features):
        return features.mean(1)


def haspath(Rs_in, l_out, p_out):
    for _, l_in, p_in in Rs_in:
        for l in range(abs(l_in - l_out), l_in + l_out + 1):
            if p_out == 0 or p_in * (-1) ** l == p_out:
                return True
    return False


class Network(torch.nn.Module):
    def __init__(self, num_classes):
        super().__init__()

        R = partial(CosineBasisModel, max_radius=3.0, number_of_basis=3, h=100, L=3, act=relu)
        K = partial(Kernel, RadialModel=R)
        C = partial(Convolution, K)

        mul = 7
        layers = []

        rs = [(1, 0, +1)]
        for i in range(3):
            scalars = [(mul, l, p) for mul, l, p in [(mul, 0, +1), (mul, 0, -1)] if haspath(rs, l, p)]
            act_scalars = [(mul, relu if p == 1 else tanh) for mul, l, p in scalars]

            nonscalars = [(mul, l, p) for mul, l, p in [(mul, 1, +1), (mul, 1, -1)] if haspath(rs, l, p)]
            gates = [(sum(mul for mul, l, p in nonscalars), 0, +1)]
            act_gates = [(-1, sigmoid)]

            print("layer {}: from {} to {}".format(i, formatRs(rs), formatRs(scalars + nonscalars)))

            block = GatedBlockParity(C, rs, scalars, act_scalars, gates, act_gates, nonscalars)
            rs = block.Rs_out
            layers.append(block)

        layers.append(GatedBlockParity(C, rs, [(mul, 0, +1), (mul, 0, -1)], [(mul, relu), (mul, tanh)], [], [], []))

        self.firstlayers = torch.nn.ModuleList(layers)

        # the last layer is not equivariant, it is allowed to mix even and odds scalars
        self.lastlayers = torch.nn.Sequential(AvgSpacial(), torch.nn.Linear(mul + mul, num_classes))

    def forward(self, features, geometry):
        for m in self.firstlayers:
            features = m(features, geometry, 4)

        return self.lastlayers(features)


def main():
    torch.set_default_dtype(torch.float64)
    device = 'cuda' if torch.cuda.is_available() else 'cpu'

    tetris, labels = get_dataset()
    tetris = tetris.to(device)
    labels = labels.to(device)
    f = Network(len(tetris))
    f = f.to(device)

    optimizer = torch.optim.Adam(f.parameters())

    feature = tetris.new_ones(tetris.size(0), tetris.size(1), 1)

    for step in range(200):
        out = f(feature, tetris)
        loss = torch.nn.functional.cross_entropy(out, labels)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        if step % 10 == 0:
            acc = out.argmax(1).eq(labels).double().mean().item()
            print("step={:03d} loss={:.3f} accuracy={:.0f}%".format(step, loss.item(), 100 * acc))

    out = f(feature, tetris)

    r_tetris, _ = get_dataset()
    r_tetris = r_tetris.to(device)
    r_out = f(feature, r_tetris)

    print('equivariance error={}'.format((out - r_out).pow(2).mean().sqrt().item()))


if __name__ == '__main__':
    main()
