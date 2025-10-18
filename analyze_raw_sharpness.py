#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从归一化锐度倒推原始锐度（方差值）
分析原始锐度与 NIMA/BRISQUE 的真实关系
"""

import pandas as pd
import numpy as np
from pathlib import Path
import sys

# CSV 文件路径
csv_path = Path("/Volumes/990PRO4TB/2025/2025-10-17/_tmp/report.csv")

if not csv_path.exists():
    print(f"❌ CSV 文件不存在: {csv_path}")
    sys.exit(1)

print("=" * 80)
print("🔬 原始锐度（方差）vs NIMA/BRISQUE 关系分析")
print("=" * 80)
print(f"📁 数据源: {csv_path}\n")

# 读取 CSV
try:
    df = pd.read_csv(csv_path)
    print(f"✅ 成功读取 CSV 文件，共 {len(df)} 行数据\n")
except Exception as e:
    print(f"❌ 读取 CSV 失败: {e}")
    sys.exit(1)

# 只分析有鸟的照片
df_birds = df[df['是否有鸟'] == '是'].copy()
print(f"🐦 有鸟照片数量: {len(df_birds)} / {len(df)} ({len(df_birds)/len(df)*100:.1f}%)\n")

# 数据预处理
print("🔧 数据预处理...")
df_birds['置信度_数值'] = df_birds['置信度'].astype(float)
df_birds['归一化锐度_数值'] = df_birds['归一化锐度'].astype(float)
df_birds['鸟占比_数值'] = df_birds['鸟占比'].str.rstrip('%').astype(float)
df_birds['像素数_数值'] = df_birds['像素数'].astype(int)

# 转换 NIMA 和 BRISQUE
df_birds['NIMA_数值'] = pd.to_numeric(df_birds['NIMA美学'], errors='coerce')
df_birds['BRISQUE_数值'] = pd.to_numeric(df_birds['BRISQUE技术'], errors='coerce')

# 移除无效数据
df_valid = df_birds.dropna(subset=['NIMA_数值', 'BRISQUE_数值'])
print(f"   有效数据: {len(df_valid)} 张\n")

# === 倒推原始锐度（方差） ===
print("=" * 80)
print("📊 倒推原始锐度（方差值）")
print("=" * 80)

print("\n【公式推导】")
print("  已知：归一化锐度 = 原始锐度 / sqrt(有效像素数)")
print("  推导：原始锐度 = 归一化锐度 × sqrt(有效像素数)")
print()

# 计算倒推的原始锐度
df_valid['倒推原始锐度'] = df_valid['归一化锐度_数值'] * np.sqrt(df_valid['像素数_数值'])

# 验证倒推的准确性（如果CSV中有原始锐度列）
if '原始锐度' in df_valid.columns:
    df_valid['原始锐度_数值'] = df_valid['原始锐度'].astype(float)

    # 计算误差
    df_valid['误差'] = abs(df_valid['倒推原始锐度'] - df_valid['原始锐度_数值'])
    avg_error = df_valid['误差'].mean()
    max_error = df_valid['误差'].max()

    print("【倒推验证】")
    print(f"  ✅ CSV中存在原始锐度列，可以验证倒推准确性")
    print(f"  平均误差: {avg_error:.2f}")
    print(f"  最大误差: {max_error:.2f}")

    if avg_error < 1.0:
        print(f"  ✅ 倒推准确（误差 < 1.0），直接使用CSV中的原始锐度")
        df_valid['原始方差锐度'] = df_valid['原始锐度_数值']
    else:
        print(f"  ⚠️  倒推与CSV不一致，CSV中的'原始锐度'可能不是方差值")
        print(f"     将使用倒推的方差值进行分析")
        df_valid['原始方差锐度'] = df_valid['倒推原始锐度']
else:
    print("【倒推验证】")
    print(f"  ⚠️  CSV中无原始锐度列，使用倒推值")
    df_valid['原始方差锐度'] = df_valid['倒推原始锐度']

print(f"\n原始方差锐度统计:")
print(f"  最小值: {df_valid['原始方差锐度'].min():.2f}")
print(f"  最大值: {df_valid['原始方差锐度'].max():.2f}")
print(f"  平均值: {df_valid['原始方差锐度'].mean():.2f}")
print(f"  中位数: {df_valid['原始方差锐度'].median():.2f}")

# === 相关性对比分析 ===
print("\n" + "=" * 80)
print("🔗 相关性对比：归一化 vs 原始方差")
print("=" * 80)

print("\n【与 NIMA 美学的相关性】")
corr_norm_nima = df_valid['归一化锐度_数值'].corr(df_valid['NIMA_数值'])
corr_raw_nima = df_valid['原始方差锐度'].corr(df_valid['NIMA_数值'])

print(f"  归一化锐度 vs NIMA: {corr_norm_nima:+.3f}")
print(f"  原始方差 vs NIMA:   {corr_raw_nima:+.3f}")

if abs(corr_raw_nima) > abs(corr_norm_nima):
    improvement = (abs(corr_raw_nima) - abs(corr_norm_nima)) / abs(corr_norm_nima) * 100
    print(f"  ✅ 原始方差相关性更强！提升 {improvement:.1f}%")
else:
    print(f"  ⚠️  归一化锐度相关性更强")

print("\n【与 BRISQUE 技术质量的相关性】")
corr_norm_brisque = df_valid['归一化锐度_数值'].corr(df_valid['BRISQUE_数值'])
corr_raw_brisque = df_valid['原始方差锐度'].corr(df_valid['BRISQUE_数值'])

print(f"  归一化锐度 vs BRISQUE: {corr_norm_brisque:+.3f}")
print(f"  原始方差 vs BRISQUE:   {corr_raw_brisque:+.3f}")

if abs(corr_raw_brisque) > abs(corr_norm_brisque):
    improvement = (abs(corr_raw_brisque) - abs(corr_norm_brisque)) / abs(corr_norm_brisque) * 100
    print(f"  ✅ 原始方差相关性更强！提升 {improvement:.1f}%")
else:
    print(f"  ⚠️  归一化锐度相关性更强")

print("\n【与鸟占比的相关性（检查偏差）】")
corr_norm_area = df_valid['归一化锐度_数值'].corr(df_valid['鸟占比_数值'])
corr_raw_area = df_valid['原始方差锐度'].corr(df_valid['鸟占比_数值'])

print(f"  归一化锐度 vs 鸟占比: {corr_norm_area:+.3f} (越接近0越公平)")
print(f"  原始方差 vs 鸟占比:   {corr_raw_area:+.3f} (越接近0越公平)")

if abs(corr_raw_area) < abs(corr_norm_area):
    improvement = (abs(corr_norm_area) - abs(corr_raw_area)) / abs(corr_norm_area) * 100
    print(f"  ✅ 原始方差更公平！偏差减少 {improvement:.1f}%")
else:
    print(f"  ⚠️  归一化锐度更公平")

# === 按鸟大小分层分析 ===
print("\n" + "=" * 80)
print("📈 按鸟占比分层分析（对比归一化 vs 原始方差）")
print("=" * 80)

df_valid['占比层级'] = pd.cut(
    df_valid['鸟占比_数值'],
    bins=[0, 5, 10, 20, 40, 100],
    labels=['极小(<5%)', '小(5-10%)', '中(10-20%)', '大(20-40%)', '极大(40%+)']
)

area_groups = df_valid.groupby('占比层级', observed=True).agg({
    '归一化锐度_数值': 'mean',
    '原始方差锐度': 'mean',
    'NIMA_数值': 'mean',
    'BRISQUE_数值': 'mean',
    '文件名': 'count'
}).round(2)

area_groups.columns = ['归一化锐度', '原始方差锐度', 'NIMA', 'BRISQUE', '照片数']
print("\n" + area_groups.to_string())

# 计算每层的变化趋势
print("\n【锐度随鸟大小的变化趋势】")
print(f"  归一化锐度: 极小→极大 = {area_groups['归一化锐度'].iloc[0]:.2f} → {area_groups['归一化锐度'].iloc[-1]:.2f} (下降 {(1 - area_groups['归一化锐度'].iloc[-1] / area_groups['归一化锐度'].iloc[0]) * 100:.1f}%)")
print(f"  原始方差:   极小→极大 = {area_groups['原始方差锐度'].iloc[0]:.2f} → {area_groups['原始方差锐度'].iloc[-1]:.2f} (下降 {(1 - area_groups['原始方差锐度'].iloc[-1] / area_groups['原始方差锐度'].iloc[0]) * 100:.1f}%)")
print(f"  NIMA美学:   极小→极大 = {area_groups['NIMA'].iloc[0]:.2f} → {area_groups['NIMA'].iloc[-1]:.2f} (上升 {(area_groups['NIMA'].iloc[-1] / area_groups['NIMA'].iloc[0] - 1) * 100:.1f}%)")

# === 分组相关性分析 ===
print("\n" + "=" * 80)
print("🔬 分组相关性分析（小鸟 vs 大鸟）")
print("=" * 80)

small_birds = df_valid[df_valid['鸟占比_数值'] < 10]
large_birds = df_valid[df_valid['鸟占比_数值'] > 20]

print("\n【小鸟组（<10%）】")
print(f"  样本数: {len(small_birds)}")
print(f"  归一化锐度 vs NIMA:    {small_birds['归一化锐度_数值'].corr(small_birds['NIMA_数值']):+.3f}")
print(f"  原始方差 vs NIMA:      {small_birds['原始方差锐度'].corr(small_birds['NIMA_数值']):+.3f}")
print(f"  归一化锐度 vs BRISQUE: {small_birds['归一化锐度_数值'].corr(small_birds['BRISQUE_数值']):+.3f}")
print(f"  原始方差 vs BRISQUE:   {small_birds['原始方差锐度'].corr(small_birds['BRISQUE_数值']):+.3f}")

print("\n【大鸟组（>20%）】")
print(f"  样本数: {len(large_birds)}")
print(f"  归一化锐度 vs NIMA:    {large_birds['归一化锐度_数值'].corr(large_birds['NIMA_数值']):+.3f}")
print(f"  原始方差 vs NIMA:      {large_birds['原始方差锐度'].corr(large_birds['NIMA_数值']):+.3f}")
print(f"  归一化锐度 vs BRISQUE: {large_birds['归一化锐度_数值'].corr(large_birds['BRISQUE_数值']):+.3f}")
print(f"  原始方差 vs BRISQUE:   {large_birds['原始方差锐度'].corr(large_birds['BRISQUE_数值']):+.3f}")

# === 极值分析 ===
print("\n" + "=" * 80)
print("🏆 Top 10 对比：归一化锐度 vs 原始方差锐度")
print("=" * 80)

print("\n【按归一化锐度排序 Top 10】")
top_norm = df_valid.nlargest(10, '归一化锐度_数值')[
    ['文件名', '归一化锐度_数值', '原始方差锐度', 'NIMA_数值', 'BRISQUE_数值', '鸟占比_数值']
]
top_norm.columns = ['文件名', '归一化锐度', '原始方差', 'NIMA', 'BRISQUE', '鸟占比%']
print(top_norm.to_string(index=False))

print("\n【按原始方差锐度排序 Top 10】")
top_raw = df_valid.nlargest(10, '原始方差锐度')[
    ['文件名', '归一化锐度_数值', '原始方差锐度', 'NIMA_数值', 'BRISQUE_数值', '鸟占比_数值']
]
top_raw.columns = ['文件名', '归一化锐度', '原始方差', 'NIMA', 'BRISQUE', '鸟占比%']
print(top_raw.to_string(index=False))

# 分析Top 10的平均质量
print("\n【Top 10 平均质量对比】")
print(f"  按归一化锐度选出的Top 10:")
print(f"    平均NIMA: {top_norm['NIMA'].mean():.3f}")
print(f"    平均BRISQUE: {top_norm['BRISQUE'].mean():.3f}")
print(f"    平均鸟占比: {top_norm['鸟占比%'].mean():.1f}%")

print(f"\n  按原始方差选出的Top 10:")
print(f"    平均NIMA: {top_raw['NIMA'].mean():.3f}")
print(f"    平均BRISQUE: {top_raw['BRISQUE'].mean():.3f}")
print(f"    平均鸟占比: {top_raw['鸟占比%'].mean():.1f}%")

# === 总结 ===
print("\n" + "=" * 80)
print("💡 结论与建议")
print("=" * 80)

print("\n【相关性对比总结】")
improvements = []

if abs(corr_raw_nima) > abs(corr_norm_nima):
    improvements.append(f"✅ 原始方差与NIMA的相关性更强 ({abs(corr_raw_nima):.3f} > {abs(corr_norm_nima):.3f})")
else:
    improvements.append(f"⚠️  归一化锐度与NIMA的相关性更强")

if abs(corr_raw_brisque) > abs(corr_norm_brisque):
    improvements.append(f"✅ 原始方差与BRISQUE的相关性更强 ({abs(corr_raw_brisque):.3f} > {abs(corr_norm_brisque):.3f})")
else:
    improvements.append(f"⚠️  归一化锐度与BRISQUE的相关性更强")

if abs(corr_raw_area) < abs(corr_norm_area):
    improvements.append(f"✅ 原始方差对鸟大小的偏差更小 ({abs(corr_raw_area):.3f} < {abs(corr_norm_area):.3f})")
else:
    improvements.append(f"⚠️  归一化锐度对鸟大小的偏差更小")

for improvement in improvements:
    print(f"  {improvement}")

# 综合判断
improvement_count = sum(1 for s in improvements if s.startswith('✅'))

print("\n【最终建议】")
if improvement_count >= 2:
    print("  🎯 强烈建议：使用原始方差锐度（无归一化）")
    print("     理由：")
    print(f"       - 与图像质量评估指标（NIMA/BRISQUE）的相关性更强")
    print(f"       - 对不同大小的鸟更加公平")
    print(f"       - 能更准确地反映真实的图像清晰度")
    print("\n  📝 修改方法：")
    print("     在 ai_model.py 第47行修改为：")
    print("     _sharpness_calculator = MaskBasedSharpnessCalculator(method='variance', normalization=None)")
    print("\n     或在 sharpness.py 中添加 normalization=None 的处理逻辑")
else:
    print("  ⚠️  当前归一化方法已经表现良好，无需修改")
    print(f"     改进项数: {improvement_count}/3")

print("\n" + "=" * 80)
print("✅ 分析完成！")
print("=" * 80)
