/**
 * gamma_squeeze.hpp
 * 机构级 Gamma 挤压检测与预警系统
 *
 * 算法来源:
 *   - SpotGamma GEX方法论
 *   - SqueezeMetrics Dark Pool + GEX研究
 *   - Bouchaud & Potters "Theory of Financial Risk"
 *
 * 关键概念:
 *   GEX > 0 (正Gamma区间): 做市商buy-low sell-high → 低波动率
 *   GEX < 0 (负Gamma区间): 做市商momentum跟随 → 高波动率/挤压
 */

#pragma once
#include "options_math.hpp"
#include <map>
#include <deque>
#include <chrono>

namespace options {

// ============================================================
// 实时Gamma挤压预警引擎
// ============================================================
class GammaSqueezeMonitor {
public:
    enum class AlertLevel {
        NORMAL,       ///< 正常市场
        WATCH,        ///< 监视（GEX快速变化）
        WARNING,      ///< 预警（接近翻转点）
        CRITICAL      ///< 临界（负Gamma区间，可能挤压）
    };

    struct Alert {
        AlertLevel level;
        double     spotPrice;
        double     flipStrike;
        double     distance;        ///< 与翻转点距离(%)
        double     gexVelocity;     ///< GEX变化速率（每分钟）
        std::string message;
        std::chrono::system_clock::time_point timestamp;
    };

    struct MarketMakerHedgeFlow {
        double deltaHedgeFlow;  ///< 做市商需对冲的Delta（股数/分钟）
        double gammaHedgeFlow;  ///< Gamma再对冲触发量
        bool   isLongGamma;     ///< 做市商当前持仓方向
    };

    /**
     * 更新实时GEX并生成预警
     *
     * @param chain    最新期权链数据
     * @param spot     当前股价
     * @param prevSpot 上一tick股价（用于计算移动方向）
     */
    Alert update(const std::vector<GammaSqueezeEngine::OptionChainEntry>& chain,
                 double spot, double prevSpot) {
        auto profile = GammaSqueezeEngine::computeProfile(chain, spot);

        // 记录GEX历史用于速率计算
        gexHistory_.push_back({std::chrono::system_clock::now(), profile.totalGEX});
        if (gexHistory_.size() > 60) gexHistory_.pop_front();  // 保留60个tick

        double gexVelocity = computeGEXVelocity();
        double distToFlip  = (spot - profile.flipStrike) / spot * 100.0;

        Alert alert;
        alert.spotPrice   = spot;
        alert.flipStrike  = profile.flipStrike;
        alert.distance    = distToFlip;
        alert.gexVelocity = gexVelocity;
        alert.timestamp   = std::chrono::system_clock::now();

        // ─── 预警逻辑 ───
        if (profile.totalGEX < 0.0) {
            // 负Gamma区间：做市商放大波动
            if (std::abs(distToFlip) < 0.5) {
                alert.level   = AlertLevel::CRITICAL;
                alert.message = "⚠️ CRITICAL: 股价位于负Gamma区间，翻转点距离<0.5%，挤压风险极高";
            } else if (std::abs(distToFlip) < 2.0) {
                alert.level   = AlertLevel::WARNING;
                alert.message = "⚠️ WARNING: 负Gamma区间，距翻转点" +
                                std::to_string(std::abs(distToFlip)) + "%";
            } else {
                alert.level   = AlertLevel::WATCH;
                alert.message = "👁 WATCH: 负Gamma区间，市场波动性增加";
            }
        } else {
            // 正Gamma区间：评估接近度
            if (std::abs(distToFlip) < 1.0 && prevSpot < spot) {
                alert.level   = AlertLevel::WARNING;
                alert.message = "⚠️ 正在向翻转点移动，距离" +
                                std::to_string(std::abs(distToFlip)) + "%";
            } else {
                alert.level   = AlertLevel::NORMAL;
                alert.message = "✅ 正Gamma区间，市场稳定";
            }
        }

        lastProfile_ = profile;
        return alert;
    }

    /**
     * 估算做市商对冲流量
     * 用于预测短期价格压力方向
     *
     * @param spot      当前价格
     * @param deltaSpot 价格变动
     * @return 对冲流量估算
     */
    MarketMakerHedgeFlow estimateHedgeFlow(double spot,
                                            double deltaSpot) const {
        if (lastProfile_.strikes.empty()) return {};

        // 做市商对冲流 ≈ -GEX × ΔS / S
        // 正GEX: 价格上涨→做市商卖出对冲（卖压）
        // 负GEX: 价格上涨→做市商追买（买压）
        double hedgeFlow = -lastProfile_.totalGEX * deltaSpot / spot;

        MarketMakerHedgeFlow flow;
        flow.deltaHedgeFlow = hedgeFlow;
        flow.gammaHedgeFlow = lastProfile_.totalGEX * deltaSpot * deltaSpot
                              / (spot * spot);
        flow.isLongGamma    = (lastProfile_.totalGEX > 0.0);

        return flow;
    }

    const GammaSqueezeEngine::GEXProfile& lastProfile() const {
        return lastProfile_;
    }

private:
    struct GEXSnapshot {
        std::chrono::system_clock::time_point time;
        double gex;
    };

    std::deque<GEXSnapshot>              gexHistory_;
    GammaSqueezeEngine::GEXProfile       lastProfile_;

    double computeGEXVelocity() const {
        if (gexHistory_.size() < 2) return 0.0;
        const auto& first = gexHistory_.front();
        const auto& last  = gexHistory_.back();
        auto dt = std::chrono::duration<double, std::ratio<60>>(
            last.time - first.time).count();
        if (dt < EPS) return 0.0;
        return (last.gex - first.gex) / dt;  // $/分钟
    }
};

// ============================================================
// Vanna-Charm 流量分析
// 期权到期日和月度rebalancing引发的系统性流量
// ============================================================
class VannaCharmFlowAnalyzer {
public:
    struct FlowEstimate {
        double vannaFlow;   ///< Vanna驱动流量（IV变化×Vanna×OI）
        double charmFlow;   ///< Charm驱动流量（时间衰减×Charm×OI）
        double totalFlow;   ///< 总预期对冲流量（股数）
        std::string direction;  ///< "BUY" / "SELL"
    };

    /**
     * 估算IV变动和时间流逝引发的做市商对冲流量
     * 在VIX飙升或接近到期日时特别重要
     *
     * @param entries   期权链数据（含Greeks）
     * @param deltaIV   IV变动（如+0.02表示IV上升2pct）
     * @param deltaT    时间流逝（交易日，通常=1/252）
     * @param spotPrice 股价
     */
    static FlowEstimate estimate(
            const std::vector<std::pair<GreeksResult, long long>>& entries,
            double deltaIV, double deltaT, double spotPrice) {

        double vannaFlow = 0.0, charmFlow = 0.0;

        for (const auto& [g, oi] : entries) {
            // Vanna流量：IV变化引发的Delta对冲需求
            // ΔDelta_vanna = Vanna × ΔIV × OI × 100
            vannaFlow += g.vanna * deltaIV * oi * 100.0;

            // Charm流量：时间流逝引发的Delta对冲需求
            // ΔDelta_charm = Charm × ΔT × OI × 100
            charmFlow += g.charm * deltaT * oi * 100.0;
        }

        FlowEstimate fe;
        fe.vannaFlow  = vannaFlow;
        fe.charmFlow  = charmFlow;
        fe.totalFlow  = vannaFlow + charmFlow;
        fe.direction  = (fe.totalFlow > 0) ? "BUY" : "SELL";

        return fe;
    }
};

}  // namespace options
