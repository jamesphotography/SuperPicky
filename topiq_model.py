"""
独立 TOPIQ (Top-down Image Quality Assessment) 模型实现
脱离 pyiqa 框架运行，用于鸟类摄影美学评分

基于:
- TOPIQ: A Top-down Approach from Semantics to Distortions for Image Quality Assessment
- Chaofeng Chen et al., IEEE TIP 2024
- 原始实现: https://github.com/chaofengc/IQA-PyTorch

关键优势:
- 从语义到失真的 Top-down 方法
- 对画面主体（如鸟类）有更好的语义理解
- 使用 ResNet50 骨干，比 NIMA 的 InceptionResNetV2 更轻量

依赖项: torch, timm, PIL
"""

import os
import sys
import copy
import numpy as np
import torch
from torch import nn
import torch.nn.functional as F
import torchvision.transforms.functional as TF
import torchvision.transforms as T
from PIL import Image
from collections import OrderedDict

import timm
from tools.i18n import t as _t

from config import get_best_device


# ImageNet 标准化参数
IMAGENET_DEFAULT_MEAN = [0.485, 0.456, 0.406]
IMAGENET_DEFAULT_STD = [0.229, 0.224, 0.225]


def _get_clones(module, N):
    return nn.ModuleList([copy.deepcopy(module) for i in range(N)])


def _get_activation_fn(activation):
    """Return an activation function given a string"""
    if activation == 'relu':
        return F.relu
    if activation == 'gelu':
        return F.gelu
    if activation == 'glu':
        return F.glu
    raise RuntimeError(f'activation should be relu/gelu, not {activation}.')


def dist_to_mos(dist_score: torch.Tensor) -> torch.Tensor:
    """
    Convert distribution prediction to MOS score.
    For datasets with detailed score labels, such as AVA.
    
    Args:
        dist_score: (*, C), C is the class number.
        
    Returns:
        (*, 1) MOS score.
    """
    num_classes = dist_score.shape[-1]
    mos_score = dist_score * torch.arange(1, num_classes + 1).to(dist_score)
    mos_score = mos_score.sum(dim=-1, keepdim=True)
    return mos_score


class TransformerEncoderLayer(nn.Module):
    def __init__(
        self,
        d_model,
        nhead,
        dim_feedforward=2048,
        dropout=0.1,
        activation='gelu',
        normalize_before=False,
    ):
        super().__init__()
        self.self_attn = nn.MultiheadAttention(d_model, nhead, dropout=dropout)
        self.linear1 = nn.Linear(d_model, dim_feedforward)
        self.dropout = nn.Dropout(dropout)
        self.linear2 = nn.Linear(dim_feedforward, d_model)

        self.norm1 = nn.LayerNorm(d_model)
        self.norm2 = nn.LayerNorm(d_model)
        self.dropout1 = nn.Dropout(dropout)
        self.dropout2 = nn.Dropout(dropout)

        self.activation = _get_activation_fn(activation)
        self.normalize_before = normalize_before

    def forward(self, src):
        src2 = self.norm1(src)
        q = k = src2
        src2, self.attn_map = self.self_attn(q, k, value=src2)
        src = src + self.dropout1(src2)
        src2 = self.norm2(src)
        src2 = self.linear2(self.dropout(self.activation(self.linear1(src2))))
        src = src + self.dropout2(src2)
        return src


class TransformerDecoderLayer(nn.Module):
    def __init__(
        self,
        d_model,
        nhead,
        dim_feedforward=2048,
        dropout=0.1,
        activation='gelu',
        normalize_before=False,
    ):
        super().__init__()
        self.self_attn = nn.MultiheadAttention(d_model, nhead, dropout=dropout)
        self.multihead_attn = nn.MultiheadAttention(d_model, nhead, dropout=dropout)
        self.linear1 = nn.Linear(d_model, dim_feedforward)
        self.dropout = nn.Dropout(dropout)
        self.linear2 = nn.Linear(dim_feedforward, d_model)

        self.norm1 = nn.LayerNorm(d_model)
        self.norm2 = nn.LayerNorm(d_model)
        self.norm3 = nn.LayerNorm(d_model)
        self.dropout1 = nn.Dropout(dropout)
        self.dropout2 = nn.Dropout(dropout)
        self.dropout3 = nn.Dropout(dropout)

        self.activation = _get_activation_fn(activation)
        self.normalize_before = normalize_before

    def forward(self, tgt, memory):
        memory = self.norm2(memory)
        tgt2 = self.norm1(tgt)
        tgt2, self.attn_map = self.multihead_attn(query=tgt2, key=memory, value=memory)
        tgt = tgt + self.dropout2(tgt2)
        tgt2 = self.norm3(tgt)
        tgt2 = self.linear2(self.dropout(self.activation(self.linear1(tgt2))))
        tgt = tgt + self.dropout3(tgt2)
        return tgt


class TransformerEncoder(nn.Module):
    def __init__(self, encoder_layer, num_layers):
        super().__init__()
        self.layers = _get_clones(encoder_layer, num_layers)
        self.num_layers = num_layers

    def forward(self, src):
        output = src
        for layer in self.layers:
            output = layer(output)
        return output


class TransformerDecoder(nn.Module):
    def __init__(self, decoder_layer, num_layers):
        super().__init__()
        self.layers = _get_clones(decoder_layer, num_layers)
        self.num_layers = num_layers

    def forward(self, tgt, memory):
        output = tgt
        for layer in self.layers:
            output = layer(output, memory)
        return output


class GatedConv(nn.Module):
    def __init__(self, weightdim, ksz=3):
        super().__init__()

        self.splitconv = nn.Conv2d(weightdim, weightdim * 2, 1, 1, 0)
        self.act = nn.GELU()

        self.weight_blk = nn.Sequential(
            nn.Conv2d(weightdim, 64, 1, stride=1),
            nn.GELU(),
            nn.Conv2d(64, 64, ksz, stride=1, padding=1),
            nn.GELU(),
            nn.Conv2d(64, 1, ksz, stride=1, padding=1),
            nn.Sigmoid(),
        )

    def forward(self, x):
        x1, x2 = self.splitconv(x).chunk(2, dim=1)
        weight = self.weight_blk(x2)
        x1 = self.act(x1)
        return x1 * weight


class CFANet(nn.Module):
    """
    TOPIQ CFANet 模型 - 美学评分 (IAA)
    
    使用 ResNet50 作为骨干网络，通过跨尺度注意力机制
    实现从语义到失真的从上到下的质量评估。
    
    对于鸟类摄影，这种方法能更好地理解画面主体，
    因为它首先识别语义信息（鸟的位置、姿态），
    然后再评估细节质量。
    """
    
    def __init__(
        self,
        semantic_model_name='resnet50',
        backbone_pretrain=False,  # 我们会加载完整权重，不需要 ImageNet 预训练
        use_ref=False,  # NR-IQA 模式（无参考）
        num_class=10,   # AVA 数据集使用 10 分类
        inter_dim=512,
        num_heads=4,
        num_attn_layers=1,
        dprate=0.1,
        activation='gelu',
        test_img_size=None,
    ):
        super().__init__()

        self.semantic_model_name = semantic_model_name
        self.use_ref = use_ref
        self.num_class = num_class
        self.test_img_size = test_img_size

        # =============================================================
        # ResNet50 骨干网络（仅提取特征）
        # =============================================================
        self.semantic_model = timm.create_model(
            semantic_model_name, pretrained=backbone_pretrain, features_only=True
        )
        feature_dim_list = self.semantic_model.feature_info.channels()
        
        # ImageNet 归一化参数
        self.default_mean = torch.Tensor(IMAGENET_DEFAULT_MEAN).view(1, 3, 1, 1)
        self.default_std = torch.Tensor(IMAGENET_DEFAULT_STD).view(1, 3, 1, 1)

        # =============================================================
        # 自注意力和跨尺度注意力模块
        # =============================================================
        ca_layers = sa_layers = num_attn_layers
        self.act_layer = nn.GELU() if activation == 'gelu' else nn.ReLU()
        dim_feedforward = min(4 * inter_dim, 2048)

        # 门控局部池化和自注意力
        tmp_layer = TransformerEncoderLayer(
            inter_dim,
            nhead=num_heads,
            dim_feedforward=dim_feedforward,
            normalize_before=True,
            dropout=dprate,
            activation=activation,
        )
        
        self.sa_attn_blks = nn.ModuleList()
        self.dim_reduce = nn.ModuleList()
        self.weight_pool = nn.ModuleList()
        
        for idx, dim in enumerate(feature_dim_list):
            self.weight_pool.append(GatedConv(dim))
            self.dim_reduce.append(
                nn.Sequential(
                    nn.Conv2d(dim, inter_dim, 1, 1),
                    self.act_layer,
                )
            )
            self.sa_attn_blks.append(TransformerEncoder(tmp_layer, sa_layers))

        # 跨尺度注意力
        self.attn_blks = nn.ModuleList()
        tmp_layer = TransformerDecoderLayer(
            inter_dim,
            nhead=num_heads,
            dim_feedforward=dim_feedforward,
            normalize_before=True,
            dropout=dprate,
            activation=activation,
        )
        for i in range(len(feature_dim_list) - 1):
            self.attn_blks.append(TransformerDecoder(tmp_layer, ca_layers))

        # 注意力池化和 MLP 层
        self.attn_pool = TransformerEncoderLayer(
            inter_dim,
            nhead=num_heads,
            dim_feedforward=dim_feedforward,
            normalize_before=True,
            dropout=dprate,
            activation=activation,
        )

        # 评分线性层
        linear_dim = inter_dim
        self.score_linear = nn.Sequential(
            nn.LayerNorm(linear_dim),
            nn.Linear(linear_dim, linear_dim),
            self.act_layer,
            nn.LayerNorm(linear_dim),
            nn.Linear(linear_dim, linear_dim),
            self.act_layer,
            nn.Linear(linear_dim, self.num_class),
            nn.Softmax(dim=-1),  # 输出概率分布
        )

        # 位置编码
        self.h_emb = nn.Parameter(torch.randn(1, inter_dim // 2, 32, 1))
        self.w_emb = nn.Parameter(torch.randn(1, inter_dim // 2, 1, 32))

        nn.init.trunc_normal_(self.h_emb.data, std=0.02)
        nn.init.trunc_normal_(self.w_emb.data, std=0.02)
        self._init_linear(self.dim_reduce)
        self._init_linear(self.sa_attn_blks)
        self._init_linear(self.attn_blks)
        self._init_linear(self.attn_pool)

        self.eps = 1e-8

    def _init_linear(self, m):
        for module in m.modules():
            if isinstance(module, nn.Linear):
                nn.init.kaiming_normal_(module.weight.data)
                nn.init.constant_(module.bias.data, 0)

    def preprocess(self, x):
        x = (x - self.default_mean.to(x)) / self.default_std.to(x)
        return x

    def fix_bn(self, model):
        for m in model.modules():
            if isinstance(m, nn.BatchNorm2d):
                for p in m.parameters():
                    p.requires_grad = False
                m.eval()

    def forward_cross_attention(self, x):
        # 测试时可选调整尺寸
        if not self.training and self.test_img_size is not None:
            x = TF.resize(x, self.test_img_size, antialias=True)

        x = self.preprocess(x)
        
        # 提取多尺度特征
        dist_feat_list = self.semantic_model(x)
        self.fix_bn(self.semantic_model)
        self.semantic_model.eval()

        start_level = 0
        end_level = len(dist_feat_list)

        b, c, th, tw = dist_feat_list[end_level - 1].shape
        pos_emb = torch.cat(
            (
                self.h_emb.repeat(1, 1, 1, self.w_emb.shape[3]),
                self.w_emb.repeat(1, 1, self.h_emb.shape[2], 1),
            ),
            dim=1,
        )

        token_feat_list = []
        for i in reversed(range(start_level, end_level)):
            tmp_dist_feat = dist_feat_list[i]
            
            # 门控局部池化（NR-IQA 模式）
            tmp_feat = self.weight_pool[i](tmp_dist_feat)

            if tmp_feat.shape[2] > th and tmp_feat.shape[3] > tw:
                tmp_feat = F.adaptive_avg_pool2d(tmp_feat, (th, tw))

            # 自注意力
            tmp_pos_emb = F.interpolate(
                pos_emb, size=tmp_feat.shape[2:], mode='bicubic', align_corners=False
            )
            tmp_pos_emb = tmp_pos_emb.flatten(2).permute(2, 0, 1)

            tmp_feat = self.dim_reduce[i](tmp_feat)
            tmp_feat = tmp_feat.flatten(2).permute(2, 0, 1)
            tmp_feat = tmp_feat + tmp_pos_emb

            tmp_feat = self.sa_attn_blks[i](tmp_feat)
            token_feat_list.append(tmp_feat)

        # 从高层到低层：粗到细
        query = token_feat_list[0]
        for i in range(len(token_feat_list) - 1):
            key_value = token_feat_list[i + 1]
            query = self.attn_blks[i](query, key_value)

        final_feat = self.attn_pool(query)
        out_score = self.score_linear(final_feat.mean(dim=0))

        return out_score

    def forward(self, x, return_mos=True, return_dist=False):
        """
        前向传播
        
        Args:
            x: 输入图像 (B, 3, H, W)，值范围 [0, 1]
            return_mos: 返回 MOS 分数
            return_dist: 返回概率分布
            
        Returns:
            MOS 分数 (1-10 范围) 和/或 概率分布
        """
        score = self.forward_cross_attention(x)
        mos = dist_to_mos(score)

        return_list = []
        if return_mos:
            return_list.append(mos)
        if return_dist:
            return_list.append(score)

        if len(return_list) > 1:
            return return_list
        else:
            return return_list[0]


def clean_state_dict(state_dict):
    """清理 checkpoint，移除 .module 前缀"""
    cleaned_state_dict = OrderedDict()
    for k, v in state_dict.items():
        name = k[7:] if k.startswith('module.') else k
        cleaned_state_dict[name] = v
    return cleaned_state_dict


def load_topiq_weights(model: CFANet, weight_path: str, device: torch.device) -> None:
    """
    加载 TOPIQ 预训练权重
    
    Args:
        model: CFANet 模型实例
        weight_path: 权重文件路径
        device: 目标设备
    """
    if not os.path.exists(weight_path):
        raise FileNotFoundError(f"权重文件不存在: {weight_path}")
    
    print(_t("logs.topiq_weight_loading", name=os.path.basename(weight_path)))
    try:
        state_dict = torch.load(weight_path, map_location=device, weights_only=True)
    except TypeError as exc:
        raise RuntimeError(
            "Current PyTorch version does not support safe weights_only loading. "
            "Please upgrade PyTorch to load model weights securely."
        ) from exc
    
    # pyiqa 权重格式: {'params': {...}}
    if 'params' in state_dict:
        state_dict = state_dict['params']
    
    state_dict = clean_state_dict(state_dict)
    
    # 加载权重 (strict=False 因为可能有一些额外的 buffer)
    missing, unexpected = model.load_state_dict(state_dict, strict=False)
    
    if missing:
        print(_t("logs.topiq_weight_missing", count=len(missing)))
    if unexpected:
        print(_t("logs.topiq_weight_unexpected", count=len(unexpected)))
    
    print(_t("logs.topiq_loaded"))


def get_topiq_weight_path():
    """
    获取 TOPIQ 权重文件路径
    
    支持:
    - PyInstaller 打包后的路径
    - 开发环境的 models/ 目录
    """
    weight_name = 'cfanet_iaa_ava_res50-3cd62bb3.pth'
    
    search_paths = []
    
    if hasattr(sys, '_MEIPASS'):
        search_paths.append(os.path.join(sys._MEIPASS, 'models', weight_name))
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    search_paths.append(os.path.join(base_dir, 'models', weight_name))
    search_paths.append(os.path.join(base_dir, weight_name))
    
    for path in search_paths:
        if os.path.exists(path):
            return path
    
    raise FileNotFoundError(
        f"TOPIQ 权重文件未找到。请确保 models/{weight_name} 存在。\n"
        f"搜索路径: {search_paths}"
    )


class TOPIQScorer:
    """
    TOPIQ 美学评分器封装类
    
    提供与 NIMAScorer 兼容的接口，方便替换
    """
    
    def __init__(self, device='mps'):
        """
        初始化 TOPIQ 评分器
        
        Args:
            device: 计算设备 ('mps', 'cuda', 'cpu')
        """
        self.device = get_best_device()
        self._model = None
        
    def _load_model(self):
        if self._model is None:
            print(f"🎨 初始化 TOPIQ 评分器 (设备: {self.device})...")
            weight_path = get_topiq_weight_path()
            
            self._model = CFANet()
            load_topiq_weights(self._model, weight_path, self.device)
            self._model.to(self.device)
            self._model.eval()
            
        return self._model
    
    def calculate_score(self, image_path: str) -> float:
        """
        计算 TOPIQ 美学评分
        
        Args:
            image_path: 图片路径
            
        Returns:
            美学分数 (1-10 范围)
        """
        if not os.path.exists(image_path):
            print(f"❌ 图片不存在: {image_path}")
            return None
        
        try:
            model = self._load_model()
            
            # 加载图片
            img = Image.open(image_path).convert('RGB')
            
            # 限制图片尺寸（避免内存溢出和MPS兼容性问题）
            # 使用固定 384x384 尺寸确保 adaptive_avg_pool2d 在 MPS 上正常工作
            target_size = 384
            img = img.resize((target_size, target_size), Image.LANCZOS)
            
            transform = T.ToTensor()
            img_tensor = transform(img).unsqueeze(0).to(self.device)
            
            # 计算评分
            with torch.no_grad():
                score = model(img_tensor, return_mos=True)
            
            if isinstance(score, torch.Tensor):
                score = score.item()
            
            # 恢复原始评分 (不进行 Scaling)
            return float(max(1.0, min(10.0, score)))
            
        except Exception as e:
            print(f"❌ TOPIQ 计算失败: {e}")
            import traceback
            traceback.print_exc()
            return None


if __name__ == "__main__":
    # 测试代码
    print("=" * 70)
    print("TOPIQ 独立模型测试")
    print("=" * 70)
    
    scorer = TOPIQScorer(device='mps')
    
    test_image = "img/_Z9W0960.jpg"
    
    if os.path.exists(test_image):
        print(f"\n📷 测试图片: {test_image}")
        
        import time
        start = time.time()
        score = scorer.calculate_score(test_image)
        elapsed = time.time() - start
        
        if score is not None:
            print(f"   ✅ TOPIQ 分数: {score:.2f} / 10")
            print(f"   ⏱️  耗时: {elapsed*1000:.0f}ms")
        else:
            print(f"   ❌ TOPIQ 计算失败")
    else:
        print(f"\n⚠️  测试图片不存在: {test_image}")
    
    print("\n" + "=" * 70)
