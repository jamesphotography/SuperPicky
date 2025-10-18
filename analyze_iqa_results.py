#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IQA 结果分析脚本
分析 CSV 报告中的锐度、置信度、NIMA 和 BRISQUE 评分之间的关系
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
print("📊 IQA 评分分析报告")
print("=" * 80)
print(f"📁 数据源: {csv_path}\n")

# 读取 CSV
try:
    df = pd.read_csv(csv_path)
    print(f"✅ 成功读取 CSV 文件，共 {len(df)} 行数据\n")
except Exception as e:
    print(f"❌ 读取 CSV 失败: {e}")
    sys.exit(1)

# 显示列名
print("📋 CSV 列名:")
print(f"   {', '.join(df.columns.tolist())}\n")

# 只分析有鸟的照片
df_birds = df[df['是否有鸟'] == '是'].copy()
print(f"🐦 有鸟照片数量: {len(df_birds)} / {len(df)} ({len(df_birds)/len(df)*100:.1f}%)\n")

if len(df_birds) == 0:
    print("⚠️  没有检测到鸟的照片，无法分析")
    sys.exit(0)

# 数据清洗和转换
print("🔧 数据预处理...")

# 转换置信度
df_birds['置信度_数值'] = df_birds['置信度'].astype(float)

# 转换锐度
df_birds['归一化锐度_数值'] = df_birds['归一化锐度'].astype(float)
df_birds['原始锐度_数值'] = df_birds['原始锐度'].astype(float)

# 转换鸟占比
df_birds['鸟占比_数值'] = df_birds['鸟占比'].str.rstrip('%').astype(float)

# 转换 NIMA 和 BRISQUE（处理 "-" 值）
df_birds['NIMA_数值'] = pd.to_numeric(df_birds['NIMA美学'], errors='coerce')
df_birds['BRISQUE_数值'] = pd.to_numeric(df_birds['BRISQUE技术'], errors='coerce')

# 移除无效数据
df_valid = df_birds.dropna(subset=['NIMA_数值', 'BRISQUE_数值'])
print(f"   有效数据: {len(df_valid)} 张（含 NIMA 和 BRISQUE 评分）\n")

if len(df_valid) == 0:
    print("⚠️  没有包含 NIMA 和 BRISQUE 评分的数据")
    sys.exit(0)

# === 1. 基础统计 ===
print("=" * 80)
print("📊 基础统计")
print("=" * 80)

stats = {
    '置信度 (AI)': df_valid['置信度_数值'],
    '归一化锐度': df_valid['归一化锐度_数值'],
    '原始锐度': df_valid['原始锐度_数值'],
    '鸟占比 (%)': df_valid['鸟占比_数值'],
    'NIMA 美学 (0-10)': df_valid['NIMA_数值'],
    'BRISQUE 技术 (0-100)': df_valid['BRISQUE_数值']
}

for name, data in stats.items():
    print(f"\n{name}:")
    print(f"  最小值: {data.min():.2f}")
    print(f"  最大值: {data.max():.2f}")
    print(f"  平均值: {data.mean():.2f}")
    print(f"  中位数: {data.median():.2f}")
    print(f"  标准差: {data.std():.2f}")

# === 2. 星级分布 ===
print("\n" + "=" * 80)
print("⭐ 星级分布")
print("=" * 80)

star_counts = df_valid['星等'].value_counts()
total = len(df_valid)

for star, count in star_counts.items():
    pct = count / total * 100
    print(f"{star}: {count} 张 ({pct:.1f}%)")

# === 3. 相关性分析 ===
print("\n" + "=" * 80)
print("🔗 相关性分析 (Pearson Correlation)")
print("=" * 80)

# 计算相关系数
corr_data = df_valid[[
    '置信度_数值',
    '归一化锐度_数值',
    '原始锐度_数值',
    '鸟占比_数值',
    'NIMA_数值',
    'BRISQUE_数值'
]].corr()

print("\n相关系数矩阵 (范围: -1 到 +1):")
print("  +1.0 = 完全正相关")
print("   0.0 = 无相关")
print("  -1.0 = 完全负相关\n")

# 重点关系
relationships = [
    ('归一化锐度_数值', 'NIMA_数值', '锐度 vs NIMA美学'),
    ('归一化锐度_数值', 'BRISQUE_数值', '锐度 vs BRISQUE技术'),
    ('置信度_数值', 'NIMA_数值', 'AI置信度 vs NIMA美学'),
    ('置信度_数值', 'BRISQUE_数值', 'AI置信度 vs BRISQUE技术'),
    ('NIMA_数值', 'BRISQUE_数值', 'NIMA美学 vs BRISQUE技术'),
    ('鸟占比_数值', 'NIMA_数值', '鸟占比 vs NIMA美学'),
    ('原始锐度_数值', 'NIMA_数值', '原始锐度 vs NIMA美学'),
    ('原始锐度_数值', 'BRISQUE_数值', '原始锐度 vs BRISQUE技术'),
]

for col1, col2, name in relationships:
    corr = corr_data.loc[col1, col2]

    # 判断相关性强度
    if abs(corr) >= 0.7:
        strength = "强"
        emoji = "🔴"
    elif abs(corr) >= 0.4:
        strength = "中等"
        emoji = "🟡"
    elif abs(corr) >= 0.2:
        strength = "弱"
        emoji = "🟢"
    else:
        strength = "极弱/无关"
        emoji = "⚪"

    direction = "正相关" if corr > 0 else "负相关"

    print(f"{emoji} {name}:")
    print(f"   相关系数: {corr:+.3f} ({direction}, {strength})")

# === 4. 分层分析 ===
print("\n" + "=" * 80)
print("📈 分层分析")
print("=" * 80)

# 按星级分层
print("\n【按星级分组的平均值】")
star_groups = df_valid.groupby('星等').agg({
    '置信度_数值': 'mean',
    '归一化锐度_数值': 'mean',
    'NIMA_数值': 'mean',
    'BRISQUE_数值': 'mean',
    '文件名': 'count'
}).round(2)
star_groups.columns = ['AI置信度', '归一化锐度', 'NIMA美学', 'BRISQUE技术', '照片数量']
print(star_groups.to_string())

# 锐度分层分析
print("\n【按锐度分层】")
df_valid['锐度层级'] = pd.cut(
    df_valid['归一化锐度_数值'],
    bins=[0, 50, 100, 150, 300],
    labels=['低锐度(0-50)', '中锐度(50-100)', '高锐度(100-150)', '极高(150+)']
)

sharpness_groups = df_valid.groupby('锐度层级', observed=True).agg({
    '置信度_数值': 'mean',
    'NIMA_数值': 'mean',
    'BRISQUE_数值': 'mean',
    '文件名': 'count'
}).round(2)
sharpness_groups.columns = ['AI置信度', 'NIMA美学', 'BRISQUE技术', '照片数量']
print(sharpness_groups.to_string())

# NIMA 分层分析
print("\n【按 NIMA 美学评分分层】")
df_valid['NIMA层级'] = pd.cut(
    df_valid['NIMA_数值'],
    bins=[0, 4, 5, 6, 10],
    labels=['差(0-4)', '一般(4-5)', '良好(5-6)', '优秀(6-10)']
)

nima_groups = df_valid.groupby('NIMA层级', observed=True).agg({
    '置信度_数值': 'mean',
    '归一化锐度_数值': 'mean',
    'BRISQUE_数值': 'mean',
    '文件名': 'count'
}).round(2)
nima_groups.columns = ['AI置信度', '归一化锐度', 'BRISQUE技术', '照片数量']
print(nima_groups.to_string())

# BRISQUE 分层分析
print("\n【按 BRISQUE 技术质量分层】")
df_valid['BRISQUE层级'] = pd.cut(
    df_valid['BRISQUE_数值'],
    bins=[0, 30, 50, 70, 100],
    labels=['优秀(0-30)', '良好(30-50)', '一般(50-70)', '较差(70-100)']
)

brisque_groups = df_valid.groupby('BRISQUE层级', observed=True).agg({
    '置信度_数值': 'mean',
    '归一化锐度_数值': 'mean',
    'NIMA_数值': 'mean',
    '文件名': 'count'
}).round(2)
brisque_groups.columns = ['AI置信度', '归一化锐度', 'NIMA美学', '照片数量']
print(brisque_groups.to_string())

# === 5. 极值分析 ===
print("\n" + "=" * 80)
print("🏆 极值分析（Top 10）")
print("=" * 80)

# Top 10 NIMA
print("\n【NIMA 美学评分 Top 10】")
top_nima = df_valid.nlargest(10, 'NIMA_数值')[
    ['文件名', 'NIMA_数值', 'BRISQUE_数值', '归一化锐度_数值', '置信度_数值', '星等']
]
top_nima.columns = ['文件名', 'NIMA', 'BRISQUE', '锐度', 'AI置信度', '星级']
print(top_nima.to_string(index=False))

# Bottom 10 BRISQUE (越低越好)
print("\n【BRISQUE 技术质量 Top 10（越低越好）】")
top_brisque = df_valid.nsmallest(10, 'BRISQUE_数值')[
    ['文件名', 'BRISQUE_数值', 'NIMA_数值', '归一化锐度_数值', '置信度_数值', '星等']
]
top_brisque.columns = ['文件名', 'BRISQUE', 'NIMA', '锐度', 'AI置信度', '星级']
print(top_brisque.to_string(index=False))

# 综合优秀（高NIMA + 低BRISQUE + 高锐度）
print("\n【综合优秀照片 Top 10（NIMA高 + BRISQUE低 + 锐度高）】")
df_valid['综合得分'] = (
    df_valid['NIMA_数值'] / 10 * 0.4 +  # NIMA 归一化到 0-1，权重 40%
    (100 - df_valid['BRISQUE_数值']) / 100 * 0.4 +  # BRISQUE 反转并归一化，权重 40%
    df_valid['归一化锐度_数值'] / 200 * 0.2  # 锐度归一化（假设最大200），权重 20%
)

top_overall = df_valid.nlargest(10, '综合得分')[
    ['文件名', 'NIMA_数值', 'BRISQUE_数值', '归一化锐度_数值', '置信度_数值', '综合得分', '星等']
]
top_overall.columns = ['文件名', 'NIMA', 'BRISQUE', '锐度', 'AI置信度', '综合得分', '星级']
print(top_overall.to_string(index=False))

# === 6. 洞察总结 ===
print("\n" + "=" * 80)
print("💡 关键洞察")
print("=" * 80)

insights = []

# 锐度 vs NIMA
corr_sharp_nima = corr_data.loc['归一化锐度_数值', 'NIMA_数值']
if abs(corr_sharp_nima) >= 0.4:
    direction = "正相关" if corr_sharp_nima > 0 else "负相关"
    insights.append(f"📌 锐度与NIMA美学呈{direction} (r={corr_sharp_nima:.3f})，说明{'清晰的照片通常更美' if corr_sharp_nima > 0 else '锐度对美学影响不大'}")
else:
    insights.append(f"📌 锐度与NIMA美学相关性较弱 (r={corr_sharp_nima:.3f})，说明美学评分不完全依赖锐度")

# 锐度 vs BRISQUE
corr_sharp_brisque = corr_data.loc['归一化锐度_数值', 'BRISQUE_数值']
if corr_sharp_brisque < -0.4:
    insights.append(f"📌 锐度与BRISQUE呈负相关 (r={corr_sharp_brisque:.3f})，符合预期：锐度越高，BRISQUE越低（质量越好）")
elif corr_sharp_brisque > 0.4:
    insights.append(f"⚠️  锐度与BRISQUE呈正相关 (r={corr_sharp_brisque:.3f})，这不符合预期，可能需要检查")
else:
    insights.append(f"📌 锐度与BRISQUE相关性较弱 (r={corr_sharp_brisque:.3f})")

# NIMA vs BRISQUE
corr_nima_brisque = corr_data.loc['NIMA_数值', 'BRISQUE_数值']
if corr_nima_brisque < -0.4:
    insights.append(f"📌 NIMA与BRISQUE呈负相关 (r={corr_nima_brisque:.3f})，美学评分高的照片技术质量也好")
elif abs(corr_nima_brisque) < 0.3:
    insights.append(f"📌 NIMA与BRISQUE相关性弱 (r={corr_nima_brisque:.3f})，美学和技术质量相对独立")

# AI置信度的影响
corr_conf_nima = corr_data.loc['置信度_数值', 'NIMA_数值']
corr_conf_brisque = corr_data.loc['置信度_数值', 'BRISQUE_数值']

if abs(corr_conf_nima) >= 0.3:
    direction = "正相关" if corr_conf_nima > 0 else "负相关"
    insights.append(f"📌 AI置信度与NIMA呈{direction} (r={corr_conf_nima:.3f})")
if abs(corr_conf_brisque) >= 0.3:
    direction = "正相关" if corr_conf_brisque > 0 else "负相关"
    insights.append(f"📌 AI置信度与BRISQUE呈{direction} (r={corr_conf_brisque:.3f})")

# 星级分析
if '⭐⭐⭐' in star_groups.index:
    three_star_nima = star_groups.loc['⭐⭐⭐', 'NIMA美学']
    three_star_brisque = star_groups.loc['⭐⭐⭐', 'BRISQUE技术']
    insights.append(f"📌 3星照片平均 NIMA={three_star_nima:.2f}, BRISQUE={three_star_brisque:.2f}")

print()
for i, insight in enumerate(insights, 1):
    print(f"{i}. {insight}")

print("\n" + "=" * 80)
print("✅ 分析完成！")
print("=" * 80)
