from torch import nn
import torch.nn.functional as F
import torch
from src.facerender.modules.util import Hourglass, make_coordinate_grid, kp2gaussian

from src.facerender.sync_batchnorm import SynchronizedBatchNorm3d as BatchNorm3d


class DenseMotionNetwork(nn.Module):
    """
    Module that predicting a dense motion from sparse motion representation given by kp_source and kp_driving
    """

    def __init__(self, block_expansion, num_blocks, max_features, num_kp, feature_channel, reshape_depth, compress,
                 estimate_occlusion_map=False):
        super(DenseMotionNetwork, self).__init__()
        # self.hourglass = Hourglass(block_expansion=block_expansion, in_features=(num_kp+1)*(feature_channel+1), max_features=max_features, num_blocks=num_blocks)
        self.hourglass = Hourglass(block_expansion=block_expansion, in_features=(num_kp+1)*(compress+1), max_features=max_features, num_blocks=num_blocks)

        self.mask = nn.Conv3d(self.hourglass.out_filters, num_kp + 1, kernel_size=7, padding=3)

        self.compress = nn.Conv3d(feature_channel, compress, kernel_size=1)
        self.norm = BatchNorm3d(compress, affine=True)

        if estimate_occlusion_map:
            # self.occlusion = nn.Conv2d(reshape_channel*reshape_depth, 1, kernel_size=7, padding=3)
            self.occlusion = nn.Conv2d(self.hourglass.out_filters*reshape_depth, 1, kernel_size=7, padding=3)
        else:
            self.occlusion = None

        self.num_kp = num_kp


    def create_sparse_motions(self, feature, kp_driving, kp_source):
        bs, _, d, h, w = feature.shape
        device = kp_source['value'].device
        dtype = kp_source['value'].dtype
        identity_grid = make_coordinate_grid((d, h, w), type=kp_source['value'].type()).to(device=device, dtype=dtype)
        # Flatten spatial dims (d, h, w) -> n to stay <= 4D (required by DirectML)
        n = d * h * w
        identity_flat = identity_grid.reshape(1, 1, n, 3)
        coordinate_grid = identity_flat - kp_driving['value'].reshape(bs, self.num_kp, 1, 3)

        if 'jacobian' in kp_driving and kp_driving['jacobian'] is not None:
            jacobian = torch.matmul(kp_source['jacobian'], torch.inverse(kp_driving['jacobian']))
            # (bs, num_kp, 3, 3) x (bs, num_kp, 3, n) -> (bs, num_kp, n, 3)
            coordinate_grid = torch.matmul(coordinate_grid, jacobian.transpose(-1, -2))

        driving_to_source = coordinate_grid + kp_source['value'].reshape(bs, self.num_kp, 1, 3)

        # adding background feature
        identity_bg = identity_flat.expand(bs, 1, n, 3).contiguous()
        sparse_motions = torch.cat([identity_bg, driving_to_source], dim=1)  # (bs, num_kp+1, n, 3)
        sparse_motions = sparse_motions.reshape(bs, self.num_kp + 1, d, h, w, 3)

        return sparse_motions

    def create_deformed_feature(self, feature, sparse_motions):
        bs, c, d, h, w = feature.shape
        feature_repeat = feature.unsqueeze(1).expand(bs, self.num_kp + 1, c, d, h, w).reshape(bs * (self.num_kp + 1), c, d, h, w)
        sparse_motions = sparse_motions.reshape(bs * (self.num_kp + 1), d, h, w, 3)
        sparse_deformed = F.grid_sample(feature_repeat, sparse_motions)
        sparse_deformed = sparse_deformed.reshape(bs, self.num_kp + 1, c, d, h, w)
        return sparse_deformed

    def create_heatmap_representations(self, feature, kp_driving, kp_source):
        spatial_size = feature.shape[3:]
        gaussian_driving = kp2gaussian(kp_driving, spatial_size=spatial_size, kp_variance=0.01)
        gaussian_source = kp2gaussian(kp_source, spatial_size=spatial_size, kp_variance=0.01)
        heatmap = gaussian_driving - gaussian_source

        # adding background feature
        zeros = torch.zeros(
            heatmap.shape[0], 1, spatial_size[0], spatial_size[1], spatial_size[2],
            device=heatmap.device, dtype=heatmap.dtype,
        )
        heatmap = torch.cat([zeros, heatmap], dim=1)
        heatmap = heatmap.unsqueeze(2)         # (bs, num_kp+1, 1, d, h, w)
        return heatmap

    def forward(self, feature, kp_driving, kp_source):
        bs, _, d, h, w = feature.shape

        feature = self.compress(feature)
        feature = self.norm(feature)
        feature = F.relu(feature)

        out_dict = dict()
        sparse_motion = self.create_sparse_motions(feature, kp_driving, kp_source)
        deformed_feature = self.create_deformed_feature(feature, sparse_motion)

        heatmap = self.create_heatmap_representations(deformed_feature, kp_driving, kp_source)

        # Cat in 5D (bs*(num_kp+1), 1+c, d, h, w) to stay <= 5D (required by DirectML)
        heatmap_5d = heatmap.reshape(bs * (self.num_kp + 1), 1, d, h, w)
        deformed_5d = deformed_feature.reshape(bs * (self.num_kp + 1), -1, d, h, w)
        input_ = torch.cat([heatmap_5d, deformed_5d], dim=1).reshape(bs, -1, d, h, w)

        # input = deformed_feature.view(bs, -1, d, h, w)      # (bs, num_kp+1 * c, d, h, w)

        prediction = self.hourglass(input_)


        mask = self.mask(prediction)
        mask = F.softmax(mask, dim=1)
        out_dict['mask'] = mask

        zeros_mask = torch.zeros_like(mask)
        mask = torch.where(mask < 1e-3, zeros_mask, mask)

        # Flatten (d, h, w) -> n to stay <= 4D (required by DirectML)
        n = d * h * w
        sparse_motion_flat = sparse_motion.reshape(bs, self.num_kp + 1, n, 3)
        mask_flat = mask.reshape(bs, self.num_kp + 1, n, 1)
        deformation = (sparse_motion_flat * mask_flat).sum(dim=1).reshape(bs, d, h, w, 3)

        out_dict['deformation'] = deformation

        if self.occlusion:
            bs, c, d, h, w = prediction.shape
            prediction = prediction.view(bs, -1, h, w)
            occlusion_map = torch.sigmoid(self.occlusion(prediction))
            out_dict['occlusion_map'] = occlusion_map

        return out_dict
