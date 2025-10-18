#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析 report.csv 中各评判标准的相关性
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from scipy.stats import pearsonr, spearmanr

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei']
plt.rcParams['axes.unicode_minus'] = False

# 读取数据
df = pd.read_csv('/Volumes/990PRO4TB/2025/2025-08-17/_tmp/report.csv')

# 只保留有鸟的数据
df = df[df['是否有鸟'] == '是'].copy()

print(f"总共有 {len(df)} 张有鸟的照片")
print("\n" + "="*80)
print("数据概览:")
print("="*80)
print(df.head())

# 提取关键的数值列
numeric_cols = ['置信度', '鸟占比', '原始锐度', '归一化锐度', 'NIMA美学', 'BRISQUE技术', 'MUSIQ综合', '居中', '锐度达标']

# 转换百分比
df['鸟占比_数值'] = df['鸟占比'].str.rstrip('%').astype(float)

# 转换布尔值为数值
df['居中_数值'] = (df['居中'] == '是').astype(int)
df['锐度达标_数值'] = (df['锐度达标'] == '是').astype(int)
df['面积达标_数值'] = (df['面积达标'] == '是').astype(int)

# 统计星级分布
print("\n" + "="*80)
print("星级分布:")
print("="*80)
star_counts = df['星等'].value_counts().sort_index()
for star, count in star_counts.items():
    print(f"{star}: {count} 张 ({count/len(df)*100:.1f}%)")

# 分析星级与各指标的关系
print("\n" + "="*80)
print("不同星级的指标均值:")
print("="*80)

star_groups = df.groupby('星等')
stats_cols = ['置信度', '鸟占比_数值', '归一化锐度', 'NIMA美学', 'BRISQUE技术', 'MUSIQ综合']

for col in stats_cols:
    print(f"\n{col}:")
    for star in ['⭐', '⭐⭐', '⭐⭐⭐']:
        if star in star_groups.groups:
            mean_val = star_groups.get_group(star)[col].mean()
            print(f"  {star}: {mean_val:.2f}")

# 计算相关性矩阵
print("\n" + "="*80)
print("指标相关性分析 (Pearson 相关系数):")
print("="*80)

corr_cols = ['置信度', '鸟占比_数值', '归一化锐度', 'NIMA美学', 'BRISQUE技术', 'MUSIQ综合',
             '居中_数值', '锐度达标_数值', '面积达标_数值']
corr_df = df[corr_cols].corr()

print("\n完整相关性矩阵:")
print(corr_df.round(3))

# 重点分析：哪些指标与你原有的判断标准（锐度、面积、居中）相关
print("\n" + "="*80)
print("原有标准 vs 新引入的IQA指标:")
print("="*80)

original_criteria = ['锐度达标_数值', '面积达标_数值', '居中_数值']
iqa_metrics = ['NIMA美学', 'BRISQUE技术', 'MUSIQ综合']

for orig in original_criteria:
    print(f"\n{orig.replace('_数值', '')}:")
    for iqa in iqa_metrics:
        corr = df[orig].corr(df[iqa])
        print(f"  与 {iqa} 的相关性: {corr:.3f}")

# 分析：锐度指标 vs BRISQUE
print("\n" + "="*80)
print("锐度指标详细分析:")
print("="*80)
print(f"归一化锐度 vs BRISQUE: {df['归一化锐度'].corr(df['BRISQUE技术']):.3f}")
print(f"归一化锐度 vs MUSIQ: {df['归一化锐度'].corr(df['MUSIQ综合']):.3f}")
print(f"归一化锐度 vs NIMA: {df['归一化锐度'].corr(df['NIMA美学']):.3f}")

# 分析置信度的影响
print("\n" + "="*80)
print("置信度与其他指标的关系:")
print("="*80)
for col in ['鸟占比_数值', '归一化锐度', 'NIMA美学', 'BRISQUE技术', 'MUSIQ综合']:
    corr = df['置信度'].corr(df[col])
    print(f"置信度 vs {col}: {corr:.3f}")

# 可视化
fig, axes = plt.subplots(2, 2, figsize=(15, 12))

# 1. 相关性热力图
sns.heatmap(corr_df, annot=True, fmt='.2f', cmap='coolwarm', center=0,
            ax=axes[0, 0], vmin=-1, vmax=1)
axes[0, 0].set_title('指标相关性热力图', fontsize=14, fontweight='bold')

# 2. 星级分布
star_counts.plot(kind='bar', ax=axes[0, 1], color=['#FFD700', '#FFA500', '#FF6347'])
axes[0, 1].set_title('星级分布', fontsize=14, fontweight='bold')
axes[0, 1].set_xlabel('星级')
axes[0, 1].set_ylabel('数量')
axes[0, 1].tick_params(axis='x', rotation=0)

# 3. 归一化锐度 vs BRISQUE
axes[1, 0].scatter(df['归一化锐度'], df['BRISQUE技术'], alpha=0.5)
axes[1, 0].set_xlabel('归一化锐度')
axes[1, 0].set_ylabel('BRISQUE技术质量')
axes[1, 0].set_title(f'锐度 vs BRISQUE (相关性: {df["归一化锐度"].corr(df["BRISQUE技术"]):.3f})',
                     fontsize=12, fontweight='bold')

# 4. 不同星级的NIMA美学分数箱线图
df.boxplot(column='NIMA美学', by='星等', ax=axes[1, 1])
axes[1, 1].set_title('不同星级的NIMA美学分数分布', fontsize=12, fontweight='bold')
axes[1, 1].set_xlabel('星级')
axes[1, 1].set_ylabel('NIMA美学分数')
plt.suptitle('')  # 移除默认标题

plt.tight_layout()
plt.savefig('/Volumes/990PRO4TB/2025/2025-08-17/_tmp/correlation_analysis.png', dpi=300, bbox_inches='tight')
print("\n可视化图表已保存到: /Volumes/990PRO4TB/2025/2025-08-17/_tmp/correlation_analysis.png")

# 关键发现总结
print("\n" + "="*80)
print("关键发现与建议:")
print("="*80)

# 找出高相关性的指标对
high_corr_pairs = []
for i in range(len(corr_df.columns)):
    for j in range(i+1, len(corr_df.columns)):
        corr_val = corr_df.iloc[i, j]
        if abs(corr_val) > 0.5:
            high_corr_pairs.append((corr_df.columns[i], corr_df.columns[j], corr_val))

print("\n高相关性指标对 (|r| > 0.5):")
for col1, col2, corr in sorted(high_corr_pairs, key=lambda x: abs(x[2]), reverse=True):
    print(f"  {col1} <-> {col2}: {corr:.3f}")

# 分析三星照片的特征
three_star = df[df['星等'] == '⭐⭐⭐']
if len(three_star) > 0:
    print(f"\n三星照片 (共{len(three_star)}张) 的特征:")
    print(f"  平均NIMA美学: {three_star['NIMA美学'].mean():.2f}")
    print(f"  平均BRISQUE: {three_star['BRISQUE技术'].mean():.2f}")
    print(f"  平均MUSIQ: {three_star['MUSIQ综合'].mean():.2f}")
    print(f"  平均归一化锐度: {three_star['归一化锐度'].mean():.2f}")
    print(f"  平均鸟占比: {three_star['鸟占比_数值'].mean():.2f}%")

print("\n" + "="*80)
