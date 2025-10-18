#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析技术指标之间的关系
专注于：锐度、BRISQUE、NIMA、YOLO置信度、鸟面积
不考虑现有星级，纯粹分析指标之间的相关性和模式
"""

import pandas as pd
import numpy as np
import sys

def analyze_technical_metrics(csv_path):
    """分析技术指标之间的关系"""

    print("=" * 90)
    print("🔬 技术指标关系分析")
    print("=" * 90)
    print(f"📂 数据源: {csv_path}\n")

    # 读取数据
    try:
        df = pd.read_csv(csv_path)
        print(f"✅ 成功读取 {len(df)} 条记录\n")
    except Exception as e:
        print(f"❌ 读取失败: {e}")
        return

    # 显示列名
    print("📋 可用字段:")
    print(f"   {', '.join(df.columns.tolist())}\n")

    # 过滤有鸟的照片
    df_birds = df[df['是否有鸟'] == '是'].copy()
    print(f"🐦 有鸟照片: {len(df_birds)} 张")

    if len(df_birds) == 0:
        print("❌ 没有检测到鸟的照片")
        return

    # 数据预处理
    df_birds['置信度_数值'] = pd.to_numeric(df_birds['置信度'], errors='coerce')
    df_birds['归一化锐度_数值'] = pd.to_numeric(df_birds['归一化锐度'], errors='coerce')
    df_birds['原始锐度_数值'] = pd.to_numeric(df_birds['原始锐度'], errors='coerce')
    df_birds['NIMA_数值'] = pd.to_numeric(df_birds['NIMA美学'], errors='coerce')
    df_birds['BRISQUE_数值'] = pd.to_numeric(df_birds['BRISQUE技术'], errors='coerce')

    # 处理鸟占比（去掉百分号）
    df_birds['鸟占比_数值'] = df_birds['鸟占比'].str.rstrip('%').astype(float)

    # 移除缺失值
    df_valid = df_birds.dropna(subset=['置信度_数值', '归一化锐度_数值', 'NIMA_数值', 'BRISQUE_数值'])
    print(f"📊 有效数据: {len(df_valid)} 张 (包含所有技术指标)\n")

    if len(df_valid) < 5:
        print("⚠️  有效数据太少，分析结果可能不可靠")
        return

    # ===== 1. 基础统计 =====
    print("=" * 90)
    print("【1】基础统计信息")
    print("=" * 90)

    stats = pd.DataFrame({
        'AI置信度': df_valid['置信度_数值'].describe(),
        '归一化锐度': df_valid['归一化锐度_数值'].describe(),
        '原始锐度': df_valid['原始锐度_数值'].describe(),
        'NIMA美学': df_valid['NIMA_数值'].describe(),
        'BRISQUE技术': df_valid['BRISQUE_数值'].describe(),
        '鸟占比%': df_valid['鸟占比_数值'].describe()
    }).round(2)

    print(stats.to_string())
    print()

    # 分析分布特征
    print("💡 分布特征:")

    # NIMA分布
    nima_mean = df_valid['NIMA_数值'].mean()
    nima_std = df_valid['NIMA_数值'].std()
    print(f"   NIMA: 均值={nima_mean:.2f}, 标准差={nima_std:.2f}", end="")
    if nima_std < 0.3:
        print(" → 变化很小，区分能力弱 ⚠️")
    elif nima_std < 0.5:
        print(" → 变化较小，区分能力一般 📊")
    else:
        print(" → 有明显变化，区分能力好 ✅")

    # BRISQUE分布
    brisque_mean = df_valid['BRISQUE_数值'].mean()
    brisque_std = df_valid['BRISQUE_数值'].std()
    print(f"   BRISQUE: 均值={brisque_mean:.2f}, 标准差={brisque_std:.2f}", end="")
    if brisque_std < 3:
        print(" → 变化很小，区分能力弱 ⚠️")
    elif brisque_std < 8:
        print(" → 变化较小，区分能力一般 📊")
    else:
        print(" → 有明显变化，区分能力好 ✅")

    # 锐度分布
    sharp_mean = df_valid['归一化锐度_数值'].mean()
    sharp_std = df_valid['归一化锐度_数值'].std()
    print(f"   归一化锐度: 均值={sharp_mean:.2f}, 标准差={sharp_std:.2f}", end="")
    if sharp_std < 500:
        print(" → 变化很小，区分能力弱 ⚠️")
    elif sharp_std < 1500:
        print(" → 变化较小，区分能力一般 📊")
    else:
        print(" → 有明显变化，区分能力好 ✅")

    print()

    # ===== 2. 相关性矩阵 =====
    print("=" * 90)
    print("【2】指标相关性矩阵")
    print("=" * 90)

    corr_data = df_valid[[
        '置信度_数值',
        '归一化锐度_数值',
        '原始锐度_数值',
        'NIMA_数值',
        'BRISQUE_数值',
        '鸟占比_数值'
    ]].corr()

    # 重命名显示
    corr_data.index = ['AI置信度', '归一化锐度', '原始锐度', 'NIMA', 'BRISQUE', '鸟占比%']
    corr_data.columns = ['AI置信度', '归一化锐度', '原始锐度', 'NIMA', 'BRISQUE', '鸟占比%']

    print(corr_data.round(3).to_string())
    print()

    # ===== 3. 关键相关性解读 =====
    print("=" * 90)
    print("【3】关键相关性解读")
    print("=" * 90)

    def interpret_correlation(r, name1, name2):
        """解释相关系数"""
        abs_r = abs(r)
        direction = "正相关" if r > 0 else "负相关"

        if abs_r >= 0.7:
            strength = "强"
            icon = "🔴"
        elif abs_r >= 0.4:
            strength = "中等"
            icon = "🟡"
        elif abs_r >= 0.2:
            strength = "弱"
            icon = "🟢"
        else:
            strength = "极弱/无"
            icon = "⚪"

        return f"{icon} {name1} vs {name2}: {r:+.3f} ({strength}{direction})"

    print("\n📌 锐度相关:")
    print(f"   {interpret_correlation(corr_data.loc['归一化锐度', 'BRISQUE'], '归一化锐度', 'BRISQUE')}")
    print(f"      → 理论预期: 负相关（锐度高，BRISQUE低）")

    print(f"   {interpret_correlation(corr_data.loc['归一化锐度', 'NIMA'], '归一化锐度', 'NIMA')}")
    print(f"      → 如果相关性低，说明NIMA不只是评估清晰度")

    print(f"   {interpret_correlation(corr_data.loc['归一化锐度', 'AI置信度'], '归一化锐度', 'AI置信度')}")
    print(f"      → 如果正相关，说明清晰的照片AI识别更准确")

    print("\n📌 NIMA相关:")
    print(f"   {interpret_correlation(corr_data.loc['NIMA', 'BRISQUE'], 'NIMA', 'BRISQUE')}")
    print(f"      → 理论预期: 负相关（美学高，技术质量好）")

    print(f"   {interpret_correlation(corr_data.loc['NIMA', 'AI置信度'], 'NIMA', 'AI置信度')}")
    print(f"      → 如果相关性低，说明美学与识别准确度无关")

    print(f"   {interpret_correlation(corr_data.loc['NIMA', '鸟占比%'], 'NIMA', '鸟占比')}")
    print(f"      → 如果正相关，说明NIMA偏好鸟占比大的构图")

    print("\n📌 BRISQUE相关:")
    print(f"   {interpret_correlation(corr_data.loc['BRISQUE', 'AI置信度'], 'BRISQUE', 'AI置信度')}")
    print(f"      → 如果负相关，说明技术质量好的照片AI识别更准")

    print(f"   {interpret_correlation(corr_data.loc['BRISQUE', '鸟占比%'], 'BRISQUE', '鸟占比')}")
    print(f"      → 如果负相关，说明鸟占比大的照片技术质量更好")

    print("\n📌 AI置信度相关:")
    print(f"   {interpret_correlation(corr_data.loc['AI置信度', '鸟占比%'], 'AI置信度', '鸟占比')}")
    print(f"      → 如果正相关，说明鸟越大AI越有信心")

    print()

    # ===== 4. 指标冲突检测 =====
    print("=" * 90)
    print("【4】指标冲突检测")
    print("=" * 90)

    # 找出高锐度但低NIMA的照片
    df_valid['锐度百分位'] = df_valid['归一化锐度_数值'].rank(pct=True)
    df_valid['NIMA百分位'] = df_valid['NIMA_数值'].rank(pct=True)
    df_valid['BRISQUE百分位'] = (100 - df_valid['BRISQUE_数值']).rank(pct=True)  # 反转

    # 冲突1: 高锐度但低NIMA
    conflict1 = df_valid[(df_valid['锐度百分位'] > 0.75) & (df_valid['NIMA百分位'] < 0.25)]
    print(f"\n⚠️  冲突1: 高锐度(Top25%) 但 低NIMA(Bottom25%)")
    print(f"   数量: {len(conflict1)} 张")
    if len(conflict1) > 0:
        print(f"   示例照片:")
        print(conflict1[['文件名', '归一化锐度_数值', 'NIMA_数值', 'BRISQUE_数值', '置信度_数值']].head(5).to_string(index=False))
        print(f"   💡 这些照片很清晰，但NIMA认为不好看")

    # 冲突2: 高NIMA但低锐度
    conflict2 = df_valid[(df_valid['NIMA百分位'] > 0.75) & (df_valid['锐度百分位'] < 0.25)]
    print(f"\n⚠️  冲突2: 高NIMA(Top25%) 但 低锐度(Bottom25%)")
    print(f"   数量: {len(conflict2)} 张")
    if len(conflict2) > 0:
        print(f"   示例照片:")
        print(conflict2[['文件名', '归一化锐度_数值', 'NIMA_数值', 'BRISQUE_数值', '置信度_数值']].head(5).to_string(index=False))
        print(f"   💡 NIMA认为好看，但清晰度不够")

    # 冲突3: 高锐度但高BRISQUE（理论上不应该）
    conflict3 = df_valid[(df_valid['锐度百分位'] > 0.75) & (df_valid['BRISQUE百分位'] < 0.25)]
    print(f"\n⚠️  冲突3: 高锐度(Top25%) 但 高BRISQUE/低技术质量(Bottom25%)")
    print(f"   数量: {len(conflict3)} 张")
    if len(conflict3) > 0:
        print(f"   示例照片:")
        print(conflict3[['文件名', '归一化锐度_数值', 'NIMA_数值', 'BRISQUE_数值', '置信度_数值']].head(5).to_string(index=False))
        print(f"   💡 锐度高但BRISQUE认为技术质量差，可能是噪点或其他问题")

    # 一致性: 高锐度+低BRISQUE+高NIMA (理想照片)
    ideal = df_valid[(df_valid['锐度百分位'] > 0.75) &
                     (df_valid['BRISQUE百分位'] > 0.75) &
                     (df_valid['NIMA百分位'] > 0.75)]
    print(f"\n✅ 理想照片: 高锐度(Top25%) + 低BRISQUE(Top25%) + 高NIMA(Top25%)")
    print(f"   数量: {len(ideal)} 张 ({len(ideal)/len(df_valid)*100:.1f}%)")
    if len(ideal) > 0:
        print(f"   示例照片:")
        print(ideal[['文件名', '归一化锐度_数值', 'NIMA_数值', 'BRISQUE_数值', '置信度_数值']].head(10).to_string(index=False))

    print()

    # ===== 5. 按鸟占比分层分析 =====
    print("=" * 90)
    print("【5】按鸟占比分层分析")
    print("=" * 90)

    # 分为小鸟、中鸟、大鸟
    df_valid['鸟大小'] = pd.cut(
        df_valid['鸟占比_数值'],
        bins=[0, 15, 30, 100],
        labels=['小鸟(<15%)', '中鸟(15-30%)', '大鸟(>30%)']
    )

    size_groups = df_valid.groupby('鸟大小', observed=True).agg({
        '归一化锐度_数值': 'mean',
        'NIMA_数值': 'mean',
        'BRISQUE_数值': 'mean',
        '置信度_数值': 'mean',
        '文件名': 'count'
    }).round(2)

    size_groups.columns = ['平均锐度', '平均NIMA', '平均BRISQUE', '平均AI置信度', '照片数']

    print("\n各尺寸鸟的指标平均值:")
    print(size_groups.to_string())

    print("\n💡 趋势分析:")
    if len(size_groups) >= 2:
        # 检查鸟大小与各指标的关系
        small_idx = size_groups.index[0]
        large_idx = size_groups.index[-1]

        nima_trend = size_groups.loc[large_idx, '平均NIMA'] - size_groups.loc[small_idx, '平均NIMA']
        brisque_trend = size_groups.loc[large_idx, '平均BRISQUE'] - size_groups.loc[small_idx, '平均BRISQUE']
        sharp_trend = size_groups.loc[large_idx, '平均锐度'] - size_groups.loc[small_idx, '平均锐度']

        print(f"   鸟越大 → NIMA {nima_trend:+.2f} ", end="")
        print("(NIMA偏好大鸟构图 ✅)" if nima_trend > 0.3 else "(NIMA对鸟大小不敏感 📊)" if abs(nima_trend) < 0.3 else "(NIMA偏好小鸟构图 ⚠️)")

        print(f"   鸟越大 → BRISQUE {brisque_trend:+.2f} ", end="")
        print("(大鸟技术质量更差 ⚠️)" if brisque_trend > 3 else "(技术质量与鸟大小无关 📊)" if abs(brisque_trend) < 3 else "(大鸟技术质量更好 ✅)")

        print(f"   鸟越大 → 锐度 {sharp_trend:+.2f} ", end="")
        print("(大鸟更清晰 ✅)" if sharp_trend > 500 else "(锐度与鸟大小无关 📊)" if abs(sharp_trend) < 500 else "(小鸟更清晰 ⚠️)")

    print()

    # ===== 6. Top/Bottom 对比 =====
    print("=" * 90)
    print("【6】极端值对比")
    print("=" * 90)

    print("\n🏆 Top 10 照片对比:\n")

    # 按NIMA排序
    print("【按 NIMA 排序 - Top 10】")
    top_nima = df_valid.nlargest(10, 'NIMA_数值')[
        ['文件名', 'NIMA_数值', '归一化锐度_数值', 'BRISQUE_数值', '置信度_数值', '鸟占比_数值']
    ]
    top_nima.columns = ['文件名', 'NIMA', '锐度', 'BRISQUE', 'AI置信度', '鸟占比%']
    print(top_nima.to_string(index=False))
    print(f"平均值: NIMA={top_nima['NIMA'].mean():.2f}, 锐度={top_nima['锐度'].mean():.2f}, BRISQUE={top_nima['BRISQUE'].mean():.2f}")

    # 按锐度排序
    print("\n【按 锐度 排序 - Top 10】")
    top_sharp = df_valid.nlargest(10, '归一化锐度_数值')[
        ['文件名', 'NIMA_数值', '归一化锐度_数值', 'BRISQUE_数值', '置信度_数值', '鸟占比_数值']
    ]
    top_sharp.columns = ['文件名', 'NIMA', '锐度', 'BRISQUE', 'AI置信度', '鸟占比%']
    print(top_sharp.to_string(index=False))
    print(f"平均值: NIMA={top_sharp['NIMA'].mean():.2f}, 锐度={top_sharp['锐度'].mean():.2f}, BRISQUE={top_sharp['BRISQUE'].mean():.2f}")

    # 按BRISQUE排序(越低越好)
    print("\n【按 BRISQUE 排序 - Top 10 (越低越好)】")
    top_brisque = df_valid.nsmallest(10, 'BRISQUE_数值')[
        ['文件名', 'NIMA_数值', '归一化锐度_数值', 'BRISQUE_数值', '置信度_数值', '鸟占比_数值']
    ]
    top_brisque.columns = ['文件名', 'NIMA', '锐度', 'BRISQUE', 'AI置信度', '鸟占比%']
    print(top_brisque.to_string(index=False))
    print(f"平均值: NIMA={top_brisque['NIMA'].mean():.2f}, 锐度={top_brisque['锐度'].mean():.2f}, BRISQUE={top_brisque['BRISQUE'].mean():.2f}")

    print()

    # ===== 7. 综合结论 =====
    print("=" * 90)
    print("【7】综合结论和建议")
    print("=" * 90)

    # 提取关键相关系数
    r_sharp_nima = corr_data.loc['归一化锐度', 'NIMA']
    r_sharp_brisque = corr_data.loc['归一化锐度', 'BRISQUE']
    r_nima_brisque = corr_data.loc['NIMA', 'BRISQUE']
    r_conf_sharp = corr_data.loc['AI置信度', '归一化锐度']
    r_nima_area = corr_data.loc['NIMA', '鸟占比%']

    conclusions = []

    # 1. NIMA的独立性
    if abs(r_sharp_nima) < 0.3:
        conclusions.append("✅ NIMA与锐度相关性很低，提供了独立于清晰度的美学评估")
        nima_value = "高"
    elif abs(r_sharp_nima) < 0.5:
        conclusions.append("📊 NIMA与锐度有一定相关性，但仍有独立价值")
        nima_value = "中等"
    else:
        conclusions.append("⚠️ NIMA与锐度高度相关，可能主要在评估清晰度")
        nima_value = "低"

    # 2. BRISQUE的有效性
    if r_sharp_brisque < -0.4:
        conclusions.append("✅ BRISQUE与锐度负相关符合预期，能有效评估技术质量")
        brisque_value = "高"
    elif r_sharp_brisque < -0.2:
        conclusions.append("📊 BRISQUE与锐度有一定负相关，基本符合预期")
        brisque_value = "中等"
    else:
        conclusions.append("⚠️ BRISQUE与锐度相关性异常，可能不适用于此数据集")
        brisque_value = "低"

    # 3. NIMA与BRISQUE的关系
    if r_nima_brisque < -0.3:
        conclusions.append("✅ NIMA与BRISQUE负相关，美学好的照片技术质量也好")
    elif abs(r_nima_brisque) < 0.3:
        conclusions.append("📊 NIMA与BRISQUE相关性弱，美学和技术质量相对独立")
    else:
        conclusions.append("⚠️ NIMA与BRISQUE正相关，违反常理")

    # 4. AI置信度与锐度
    if r_conf_sharp > 0.3:
        conclusions.append("✅ AI置信度与锐度正相关，清晰照片识别更准确")
    elif abs(r_conf_sharp) < 0.3:
        conclusions.append("📊 AI置信度与锐度相关性弱，识别准确度与清晰度无关")

    # 5. NIMA对鸟大小的偏好
    if r_nima_area > 0.3:
        conclusions.append("⚠️ NIMA偏好鸟占比大的构图，可能不公平")
    elif abs(r_nima_area) < 0.3:
        conclusions.append("✅ NIMA对鸟占比无明显偏好，评估相对公平")

    print("\n🔍 发现:")
    for i, conclusion in enumerate(conclusions, 1):
        print(f"   {i}. {conclusion}")

    # 权重建议
    print("\n🎯 星级评定权重建议:\n")

    if nima_value == "高" and brisque_value == "高":
        print("   【推荐方案】平衡方案 - NIMA和BRISQUE都有价值")
        print("   ─────────────────────────────────")
        print("   AI置信度      30%  ← 确认主体")
        print("   归一化锐度    25%  ← 清晰度")
        print("   NIMA美学      20%  ← 美学评估 ⭐")
        print("   鸟占比/居中   15%  ← 构图")
        print("   (100-BRISQUE) 10%  ← 技术质量 ⭐")

    elif nima_value == "高" and brisque_value != "高":
        print("   【推荐方案】NIMA优先方案")
        print("   ─────────────────────────────────")
        print("   AI置信度      30%  ← 确认主体")
        print("   归一化锐度    25%  ← 清晰度")
        print("   NIMA美学      25%  ← 美学评估 ⭐")
        print("   鸟占比/居中   15%  ← 构图")
        print("   (100-BRISQUE)  5%  ← 技术参考")

    elif nima_value != "高" and brisque_value == "高":
        print("   【推荐方案】技术优先方案")
        print("   ─────────────────────────────────")
        print("   AI置信度      30%  ← 确认主体")
        print("   归一化锐度    25%  ← 清晰度")
        print("   鸟占比/居中   20%  ← 构图")
        print("   (100-BRISQUE) 15%  ← 技术质量 ⭐")
        print("   NIMA美学      10%  ← 美学参考")

    else:
        print("   【推荐方案】保守方案 - NIMA和BRISQUE价值有限")
        print("   ─────────────────────────────────")
        print("   AI置信度      35%  ← 确认主体")
        print("   归一化锐度    30%  ← 清晰度优先")
        print("   鸟占比/居中   20%  ← 构图")
        print("   NIMA美学      10%  ← 仅作参考")
        print("   (100-BRISQUE)  5%  ← 仅作参考")

    print("\n" + "=" * 90)
    print("分析完成！")
    print("=" * 90)


if __name__ == "__main__":
    # 默认路径
    default_path = "/Volumes/990PRO4TB/2025/2025-10-17/_tmp/report.csv"

    if len(sys.argv) > 1:
        csv_path = sys.argv[1]
    else:
        csv_path = default_path

    analyze_technical_metrics(csv_path)
