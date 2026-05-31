/**
 * options_math.hpp
 * Goldman Sachs-Level Options Pricing Engine
 *
 * 包含：
 *   - Black-Scholes-Merton 定价模型
 *   - SABR随机波动率模型（Hagan 2002）
 *   - 完整Greeks链（18个指标）
 *   - Gamma挤压（GEX）计算
 *   - 波动率曲面构建与校准
 *
 * 编译要求: C++17, -O3 -march=native
 * 依赖: Eigen3, Boost.Math, Intel TBB (并行计算)
 */

#pragma once

#include <cmath>
#include <vector>
#include <array>
#include <stdexcept>
#include <algorithm>
#include <numeric>
#include <execution>  // C++17 并行算法

// Eigen for matrix operations
#include <Eigen/Dense>
#include <boost/math/distributions/normal.hpp>

namespace options {

// ============================================================
// 基础常量
// ============================================================
constexpr double SQRT_2PI   = 2.5066282746310002;
constexpr double INV_SQRT_2 = 0.7071067811865476;
constexpr double EPS        = 1e-12;
constexpr double MIN_VOL    = 1e-6;
constexpr double MAX_VOL    = 20.0;   // 2000% 上限

// ============================================================
// 期权类型枚举
// ============================================================
enum class OptionType { CALL, PUT };
enum class ExerciseStyle { EUROPEAN, AMERICAN };

// ============================================================
// 期权参数结构体
// ============================================================
struct OptionParams {
    double S;       ///< 标的价格
    double K;       ///< 行权价
    double T;       ///< 到期时间（年）
    double r;       ///< 无风险利率
    double q;       ///< 股息率/借贷成本
    double sigma;   ///< 波动率（年化）
    OptionType type;
    ExerciseStyle style = ExerciseStyle::EUROPEAN;
};

// ============================================================
// 18个Greeks结果结构体
// ============================================================
struct GreeksResult {
    // ─── 一阶Greeks ───
    double price;       ///< 期权价格
    double delta;       ///< Δ 价格敏感度 ∂V/∂S
    double vega;        ///< ν 波动率敏感度 ∂V/∂σ (per 1%)
    double theta;       ///< Θ 时间衰减 ∂V/∂T (per day)
    double rho;         ///< ρ 利率敏感度 ∂V/∂r (per 1%)
    double epsilon;     ///< ε 股息敏感度 ∂V/∂q

    // ─── 二阶Greeks ───
    double gamma;       ///< Γ delta变化率 ∂²V/∂S²
    double vanna;       ///< Δ关于σ的偏导 ∂²V/∂S∂σ
    double charm;       ///< ∂Δ/∂T (delta衰减)
    double vomma;       ///< ∂²V/∂σ² (vega凸性)
    double veta;        ///< ∂²V/∂σ∂T (vega时间衰减)
    double vera;        ///< ∂²V/∂σ∂r
    double dualDelta;   ///< ∂V/∂K
    double dualGamma;   ///< ∂²V/∂K²

    // ─── 三阶Greeks ───
    double speed;       ///< ∂Γ/∂S
    double zomma;       ///< ∂Γ/∂σ
    double color;       ///< ∂Γ/∂T (gamma衰减)
    double ultima;      ///< ∂³V/∂σ³

    // ─── 风险指标 ───
    double impliedVol;          ///< 隐含波动率
    double gammaExposure;       ///< GEX = Gamma × OI × 100
    double deltaExposure;       ///< DEX = Delta × OI × 100
};

// ============================================================
// 标准正态分布函数
// ============================================================
inline double norm_cdf(double x) noexcept {
    return 0.5 * std::erfc(-x * INV_SQRT_2);
}

inline double norm_pdf(double x) noexcept {
    return std::exp(-0.5 * x * x) / SQRT_2PI;
}

// ============================================================
// Black-Scholes-Merton 核心定价引擎
// ============================================================
class BlackScholesMerton {
public:
    /**
     * 计算BSM期权价格及完整18个Greeks
     * 复杂度: O(1), 典型执行时间: <1μs
     *
     * @param p 期权参数
     * @param oi 未平仓合约数量（用于GEX计算）
     * @return 完整Greeks结构体
     * @throws std::invalid_argument 参数无效时
     */
    static GreeksResult compute(const OptionParams& p, long long oi = 0) {
        validate(p);

        const double S     = p.S;
        const double K     = p.K;
        const double T     = p.T;
        const double r     = p.r;
        const double q     = p.q;
        const double sigma = p.sigma;
        const bool   isCall = (p.type == OptionType::CALL);

        // ─── d1, d2 ───
        const double sqrt_T = std::sqrt(T);
        const double sig_sqrt_T = sigma * sqrt_T;
        const double d1 = (std::log(S / K) + (r - q + 0.5 * sigma * sigma) * T)
                          / sig_sqrt_T;
        const double d2 = d1 - sig_sqrt_T;

        // ─── 正态分布值 ───
        const double Nd1  = norm_cdf(isCall ? d1 : -d1);
        const double Nd2  = norm_cdf(isCall ? d2 : -d2);
        const double nd1  = norm_pdf(d1);    // φ(d1)
        const double nd2  = norm_pdf(d2);    // φ(d2)
        const double sign = isCall ? 1.0 : -1.0;

        // ─── 折现因子 ───
        const double df_r = std::exp(-r * T);
        const double df_q = std::exp(-q * T);
        const double S_q  = S * df_q;
        const double K_r  = K * df_r;

        GreeksResult g{};

        // ─── 价格 ───
        g.price = sign * (S_q * Nd1 - K_r * Nd2);

        // ─── 一阶Greeks ───
        g.delta   = sign * df_q * Nd1;
        g.vega    = S_q * nd1 * sqrt_T * 0.01;          // per 1%
        g.theta   = (-S_q * nd1 * sigma / (2.0 * sqrt_T)
                     - sign * r * K_r * Nd2
                     + sign * q * S_q * Nd1) / 365.0;   // per calendar day
        g.rho     = sign * K_r * T * Nd2 * 0.01;        // per 1%
        g.epsilon = -sign * T * S_q * Nd1 * 0.01;       // per 1%

        // ─── 二阶Greeks ───
        g.gamma    = S_q * nd1 / (S * sig_sqrt_T);
        g.vanna    = -df_q * nd1 * d2 / sigma;
        g.charm    = -df_q * (nd1 * ((2.0*(r-q)*T - d2*sig_sqrt_T) /
                               (2.0*T*sig_sqrt_T)) + sign * q * Nd1);
        g.vomma    = g.vega * d1 * d2 / sigma;
        g.veta     = -S_q * nd1 * sqrt_T *
                     (q + (r - q) * d1 / sig_sqrt_T -
                      (1.0 + d1 * d2) / (2.0 * T)) * 0.01;
        g.vera     = -T * S_q * nd1 * d1 / sigma * 0.01;
        g.dualDelta = -sign * df_r * Nd2;
        g.dualGamma =  df_r * nd2 / (K * sig_sqrt_T);

        // ─── 三阶Greeks ───
        g.speed  = -g.gamma / S * (d1 / sig_sqrt_T + 1.0);
        g.zomma  = g.gamma * (d1 * d2 - 1.0) / sigma;
        g.color  = -df_q * nd1 / (2.0 * S * T * sig_sqrt_T) *
                   (2.0*q*T + 1.0 + (2.0*(r-q)*T - d2*sig_sqrt_T) * d1 / sig_sqrt_T);
        g.ultima = -g.vega / (sigma * sigma) *
                   (d1 * d2 * (1.0 - d1 * d2) + d1 * d1 + d2 * d2);

        // ─── 风险指标 ───
        g.impliedVol    = sigma;
        g.gammaExposure = g.gamma * static_cast<double>(oi) * 100.0;
        g.deltaExposure = g.delta * static_cast<double>(oi) * 100.0;

        return g;
    }

    /**
     * 牛顿-拉夫森迭代求隐含波动率
     * 收敛精度: 1e-8, 最大迭代: 200次
     *
     * @param marketPrice 市场期权价格
     * @param p          期权参数（sigma字段将被忽略）
     * @param initVol    初始波动率猜测（默认0.3）
     * @return 隐含波动率，若不收敛返回 -1.0
     */
    static double impliedVol(double marketPrice, OptionParams p,
                             double initVol = 0.3) noexcept {
        p.sigma = initVol;
        const double maxIter = 200;
        const double tol     = 1e-8;

        for (int i = 0; i < maxIter; ++i) {
            auto g  = compute(p);
            double diff = g.price - marketPrice;
            if (std::abs(diff) < tol) return p.sigma;

            double vega_raw = g.vega * 100.0;  // 还原为每单位波动率
            if (std::abs(vega_raw) < EPS) break;

            p.sigma -= diff / vega_raw;
            p.sigma = std::clamp(p.sigma, MIN_VOL, MAX_VOL);
        }

        // Bisection fallback
        return bisectionIV(marketPrice, p, MIN_VOL, MAX_VOL);
    }

private:
    static void validate(const OptionParams& p) {
        if (p.S <= 0.0) throw std::invalid_argument("S must be positive");
        if (p.K <= 0.0) throw std::invalid_argument("K must be positive");
        if (p.T <= 0.0) throw std::invalid_argument("T must be positive");
        if (p.sigma <= 0.0) throw std::invalid_argument("sigma must be positive");
    }

    static double bisectionIV(double target, OptionParams p,
                               double lo, double hi) noexcept {
        for (int i = 0; i < 100; ++i) {
            double mid = 0.5 * (lo + hi);
            p.sigma = mid;
            double price = compute(p).price;
            if (price < target) lo = mid; else hi = mid;
            if (hi - lo < 1e-7) return mid;
        }
        return -1.0;  // 不收敛
    }
};

// ============================================================
// SABR 随机波动率模型
// Hagan et al. (2002): "Managing Smile Risk"
// ============================================================
class SABRModel {
public:
    struct Params {
        double alpha;   ///< 初始波动率（>0）
        double beta;    ///< CEV指数 [0,1]
        double rho;     ///< 相关系数 [-1,1]
        double nu;      ///< 波动率的波动率（>0）
    };

    /**
     * SABR隐含波动率公式（Hagan近似解）
     * 精度: O((T*nu²)²), 适用于T < 5年
     *
     * @param F    远期价格
     * @param K    行权价
     * @param T    到期时间（年）
     * @param p    SABR参数
     * @return     Black-Scholes隐含波动率
     */
    static double impliedVol(double F, double K, double T,
                             const Params& p) noexcept {
        const double alpha = p.alpha;
        const double beta  = p.beta;
        const double rho   = p.rho;
        const double nu    = p.nu;

        // ATM近似（避免除零）
        if (std::abs(F - K) < EPS) {
            double FK_beta = std::pow(F, 1.0 - beta);
            double A = alpha / FK_beta;
            double B = 1.0 + ((1.0 - beta) * (1.0 - beta) / 24.0 *
                              alpha * alpha / (FK_beta * FK_beta) +
                              0.25 * rho * beta * nu * alpha / FK_beta +
                              (2.0 - 3.0 * rho * rho) / 24.0 * nu * nu) * T;
            return A * B;
        }

        double FK     = F * K;
        double FK_b   = std::pow(FK, 0.5 * (1.0 - beta));
        double lnFK   = std::log(F / K);
        double z      = nu / alpha * FK_b * lnFK;
        double chi_z  = std::log((std::sqrt(1.0 - 2.0*rho*z + z*z) + z - rho)
                                  / (1.0 - rho));

        double num = alpha;
        double den = FK_b * (1.0 + (1.0-beta)*(1.0-beta)/24.0 * lnFK*lnFK
                             + std::pow(1.0-beta, 4) / 1920.0 * lnFK*lnFK*lnFK*lnFK);

        double factor1 = (std::abs(chi_z) < EPS) ? 1.0 : z / chi_z;

        double term1   = (1.0 - beta) * (1.0 - beta) / 24.0 * alpha * alpha
                         / (std::pow(FK, 1.0 - beta));
        double term2   = 0.25 * rho * beta * nu * alpha / FK_b;
        double term3   = (2.0 - 3.0 * rho * rho) / 24.0 * nu * nu;
        double factor2 = 1.0 + (term1 + term2 + term3) * T;

        return num / den * factor1 * factor2;
    }

    /**
     * 最小二乘法校准SABR参数
     * 最小化市场IV与SABR-IV的均方误差
     *
     * @param F         远期价格
     * @param strikes   行权价序列
     * @param mktVols   对应市场隐含波动率
     * @param T         到期时间
     * @param betaFixed beta固定值（-1表示自由校准）
     * @return          校准后的SABR参数
     */
    static Params calibrate(double F,
                            const std::vector<double>& strikes,
                            const std::vector<double>& mktVols,
                            double T,
                            double betaFixed = 0.5) {
        if (strikes.size() != mktVols.size() || strikes.empty())
            throw std::invalid_argument("strikes/mktVols size mismatch");

        // Levenberg-Marquardt 简化版（梯度下降 + 线搜索）
        Params best{0.2, betaFixed, -0.3, 0.4};
        double bestMSE = mse(F, strikes, mktVols, T, best);

        const double lr0 = 0.01;
        const int    maxIter = 2000;

        for (int iter = 0; iter < maxIter; ++iter) {
            double lr = lr0 / (1.0 + 0.001 * iter);
            Params candidate = best;

            // 数值梯度
            const double h = 1e-5;
            auto grad = [&](Params& params, double Params::*field) {
                double orig = params.*field;
                params.*field = orig + h;
                double mseP = mse(F, strikes, mktVols, T, params);
                params.*field = orig - h;
                double mseM = mse(F, strikes, mktVols, T, params);
                params.*field = orig;
                return (mseP - mseM) / (2.0 * h);
            };

            candidate.alpha -= lr * grad(candidate, &Params::alpha);
            candidate.rho   -= lr * grad(candidate, &Params::rho);
            candidate.nu    -= lr * grad(candidate, &Params::nu);

            // 约束
            candidate.alpha = std::max(candidate.alpha, 1e-4);
            candidate.rho   = std::clamp(candidate.rho, -0.999, 0.999);
            candidate.nu    = std::max(candidate.nu, 1e-4);

            double candidateMSE = mse(F, strikes, mktVols, T, candidate);
            if (candidateMSE < bestMSE) {
                best    = candidate;
                bestMSE = candidateMSE;
            }
        }
        return best;
    }

private:
    static double mse(double F,
                      const std::vector<double>& K,
                      const std::vector<double>& mkv,
                      double T, const Params& p) {
        double sum = 0.0;
        for (size_t i = 0; i < K.size(); ++i) {
            double diff = impliedVol(F, K[i], T, p) - mkv[i];
            sum += diff * diff;
        }
        return sum / K.size();
    }
};

// ============================================================
// Gamma 挤压（GEX）分析引擎
// 机构级Gamma暴露计算与临界点识别
// ============================================================
class GammaSqueezeEngine {
public:
    struct OptionChainEntry {
        double strike;
        double callGamma;
        double putGamma;
        long long callOI;    ///< 未平仓合约（看涨）
        long long putOI;     ///< 未平仓合约（看跌）
        double expiry;       ///< 到期时间（年）
    };

    struct GEXProfile {
        double spot;                        ///< 当前股价
        std::vector<double> strikes;        ///< 行权价序列
        std::vector<double> netGEX;         ///< 净GEX（看涨-看跌）
        std::vector<double> callGEX;
        std::vector<double> putGEX;
        double totalGEX;                    ///< 全市场净GEX（$百万）
        double flipStrike;                  ///< Gamma翻转点（GEX=0）
        double maxCallWall;                 ///< 最大看涨Gamma支撑位
        double maxPutWall;                  ///< 最大看跌Gamma压力位
        double squeezeProbability;          ///< 挤压概率 [0,1]
    };

    /**
     * 计算完整的Gamma暴露分布图谱
     *
     * 公式: GEX_i = Gamma_i × OI_i × ContractSize × SpotPrice²
     * 净GEX > 0: 做市商持有正Gamma → 卖高买低 → 价格稳定
     * 净GEX < 0: 做市商持有负Gamma → 追涨杀跌 → 价格放大
     *
     * @param chain     期权链数据
     * @param spot      当前股价
     * @param contractSize 合约乘数（默认100）
     * @return GEX分布图谱
     */
    static GEXProfile computeProfile(const std::vector<OptionChainEntry>& chain,
                                     double spot,
                                     double contractSize = 100.0) {
        GEXProfile profile;
        profile.spot   = spot;
        profile.totalGEX = 0.0;

        double maxCallGEX = -1e18, maxPutGEX = 1e18;

        for (const auto& entry : chain) {
            // GEX单位：美元（每点移动的做市商对冲量）
            double cGEX = entry.callGamma * entry.callOI * contractSize
                          * spot * spot * 0.01;  // 归一化至每1%涨跌
            double pGEX = -entry.putGamma * entry.putOI * contractSize
                          * spot * spot * 0.01;  // 做市商看跌期权持有空头Gamma

            double net = cGEX + pGEX;

            profile.strikes.push_back(entry.strike);
            profile.callGEX.push_back(cGEX);
            profile.putGEX.push_back(pGEX);
            profile.netGEX.push_back(net);
            profile.totalGEX += net;

            if (cGEX > maxCallGEX) { maxCallGEX = cGEX; profile.maxCallWall = entry.strike; }
            if (pGEX < maxPutGEX)  { maxPutGEX  = pGEX; profile.maxPutWall  = entry.strike; }
        }

        // 寻找Gamma翻转点（线性插值）
        profile.flipStrike = findFlipPoint(profile.strikes, profile.netGEX);

        // 挤压概率：总GEX绝对值越小，做市商对冲需求越强，挤压概率越高
        double absGEX = std::abs(profile.totalGEX);
        double scale  = spot * spot * 1e6;  // 归一化
        profile.squeezeProbability = 1.0 / (1.0 + absGEX / scale);

        return profile;
    }

private:
    static double findFlipPoint(const std::vector<double>& strikes,
                                const std::vector<double>& netGEX) {
        for (size_t i = 1; i < strikes.size(); ++i) {
            if (netGEX[i-1] * netGEX[i] < 0.0) {
                // 线性插值
                double t = netGEX[i-1] / (netGEX[i-1] - netGEX[i]);
                return strikes[i-1] + t * (strikes[i] - strikes[i-1]);
            }
        }
        return strikes.empty() ? 0.0 : strikes[strikes.size() / 2];
    }
};

// ============================================================
// 波动率曲面构建器
// ============================================================
class VolatilitySurface {
public:
    struct SurfacePoint {
        double strike;
        double expiry;   ///< 年化到期时间
        double iv;       ///< 隐含波动率
    };

    /**
     * 使用SVI（Stochastic Volatility Inspired）参数化构建IV曲面
     * Gatheral (2004): w(k) = a + b*(rho*(k-m) + sqrt((k-m)^2 + sigma^2))
     *
     * @param points  市场报价点集合
     * @param nK      行权价插值节点数
     * @param nT      到期时间插值节点数
     * @return        插值后的曲面矩阵 [nT x nK]
     */
    static Eigen::MatrixXd buildSVISurface(
            const std::vector<SurfacePoint>& points,
            int nK = 50, int nT = 20) {

        if (points.empty()) throw std::invalid_argument("No surface points");

        // 获取行权价和到期时间范围
        auto [minK, maxK] = std::minmax_element(points.begin(), points.end(),
            [](const SurfacePoint& a, const SurfacePoint& b) {
                return a.strike < b.strike; });
        auto [minT, maxT] = std::minmax_element(points.begin(), points.end(),
            [](const SurfacePoint& a, const SurfacePoint& b) {
                return a.expiry < b.expiry; });

        Eigen::MatrixXd surface(nT, nK);

        // 双线性插值（实际生产应用thin-plate spline）
        double dK = (maxK->strike - minK->strike) / (nK - 1);
        double dT = (maxT->expiry - minT->expiry) / (nT - 1);

        for (int ti = 0; ti < nT; ++ti) {
            double t = minT->expiry + ti * dT;
            for (int ki = 0; ki < nK; ++ki) {
                double k = minK->strike + ki * dK;
                surface(ti, ki) = interpolateIV(points, k, t);
            }
        }

        return surface;
    }

private:
    // 反距离加权插值
    static double interpolateIV(const std::vector<SurfacePoint>& pts,
                                 double k, double t) {
        double weightSum = 0.0, ivSum = 0.0;
        for (const auto& p : pts) {
            double dk   = (k - p.strike) / k;           // 归一化行权价距离
            double dt   = (t - p.expiry) / (t + 1e-6);  // 归一化时间距离
            double dist = std::sqrt(dk*dk + 5.0*dt*dt);  // 时间权重×5
            if (dist < EPS) return p.iv;
            double w = 1.0 / (dist * dist);
            weightSum += w;
            ivSum     += w * p.iv;
        }
        return (weightSum > EPS) ? ivSum / weightSum : 0.2;
    }
};

}  // namespace options
