#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析 NIMA 在鸟类摄影中的有效性
帮助决定 NIMA 是否应该作为星级评定因素
"""

import pandas as pd
import os

def analyze_nima_for_bird_photography():
    """分析 NIMA 评分在鸟类摄影中的表现"""

    print("=" * 80)
    print("🎨 NIMA 美学评分在鸟类摄影中的有效性分析")
    print("=" * 80)

    # 查找最新的 CSV 报告
    csv_files = []
    for root, dirs, files in os.walk('.'):
        for file in files:
            if file.endswith('.csv') and 'report' in file.lower():
                csv_path = os.path.join(root, file)
                csv_files.append(csv_path)

    if not csv_files:
        print("\n❌ 未找到 CSV 报告文件")
        print("   请先运行 SuperPicky 处理照片，生成报告后再运行此分析")
        return

    # 使用最新的报告
    latest_csv = max(csv_files, key=os.path.getmtime)
    print(f"\n📂 使用报告: {latest_csv}")

    # 读取数据
    try:
        df = pd.read_csv(latest_csv)
    except Exception as e:
        print(f"❌ 读取 CSV 失败: {e}")
        return

    # 检查必要列
    required_cols = ['文件名', 'NIMA美学', '归一化锐度', 'AI置信度', '星等']
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        print(f"⚠️  缺少必要列: {missing_cols}")
        print(f"   可用列: {df.columns.tolist()}")
        return

    # 过滤有鸟的照片
    df_birds = df[df['有鸟'] == '是'].copy()
    print(f"\n📊 数据概览:")
    print(f"   总照片数: {len(df)}")
    print(f"   有鸟照片: {len(df_birds)}")

    if len(df_birds) == 0:
        print("❌ 没有检测到鸟的照片")
        return

    # 转换数值
    df_birds['NIMA_数值'] = pd.to_numeric(df_birds['NIMA美学'], errors='coerce')
    df_birds['锐度_数值'] = pd.to_numeric(df_birds['归一化锐度'], errors='coerce')
    df_birds['置信度_数值'] = pd.to_numeric(df_birds['AI置信度'], errors='coerce')

    # 移除缺失值
    df_valid = df_birds.dropna(subset=['NIMA_数值', '锐度_数值', '置信度_数值'])
    print(f"   有效数据: {len(df_valid)} 张")

    if len(df_valid) < 10:
        print("⚠️  有效数据太少，分析结果可能不准确")

    # ===== 关键问题1: NIMA 能否区分好照片和差照片？ =====
    print("\n" + "=" * 80)
    print("【问题1】NIMA 能否区分不同星级的照片？")
    print("=" * 80)

    star_groups = df_valid.groupby('星等').agg({
        'NIMA_数值': ['mean', 'std', 'count'],
        '锐度_数值': 'mean',
        '置信度_数值': 'mean'
    }).round(3)

    print("\n各星级的 NIMA 平均分:")
    print(star_groups.to_string())

    # 检查趋势
    if '⭐⭐⭐' in star_groups.index and '⭐' in star_groups.index:
        nima_3star = star_groups.loc['⭐⭐⭐', ('NIMA_数值', 'mean')]
        nima_1star = star_groups.loc['⭐', ('NIMA_数值', 'mean')]
        diff = nima_3star - nima_1star

        print(f"\n💡 分析:")
        if diff > 0.5:
            print(f"   ✅ NIMA 能明显区分星级！3星比1星高 {diff:.2f} 分")
            print(f"      → 建议：可以将 NIMA 作为星级评定因素，权重 20-25%")
        elif diff > 0.2:
            print(f"   📊 NIMA 有一定区分能力，3星比1星高 {diff:.2f} 分")
            print(f"      → 建议：可以将 NIMA 作为辅助因素，权重 10-15%")
        elif diff > -0.2:
            print(f"   ⚠️  NIMA 区分能力较弱，3星比1星仅高 {diff:.2f} 分")
            print(f"      → 建议：NIMA 仅作参考，权重 5-10% 或不使用")
        else:
            print(f"   ❌ NIMA 可能与人工评分相反！3星比1星低 {abs(diff):.2f} 分")
            print(f"      → 建议：不要使用 NIMA 作为星级因素")

    # ===== 关键问题2: NIMA 与锐度的相关性 =====
    print("\n" + "=" * 80)
    print("【问题2】NIMA 是否只是在评估清晰度？")
    print("=" * 80)

    corr_nima_sharp = df_valid['NIMA_数值'].corr(df_valid['锐度_数值'])
    corr_nima_conf = df_valid['NIMA_数值'].corr(df_valid['置信度_数值'])

    print(f"\nNIMA 与其他指标的相关性:")
    print(f"   NIMA vs 锐度:     {corr_nima_sharp:+.3f}")
    print(f"   NIMA vs AI置信度: {corr_nima_conf:+.3f}")

    print(f"\n💡 分析:")
    if abs(corr_nima_sharp) > 0.7:
        print(f"   ⚠️  NIMA 与锐度高度相关 (r={corr_nima_sharp:.3f})")
        print(f"      → NIMA 可能只是在评估清晰度，价值有限")
        print(f"      → 建议：降低 NIMA 权重，以免与锐度重复")
    elif abs(corr_nima_sharp) > 0.4:
        print(f"   📊 NIMA 与锐度中度相关 (r={corr_nima_sharp:.3f})")
        print(f"      → NIMA 部分评估清晰度，但也包含其他因素")
        print(f"      → 建议：可以使用，但注意权重平衡")
    else:
        print(f"   ✅ NIMA 与锐度相关性较低 (r={corr_nima_sharp:.3f})")
        print(f"      → NIMA 在评估清晰度之外的美学因素（构图、色彩等）")
        print(f"      → 建议：NIMA 有独立价值，可以使用")

    # ===== 关键问题3: 高 NIMA 的照片是否真的好？ =====
    print("\n" + "=" * 80)
    print("【问题3】NIMA 最高分的照片质量如何？")
    print("=" * 80)

    top_nima = df_valid.nlargest(10, 'NIMA_数值')[
        ['文件名', 'NIMA_数值', '锐度_数值', '置信度_数值', '星等']
    ]
    top_nima.columns = ['文件名', 'NIMA', '锐度', 'AI置信度', '星级']

    print("\nNIMA 最高的 10 张照片:")
    print(top_nima.to_string(index=False))

    # 统计星级分布
    top_nima_stars = top_nima['星级'].value_counts()
    print(f"\n星级分布:")
    for star, count in top_nima_stars.items():
        print(f"   {star}: {count} 张")

    three_star_count = top_nima_stars.get('⭐⭐⭐', 0)
    if three_star_count >= 7:
        print(f"\n   ✅ Top10 中有 {three_star_count} 张是3星，NIMA 识别准确！")
    elif three_star_count >= 5:
        print(f"\n   📊 Top10 中有 {three_star_count} 张是3星，NIMA 有一定准确性")
    else:
        print(f"\n   ⚠️  Top10 中只有 {three_star_count} 张是3星，NIMA 可能不可靠")

    # ===== 关键问题4: 低 NIMA 的照片是否都差？ =====
    print("\n" + "=" * 80)
    print("【问题4】NIMA 最低分的照片是否确实质量差？")
    print("=" * 80)

    bottom_nima = df_valid.nsmallest(10, 'NIMA_数值')[
        ['文件名', 'NIMA_数值', '锐度_数值', '置信度_数值', '星等']
    ]
    bottom_nima.columns = ['文件名', 'NIMA', '锐度', 'AI置信度', '星级']

    print("\nNIMA 最低的 10 张照片:")
    print(bottom_nima.to_string(index=False))

    # 检查是否有高星级照片被误判
    bottom_nima_stars = bottom_nima['星级'].value_counts()
    three_star_in_bottom = bottom_nima_stars.get('⭐⭐⭐', 0)

    if three_star_in_bottom > 0:
        print(f"\n   ⚠️  警告：Bottom10 中有 {three_star_in_bottom} 张是3星！")
        print(f"      → NIMA 可能误判了一些好照片")
        print(f"\n   被误判的3星照片:")
        misjudged = bottom_nima[bottom_nima['星级'] == '⭐⭐⭐']
        print(misjudged.to_string(index=False))
    else:
        print(f"\n   ✅ Bottom10 中没有3星照片，NIMA 没有误判好照片")

    # ===== 最终推荐 =====
    print("\n" + "=" * 80)
    print("【最终推荐】NIMA 权重建议")
    print("=" * 80)

    # 计算综合评分
    score = 0
    reasons = []

    # 评分标准1: 能否区分星级
    if '⭐⭐⭐' in star_groups.index and '⭐' in star_groups.index:
        diff = star_groups.loc['⭐⭐⭐', ('NIMA_数值', 'mean')] - star_groups.loc['⭐', ('NIMA_数值', 'mean')]
        if diff > 0.5:
            score += 30
            reasons.append("✅ NIMA 能明显区分星级")
        elif diff > 0.2:
            score += 20
            reasons.append("📊 NIMA 有一定区分能力")
        elif diff > -0.2:
            score += 5
            reasons.append("⚠️ NIMA 区分能力较弱")
        else:
            score -= 10
            reasons.append("❌ NIMA 与星级负相关")

    # 评分标准2: 与锐度的独立性
    if abs(corr_nima_sharp) < 0.4:
        score += 25
        reasons.append("✅ NIMA 与锐度相关性低，有独立价值")
    elif abs(corr_nima_sharp) < 0.7:
        score += 10
        reasons.append("📊 NIMA 部分独立于锐度")
    else:
        score -= 10
        reasons.append("⚠️ NIMA 与锐度高度相关，价值有限")

    # 评分标准3: Top10准确性
    if three_star_count >= 7:
        score += 25
        reasons.append("✅ NIMA Top10 中多数是3星")
    elif three_star_count >= 5:
        score += 15
        reasons.append("📊 NIMA Top10 有一定准确性")
    else:
        score -= 5
        reasons.append("⚠️ NIMA Top10 准确性不高")

    # 评分标准4: 无误判
    if three_star_in_bottom == 0:
        score += 20
        reasons.append("✅ NIMA 没有将3星照片误判为低分")
    else:
        score -= 10
        reasons.append(f"⚠️ NIMA 误判了 {three_star_in_bottom} 张3星照片")

    print(f"\n综合评估得分: {score}/100")
    print(f"\n评分依据:")
    for reason in reasons:
        print(f"   {reason}")

    print(f"\n📋 权重建议:")
    if score >= 70:
        print(f"""
   🎯 推荐方案（平衡方案）：
   ──────────────────────────────────────
   AI置信度      30%  ← 确认是鸟
   归一化锐度    25%  ← 清晰度
   NIMA美学      25%  ← 美学评分 ⭐
   鸟占比/居中   15%  ← 构图
   (100-BRISQUE)  5%  ← 技术兜底
   ──────────────────────────────────────

   💡 理由: NIMA 表现优秀，能有效识别美学质量
        """)
    elif score >= 40:
        print(f"""
   🎯 推荐方案（保守方案）：
   ──────────────────────────────────────
   AI置信度      35%  ← 确认是鸟
   归一化锐度    30%  ← 清晰度优先
   鸟占比/居中   15%  ← 构图
   NIMA美学      15%  ← 美学加分 ⭐
   (100-BRISQUE)  5%  ← 技术兜底
   ──────────────────────────────────────

   💡 理由: NIMA 有一定价值，但不应作为主要因素
        """)
    else:
        print(f"""
   🎯 推荐方案（技术优先）：
   ──────────────────────────────────────
   AI置信度      35%  ← 确认是鸟
   归一化锐度    30%  ← 清晰度优先
   鸟占比/居中   20%  ← 构图
   NIMA美学       5%  ← 仅作参考 ⭐
   (100-BRISQUE) 10%  ← 技术质量
   ──────────────────────────────────────

   💡 理由: NIMA 在你的数据集上表现不佳，不建议重度使用
   ⚠️  可能原因:
      - 训练数据偏差（AVA数据集非鸟类摄影专用）
      - 鸟类摄影有独特审美标准
      - 你的星级评定标准与NIMA训练数据不一致
        """)

    print("\n" + "=" * 80)
    print("💡 使用建议:")
    print("=" * 80)
    print("""
1. 📸 人工验证: 查看 NIMA 最高分和最低分的实际照片
   - 打开上面列出的文件，看看 NIMA 评分是否符合你的审美

2. 🧪 A/B 测试: 用不同权重跑两次，对比结果
   - 一次用 NIMA 25%，一次用 NIMA 5%
   - 看哪个结果更符合你的期望

3. 📊 迭代优化: 根据实际使用效果调整权重
   - 如果发现 NIMA 经常选错，降低权重
   - 如果发现 NIMA 能找到你喜欢的照片，提高权重

4. 🎨 考虑场景: 不同拍摄条件可能需要不同权重
   - 光线好、背景简单: NIMA 可能更准
   - 光线差、环境复杂: NIMA 可能不准
    """)


if __name__ == "__main__":
    analyze_nima_for_bird_photography()
