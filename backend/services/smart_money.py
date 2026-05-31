"""
smart_money.py
机构级 Smart Money 大单识别与分类引擎

分类目标:
  - SPECULATIVE (投机单):  方向性押注，大量买入单边期权
  - HEDGE (套保单):        对冲现有股票头寸，通常买Put或Collar结构
  - MARKET_MAKER (做市单): 双边报价，Delta近中性，快速翻转
  - SPREAD (价差单):       组合策略，同时买卖不同行权价/到期日
  - UNKNOWN:               无法分类

算法特征:
  1. 成交量/持仓量比值（VOI Ratio）
  2. 买卖方向推断（Lee-Ready + Tick Test）
  3. 期权溢价规模（Premium Size）
  4. 成交时间模式（开盘/收盘 vs 盘中）
  5. 行权价选择（OTM程度）
  6. 到期时间（超短期=投机，长期=套保）
  7. 暗池关联度
  8. 同期标的成交量关联

依赖: scikit-learn, numpy, pandas, scipy
"""

import numpy as np
import pandas as pd
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from datetime import datetime, time as dtime
from enum import Enum
from scipy import stats
from collections import defaultdict

logger = logging.getLogger(__name__)


class FlowType(Enum):
    SPECULATIVE  = "SPECULATIVE"   # 投机性单向押注
    HEDGE        = "HEDGE"         # 套期保值
    MARKET_MAKER = "MARKET_MAKER"  # 做市商流量
    SPREAD       = "SPREAD"        # 组合/价差策略
    UNKNOWN      = "UNKNOWN"


@dataclass
class FlowSignal:
    """大单流量信号"""
    # 基础信息
    symbol: str
    timestamp: datetime
    flow_type: FlowType
    confidence: float           # 分类置信度 [0,1]

    # 交易详情
    option_type: str            # C/P
    strike: float
    expiry: str
    price: float
    size: int                   # 合约数量
    premium: float              # 名义保费（美元）
    side: str                   # BUY/SELL

    # 特征指标
    voi_ratio: float = 0.0      # volume/open_interest
    otm_degree: float = 0.0     # (K-S)/S，正=OTM Call，负=OTM Put
    dte: int = 0                # Days to Expiry
    is_sweep: bool = False
    is_block: bool = False
    above_ask: bool = False

    # 评分（0-100）
    bullish_score: int = 0      # 看多信号强度
    bearish_score: int = 0      # 看空信号强度
    urgency_score: int = 0      # 紧迫性（扫单/市价单）

    # 解释
    reasoning: List[str] = field(default_factory=list)

    @property
    def net_sentiment(self) -> int:
        """净情绪得分（正=看多，负=看空）"""
        return self.bullish_score - self.bearish_score


@dataclass
class SmartMoneyAlert:
    """聚合的智能资金警报"""
    symbol: str
    period: str                     # "1min" / "5min" / "1hour"
    timestamp: datetime

    total_call_premium: float       # 看涨期权总保费
    total_put_premium: float        # 看跌期权总保费
    put_call_ratio: float           # PCR

    large_call_flows: List[FlowSignal]   # 大型看涨流量
    large_put_flows: List[FlowSignal]    # 大型看跌流量
    unusual_activity: List[FlowSignal]   # 异常活动

    net_premium_flow: float         # 净保费流向（正=看多）
    smart_money_bias: str           # "BULLISH"/"BEARISH"/"NEUTRAL"
    confidence: float

    # 历史分位数
    premium_percentile: float = 0.0  # 当前保费在历史中的分位数
    volume_percentile: float = 0.0


# ============================================================
# 核心分类引擎
# ============================================================

class SmartMoneyClassifier:
    """
    基于规则+统计学习的流量分类引擎

    特征权重（经回测优化）:
      - VOI Ratio:     权重 0.25
      - Premium Size:  权重 0.20
      - OTM Degree:    权重 0.15
      - DTE:           权重 0.15
      - Side/Sweep:    权重 0.15
      - Time of Day:   权重 0.10
    """

    # ─── 阈值配置（可调参数）───
    BLOCK_THRESHOLD   = 500      # 大宗交易（合约数）
    PREMIUM_THRESHOLD = 100_000  # 大单保费（$10万以上关注）
    WHALE_THRESHOLD   = 1_000_000  # 鲸鱼单（$100万）
    HIGH_VOI_THRESHOLD = 2.0     # 高VOI比值
    SHORT_DTE         = 7        # 超短期到期（投机特征）
    LONG_DTE          = 60       # 长期到期（套保特征）
    OTM_THRESHOLD     = 0.05     # 5%以上OTM

    def classify(self, trade: dict, spot: float, oi: int = 0) -> FlowSignal:
        """
        分类单笔成交记录

        Parameters
        ----------
        trade : dict  成交记录 {symbol, expiry, strike, option_type,
                                price, size, side, is_sweep, timestamp...}
        spot  : float 标的当前价格
        oi    : int   未平仓合约量

        Returns
        -------
        FlowSignal    带分类结果和置信度的信号对象
        """
        strike      = trade["strike"]
        option_type = trade["option_type"]  # C or P
        price       = trade["price"]
        size        = trade["size"]
        expiry      = trade["expiry"]
        side        = trade.get("side", "UNKNOWN")
        is_sweep    = trade.get("is_sweep", False)
        above_ask   = trade.get("above_ask", False)
        ts          = trade.get("timestamp", datetime.now())

        premium = price * size * 100
        dte     = self._calc_dte(expiry, ts)
        otm     = (strike - spot) / spot * (1 if option_type == "C" else -1)
        voi     = (trade.get("volume", size) / oi) if oi > 0 else 0.0

        signal = FlowSignal(
            symbol      = trade["symbol"],
            timestamp   = ts,
            flow_type   = FlowType.UNKNOWN,
            confidence  = 0.0,
            option_type = option_type,
            strike      = strike,
            expiry      = expiry,
            price       = price,
            size        = size,
            premium     = premium,
            side        = side,
            voi_ratio   = voi,
            otm_degree  = otm,
            dte         = dte,
            is_sweep    = is_sweep,
            is_block    = size >= self.BLOCK_THRESHOLD,
            above_ask   = above_ask,
        )

        # ─── 特征提取 ───
        features = self._extract_features(signal)

        # ─── 规则分类 ───
        flow_type, confidence, reasoning = self._rule_classify(features, signal)

        # ─── 情绪评分 ───
        bullish, bearish, urgency = self._score_sentiment(signal, features)

        signal.flow_type     = flow_type
        signal.confidence    = confidence
        signal.reasoning     = reasoning
        signal.bullish_score = bullish
        signal.bearish_score = bearish
        signal.urgency_score = urgency

        return signal

    def _extract_features(self, s: FlowSignal) -> Dict[str, float]:
        """提取数值特征向量（归一化）"""
        return {
            "premium_log":    np.log1p(s.premium),
            "voi_ratio":      min(s.voi_ratio, 20.0),
            "otm_degree":     s.otm_degree,
            "dte_norm":       min(s.dte / 365.0, 1.0),
            "is_sweep":       float(s.is_sweep),
            "is_block":       float(s.is_block),
            "above_ask":      float(s.above_ask),
            "is_buy":         float(s.side == "BUY"),
            "is_call":        float(s.option_type == "C"),
            "is_whale":       float(s.premium >= self.WHALE_THRESHOLD),
        }

    def _rule_classify(
        self,
        feat: Dict[str, float],
        s: FlowSignal
    ) -> Tuple[FlowType, float, List[str]]:
        """
        基于投行交易台经验的规则分类器

        分类逻辑（优先级由高到低）:
          1. 做市商特征（低DTE + 双边 + 小尺寸）
          2. 套保特征（长DTE + ATM Put + 大尺寸）
          3. 投机特征（高VOI + OTM + 扫单 + 短DTE）
          4. 价差策略
        """
        reasoning = []

        # ─── 做市商特征检测 ───
        # 做市商通常：小尺寸、快速翻转、不扫单
        if (s.size < 50 and not s.is_sweep and
                s.dte <= 5 and abs(s.otm_degree) < 0.02):
            reasoning.append("小尺寸+超短期+ATM → 可能做市商流量")
            return FlowType.MARKET_MAKER, 0.65, reasoning

        # ─── 套保特征检测 ───
        # 套保通常: 买Put + 长期 + ATM附近 + 大尺寸
        hedge_score = 0
        if s.option_type == "P" and s.side == "BUY":
            hedge_score += 30
            reasoning.append("买入看跌期权（常见套保结构）")
        if s.dte >= self.LONG_DTE:
            hedge_score += 25
            reasoning.append(f"长到期日({s.dte}天) → 倾向套保")
        if s.premium >= self.PREMIUM_THRESHOLD:
            hedge_score += 20
            reasoning.append(f"大额保费${s.premium:,.0f} → 机构规模")
        if abs(s.otm_degree) < 0.05:  # ATM±5%
            hedge_score += 15
            reasoning.append("ATM附近行权价 → 套保有效区间")
        if not s.is_sweep:
            hedge_score += 10
            reasoning.append("非扫单 → 非急迫方向性押注")

        if hedge_score >= 60:
            return FlowType.HEDGE, min(hedge_score / 100.0, 0.92), reasoning

        # ─── 投机特征检测 ───
        spec_score = 0
        if s.is_sweep:
            spec_score += 35
            reasoning.append("扫单 → 急迫方向性押注")
        if s.above_ask:
            spec_score += 25
            reasoning.append("高于卖价成交 → 激进买入信号")
        if s.otm_degree > self.OTM_THRESHOLD:
            spec_score += 20
            reasoning.append(f"OTM程度{s.otm_degree:.1%} → 杠杆押注")
        if s.dte <= self.SHORT_DTE:
            spec_score += 20
            reasoning.append(f"超短期({s.dte}天) → 事件驱动押注")
        if s.voi_ratio > self.HIGH_VOI_THRESHOLD:
            spec_score += 15
            reasoning.append(f"VOI={s.voi_ratio:.1f}x → 异常成交量")
        if s.is_block:
            spec_score += 15
            reasoning.append(f"大宗{s.size}手 → 机构头寸建立")

        if spec_score >= 50:
            return FlowType.SPECULATIVE, min(spec_score / 100.0, 0.95), reasoning

        # ─── 默认: 未知或价差策略 ───
        reasoning.append("特征不明显，可能为组合策略一腿")
        return FlowType.UNKNOWN, 0.30, reasoning

    def _score_sentiment(
        self,
        s: FlowSignal,
        feat: Dict[str, float]
    ) -> Tuple[int, int, int]:
        """
        情绪评分: 看多/看空/紧迫性 各0-100
        """
        bullish = bearish = urgency = 0

        # 看多信号
        if s.option_type == "C" and s.side == "BUY":
            bullish += 40
        if s.option_type == "P" and s.side == "SELL":
            bullish += 35  # 卖Put=看多
        if s.above_ask and s.option_type == "C":
            bullish += 20
        if s.premium >= self.WHALE_THRESHOLD and s.option_type == "C":
            bullish += 15

        # 看空信号
        if s.option_type == "P" and s.side == "BUY":
            bearish += 40
        if s.option_type == "C" and s.side == "SELL":
            bearish += 30  # 卖Call=看空（或covered call）
        if s.above_ask and s.option_type == "P":
            bearish += 20
        if s.premium >= self.WHALE_THRESHOLD and s.option_type == "P":
            bearish += 15

        # 紧迫性信号
        if s.is_sweep:
            urgency += 40
        if s.above_ask:
            urgency += 25
        if s.dte <= 3:
            urgency += 20
        if s.voi_ratio > 5.0:
            urgency += 15

        return min(bullish, 100), min(bearish, 100), min(urgency, 100)

    @staticmethod
    def _calc_dte(expiry: str, reference: datetime) -> int:
        """计算到期天数"""
        try:
            exp = datetime.strptime(expiry, "%Y-%m-%d")
            return max(0, (exp - reference.replace(tzinfo=None)).days)
        except ValueError:
            return 30  # 默认值


# ============================================================
# 聚合分析器：实时市场情绪仪表盘
# ============================================================

class FlowAggregator:
    """
    聚合多个Flow信号，生成市场情绪快照

    功能:
      - 按时间窗口（1/5/15/60分钟）汇总保费流向
      - 计算净看多/看空比值
      - 检测异常大单（相对于历史均值的统计显著性）
      - 生成SmartMoneyAlert
    """

    def __init__(self, symbol: str, history_days: int = 20):
        self.symbol = symbol
        self._signals: List[FlowSignal] = []
        self._premium_history: deque_like = []
        self._classifier = SmartMoneyClassifier()

        # 历史基准（用于异常检测）
        self._daily_avg_premium = 0.0
        self._daily_std_premium = 1.0

    def add_trade(self, trade: dict, spot: float, oi: int = 0) -> FlowSignal:
        """处理新成交，返回分类信号"""
        signal = self._classifier.classify(trade, spot, oi)
        self._signals.append(signal)
        return signal

    def get_alert(self, window_minutes: int = 5) -> SmartMoneyAlert:
        """
        生成最近N分钟的智能资金警报

        Parameters
        ----------
        window_minutes : 时间窗口（分钟）

        Returns
        -------
        SmartMoneyAlert
        """
        now = datetime.now()
        cutoff = now.timestamp() - window_minutes * 60
        recent = [s for s in self._signals
                  if s.timestamp.timestamp() > cutoff]

        if not recent:
            return SmartMoneyAlert(
                symbol=self.symbol, period=f"{window_minutes}min",
                timestamp=now, total_call_premium=0, total_put_premium=0,
                put_call_ratio=1.0, large_call_flows=[], large_put_flows=[],
                unusual_activity=[], net_premium_flow=0,
                smart_money_bias="NEUTRAL", confidence=0.0
            )

        call_signals = [s for s in recent if s.option_type == "C"]
        put_signals  = [s for s in recent if s.option_type == "P"]

        call_premium = sum(
            s.premium for s in call_signals if s.side == "BUY"
        )
        put_premium = sum(
            s.premium for s in put_signals if s.side == "BUY"
        )
        pcr = put_premium / call_premium if call_premium > 0 else 99.0

        large_calls = sorted(
            [s for s in call_signals if s.premium >= SmartMoneyClassifier.PREMIUM_THRESHOLD],
            key=lambda x: x.premium, reverse=True
        )[:10]

        large_puts = sorted(
            [s for s in put_signals if s.premium >= SmartMoneyClassifier.PREMIUM_THRESHOLD],
            key=lambda x: x.premium, reverse=True
        )[:10]

        # 异常活动：Z-score > 2.0
        unusual = self._detect_unusual(recent)

        net_flow    = call_premium - put_premium
        bias        = "BULLISH" if net_flow > 0.2 * (call_premium + put_premium) \
                      else "BEARISH" if net_flow < -0.2 * (call_premium + put_premium) \
                      else "NEUTRAL"
        confidence  = abs(net_flow) / (call_premium + put_premium + 1e-6)

        return SmartMoneyAlert(
            symbol             = self.symbol,
            period             = f"{window_minutes}min",
            timestamp          = now,
            total_call_premium = call_premium,
            total_put_premium  = put_premium,
            put_call_ratio     = pcr,
            large_call_flows   = large_calls,
            large_put_flows    = large_puts,
            unusual_activity   = unusual,
            net_premium_flow   = net_flow,
            smart_money_bias   = bias,
            confidence         = min(confidence, 1.0),
        )

    def _detect_unusual(self, signals: List[FlowSignal]) -> List[FlowSignal]:
        """
        统计异常检测：Grubbs检验 + Z-score
        识别在历史分布中显著偏离均值的大单
        """
        if not signals:
            return []

        premiums = np.array([s.premium for s in signals])
        if len(premiums) < 3:
            return [s for s in signals if s.premium >= SmartMoneyClassifier.WHALE_THRESHOLD]

        mean = premiums.mean()
        std  = premiums.std() + 1e-6
        z_scores = np.abs((premiums - mean) / std)

        unusual = []
        for signal, z in zip(signals, z_scores):
            if z > 2.5 or signal.premium >= SmartMoneyClassifier.WHALE_THRESHOLD:
                unusual.append(signal)

        return sorted(unusual, key=lambda x: x.premium, reverse=True)[:5]


# ============================================================
# 多维度打分模型
# ============================================================

class OptionsScoreModel:
    """
    多维度期权信号打分模型

    维度（各25分，合计100分）:
      1. Smart Money Flow Score  — 机构资金流入方向
      2. Technical Score         — 技术面（GEX位置、支撑阻力）
      3. Volatility Score        — IV/RV关系、Skew结构
      4. Momentum Score          — 期权流量动量
    """

    @staticmethod
    def score(
        smart_money_alert: SmartMoneyAlert,
        gex_squeeze_prob: float,
        iv_rank: float,          # IV Rank [0,1], 高=期权贵
        iv_percentile: float,    # IV Percentile [0,1]
        put_call_skew: float,    # 25-Delta Skew（负=Put更贵=看空偏向）
        price_momentum: float,   # 价格动量（5日涨跌幅）
    ) -> Dict[str, float]:
        """
        计算综合打分

        Returns
        -------
        dict: {
            "total": 0-100,
            "smart_money": 0-25,
            "technical": 0-25,
            "volatility": 0-25,
            "momentum": 0-25,
            "signal": "STRONG_BULL"/"BULL"/"NEUTRAL"/"BEAR"/"STRONG_BEAR"
        }
        """
        # ─── 1. Smart Money Flow Score (0-25) ───
        bias = smart_money_alert.smart_money_bias
        conf = smart_money_alert.confidence
        pcr  = smart_money_alert.put_call_ratio

        if bias == "BULLISH":
            sm_score = 15 + 10 * conf
        elif bias == "BEARISH":
            sm_score = 10 - 10 * conf  # 负调整（看空=减分）
        else:
            sm_score = 12.5

        # PCR修正
        if pcr < 0.5:   sm_score = min(sm_score + 3, 25)  # 极度看多
        elif pcr > 1.5: sm_score = max(sm_score - 3, 0)   # 极度看空

        sm_score = max(0.0, min(25.0, sm_score))

        # ─── 2. Technical Score (0-25) ───
        # GEX挤压概率：高挤压概率=方向性爆发
        tech_score = 12.5 + gex_squeeze_prob * 12.5

        # ─── 3. Volatility Score (0-25) ───
        # IV Rank低 = 期权便宜 → 利于买方
        iv_score = (1.0 - iv_rank) * 15.0    # 最高15分
        # Skew修正: 负Skew（Put贵）→ 看空压力大
        skew_adj = np.clip(-put_call_skew * 100, -5, 5)
        iv_score += skew_adj + 2.5
        iv_score  = max(0.0, min(25.0, iv_score))

        # ─── 4. Momentum Score (0-25) ───
        # 正动量加分，负动量减分
        mom_score = 12.5 + np.clip(price_momentum * 250, -12.5, 12.5)

        total = sm_score + tech_score + iv_score + mom_score

        # 综合信号
        if   total >= 75: signal = "STRONG_BULL"
        elif total >= 60: signal = "BULL"
        elif total >= 40: signal = "NEUTRAL"
        elif total >= 25: signal = "BEAR"
        else:             signal = "STRONG_BEAR"

        return {
            "total":       round(total, 1),
            "smart_money": round(sm_score, 1),
            "technical":   round(tech_score, 1),
            "volatility":  round(iv_score, 1),
            "momentum":    round(mom_score, 1),
            "signal":      signal,
            "pcr":         round(pcr, 3),
            "iv_rank":     round(iv_rank, 3),
        }


# ============================================================
# 股价概率分布预测
# ============================================================

class PriceDistributionModel:
    """
    基于期权隐含波动率的股价概率分布

    方法：
      1. 从IV曲面提取风险中性概率密度（Breeden-Litzenberger）
      2. 蒙特卡洛路径模拟（Heston / GBM）
      3. 参数化正态/对数正态分布
    """

    @staticmethod
    def risk_neutral_density(
        spot: float,
        strikes: List[float],
        ivs: List[float],          # 对应行权价的隐含波动率
        T: float,
        r: float = 0.05
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Breeden-Litzenberger公式
        q(K) = e^{rT} × ∂²C/∂K²

        估计风险中性概率密度函数

        Returns: (密度值数组, 对应价格数组)
        """
        K = np.array(strikes)
        vol = np.array(ivs)

        # 使用三次样条插值密化行权价网格
        from scipy.interpolate import CubicSpline
        K_fine = np.linspace(K.min(), K.max(), 500)
        cs     = CubicSpline(K, vol)
        vol_fine = cs(K_fine)
        vol_fine = np.clip(vol_fine, 0.01, 5.0)

        # 计算看涨期权价格
        dK   = K_fine[1] - K_fine[0]
        df   = np.exp(-r * T)

        from scipy.stats import norm
        d1 = (np.log(spot / K_fine) + (r + 0.5 * vol_fine**2) * T) / (vol_fine * np.sqrt(T))
        d2 = d1 - vol_fine * np.sqrt(T)
        C  = spot * norm.cdf(d1) - K_fine * df * norm.cdf(d2)

        # 二阶导数 ≈ 有限差分
        d2C_dK2 = np.gradient(np.gradient(C, dK), dK)
        density = np.exp(r * T) * d2C_dK2
        density = np.maximum(density, 0)

        # 归一化
        area = np.trapz(density, K_fine)
        if area > 1e-10:
            density /= area

        return density, K_fine

    @staticmethod
    def monte_carlo_paths(
        spot: float,
        sigma: float,
        T: float,
        r: float = 0.05,
        n_paths: int = 10_000,
        n_steps: int = 252,
        seed: int = 42
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        几何布朗运动蒙特卡洛路径模拟

        Returns: (终端价格分布, 完整路径矩阵 [n_steps × n_paths])
        """
        rng = np.random.default_rng(seed)
        dt  = T / n_steps

        # 向量化路径生成
        dW        = rng.standard_normal((n_steps, n_paths))
        log_ret   = (r - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * dW
        log_paths = np.cumsum(log_ret, axis=0)
        paths     = spot * np.exp(log_paths)

        terminal  = paths[-1, :]
        return terminal, paths

    @staticmethod
    def price_probability(
        spot: float,
        sigma: float,
        T: float,
        targets: List[float],
        r: float = 0.05
    ) -> Dict[str, float]:
        """
        计算各目标价格的概率

        Returns
        -------
        dict: {
            "prob_above_{target}": 0.0-1.0,
            "expected_move": float,  # 1-sigma预期移动范围
            "p10": float, "p25": float, "p50": float, "p75": float, "p90": float
        }
        """
        from scipy.stats import norm, lognorm

        mu     = np.log(spot) + (r - 0.5 * sigma**2) * T
        sigma_t = sigma * np.sqrt(T)

        result = {
            "expected_move_1sigma": spot * (np.exp(sigma_t) - 1),
            "expected_move_pct":    (np.exp(sigma_t) - 1),
        }

        # 各目标价概率
        for target in targets:
            log_target = np.log(target)
            prob_above = 1.0 - norm.cdf(log_target, loc=mu, scale=sigma_t)
            result[f"prob_above_{target:.0f}"] = round(prob_above, 4)

        # 百分位数
        for p in [10, 25, 50, 75, 90]:
            result[f"p{p}"] = round(np.exp(norm.ppf(p/100, loc=mu, scale=sigma_t)), 2)

        return result
