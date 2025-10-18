#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
锐度算法偏差分析
分析当前锐度算法是否对大鸟面积存在低估问题
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
print("🔍 锐度算法偏差分析")
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

if len(df_birds) == 0:
    print("⚠️  没有检测到鸟的照片，无法分析")
    sys.exit(0)

# 数据预处理
print("🔧 数据预处理...")
df_birds['置信度_数值'] = df_birds['置信度'].astype(float)
df_birds['归一化锐度_数值'] = df_birds['归一化锐度'].astype(float)
df_birds['原始锐度_数值'] = df_birds['原始锐度'].astype(float)
df_birds['鸟占比_数值'] = df_birds['鸟占比'].str.rstrip('%').astype(float)
df_birds['像素数_数值'] = df_birds['像素数'].astype(int)

# 转换 NIMA 和 BRISQUE
df_birds['NIMA_数值'] = pd.to_numeric(df_birds['NIMA美学'], errors='coerce')
df_birds['BRISQUE_数值'] = pd.to_numeric(df_birds['BRISQUE技术'], errors='coerce')

# 移除无效数据
df_valid = df_birds.dropna(subset=['NIMA_数值', 'BRISQUE_数值'])
print(f"   有效数据: {len(df_valid)} 张\n")

# === 核心分析：锐度 vs 鸟占比 ===
print("=" * 80)
print("📊 当前锐度算法分析")
print("=" * 80)

print("\n【算法配置】")
print("  方法: variance (拉普拉斯方差)")
print("  归一化: sqrt (除以像素数平方根)")
print("  公式: 归一化锐度 = 原始锐度 / sqrt(有效像素数)")

# 计算相关性
corr_norm_area = df_valid['归一化锐度_数值'].corr(df_valid['鸟占比_数值'])
corr_raw_area = df_valid['原始锐度_数值'].corr(df_valid['鸟占比_数值'])
corr_norm_pixels = df_valid['归一化锐度_数值'].corr(df_valid['像素数_数值'])
corr_raw_pixels = df_valid['原始锐度_数值'].corr(df_valid['像素数_数值'])

print("\n【相关性分析】")
print(f"  归一化锐度 vs 鸟占比: {corr_norm_area:+.3f}")
print(f"  原始锐度 vs 鸟占比:   {corr_raw_area:+.3f}")
print(f"  归一化锐度 vs 像素数: {corr_norm_pixels:+.3f}")
print(f"  原始锐度 vs 像素数:   {corr_raw_pixels:+.3f}")

# 判断是否存在偏差
if corr_norm_area < -0.15:
    print("\n⚠️  发现问题：归一化锐度与鸟占比呈负相关！")
    print("    这意味着：鸟越大，归一化锐度越低（存在低估大鸟的倾向）")
elif corr_norm_area > 0.15:
    print("\n✅ 归一化锐度与鸟占比呈正相关")
    print("    这意味着：鸟越大，归一化锐度越高（可能存在高估大鸟的倾向）")
else:
    print("\n✅ 归一化锐度与鸟占比相关性弱")
    print("    这意味着：锐度评分基本不受鸟大小影响（相对公平）")

# === 按鸟占比分层分析 ===
print("\n" + "=" * 80)
print("📈 按鸟占比分层分析")
print("=" * 80)

df_valid['占比层级'] = pd.cut(
    df_valid['鸟占比_数值'],
    bins=[0, 5, 10, 20, 40, 100],
    labels=['极小(<5%)', '小(5-10%)', '中(10-20%)', '大(20-40%)', '极大(40%+)']
)

area_groups = df_valid.groupby('占比层级', observed=True).agg({
    '归一化锐度_数值': 'mean',
    '原始锐度_数值': 'mean',
    'NIMA_数值': 'mean',
    'BRISQUE_数值': 'mean',
    '置信度_数值': 'mean',
    '像素数_数值': 'mean',
    '文件名': 'count'
}).round(2)

area_groups.columns = ['归一化锐度', '原始锐度', 'NIMA', 'BRISQUE', 'AI置信度', '平均像素数', '照片数']
print("\n" + area_groups.to_string())

# === 计算归一化效果 ===
print("\n" + "=" * 80)
print("🔬 归一化方法对比（理论模拟）")
print("=" * 80)

# 模拟不同归一化方法
df_valid['sqrt归一化'] = df_valid['原始锐度_数值'] / np.sqrt(df_valid['像素数_数值'])
df_valid['linear归一化'] = df_valid['原始锐度_数值'] / df_valid['像素数_数值']
df_valid['log归一化'] = df_valid['原始锐度_数值'] / np.log10(df_valid['像素数_数值'] + 10)
df_valid['gentle归一化'] = df_valid['原始锐度_数值'] / (df_valid['像素数_数值'] ** 0.35)
df_valid['无归一化'] = df_valid['原始锐度_数值']

# 计算各方法与鸟占比的相关性
methods = {
    'sqrt (当前)': df_valid['sqrt归一化'].corr(df_valid['鸟占比_数值']),
    'linear (方案A)': df_valid['linear归一化'].corr(df_valid['鸟占比_数值']),
    'log (方案C)': df_valid['log归一化'].corr(df_valid['鸟占比_数值']),
    'gentle (温和)': df_valid['gentle归一化'].corr(df_valid['鸟占比_数值']),
    '无归一化': df_valid['无归一化'].corr(df_valid['鸟占比_数值'])
}

print("\n【各归一化方法与鸟占比的相关性】")
print("  (理想值接近0 = 不受鸟大小影响)\n")

for method, corr in sorted(methods.items(), key=lambda x: abs(x[1])):
    bias = "低估大鸟" if corr < -0.1 else ("高估大鸟" if corr > 0.1 else "相对公平")
    emoji = "⚠️ " if abs(corr) > 0.15 else "✅"
    print(f"  {emoji} {method:15s}: {corr:+.3f}  ({bias})")

# === 分析 NIMA/BRISQUE 与鸟占比的关系 ===
print("\n" + "=" * 80)
print("🎨 美学/技术质量 vs 鸟占比")
print("=" * 80)

corr_nima_area = df_valid['NIMA_数值'].corr(df_valid['鸟占比_数值'])
corr_brisque_area = df_valid['BRISQUE_数值'].corr(df_valid['鸟占比_数值'])

print(f"\n  NIMA美学 vs 鸟占比:   {corr_nima_area:+.3f}")
print(f"  BRISQUE技术 vs 鸟占比: {corr_brisque_area:+.3f}")

if corr_nima_area > 0.15:
    print("\n  💡 发现：鸟越大，NIMA美学评分越高")
    print("     可能原因：大鸟更容易拍出好构图，细节更清晰")

if corr_brisque_area < -0.15:
    print("\n  💡 发现：鸟越大，BRISQUE技术质量越好（分数越低）")
    print("     可能原因：大鸟填充画面，降低了压缩噪点影响")

# === 锐度与 NIMA/BRISQUE 的关系（按鸟大小分组）===
print("\n" + "=" * 80)
print("🔗 锐度 vs NIMA/BRISQUE（按鸟大小分组）")
print("=" * 80)

print("\n【小鸟组（<10%）】")
small_birds = df_valid[df_valid['鸟占比_数值'] < 10]
if len(small_birds) > 50:
    corr_small_nima = small_birds['归一化锐度_数值'].corr(small_birds['NIMA_数值'])
    corr_small_brisque = small_birds['归一化锐度_数值'].corr(small_birds['BRISQUE_数值'])
    print(f"  样本数: {len(small_birds)}")
    print(f"  归一化锐度 vs NIMA:    {corr_small_nima:+.3f}")
    print(f"  归一化锐度 vs BRISQUE: {corr_small_brisque:+.3f}")

print("\n【大鸟组（>20%）】")
large_birds = df_valid[df_valid['鸟占比_数值'] > 20]
if len(large_birds) > 50:
    corr_large_nima = large_birds['归一化锐度_数值'].corr(large_birds['NIMA_数值'])
    corr_large_brisque = large_birds['归一化锐度_数值'].corr(large_birds['BRISQUE_数值'])
    print(f"  样本数: {len(large_birds)}")
    print(f"  归一化锐度 vs NIMA:    {corr_large_nima:+.3f}")
    print(f"  归一化锐度 vs BRISQUE: {corr_large_brisque:+.3f}")

# === 结论与建议 ===
print("\n" + "=" * 80)
print("💡 结论与建议")
print("=" * 80)

print("\n【当前算法表现】")
if abs(corr_norm_area) < 0.1:
    print("  ✅ 当前 sqrt 归一化方法表现良好，基本不受鸟大小影响")
elif corr_norm_area < -0.15:
    print("  ⚠️  当前 sqrt 归一化方法对大鸟存在低估")
    print("     建议考虑切换到 'gentle' 或 'log' 归一化方法")
elif corr_norm_area > 0.15:
    print("  ⚠️  当前 sqrt 归一化方法对大鸟存在高估")
    print("     建议考虑切换到 'linear' 归一化方法")

print("\n【NIMA/BRISQUE 与鸟大小的关系】")
if corr_nima_area > 0.15 and corr_brisque_area < -0.15:
    print("  📌 大鸟照片确实在美学和技术质量上更优")
    print("  📌 这是真实的摄影规律，而非算法偏差")
    print("  📌 因此锐度算法应该尽量保持对鸟大小的中立性")
    print("     （不要因为鸟大就自动提高锐度评分）")

print("\n【优化建议】")
best_method = min(methods.items(), key=lambda x: abs(x[1]))
print(f"  🏆 最公平的归一化方法: {best_method[0]} (相关性={best_method[1]:+.3f})")

if best_method[0] != 'sqrt (当前)':
    print(f"\n  💡 建议修改 sharpness.py 中的归一化方法:")
    if 'linear' in best_method[0]:
        print("     _sharpness_calculator = MaskBasedSharpnessCalculator(method='variance', normalization='linear')")
    elif 'log' in best_method[0]:
        print("     _sharpness_calculator = MaskBasedSharpnessCalculator(method='variance', normalization='log')")
    elif 'gentle' in best_method[0]:
        print("     _sharpness_calculator = MaskBasedSharpnessCalculator(method='variance', normalization='gentle')")
else:
    print("  ✅ 当前方法已经是最优选择，无需修改")

print("\n" + "=" * 80)
print("✅ 分析完成！")
print("=" * 80)
