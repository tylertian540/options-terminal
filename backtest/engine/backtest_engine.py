"""
backtest_engine.py
事件驱动期权策略回测引擎

特性:
  - 支持5年历史数据（OPRA历史或Yahoo Finance期权链）
  - 事件驱动架构（逐Tick或逐日）
  - 完整的期权生命周期管理（建仓/调仓/到期/行权）
  - 机构级绩效指标（Sharpe/Sortino/最大回撤/VaR/CVaR）
  - 自定义策略接口
  - 多行情场景测试（牛/熊/震荡/崩盘）

依赖: pandas, numpy, scipy, vectorbt (可选), yfinance
"""

import numpy as np
import pandas as pd
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any, Tuple
from datetime import datetime, timedelta, date
from enum import Enum
from copy import deepcopy

logger = logging.getLogger(__name__)


# ============================================================
# 枚举和配置
# ============================================================

class OrderType(Enum):
    MARKET  = "MARKET"
    LIMIT   = "LIMIT"
    STOP    = "STOP"

class OrderSide(Enum):
    BUY  = "BUY"
    SELL = "SELL"

class PositionStatus(Enum):
    OPEN   = "OPEN"
    CLOSED = "CLOSED"
    EXPIRED= "EXPIRED"
    EXERCISED = "EXERCISED"


# ============================================================
# 数据结构
# ============================================================

@dataclass
class OptionContract:
    """期权合约标识"""
    symbol:      str
    expiry:      date
    strike:      float
    option_type: str        # "C" or "P"
    multiplier:  int = 100

    @property
    def key(self) -> str:
        return f"{self.symbol}_{self.expiry}_{self.strike}_{self.option_type}"

    @property
    def dte(self) -> int:
        return (self.expiry - date.today()).days


@dataclass
class Quote:
    """单条报价快照"""
    contract:   OptionContract
    date:       date
    bid:        float
    ask:        float
    last:       float
    iv:         float
    delta:      float
    gamma:      float
    theta:      float
    vega:       float
    volume:     int
    open_interest: int

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass
class Order:
    """订单"""
    contract:   OptionContract
    side:       OrderSide
    quantity:   int           # 正数
    order_type: OrderType
    limit_price: Optional[float] = None
    stop_price:  Optional[float] = None
    created_at:  Optional[date] = None
    filled_at:   Optional[date] = None
    fill_price:  Optional[float] = None
    commission:  float = 0.0
    status:      str = "PENDING"   # PENDING/FILLED/CANCELLED/REJECTED

    @property
    def is_filled(self) -> bool:
        return self.status == "FILLED"


@dataclass
class Position:
    """持仓记录"""
    contract:     OptionContract
    quantity:     int              # 正=多头，负=空头
    avg_cost:     float            # 平均成本（单合约）
    opened_at:    date
    closed_at:    Optional[date]   = None
    close_price:  Optional[float]  = None
    status:       PositionStatus   = PositionStatus.OPEN

    @property
    def notional(self) -> float:
        return self.avg_cost * abs(self.quantity) * self.contract.multiplier

    def pnl(self, current_price: float) -> float:
        """未实现盈亏"""
        return (current_price - self.avg_cost) * self.quantity * self.contract.multiplier

    def close(self, price: float, close_date: date):
        self.close_price = price
        self.closed_at   = close_date
        self.status      = PositionStatus.CLOSED

    @property
    def realized_pnl(self) -> float:
        if self.close_price is None:
            return 0.0
        return (self.close_price - self.avg_cost) * self.quantity * self.contract.multiplier


@dataclass
class Portfolio:
    """投资组合状态"""
    cash:       float
    positions:  Dict[str, Position] = field(default_factory=dict)
    orders:     List[Order]         = field(default_factory=list)
    trades:     List[dict]          = field(default_factory=list)  # 成交记录

    @property
    def open_positions(self) -> Dict[str, Position]:
        return {k: v for k, v in self.positions.items()
                if v.status == PositionStatus.OPEN}

    def market_value(self, quote_lookup: Dict[str, float]) -> float:
        """计算持仓市值"""
        mv = 0.0
        for key, pos in self.open_positions.items():
            price = quote_lookup.get(key, pos.avg_cost)
            mv += price * pos.quantity * pos.contract.multiplier
        return mv

    def total_equity(self, quote_lookup: Dict[str, float]) -> float:
        return self.cash + self.market_value(quote_lookup)


# ============================================================
# 策略基类
# ============================================================

class Strategy(ABC):
    """
    策略基类 — 所有自定义策略必须继承此类

    子类需实现：
      - on_start()       策略初始化
      - on_bar(bar)      每个时间步的主逻辑
      - on_fill(order)   订单成交回调
      - on_expiry(pos)   持仓到期回调
    """

    def __init__(self, params: dict = None):
        self.params    = params or {}
        self.portfolio: Optional[Portfolio] = None
        self.engine: Optional['BacktestEngine'] = None
        self.log: List[str] = []

    def buy(self, contract: OptionContract, quantity: int,
            order_type: OrderType = OrderType.MARKET,
            limit_price: float = None) -> Order:
        return self.engine.submit_order(Order(
            contract=contract, side=OrderSide.BUY,
            quantity=quantity, order_type=order_type,
            limit_price=limit_price
        ))

    def sell(self, contract: OptionContract, quantity: int,
             order_type: OrderType = OrderType.MARKET,
             limit_price: float = None) -> Order:
        return self.engine.submit_order(Order(
            contract=contract, side=OrderSide.SELL,
            quantity=quantity, order_type=order_type,
            limit_price=limit_price
        ))

    def close_position(self, pos: Position) -> Optional[Order]:
        """平仓指定持仓"""
        if pos.status != PositionStatus.OPEN:
            return None
        side = OrderSide.SELL if pos.quantity > 0 else OrderSide.BUY
        return self.engine.submit_order(Order(
            contract=pos.contract,
            side=side,
            quantity=abs(pos.quantity),
            order_type=OrderType.MARKET,
        ))

    @abstractmethod
    def on_start(self): ...

    @abstractmethod
    def on_bar(self, bar: 'Bar'): ...

    def on_fill(self, order: Order): pass
    def on_expiry(self, pos: Position): pass
    def on_stop(self): pass


@dataclass
class Bar:
    """单个时间步的市场数据快照"""
    date:     date
    symbol:   str
    open:     float
    high:     float
    low:      float
    close:    float
    volume:   int
    quotes:   Dict[str, Quote] = field(default_factory=dict)  # 期权链报价
    vix:      float = 20.0


# ============================================================
# 回测引擎
# ============================================================

class BacktestEngine:
    """
    事件驱动期权回测引擎

    支持的历史数据源:
      1. CSV文件（OPRA格式）
      2. yfinance（免费，数据有限）
      3. 自定义数据适配器

    回测流程:
      ┌──────────────────┐
      │  加载历史数据     │
      └────────┬─────────┘
               │
      ┌────────▼─────────┐
      │  时间步循环       │
      │  1. 检查到期      │
      │  2. 更新报价      │
      │  3. 调用strategy  │
      │  4. 撮合订单      │
      │  5. 记录NAV       │
      └────────┬─────────┘
               │
      ┌────────▼─────────┐
      │  绩效分析         │
      └──────────────────┘
    """

    def __init__(
        self,
        initial_capital: float = 100_000,
        commission_per_contract: float = 0.65,
        slippage_pct: float = 0.005,       # 0.5% 滑点
        margin_requirement: float = 0.20,   # 20% 保证金
    ):
        self.initial_capital = initial_capital
        self.commission_per  = commission_per_contract
        self.slippage_pct    = slippage_pct
        self.margin_req      = margin_requirement

        self.portfolio: Optional[Portfolio] = None
        self.strategy:  Optional[Strategy]  = None
        self._bars: List[Bar] = []

    # ─── 数据加载 ───

    def load_data_from_csv(self, filepath: str) -> None:
        """加载CSV格式历史数据"""
        df = pd.read_csv(filepath, parse_dates=['date', 'expiry'])
        self._bars = self._build_bars(df)
        logger.info(f"已加载 {len(self._bars)} 个交易日数据")

    def load_data_yfinance(
        self,
        symbol: str,
        start: str,
        end: str,
    ) -> None:
        """使用yfinance加载历史数据（仅用于测试）"""
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            hist   = ticker.history(start=start, end=end)

            self._bars = []
            for dt, row in hist.iterrows():
                bar = Bar(
                    date   = dt.date(),
                    symbol = symbol,
                    open   = row['Open'],
                    high   = row['High'],
                    low    = row['Low'],
                    close  = row['Close'],
                    volume = int(row['Volume']),
                    quotes = {},   # yfinance不提供历史期权链
                )
                self._bars.append(bar)
            logger.info(f"yfinance加载 {symbol}: {len(self._bars)} 天")
        except ImportError:
            raise ImportError("请安装: pip install yfinance")

    def generate_synthetic_options(
        self,
        bar: Bar,
        r: float = 0.05,
        strike_range: float = 0.20,   # ±20%行权价范围
        n_strikes: int = 21,
        expiries_days: List[int] = [7, 14, 30, 60, 90, 180],
    ) -> Dict[str, Quote]:
        """
        基于BSM生成合成期权报价（当真实历史期权链不可用时）
        用于开发测试
        """
        try:
            import options_core as oc
        except ImportError:
            from .bsm_fallback import compute_greeks as _compute
            oc = None

        spot    = bar.close
        quotes  = {}
        today   = bar.date
        base_iv = bar.vix / 100.0 / np.sqrt(252) * np.sqrt(252)  # 年化

        strikes = np.linspace(
            spot * (1 - strike_range),
            spot * (1 + strike_range),
            n_strikes
        )

        for days in expiries_days:
            exp_date = today + timedelta(days=days)
            T = days / 365.0
            if T <= 0:
                continue

            # SABR风格的skew：OTM Put IV偏高
            for strike in strikes:
                moneyness = strike / spot
                skew_adj  = max(0, (1.0 - moneyness) * 0.3)  # Put skew
                sigma     = max(0.05, base_iv + skew_adj)

                for opt_type in ("C", "P"):
                    contract = OptionContract(bar.symbol, exp_date, round(strike, 0), opt_type)

                    if oc:
                        params = oc.OptionParams(
                            S=spot, K=strike, T=T, r=r, q=0.0,
                            sigma=sigma,
                            type=oc.OptionType.CALL if opt_type == "C" else oc.OptionType.PUT
                        )
                        g = oc.BlackScholesMerton.compute(params)
                        price = g.price
                        delta = g.delta
                        gamma = g.gamma
                        theta = g.theta
                        vega  = g.vega
                    else:
                        # Python fallback
                        price, delta, gamma, theta, vega = self._bsm_python(
                            spot, strike, T, r, sigma, opt_type
                        )

                    spread = max(0.02, price * 0.02)  # 2%价差

                    q = Quote(
                        contract       = contract,
                        date           = today,
                        bid            = max(0.01, price - spread / 2),
                        ask            = price + spread / 2,
                        last           = price,
                        iv             = sigma,
                        delta          = delta,
                        gamma          = gamma,
                        theta          = theta,
                        vega           = vega,
                        volume         = int(np.random.exponential(500)),
                        open_interest  = int(np.random.exponential(5000)),
                    )
                    quotes[contract.key] = q

        return quotes

    # ─── 回测执行 ───

    def run(
        self,
        strategy: Strategy,
        start_date: Optional[date] = None,
        end_date:   Optional[date] = None,
        scenario:   str = "historical",  # historical/bull/bear/crash/volatile
    ) -> 'BacktestResult':
        """
        执行回测

        Parameters
        ----------
        strategy  : 策略实例
        start_date: 开始日期
        end_date  : 结束日期
        scenario  : 行情场景（用于合成数据压力测试）

        Returns
        -------
        BacktestResult: 完整绩效分析结果
        """
        # 初始化
        self.portfolio = Portfolio(cash=self.initial_capital)
        self.strategy  = strategy
        strategy.portfolio = self.portfolio
        strategy.engine    = self

        # 过滤日期范围
        bars = self._filter_bars(start_date, end_date)
        if not bars:
            raise ValueError("无可用数据，请检查日期范围")

        # 应用行情场景调整
        bars = self._apply_scenario(bars, scenario)

        # 预处理：生成合成期权链（如无真实链）
        for bar in bars:
            if not bar.quotes:
                bar.quotes = self.generate_synthetic_options(bar)

        strategy.on_start()

        # 记录净值曲线
        nav_series: List[Tuple[date, float]] = []

        for i, bar in enumerate(bars):
            quote_lookup = {k: q.mid for k, q in bar.quotes.items()}

            # 1. 检查到期合约
            self._process_expirations(bar, quote_lookup)

            # 2. 执行策略逻辑
            try:
                strategy.on_bar(bar)
            except Exception as e:
                logger.error(f"策略on_bar异常 [{bar.date}]: {e}", exc_info=True)

            # 3. 撮合所有挂单
            self._match_orders(bar, quote_lookup)

            # 4. 记录NAV
            equity = self.portfolio.total_equity(quote_lookup)
            nav_series.append((bar.date, equity))

        strategy.on_stop()

        return BacktestResult(
            nav_series     = nav_series,
            portfolio      = self.portfolio,
            initial_capital= self.initial_capital,
            strategy_name  = type(strategy).__name__,
            params         = strategy.params,
        )

    def submit_order(self, order: Order) -> Order:
        order.created_at = self._current_bar_date
        self.portfolio.orders.append(order)
        return order

    # ─── 内部方法 ───

    def _match_orders(self, bar: Bar, quote_lookup: Dict[str, float]):
        """订单撮合（市价单立即成交）"""
        for order in self.portfolio.orders:
            if order.status != "PENDING":
                continue

            key   = order.contract.key
            quote = bar.quotes.get(key)
            if not quote:
                continue

            # 确定成交价
            if order.order_type == OrderType.MARKET:
                fill_price = quote.ask if order.side == OrderSide.BUY else quote.bid
                fill_price *= (1 + self.slippage_pct if order.side == OrderSide.BUY
                               else 1 - self.slippage_pct)
            elif order.order_type == OrderType.LIMIT:
                if order.side == OrderSide.BUY and order.limit_price >= quote.ask:
                    fill_price = order.limit_price
                elif order.side == OrderSide.SELL and order.limit_price <= quote.bid:
                    fill_price = order.limit_price
                else:
                    continue  # 未触及limit价格
            else:
                continue

            # 计算佣金
            commission = order.quantity * self.commission_per

            # 更新现金
            notional = fill_price * order.quantity * order.contract.multiplier
            if order.side == OrderSide.BUY:
                self.portfolio.cash -= notional + commission
            else:
                self.portfolio.cash += notional - commission

            # 更新持仓
            pos_key = key
            if pos_key in self.portfolio.positions:
                pos = self.portfolio.positions[pos_key]
                if pos.status == PositionStatus.OPEN:
                    if order.side == OrderSide.BUY:
                        # 加仓
                        total_qty  = pos.quantity + order.quantity
                        pos.avg_cost = (pos.avg_cost * pos.quantity + fill_price * order.quantity) / total_qty
                        pos.quantity = total_qty
                    else:
                        # 减仓/平仓
                        if order.quantity >= pos.quantity:
                            pos.close(fill_price, bar.date)
                        else:
                            pos.quantity -= order.quantity
            else:
                qty = order.quantity if order.side == OrderSide.BUY else -order.quantity
                self.portfolio.positions[pos_key] = Position(
                    contract  = order.contract,
                    quantity  = qty,
                    avg_cost  = fill_price,
                    opened_at = bar.date,
                )

            order.status     = "FILLED"
            order.fill_price = fill_price
            order.filled_at  = bar.date
            order.commission = commission

            self.portfolio.trades.append({
                "date":      bar.date,
                "contract":  key,
                "side":      order.side.value,
                "qty":       order.quantity,
                "price":     fill_price,
                "commission":commission,
            })

            self.strategy.on_fill(order)

    def _process_expirations(self, bar: Bar, quote_lookup: Dict[str, float]):
        """处理到期合约"""
        for key, pos in list(self.portfolio.open_positions.items()):
            if pos.contract.expiry <= bar.date:
                spot  = bar.close
                K     = pos.contract.strike
                is_call = pos.contract.option_type == "C"

                # 计算内在价值
                intrinsic = max(0, spot - K) if is_call else max(0, K - spot)

                if intrinsic > 0 and pos.quantity > 0:
                    # 价内行权
                    pos.status = PositionStatus.EXERCISED
                    self.portfolio.cash += intrinsic * pos.quantity * pos.contract.multiplier
                else:
                    pos.status = PositionStatus.EXPIRED

                pos.close_price = intrinsic
                pos.closed_at   = bar.date

                self.strategy.on_expiry(pos)

    def _filter_bars(self, start, end) -> List[Bar]:
        bars = self._bars
        if start: bars = [b for b in bars if b.date >= start]
        if end:   bars = [b for b in bars if b.date <= end]
        return bars

    def _apply_scenario(self, bars: List[Bar], scenario: str) -> List[Bar]:
        """应用行情场景（压力测试用）"""
        if scenario == "historical":
            return bars

        bars = deepcopy(bars)
        t    = np.arange(len(bars))

        if scenario == "bull":
            # 年化+30%趋势
            drift = np.exp(0.30 / 252 * t)
        elif scenario == "bear":
            # 年化-30%趋势
            drift = np.exp(-0.30 / 252 * t)
        elif scenario == "crash":
            # 前75%正常，后25%崩盘-40%
            n75   = int(len(bars) * 0.75)
            drift = np.ones(len(bars))
            drift[n75:] = np.exp(np.linspace(0, np.log(0.60), len(bars) - n75))
        elif scenario == "volatile":
            # 高波动震荡（VIX×2）
            drift = np.ones(len(bars))
            for b in bars: b.vix = min(80, b.vix * 2.0)
        else:
            return bars

        base = bars[0].close
        for i, (bar, d) in enumerate(zip(bars, drift)):
            scale    = d / (bars[i-1].close / base if i > 0 else 1.0)
            bar.open  *= scale
            bar.high  *= scale
            bar.low   *= scale
            bar.close *= scale

        return bars

    @staticmethod
    def _bsm_python(S, K, T, r, sigma, opt_type):
        """Python fallback BSM（当C++引擎不可用时）"""
        from scipy.stats import norm
        import math
        sqrt_T = math.sqrt(T)
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T
        if opt_type == "C":
            price = S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
            delta = norm.cdf(d1)
        else:
            price = K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
            delta = norm.cdf(d1) - 1
        gamma = norm.pdf(d1) / (S * sigma * sqrt_T)
        theta = (-S * norm.pdf(d1) * sigma / (2 * sqrt_T)
                 - r * K * math.exp(-r * T) * norm.cdf(d2 if opt_type == "C" else -d2)) / 365
        vega  = S * norm.pdf(d1) * sqrt_T * 0.01
        return price, delta, gamma, theta, vega

    @property
    def _current_bar_date(self) -> Optional[date]:
        return None


# ============================================================
# 绩效分析
# ============================================================

@dataclass
class BacktestResult:
    """回测结果与绩效分析"""
    nav_series:     List[Tuple[date, float]]
    portfolio:      Portfolio
    initial_capital:float
    strategy_name:  str
    params:         dict

    def metrics(self) -> Dict[str, Any]:
        """计算完整绩效指标"""
        if len(self.nav_series) < 2:
            return {}

        dates  = [d for d, _ in self.nav_series]
        navs   = np.array([v for _, v in self.nav_series])
        rets   = np.diff(navs) / navs[:-1]

        total_ret    = navs[-1] / self.initial_capital - 1.0
        n_days       = (dates[-1] - dates[0]).days
        annual_ret   = (1 + total_ret) ** (365 / n_days) - 1 if n_days > 0 else 0
        annual_vol   = rets.std() * np.sqrt(252)
        sharpe       = annual_ret / annual_vol if annual_vol > 0 else 0

        # Sortino（仅下行波动）
        downside_rets = rets[rets < 0]
        sortino_vol   = downside_rets.std() * np.sqrt(252) if len(downside_rets) > 0 else 1e-6
        sortino       = annual_ret / sortino_vol

        # 最大回撤
        peak     = np.maximum.accumulate(navs)
        drawdown = (navs - peak) / peak
        max_dd   = drawdown.min()
        calmar   = annual_ret / abs(max_dd) if max_dd != 0 else 0

        # VaR/CVaR（95%置信度）
        var_95  = np.percentile(rets, 5)
        cvar_95 = rets[rets <= var_95].mean() if (rets <= var_95).any() else var_95

        # 交易统计
        trades     = self.portfolio.trades
        n_trades   = len(trades)
        closed_pos = [p for p in self.portfolio.positions.values()
                      if p.status in (PositionStatus.CLOSED, PositionStatus.EXPIRED, PositionStatus.EXERCISED)]
        win_pos    = [p for p in closed_pos if p.realized_pnl > 0]
        win_rate   = len(win_pos) / len(closed_pos) if closed_pos else 0
        total_pnl  = sum(p.realized_pnl for p in closed_pos)

        return {
            "strategy":          self.strategy_name,
            "params":            self.params,
            "period_days":       n_days,
            "total_return":      f"{total_ret:.2%}",
            "annual_return":     f"{annual_ret:.2%}",
            "annual_volatility": f"{annual_vol:.2%}",
            "sharpe_ratio":      round(sharpe, 3),
            "sortino_ratio":     round(sortino, 3),
            "calmar_ratio":      round(calmar, 3),
            "max_drawdown":      f"{max_dd:.2%}",
            "var_95":            f"{var_95:.2%}",
            "cvar_95":           f"{cvar_95:.2%}",
            "win_rate":          f"{win_rate:.1%}",
            "total_trades":      n_trades,
            "total_positions":   len(closed_pos),
            "realized_pnl":      f"${total_pnl:,.0f}",
            "final_equity":      f"${navs[-1]:,.0f}",
        }

    def to_dataframe(self) -> pd.DataFrame:
        """NAV曲线DataFrame"""
        return pd.DataFrame(self.nav_series, columns=['date', 'nav'])

    def plot_nav(self, benchmark: Optional[List[Tuple[date, float]]] = None):
        """绘制NAV曲线（需matplotlib）"""
        try:
            import matplotlib.pyplot as plt
            fig, axes = plt.subplots(2, 1, figsize=(14, 8),
                                     gridspec_kw={'height_ratios': [3, 1]})

            df = self.to_dataframe()
            df['return'] = df['nav'] / self.initial_capital

            ax1 = axes[0]
            ax1.plot(df['date'], df['return'], color='#00e5ff', linewidth=1.5,
                     label=self.strategy_name)

            if benchmark:
                bench_df = pd.DataFrame(benchmark, columns=['date', 'nav'])
                bench_df['return'] = bench_df['nav'] / benchmark[0][1]
                ax1.plot(bench_df['date'], bench_df['return'],
                         color='#666', linewidth=1, linestyle='--', label='Benchmark')

            ax1.axhline(1.0, color='#333', linewidth=0.8, linestyle=':')
            ax1.set_facecolor('#0d1117')
            ax1.set_title(f'{self.strategy_name} 净值曲线', color='#ddd', fontsize=13)
            ax1.legend(facecolor='#161b22', labelcolor='#ccc')
            ax1.tick_params(colors='#666')

            # 回撤图
            navs = df['nav'].values
            peak = np.maximum.accumulate(navs)
            dd   = (navs - peak) / peak
            ax2  = axes[1]
            ax2.fill_between(df['date'], dd, 0, color='#f44336', alpha=0.4)
            ax2.set_facecolor('#0d1117')
            ax2.set_title('回撤', color='#ddd', fontsize=10)
            ax2.tick_params(colors='#666')

            fig.patch.set_facecolor('#0d1117')
            plt.tight_layout()
            return fig
        except ImportError:
            logger.warning("matplotlib未安装，无法绘图")
            return None


# ============================================================
# 示例策略：Covered Put + 收益增强
# ============================================================

class WheelStrategy(Strategy):
    """
    Wheel策略（现金担保Put + Covered Call）
    参数:
      delta_target: 目标Delta（如-0.30 = 卖30-Delta Put）
      dte_entry:    建仓DTE（如45天）
      dte_exit:     平仓DTE（如21天，获取50%利润）
    """

    def on_start(self):
        self.state   = "SELL_PUT"  # 当前状态机
        self.holding = None        # 持有股票时的合约
        logger.info(f"Wheel策略启动: {self.params}")

    def on_bar(self, bar: Bar):
        if not bar.quotes:
            return

        if self.state == "SELL_PUT":
            self._try_sell_put(bar)
        elif self.state == "SELL_CALL":
            self._try_sell_call(bar)

        # 检查止损
        self._check_stop_loss(bar)

    def _try_sell_put(self, bar: Bar):
        """寻找目标Delta的OTM Put卖出"""
        delta_target = self.params.get("delta_target", -0.30)
        dte_target   = self.params.get("dte_entry", 45)

        best: Optional[Quote] = None
        for q in bar.quotes.values():
            if q.contract.option_type != "P":
                continue
            dte = (q.contract.expiry - bar.date).days
            if abs(dte - dte_target) > 10:
                continue
            if abs(q.delta - delta_target) < abs(best.delta - delta_target if best else 1.0):
                best = q

        if best and self.portfolio.cash >= best.contract.strike * 100:
            self.sell(best.contract, 1)

    def _try_sell_call(self, bar: Bar):
        """持股后卖出ATM/OTM Call"""
        if not self.holding:
            return
        delta_target = self.params.get("call_delta", 0.30)
        dte_target   = self.params.get("dte_entry", 30)

        best: Optional[Quote] = None
        for q in bar.quotes.values():
            if q.contract.option_type != "C":
                continue
            dte = (q.contract.expiry - bar.date).days
            if abs(dte - dte_target) > 10:
                continue
            if abs(q.delta - delta_target) < abs(best.delta - delta_target if best else 1.0):
                best = q

        if best:
            self.sell(best.contract, 1)

    def _check_stop_loss(self, bar: Bar):
        """止损：Put损失超过2× premium时平仓"""
        for pos in list(self.portfolio.open_positions.values()):
            q = bar.quotes.get(pos.contract.key)
            if q and pos.quantity < 0:  # 空头期权
                loss = (q.mid - pos.avg_cost) * abs(pos.quantity) * 100
                if loss > pos.avg_cost * abs(pos.quantity) * 100 * 2.0:
                    self.close_position(pos)

    def on_fill(self, order: Order):
        if order.is_filled:
            logger.debug(f"成交: {order.contract.key} @ ${order.fill_price:.2f}")

    def on_expiry(self, pos: Position):
        if pos.status == PositionStatus.EXERCISED and pos.contract.option_type == "P":
            self.state   = "SELL_CALL"  # Put被行权→持股→卖Call
            self.holding = pos.contract
