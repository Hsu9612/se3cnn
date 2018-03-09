# pylint: disable=C,R,E1101
import torch
from se3_cnn import SE3BNConvolution, SE3ConvolutionBN
from se3_cnn.non_linearities import ScalarActivation
from se3_cnn import SO3
from se3_cnn.dropout import SE3Dropout


class GatedBlock(torch.nn.Module):
    def __init__(self,
                 repr_in, repr_out, size, radial_window_dict,  # kernel params
                 activation=(None, None), stride=1, padding=0, p_drop=0,  # conv/nonlinearity/dropout params
                 batch_norm_momentum=0.1, batch_norm_mode='normal', batch_norm_before_conv=True):  # batch norm params
        '''
        :param repr_in: tuple with multiplicities of repr. (1, 3, 5, ..., 15)
        :param repr_out: same but for the output
        :param int size: the filters are cubes of dimension = size x size x size
        :param radial_window_dict: contains both radial window function and the keyword arguments for the radial window function
        :param activation: (scalar activation, gate activation) which are functions like torch.nn.functional.relu or None
        :param int stride: stride of the convolution (for torch.nn.functional.conv3d)
        :param int padding: padding of the convolution (for torch.nn.functional.conv3d)
        :param float batch_norm_momentum: batch normalization momentum (put it to zero to disable the batch normalization)
        :param batch_norm_mode: the mode of the batch normalization
        :param bool batch_norm_before_conv: perform the batch normalization before or after the convolution
        '''
        super().__init__()

        if type(activation) is tuple:
            scalar_activation, gate_activation = activation
        else:
            scalar_activation, gate_activation = activation, activation

        self.repr_out = repr_out

        irreducible_repr = [SO3.repr1, SO3.repr3, SO3.repr5, SO3.repr7, SO3.repr9, SO3.repr11, SO3.repr13, SO3.repr15]

        Rs_in = list(zip(repr_in, irreducible_repr))
        Rs_out_with_gate = list(zip(repr_out, irreducible_repr))

        if (scalar_activation is not None and repr_out[0] > 0):
            self.scalar_act = ScalarActivation([(repr_out[0], scalar_activation)])
        else:
            self.scalar_act = None

        n_non_scalar = sum(repr_out[1:])
        if gate_activation is not None and n_non_scalar > 0:
            Rs_out_with_gate.append((n_non_scalar, SO3.repr1))  # concatenate scalar gate capsules after normal capsules
            self.gate_act = ScalarActivation([(n_non_scalar, gate_activation)])
        else:
            self.gate_act = None

        self.bn_conv = (SE3BNConvolution if batch_norm_before_conv else SE3ConvolutionBN)(
            Rs_in=Rs_in,
            Rs_out=Rs_out_with_gate,
            size=size,
            radial_window_dict=radial_window_dict,
            stride=stride,
            padding=padding,
            momentum=batch_norm_momentum,
            mode=batch_norm_mode)

        if p_drop != 0:
            Rs_out_without_gate = [(mul, 2 * n + 1) for n, mul in enumerate(repr_out)]  # Rs_out without gates
            self.drop_layer = SE3Dropout(Rs_out_without_gate, p_drop)
        else:
            self.drop_layer = None

    def forward(self, x):  # pylint: disable=W

        # convolution
        y = self.bn_conv(x)

        if self.scalar_act is None and self.gate_act is None:
            z = y
        else:
            nbatch = y.size(0)
            nx = y.size(2)
            ny = y.size(3)
            nz = y.size(4)

            begin_y = 0  # index of first non-scalar capsule

            if self.gate_act is not None:
                g = y[:, sum(mul * (2 * n + 1) for n, mul in enumerate(self.repr_out)):]
                g = self.gate_act(g)
                begin_g = 0  # index of first scalar gate capsule

            zs = []

            for n, mul in enumerate(self.repr_out):
                if mul == 0:
                    continue
                dim = 2 * n + 1

                # crop out capsules of order n
                field_y = y[:, begin_y: begin_y + mul * dim]  # [batch, feature * repr, x, y, z]
                begin_y += mul * dim

                if n == 0:
                    # Scalar activation
                    if self.scalar_act is not None:
                        field = self.scalar_act(field_y)
                    else:
                        field = field_y
                else:
                    if self.gate_act is not None:
                        # reshape channels in capsules and capsule entries
                        field_y = field_y.contiguous()
                        field_y = field_y.view(nbatch, mul, dim, nx, ny, nz)  # [batch, feature, repr, x, y, z]

                        # crop out corresponding scalar gates
                        field_g = g[:, begin_g: begin_g + mul]  # [batch, feature, x, y, z]
                        begin_g += mul
                        # reshape channels for broadcasting
                        field_g = field_g.contiguous()
                        field_g = field_g.view(nbatch, mul, 1, nx, ny, nz)  # [batch, feature, repr, x, y, z]

                        # scale non-scalar capsules by gate values
                        field = field_y * field_g  # [batch, feature, repr, x, y, z]
                        field = field.view(nbatch, mul * dim, nx, ny, nz)  # [batch, feature * repr, x, y, z]
                    else:
                        field = field_y

                zs.append(field)

            z = torch.cat(zs, dim=1)

        # dropout
        if self.drop_layer is not None:
            z = self.drop_layer(z)

        return z
