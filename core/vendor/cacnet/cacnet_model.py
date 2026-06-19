# -*- coding: utf-8 -*-
"""
CACNet — Composition-Aware Cropping Network (MIT License)
原始来源 / Original source:
  https://github.com/bo-zhang-cs/CACNet-Pytorch (MIT License)
  作者 / Author: Bo Zhang et al.

改动说明 / Adaptation notes (vendored for SuperPicky):
  1. 移除 `from config_cropping import cfg`（同仓库配置文件，不随本包发布）
     Removed `from config_cropping import cfg` (same-repo config, not vendored).
  2. 内联 image_size = (224, 224)（来自 config_cropping.py 中 cfg.image_size）
     Inlined image_size = (224, 224) (was cfg.image_size in config_cropping.py).
  3. 移除 `assert cfg.backbone == 'vgg16'`，骨干网络固定为 vgg16
     Removed backbone assert; backbone is fixed to vgg16.
  4. 移除 `if __name__ == '__main__':` 块（引用了已删除的 cfg 和 cuda）
     Removed __main__ block (referenced removed cfg and cuda).
  5. 修正 vgg16(pretrained=...) → vgg16(weights=...) 以避免 PyTorch 弃用警告
     Changed vgg16(pretrained=...) to vgg16(weights=...) to avoid deprecation warning.
"""

import torch
import torch.nn as nn
import torchvision.models as models
import torch.nn.functional as F
import torch.nn.init as init  # noqa: F401  (原文件保留，未显式用但保持原样)
import einops
import numpy as np
from torchvision.ops import roi_pool  # noqa: F401  (原文件保留，CACNet 扩展可能用到)

# ── 内联配置 / Inlined from config_cropping.cfg ───────────────────────────────
# 原 cfg.image_size = (224, 224)；骨干网固定为 vgg16
# Was cfg.image_size = (224, 224); backbone is fixed to vgg16.
image_size: tuple = (224, 224)


class vgg_base(nn.Module):
    """VGG16 特征提取骨干，输出 f2/f3/f4 三级特征。
    VGG16 feature extraction backbone, outputs f2/f3/f4 multi-scale features."""

    def __init__(self, loadweights: bool = True):
        super(vgg_base, self).__init__()
        # weights=DEFAULT 等同于原来的 pretrained=True；loadweights=False 时传 None
        # weights=DEFAULT is equivalent to pretrained=True; None when loadweights=False.
        vgg_weights = models.VGG16_Weights.DEFAULT if loadweights else None
        vgg = models.vgg16(weights=vgg_weights)
        self.feature1 = nn.Sequential(vgg.features[:6])      # stride /2
        self.feature2 = nn.Sequential(vgg.features[6:10])    # stride /4
        self.feature3 = nn.Sequential(vgg.features[10:17])   # stride /8
        self.feature4 = nn.Sequential(vgg.features[17:30])   # stride /16

    def forward(self, x: torch.Tensor):
        f1 = self.feature1(x)
        f2 = self.feature2(f1)
        f3 = self.feature3(f2)
        f4 = self.feature4(f3)
        return f2, f3, f4


class CompositionModel(nn.Module):
    """构图分类模块，输出构图 logits 和 KCM 热力图。
    Composition classification module; outputs composition logits and KCM heatmap."""

    def __init__(self):
        super(CompositionModel, self).__init__()
        self.comp_types = 9
        self.conv1 = nn.Sequential(
            nn.Conv2d(512, 256, kernel_size=3, padding=1),
            nn.BatchNorm2d(256),
            nn.ReLU(True)
        )
        self.conv2 = nn.Sequential(
            nn.Conv2d(256, 256, kernel_size=3, padding=1),
            nn.BatchNorm2d(256),
            nn.ReLU(True)
        )
        self.conv3 = nn.Sequential(
            nn.Conv2d(256, 128, kernel_size=1, padding=0),
            nn.ReLU(True)
        )
        self.conv4 = nn.Sequential(
            nn.Conv2d(128, 128, kernel_size=1, padding=0),
            nn.ReLU(True)
        )
        self.GAP = nn.Sequential(
            nn.AdaptiveAvgPool2d(1),
            nn.Flatten(1))
        self.fc_layer = nn.Linear(128, self.comp_types, bias=True)
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.xavier_normal_(m.weight.data)
                nn.init.zeros_(m.bias.data)
            elif isinstance(m, nn.BatchNorm2d):
                m.weight.data.fill_(1)
                m.bias.data.zero_()

    def forward(self, f2: torch.Tensor, f3: torch.Tensor, f4: torch.Tensor):
        x = self.conv1(f4)
        x = self.conv2(x)
        x = F.interpolate(x, scale_factor=2, mode='bilinear', align_corners=True) + f3
        x = self.conv3(x)
        x = F.interpolate(x, scale_factor=2, mode='bilinear', align_corners=True) + f2
        x = self.conv4(x)
        gap = self.GAP(x)
        logits = self.fc_layer(gap)
        conf = F.softmax(logits, dim=1)
        with torch.no_grad():
            B, C, H, W = x.shape
            w = self.fc_layer.weight.data  # cls_num, channels
            trans_w = einops.repeat(w, 'n c -> b n c', b=B)
            trans_x = einops.rearrange(x, 'b c h w -> b c (h w)')
            cam = torch.matmul(trans_w, trans_x)  # b n hw
            cam = cam - cam.min(dim=-1)[0].unsqueeze(-1)
            cam = cam / (cam.max(dim=-1)[0].unsqueeze(-1) + 1e-12)
            cam = einops.rearrange(cam, 'b n (h w) -> b n h w', h=H, w=W)
            kcm = torch.sum(conf[:, :, None, None] * cam, dim=1, keepdim=True)
            kcm = F.interpolate(kcm, scale_factor=4, mode='bilinear', align_corners=True)
            return logits, kcm


class CroppingModel(nn.Module):
    """裁剪框回归模块，输出 anchor offset。
    Cropping box regression module; outputs anchor offsets."""

    def __init__(self, anchor_stride: int):
        super(CroppingModel, self).__init__()
        self.anchor_stride = anchor_stride
        self.conv1 = nn.Sequential(
            nn.Conv2d(512, 256, kernel_size=3, padding=1),
            nn.BatchNorm2d(256),
            nn.ReLU(True)
        )
        self.conv2 = nn.Sequential(
            nn.Conv2d(256, 256, kernel_size=3, padding=1),
            nn.BatchNorm2d(256),
            nn.ReLU(True)
        )
        self.conv3 = nn.Sequential(
            nn.Conv2d(256, 256, kernel_size=3, padding=1),
            nn.BatchNorm2d(256),
            nn.ReLU(True)
        )
        self.conv4 = nn.Sequential(
            nn.Conv2d(256, 256, kernel_size=3, padding=1),
            nn.BatchNorm2d(256),
            nn.ReLU(True)
        )
        out_channel = int((16 / anchor_stride) ** 2 * 4)
        self.output = nn.Conv2d(256, out_channel, kernel_size=3, padding=1)
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.xavier_normal_(m.weight.data)
                nn.init.zeros_(m.bias.data)
            elif isinstance(m, nn.BatchNorm2d):
                m.weight.data.fill_(1)
                m.bias.data.zero_()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        参数 / Args:
            x: b,512,H/16,W/16
        返回 / Returns:
            b,4 — 最佳裁剪框的 anchor 偏移 / anchor shifts of the best crop
        """
        x = self.conv1(x)
        x = self.conv2(x)
        x = self.conv3(x)
        x = self.conv4(x)
        out = self.output(x)
        return out


def generate_anchors(anchor_stride: int) -> torch.Tensor:
    """生成 anchor 中心点坐标网格。
    Generate anchor center coordinate grid."""
    assert anchor_stride <= 16, 'not implement for anchor_stride{} > 16'.format(anchor_stride)
    P_h = np.array([2 + i * 4 for i in range(16 // anchor_stride)])
    P_w = np.array([2 + i * 4 for i in range(16 // anchor_stride)])

    num_anchors = len(P_h) * len(P_h)

    # 初始化 anchor 输出 / Initialize output anchors
    anchors = torch.zeros((num_anchors, 2))
    k = 0
    for i in range(len(P_w)):
        for j in range(len(P_h)):
            anchors[k, 1] = P_w[j]
            anchors[k, 0] = P_h[i]
            k += 1
    return anchors


def shift(shape, stride: int, anchors: torch.Tensor) -> torch.Tensor:
    """将 anchor 中心平移到特征图每个位置。
    Shift anchor centers to every location in the feature map."""
    shift_w = torch.arange(0, shape[0]) * stride
    shift_h = torch.arange(0, shape[1]) * stride
    shift_w, shift_h = torch.meshgrid([shift_w, shift_h])
    shifts = torch.stack([shift_w, shift_h], dim=-1)  # h,w,2
    # add A anchors (A,2) to shifts (h,w,2) → all_anchors (A,h,w,2)
    trans_anchors = einops.rearrange(anchors, 'a c -> a 1 1 c')
    trans_shifts = einops.rearrange(shifts, 'h w c -> 1 h w c')
    all_anchors = trans_anchors + trans_shifts
    return all_anchors


class PostProcess(nn.Module):
    """后处理模块：将 offset 解码为最优裁剪框坐标。
    Post-processing module: decodes offsets into the best crop box coordinates."""

    def __init__(self, anchor_stride: int, img_size: tuple):
        super(PostProcess, self).__init__()
        self.num_anchors = (16 // anchor_stride) ** 2
        anchors = generate_anchors(anchor_stride)
        feat_shape = (img_size[0] // 16, img_size[1] // 16)
        all_anchors = shift(feat_shape, 16, anchors)
        all_anchors = all_anchors.float().unsqueeze(0)  # 1,num_anchors,h//16,w//16,2
        self.upscale_factor = self.num_anchors // 2
        anchors_x = F.pixel_shuffle(all_anchors[..., 0], upscale_factor=self.upscale_factor)
        anchors_y = F.pixel_shuffle(all_anchors[..., 1], upscale_factor=self.upscale_factor)
        # 1,h//s,w//s,2 where s=16//anchor_stride
        all_anchors = torch.stack([anchors_x, anchors_y], dim=-1).squeeze(1)
        self.register_buffer('all_anchors', all_anchors)
        # 构建 KCM 采样网格 / Build grid for sampling pixel from KCM
        grid_x = (all_anchors[..., 0] - img_size[0] / 2) / (img_size[0] / 2)
        grid_y = (all_anchors[..., 1] - img_size[1] / 2) / (img_size[1] / 2)
        # 1,h//s,w//s,2, range [-1,1]
        grid = torch.stack([grid_x, grid_y], dim=-1)
        self.register_buffer('grid', grid)

    def forward(self, offsets: torch.Tensor, kcm: torch.Tensor) -> torch.Tensor:
        """
        参数 / Args:
            offsets: b,num_anchors*4,h//16,w//16
            kcm:     b,1,h,w
        返回 / Returns:
            b,4 — 最优裁剪框坐标(网络输入尺度) / best crop box in network-input scale
        """
        offsets = einops.rearrange(offsets, 'b (n c) h w -> b n h w c',
                                   n=self.num_anchors, c=4)
        coords = [F.pixel_shuffle(offsets[..., i], upscale_factor=self.upscale_factor) for i in range(4)]
        # b, h//s, w//s, 4, where s=16//anchor_stride
        offsets = torch.stack(coords, dim=-1).squeeze(1)
        regression = torch.zeros_like(offsets)  # b,h,w,4
        regression[..., 0::2] = offsets[..., 0::2] + self.all_anchors[..., 0:1]
        regression[..., 1::2] = offsets[..., 1::2] + self.all_anchors[..., 1:2]

        trans_grid = einops.repeat(self.grid, '1 h w c -> b h w c',
                                   b=offsets.shape[0])
        # b,1,h//s, w//s
        sample_kcm = F.grid_sample(kcm, trans_grid, mode='bilinear', align_corners=True)
        reg_weight = F.softmax(sample_kcm.flatten(1), dim=1).unsqueeze(-1)
        regression = einops.rearrange(regression, 'b h w c -> b (h w) c')
        weighted_reg = torch.sum(reg_weight * regression, dim=1)
        return weighted_reg


class ComClassifier(nn.Module):
    """纯构图分类器（不含裁剪回归）。
    Composition-only classifier (no cropping regression)."""

    def __init__(self, loadweights: bool = True):
        super(ComClassifier, self).__init__()
        self.backbone = vgg_base(loadweights=loadweights)
        self.composition_module = CompositionModel()

    def forward(self, x: torch.Tensor, only_classify: bool = False):
        f2, f3, f4 = self.backbone(x)
        logits, kcm = self.composition_module(f2, f3, f4)
        return logits, kcm


class CACNet(nn.Module):
    """
    构图感知裁剪网络主体 (Composition-Aware Cropping Network).

    参数 / Args:
        loadweights (bool): 是否加载 VGG16 ImageNet 预训练权重。
                            Whether to load VGG16 ImageNet pretrained weights.
                            设为 False 后再手动 load_state_dict。
                            Set False then manually load_state_dict.
    """

    def __init__(self, loadweights: bool = True):
        super(CACNet, self).__init__()
        # anchor_stride=8 来自原始仓库（硬编码，不依赖 cfg）
        # anchor_stride=8 from original repo (hardcoded, does not depend on cfg).
        anchor_stride = 8
        # image_size 来自模块级内联常量（原 cfg.image_size=(224,224)）
        # image_size from module-level inlined constant (was cfg.image_size=(224,224)).
        self.backbone = vgg_base(loadweights=loadweights)
        self.composition_module = CompositionModel()
        self.cropping_module = CroppingModel(anchor_stride)
        self.post_process = PostProcess(anchor_stride, image_size)

    def forward(self, im: torch.Tensor, only_classify: bool = False):
        """
        参数 / Args:
            im (Tensor): b,3,H,W  (归一化后的 RGB / Normalized RGB)
            only_classify (bool): True 只返回构图分类; False 同时返回裁剪框。
                                  True returns only composition logits; False also returns crop box.
        返回 / Returns:
            only_classify=True:  (logits, kcm)
            only_classify=False: (logits, kcm, box)  —— box shape: b,4 (x1,y1,x2,y2)
        """
        f2, f3, f4 = self.backbone(im)       # 特征 1/4, 1/8, 1/16
        logits, kcm = self.composition_module(f2, f3, f4)
        if only_classify:
            return logits, kcm
        else:
            offsets = self.cropping_module(f4)
            box = self.post_process(offsets, kcm)
            return logits, kcm, box
