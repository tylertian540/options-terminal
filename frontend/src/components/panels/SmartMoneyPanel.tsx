/**
 * SmartMoneyPanel.tsx
 * Smart Money 大单流量监控面板
 *
 * 实时展示:
 *   - 鲸鱼大单流水（>$100K）
 *   - 看多/看空净流量
 *   - 流量分类（投机/套保/做市/价差）
 *   - 30天历史保费流量对比
 *   - 异常活动预警
 */

import React, { useState, useEffect, useRef, useMemo } from 'react';
import * as d3 from 'd3';

// ─── 类型定义 ────────────────────────────────────────────────

export type FlowType = 'SPECULATIVE' | 'HEDGE' | 'MARKET_MAKER' | 'SPREAD' | 'UNKNOWN';
export type Sentiment = 'STRONG_BULL' | 'BULL' | 'NEUTRAL' | 'BEAR' | 'STRONG_BEAR';

export interface FlowRecord {
  id:         string;
  timestamp:  Date;
  symbol:     string;
  optionType: 'C' | 'P';
  strike:     number;
  expiry:     string;
  price:      number;
  size:       number;
  premium:    number;         // 美元
  side:       'BUY' | 'SELL';
  flowType:   FlowType;
  confidence: number;
  isSweep:    boolean;
  isBlock:    boolean;
  aboveAsk:   boolean;
  bullishScore:  number;
  bearishScore:  number;
  dte:           number;
  otmDegree:     number;
}

export interface SmartMoneyData {
  symbol:            string;
  callPremium:       number;
  putPremium:        number;
  putCallRatio:      number;
  netFlow:           number;
  bias:              'BULLISH' | 'BEARISH' | 'NEUTRAL';
  confidence:        number;
  recentFlows:       FlowRecord[];
  score:             number;    // 0-100综合评分
  sentiment:         Sentiment;
  unusualActivity:   FlowRecord[];
}

interface Props {
  data: SmartMoneyData;
  onFlowClick?: (flow: FlowRecord) => void;
  height?: number;
}

// ─── 颜色配置 ────────────────────────────────────────────────

const FLOW_TYPE_CONFIG: Record<FlowType, { label: string; color: string; bg: string }> = {
  SPECULATIVE:  { label: '投机', color: '#ff6b6b', bg: 'rgba(255,107,107,0.12)' },
  HEDGE:        { label: '套保', color: '#4fc3f7', bg: 'rgba(79,195,247,0.12)'  },
  MARKET_MAKER: { label: '做市', color: '#9e9e9e', bg: 'rgba(158,158,158,0.12)' },
  SPREAD:       { label: '价差', color: '#ce93d8', bg: 'rgba(206,147,216,0.12)' },
  UNKNOWN:      { label: '未知', color: '#555',    bg: 'rgba(85,85,85,0.12)'    },
};

const SENTIMENT_CONFIG: Record<Sentiment, { label: string; color: string; icon: string }> = {
  STRONG_BULL: { label: '强烈看多', color: '#00c853', icon: '🚀' },
  BULL:        { label: '偏多',     color: '#69f0ae', icon: '📈' },
  NEUTRAL:     { label: '中性',     color: '#ffd740', icon: '➖' },
  BEAR:        { label: '偏空',     color: '#ff6b6b', icon: '📉' },
  STRONG_BEAR: { label: '强烈看空', color: '#f44336', icon: '🐻' },
};

// ─── 主组件 ─────────────────────────────────────────────────

export const SmartMoneyPanel: React.FC<Props> = ({
  data,
  onFlowClick,
  height = 600,
}) => {
  const [activeTab, setActiveTab] = useState<'all' | 'unusual' | 'whale'>('all');
  const [sortBy, setSortBy] = useState<'time' | 'premium' | 'score'>('time');

  const sentiment = SENTIMENT_CONFIG[data.sentiment];

  const displayFlows = useMemo(() => {
    let flows = activeTab === 'unusual'
      ? data.unusualActivity
      : activeTab === 'whale'
        ? data.recentFlows.filter(f => f.premium >= 1_000_000)
        : data.recentFlows;

    return [...flows].sort((a, b) => {
      if (sortBy === 'premium') return b.premium - a.premium;
      if (sortBy === 'score')   return (b.bullishScore - b.bearishScore) - (a.bullishScore - a.bearishScore);
      return b.timestamp.getTime() - a.timestamp.getTime();
    }).slice(0, 50);
  }, [data, activeTab, sortBy]);

  return (
    <div style={{
      background: '#0d1117', border: '1px solid #21262d',
      borderRadius: 8, display: 'flex', flexDirection: 'column',
      height, overflow: 'hidden',
    }}>
      {/* ─── 顶部摘要 ─── */}
      <SummaryBar data={data} sentiment={sentiment} />

      {/* ─── 保费流量条形图 ─── */}
      <PremiumFlowBar
        callPremium={data.callPremium}
        putPremium={data.putPremium}
      />

      {/* ─── 控制栏 ─── */}
      <div style={{
        display: 'flex', justifyContent: 'space-between',
        padding: '6px 12px', borderBottom: '1px solid #21262d',
        alignItems: 'center',
      }}>
        <div style={{ display: 'flex', gap: 8 }}>
          {(['all', 'unusual', 'whale'] as const).map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              style={{
                padding: '3px 10px', fontSize: 11,
                background: activeTab === tab ? '#1f6feb' : 'transparent',
                color: activeTab === tab ? '#fff' : '#888',
                border: '1px solid ' + (activeTab === tab ? '#1f6feb' : '#333'),
                borderRadius: 4, cursor: 'pointer',
              }}
            >
              {tab === 'all' ? '全部' : tab === 'unusual' ? '⚡异常' : '🐋鲸鱼'}
            </button>
          ))}
        </div>
        <select
          value={sortBy}
          onChange={e => setSortBy(e.target.value as typeof sortBy)}
          style={{
            background: '#161b22', color: '#ccc', border: '1px solid #333',
            borderRadius: 4, padding: '2px 6px', fontSize: 11,
          }}
        >
          <option value="time">按时间</option>
          <option value="premium">按保费</option>
          <option value="score">按评分</option>
        </select>
      </div>

      {/* ─── 流量列表 ─── */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {displayFlows.length === 0 ? (
          <div style={{ textAlign: 'center', color: '#555', padding: 40 }}>
            暂无数据
          </div>
        ) : (
          displayFlows.map(flow => (
            <FlowRow
              key={flow.id}
              flow={flow}
              onClick={() => onFlowClick?.(flow)}
            />
          ))
        )}
      </div>
    </div>
  );
};

// ─── 摘要栏 ─────────────────────────────────────────────────

const SummaryBar: React.FC<{
  data: SmartMoneyData;
  sentiment: typeof SENTIMENT_CONFIG[Sentiment];
}> = ({ data, sentiment }) => (
  <div style={{
    display: 'flex', gap: 16, padding: '10px 14px',
    borderBottom: '1px solid #21262d', flexWrap: 'wrap',
    alignItems: 'center',
  }}>
    <div>
      <div style={{ color: '#555', fontSize: 10 }}>综合评分</div>
      <ScoreGauge score={data.score} />
    </div>
    <div style={{ flex: 1 }}>
      <div style={{ color: '#555', fontSize: 10 }}>市场情绪</div>
      <div style={{ color: sentiment.color, fontSize: 15, fontWeight: 'bold' }}>
        {sentiment.icon} {sentiment.label}
        <span style={{ color: '#555', fontSize: 10, marginLeft: 6 }}>
          置信度 {(data.confidence * 100).toFixed(0)}%
        </span>
      </div>
    </div>
    {[
      { label: 'Call保费', value: formatPremium(data.callPremium), color: '#4caf50' },
      { label: 'Put保费',  value: formatPremium(data.putPremium),  color: '#f44336' },
      { label: 'PCR',      value: data.putCallRatio.toFixed(2),    color: data.putCallRatio > 1 ? '#f44336' : '#4caf50' },
      { label: '净流向',   value: (data.netFlow >= 0 ? '+' : '') + formatPremium(data.netFlow),
        color: data.netFlow >= 0 ? '#4caf50' : '#f44336' },
    ].map(({ label, value, color }) => (
      <div key={label}>
        <div style={{ color: '#555', fontSize: 10 }}>{label}</div>
        <div style={{ color, fontSize: 13, fontWeight: 'bold' }}>{value}</div>
      </div>
    ))}
  </div>
);

// ─── 保费流量双向条形 ────────────────────────────────────────

const PremiumFlowBar: React.FC<{ callPremium: number; putPremium: number }> = ({
  callPremium, putPremium,
}) => {
  const total = callPremium + putPremium + 1e-6;
  const callPct = callPremium / total * 100;

  return (
    <div style={{ padding: '6px 14px', borderBottom: '1px solid #21262d' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#555', marginBottom: 3 }}>
        <span>📈 Call {callPct.toFixed(0)}%</span>
        <span>Put {(100 - callPct).toFixed(0)}% 📉</span>
      </div>
      <div style={{ height: 8, background: '#21262d', borderRadius: 4, overflow: 'hidden' }}>
        <div style={{
          height: '100%', width: `${callPct}%`,
          background: 'linear-gradient(90deg, #4caf50, #8bc34a)',
          transition: 'width 0.5s ease',
        }} />
      </div>
    </div>
  );
};

// ─── 单条流量记录 ────────────────────────────────────────────

const FlowRow: React.FC<{ flow: FlowRecord; onClick: () => void }> = ({
  flow, onClick,
}) => {
  const ftc = FLOW_TYPE_CONFIG[flow.flowType];
  const isCall  = flow.optionType === 'C';
  const isBull  = (isCall && flow.side === 'BUY') || (!isCall && flow.side === 'SELL');
  const sentColor = isBull ? '#4caf50' : '#f44336';

  return (
    <div
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'center', gap: 10,
        padding: '5px 12px', borderBottom: '1px solid #161b22',
        cursor: 'pointer', fontSize: 11,
        background: flow.aboveAsk ? 'rgba(255,107,107,0.05)' : 'transparent',
        transition: 'background 0.15s',
      }}
      onMouseEnter={e => (e.currentTarget.style.background = '#161b22')}
      onMouseLeave={e => (e.currentTarget.style.background = flow.aboveAsk ? 'rgba(255,107,107,0.05)' : 'transparent')}
    >
      {/* 时间 */}
      <span style={{ color: '#444', width: 50, flexShrink: 0, fontSize: 10 }}>
        {formatTime(flow.timestamp)}
      </span>

      {/* 方向徽标 */}
      <span style={{
        background: sentColor, color: '#fff', borderRadius: 3,
        padding: '1px 5px', fontSize: 10, flexShrink: 0,
      }}>
        {flow.side} {flow.optionType}
      </span>

      {/* 合约 */}
      <span style={{ color: '#ddd', fontWeight: 'bold', minWidth: 100 }}>
        {flow.symbol} ${flow.strike} {flow.expiry.slice(2)}
      </span>

      {/* 价格 × 数量 */}
      <span style={{ color: '#888' }}>
        ${flow.price.toFixed(2)} × {flow.size}
      </span>

      {/* 保费 */}
      <span style={{ color: '#ffd740', fontWeight: 'bold', marginLeft: 'auto' }}>
        {formatPremium(flow.premium)}
      </span>

      {/* 标签 */}
      <div style={{ display: 'flex', gap: 4, marginLeft: 8 }}>
        <span style={{
          background: ftc.bg, color: ftc.color,
          borderRadius: 3, padding: '1px 5px', fontSize: 9,
        }}>
          {ftc.label}
        </span>
        {flow.isSweep  && <Tag color="#ff9800">SWEEP</Tag>}
        {flow.isBlock  && <Tag color="#9c27b0">BLOCK</Tag>}
        {flow.aboveAsk && <Tag color="#f44336">ABOVE ASK</Tag>}
      </div>

      {/* DTE */}
      <span style={{ color: '#555', fontSize: 10, marginLeft: 4, width: 36, textAlign: 'right' }}>
        {flow.dte}d
      </span>
    </div>
  );
};

// ─── 得分仪表盘 ──────────────────────────────────────────────

const ScoreGauge: React.FC<{ score: number }> = ({ score }) => {
  const color = score >= 70 ? '#4caf50' : score >= 45 ? '#ffd740' : '#f44336';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div style={{
        width: 36, height: 36, borderRadius: '50%',
        background: `conic-gradient(${color} ${score * 3.6}deg, #21262d 0)`,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <div style={{
          width: 26, height: 26, borderRadius: '50%',
          background: '#0d1117',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 10, fontWeight: 'bold', color,
        }}>
          {score}
        </div>
      </div>
    </div>
  );
};

// ─── 工具组件 ────────────────────────────────────────────────

const Tag: React.FC<{ color: string; children: React.ReactNode }> = ({ color, children }) => (
  <span style={{
    background: `${color}22`, color, borderRadius: 3,
    padding: '1px 4px', fontSize: 9, fontWeight: 'bold',
  }}>
    {children}
  </span>
);

// ─── 工具函数 ────────────────────────────────────────────────

function formatPremium(v: number): string {
  const abs = Math.abs(v);
  const sign = v < 0 ? '-' : '';
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000)     return `${sign}$${(abs / 1_000).toFixed(0)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

function formatTime(d: Date): string {
  return d.toLocaleTimeString('en-US', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
}

export default SmartMoneyPanel;
