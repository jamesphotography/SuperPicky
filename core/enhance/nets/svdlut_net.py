# -*- coding: utf-8 -*-
"""
SVDLUT 调色网络(生成器部分, vendored)/ SVDLUT color network generators, vendored.

来源 / Source: https://github.com/WontaeaeKim/SVDLUT  models.py
许可证 / License: Apache-2.0 (见同目录 LICENSE-SVDLUT / see LICENSE-SVDLUT).
本地改动 / Local edits: 仅保留 cnn backbone + 4 个生成器(去 resnet18_224 / 官方 SVDLUT /
cpp_ext_interface CUDA 依赖 / to_pil_image);SVDLUTNet 用纯 torch 切片(svdlut_slicing)替代
官方 CUDA 算子。构造参数据 FiveK sRGB 权重形状反推(见 svdlut_color spec)。

The official forward depends on a CUDA-only custom op; here the generators are
vendored as-is (pure torch) and SVDLUTNet composes them with a pure-torch slicing
op (svdlut_slicing) — no compiled extension, cross-platform.
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


def discriminator_block(in_filters, out_filters, normalization=False):
    """下采样块 / downsampling block (vendored)."""
    layers = [nn.Conv2d(in_filters, out_filters, 3, stride=2, padding=1)]
    layers.append(nn.LeakyReLU(0.2))
    if normalization:
        layers.append(nn.InstanceNorm2d(out_filters, affine=True))
    return layers


class Backbone(nn.Module):
    def __init__(self, backbone_coef=8):
        super(Backbone, self).__init__()
        self.backbone_coef = backbone_coef
        self.model = nn.Sequential(
            nn.Upsample(size=(256,256),mode='bilinear'),
            nn.Conv2d(3, backbone_coef, 3, stride=2, padding=1), #8 x 128 x 128
            nn.LeakyReLU(0.2),
            nn.InstanceNorm2d(backbone_coef, affine=True),
            *discriminator_block(backbone_coef, 2*backbone_coef, normalization=True), #16 x 64 x 64
            *discriminator_block(2*backbone_coef, 4*backbone_coef, normalization=True), #32 x 32 x 32
            *discriminator_block(4*backbone_coef, 8*backbone_coef, normalization=True), #64 x 16 x 16
            *discriminator_block(8*backbone_coef, 8*backbone_coef),   #64 x 8 x 8
            #*discriminator_block(128, 128, normalization=True),
            nn.Dropout(p=0.5),
            nn.AvgPool2d(5, stride=2) #64 x 2 x 2
        )

    def forward(self, img_input):

        return self.model(img_input).view([-1,self.backbone_coef*32])


class Gen_2D_SVD_LUT(nn.Module):
    def __init__(self, n_colors=3, ch_per_lut = 3, n_lut_dim=2, n_vertices=17, n_feats=256, n_ranks=24, n_singlar=8):
        super(Gen_2D_SVD_LUT, self).__init__()
        
        # h0
        self.weights_generator = nn.Linear(n_feats, n_ranks)
        
        self.n_svd = n_vertices * n_singlar + n_singlar + n_singlar * n_vertices
        # h1
        self.basis_luts_bank = nn.Linear(
            n_ranks, n_colors * ch_per_lut * self.n_svd)

        self.n_colors = n_colors
        self.n_vertices = n_vertices
        self.n_feats = n_feats
        self.n_ranks = n_ranks
        self.ch_per_lut = ch_per_lut
        self.n_singlar = n_singlar
    
    def init_weights(self):
        r"""Init weights for models.

        For the mapping f (`backbone`) and h (`lut_generator`), we follow the initialization in
            [3D-LUT](https://github.com/HuiZeng/Image-Adaptive-3DLUT).

        """
        nn.init.ones_(self.weights_generator.bias)
        nn.init.zeros_(self.basis_luts_bank.bias)
        cols, rows = torch.stack(torch.meshgrid(*[torch.arange(self.n_vertices) for _ in range(2)]),dim=0).div(self.n_vertices - 1).flip(0)
        zero2d = torch.zeros(self.n_vertices, self.n_vertices)
        d = torch.stack([cols,cols,zero2d, 
                         rows,zero2d,cols,
                         zero2d,rows,rows], dim=0)
        
        u,s,v =torch.svd(d)
        
        u = u[:,:,:self.n_singlar].contiguous().view([3*self.ch_per_lut,-1])
        s = s[:,:self.n_singlar]
        v = v[:,:,:self.n_singlar].mT.contiguous().view([3*self.ch_per_lut,-1])
        
        d= torch.cat([u,s,v], dim=1)
        
        identity_lut = torch.stack([d,*[torch.zeros(3 * self.ch_per_lut, self.n_svd) for _ in range(self.n_ranks - 1)]], dim=0).view(self.n_ranks, -1)
        
        self.basis_luts_bank.weight.data.copy_(identity_lut.t())
       
    def forward(self, img_feature):
        weights = self.weights_generator(img_feature)
        lut_svd = self.basis_luts_bank(weights)

        lut_svd = lut_svd.view([-1, self.n_svd])
        
        lut_u = lut_svd[:,:self.n_vertices * self.n_singlar]
        lut_s = lut_svd[:,self.n_vertices * self.n_singlar:self.n_vertices * self.n_singlar + self.n_singlar]
        lut_v = lut_svd[:,self.n_vertices * self.n_singlar + self.n_singlar:]
        
        lut_u = lut_u.view([-1, self.n_vertices, self.n_singlar])
        lut_s = torch.diag_embed(lut_s)
        lut_v = lut_v.view([-1, self.n_singlar, self.n_vertices])
        
        luts = torch.bmm(torch.bmm(lut_u,lut_s), lut_v)
        
        luts = luts.view([-1,self.n_colors, self.ch_per_lut, self.n_vertices,self.n_vertices])
        return luts, weights


class Gen_2D_LUT_weight_bias(nn.Module):
    def __init__(self, n_colors=3, ch_per_lut = 3, n_vertices=17, n_feats=256, n_ranks=24):
        super(Gen_2D_LUT_weight_bias, self).__init__()
        
        # h0
        self.weights_generator = nn.Linear(n_feats, n_ranks)
        # h1
        self.basis_luts_bank = nn.Linear(
            n_ranks, n_colors * (ch_per_lut + 1))

        self.n_colors = n_colors
        self.n_vertices = n_vertices
        self.n_feats = n_feats
        self.n_ranks = n_ranks
        self.ch_per_lut = ch_per_lut
  
    
    def init_weights(self):
        nn.init.ones_(self.weights_generator.bias)
        nn.init.zeros_(self.basis_luts_bank.bias)
        
        d = torch.tensor([[0.5,0.5,0,0],
                         [0.5,0,0.5,0],
                         [0,0.5,0.5,0]])
        
        
        identity_lut = torch.stack([d,
            *[torch.zeros(self.n_colors, self.ch_per_lut + 1) for _ in range(self.n_ranks - 1)]], dim=0).view(self.n_ranks, -1)
        self.basis_luts_bank.weight.data.copy_(identity_lut.t())
       
    def forward(self, img_feature):
        weights = self.weights_generator(img_feature)
        weights_bias = self.basis_luts_bank(weights)

        weights_bias = weights_bias.view([-1,self.n_colors, self.ch_per_lut + 1])
        lut_param_weights = weights_bias[:, :, :self.n_colors]
        lut_param_bias = weights_bias[:, :, self.n_colors:]
        return lut_param_weights, lut_param_bias


class Gen_2D_bilateral_grids(nn.Module):
    def __init__(self, n_grid_dim=2, n_vertices=17, n_feats=256, n_ranks=24, ch_per_grid=2):
        super(Gen_2D_bilateral_grids, self).__init__()
        
        # h0
        self.weights_generator = nn.Linear(n_feats, n_ranks)
        # h1
        self.basis_grids_bank = nn.Linear(
            n_ranks, ch_per_grid * 3 * 3 * (n_vertices ** n_grid_dim))

        self.n_grid_dim = n_grid_dim
        self.n_vertices = n_vertices
        self.n_feats = n_feats
        self.n_ranks = n_ranks
        self.ch_per_grid = ch_per_grid
        self.n_grids = ch_per_grid * 3
  
    
    def init_weights(self):
        r"""Init weights for models.

        For the mapping f (`backbone`) and h (`lut_generator`), we follow the initialization in
            [3D-LUT](https://github.com/HuiZeng/Image-Adaptive-3DLUT).

        """
        nn.init.ones_(self.weights_generator.bias)
        nn.init.zeros_(self.basis_grids_bank.bias)
        cols, rows = torch.stack(torch.meshgrid(*[torch.arange(self.n_vertices) for _ in range(2)]),dim=0).div(self.n_vertices - 1).flip(0)
        zero2d = torch.zeros(self.n_vertices, self.n_vertices)
        d = torch.stack([*[zero2d,rows,rows, 
                         zero2d,rows,rows,
                         zero2d,rows,rows] * self.ch_per_grid], dim=0)
        identity_grid = torch.stack([d,*[torch.zeros(self.n_grids * 3,self.n_vertices, self.n_vertices) for _ in range(self.n_ranks - 1)]], dim=0).view(self.n_ranks, -1)
        self.basis_grids_bank.weight.data.copy_(identity_grid.t())
        
    def forward(self, img_feature):
        weights = self.weights_generator(img_feature)
        grids = self.basis_grids_bank(weights)
        grids = grids.view([-1,self.n_grids,3,self.n_vertices,self.n_vertices])
        return grids, weights


class Gen_2D_bilateral_grids_weight_bias(nn.Module):
    def __init__(self, n_colors=3,  ch_per_grid=2, n_vertices=17, n_feats=256, n_ranks=24):
        super(Gen_2D_bilateral_grids_weight_bias, self).__init__()
        
        # h0
        self.weights_generator = nn.Linear(n_feats, n_ranks)
        # h1
        self.basis_luts_bank = nn.Linear(
            n_ranks, ch_per_grid * (3 * n_colors  + n_colors))

        self.n_colors = n_colors
        self.n_vertices = n_vertices
        self.n_feats = n_feats
        self.n_ranks = n_ranks
        self.ch_per_grid = ch_per_grid

  
    
    def init_weights(self):
        r"""Init weights for models.

        For the mapping f (`backbone`) and h (`lut_generator`), we follow the initialization in
            [3D-LUT](https://github.com/HuiZeng/Image-Adaptive-3DLUT).

        """
        nn.init.ones_(self.weights_generator.bias)
        nn.init.zeros_(self.basis_luts_bank.bias)
        d = torch.tensor([*[[0,1,1,0],
                         [0,1,1,0],
                         [0,1,1,0]] * self.ch_per_grid]).div(self.ch_per_grid * 2)
       
        
        identity_lut = torch.stack([d,
            *[torch.zeros(3*self.ch_per_grid, self.n_colors + 1) for _ in range(self.n_ranks - 1)]], dim=0).view(self.n_ranks, -1)
        self.basis_luts_bank.weight.data.copy_(identity_lut.t())
       
    def forward(self, img_feature):
        weights = self.weights_generator(img_feature)
        weights_bias = self.basis_luts_bank(weights)
        
        weights_bias = weights_bias.view([-1,self.ch_per_grid, 3 *self.n_colors + self.n_colors])
        
        grid_param_weights = weights_bias[:, :, : 3 * self.n_colors]
        grid_param_bias = weights_bias[:, :, 3 * self.n_colors:]
        return grid_param_weights, grid_param_bias




class SVDLUTNet(nn.Module):
    """
    SVDLUT 调色网络(纯 torch)/ pure-torch SVDLUT color net.

    构造参数据 FiveK sRGB 权重形状反推:LUT 33 点、grid 17 点、各 ranks=8、ch_per_grid=2。
    forward 在 Task 3(P2)接入 svdlut_slicing;在此之前调用 forward 抛 NotImplementedError,
    使 pipeline 优雅降级(跳过调色)。
    """

    def __init__(self):
        super().__init__()
        self.backbone = Backbone(backbone_coef=8)
        n_feats = 256  # 32 * backbone_coef
        self.gen_2d_lut = Gen_2D_SVD_LUT(
            n_vertices=33, n_feats=n_feats, n_ranks=8, n_singlar=8)
        self.gen_2d_lut_weight_bias = Gen_2D_LUT_weight_bias(
            n_vertices=33, n_feats=n_feats, n_ranks=8)
        self.gen_2d_bilateral = Gen_2D_bilateral_grids(
            n_vertices=17, n_feats=n_feats, n_ranks=8, ch_per_grid=2)
        self.gen_2d_grid_weight_bias = Gen_2D_bilateral_grids_weight_bias(
            n_vertices=17, n_feats=n_feats, n_ranks=8, ch_per_grid=2)

    def forward(self, img):
        # Task 3(P2)接入纯 torch 切片;此前抛错 → pipeline 优雅降级跳过调色。
        raise NotImplementedError(
            "SVDLUT slicing 待 P2 接入 / pure-torch slicing not wired yet")
