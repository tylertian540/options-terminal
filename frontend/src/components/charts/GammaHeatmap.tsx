/**
 * GammaHeatmap.tsx
 * GEX（Gamma暴露）热力图 + 翻转点标注
 *
 * 展示每个行权价的净Gamma暴露强度
 * 绿色 = 正GEX（做市商正Gamma，价格稳定区间）
 * 红色 = 负GEX（做市商负Gamma，价格放大区间）
 * 黄色虚线 = Gamma翻转点
 */

import React, { useRef, useEffect, useMemo } from 'react';
import * as d3 from 'd3';

// ─── 类型定义 ────────────────────────────────────────────────

export interface GEXData {
  strikes:      number[];
  netGEX:       number[];   // 净GEX（百万美元）
  callGEX:      number[];
  putGEX:       number[];
  spotPrice:    number;
  flipStrike:   number;     // Gamma翻转点
  maxCallWall:  number;     // 最大Call支撑位
  maxPutWall:   number;     // 最大Put压力位
}

interface Props {
  data: GEXData;
  width?: number;
  height?: number;
  showCallPut?: boolean;    // 是否分开显示Call/Put GEX
}

// ─── 颜色比例尺 ──────────────────────────────────────────────

const gexColorScale = d3.scaleDiverging(d3.interpolateRdYlGn)
  .domain([-1, 0, 1]);  // 动态域在渲染时设置

// ─── 主组件 ─────────────────────────────────────────────────

export const GammaHeatmap: React.FC<Props> = ({
  data,
  width  = 900,
  height = 420,
  showCallPut = true,
}) => {
  const svgRef = useRef<SVGSVGElement>(null);

  const margin = useMemo(() => ({
    top: 20, right: 30, bottom: 60, left: 80
  }), []);

  useEffect(() => {
    if (!svgRef.current || !data.strikes.length) return;

    const W = width  - margin.left - margin.right;
    const H = height - margin.top  - margin.bottom;
    const barH = showCallPut ? H / 3 : H;  // 3行：净GEX / Call GEX / Put GEX

    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    const g = svg.append('g')
      .attr('transform', `translate(${margin.left},${margin.top})`);

    // ─── 比例尺 ───
    const xScale = d3.scaleBand()
      .domain(data.strikes.map(String))
      .range([0, W])
      .padding(0.05);

    const maxAbsGEX = Math.max(...data.netGEX.map(Math.abs), 1);
    const colorScale = d3.scaleDiverging(d3.interpolateRdYlGn)
      .domain([-maxAbsGEX, 0, maxAbsGEX]);

    // ─── 绘制净GEX热力行 ───
    renderGEXRow(
      g, data.strikes, data.netGEX,
      xScale, barH * 0, barH,
      colorScale, '净 GEX ($M)'
    );

    if (showCallPut) {
      // ─── Call GEX（仅正值） ───
      renderGEXRow(
        g, data.strikes, data.callGEX,
        xScale, barH * 1 + 8, barH - 4,
        d3.scaleSequential(d3.interpolateGreens).domain([0, maxAbsGEX]),
        'Call GEX'
      );

      // ─── Put GEX（仅负值，取绝对值展示）───
      renderGEXRow(
        g, data.strikes, data.putGEX.map(v => Math.abs(v)),
        xScale, barH * 2 + 16, barH - 4,
        d3.scaleSequential(d3.interpolateReds).domain([0, maxAbsGEX]),
        'Put GEX'
      );
    }

    // ─── X 轴标签（行权价）───
    const xAxis = d3.axisBottom(xScale)
      .tickValues(
        data.strikes.filter((_, i) =>
          i % Math.ceil(data.strikes.length / 15) === 0
        ).map(String)
      );

    g.append('g')
      .attr('transform', `translate(0,${H + 4})`)
      .call(xAxis)
      .selectAll('text')
      .attr('fill', '#888')
      .attr('font-size', 10)
      .attr('transform', 'rotate(-35)')
      .attr('text-anchor', 'end');

    g.select('.domain').attr('stroke', '#444');
    g.selectAll('.tick line').attr('stroke', '#444');

    // ─── 股价当前位置（垂直线）───
    const spotX = xScale(String(
      data.strikes.reduce((best, s) =>
        Math.abs(s - data.spotPrice) < Math.abs(best - data.spotPrice) ? s : best,
        data.strikes[0]
      )
    ));

    if (spotX !== undefined) {
      g.append('line')
        .attr('x1', spotX! + xScale.bandwidth() / 2)
        .attr('x2', spotX! + xScale.bandwidth() / 2)
        .attr('y1', -10).attr('y2', H + 4)
        .attr('stroke', '#00e5ff')
        .attr('stroke-width', 2)
        .attr('stroke-dasharray', '6,3');

      g.append('text')
        .attr('x', spotX! + xScale.bandwidth() / 2 + 4)
        .attr('y', -12)
        .attr('fill', '#00e5ff')
        .attr('font-size', 10)
        .text(`现价 $${data.spotPrice.toFixed(0)}`);
    }

    // ─── Gamma翻转点（黄色线）───
    const flipX = xScale(String(
      data.strikes.reduce((best, s) =>
        Math.abs(s - data.flipStrike) < Math.abs(best - data.flipStrike) ? s : best,
        data.strikes[0]
      )
    ));

    if (flipX !== undefined) {
      g.append('line')
        .attr('x1', flipX! + xScale.bandwidth() / 2)
        .attr('x2', flipX! + xScale.bandwidth() / 2)
        .attr('y1', 0).attr('y2', H)
        .attr('stroke', '#ffd700')
        .attr('stroke-width', 1.5)
        .attr('stroke-dasharray', '4,2');

      g.append('text')
        .attr('x', flipX! + xScale.bandwidth() / 2 + 4)
        .attr('y', 14)
        .attr('fill', '#ffd700')
        .attr('font-size', 9)
        .text(`Flip $${data.flipStrike.toFixed(0)}`);
    }

    // ─── Call Wall / Put Wall ───
    [
      { strike: data.maxCallWall, label: 'Call Wall', color: '#4caf50' },
      { strike: data.maxPutWall,  label: 'Put Wall',  color: '#f44336' },
    ].forEach(({ strike, label, color }) => {
      const wallX = xScale(String(
        data.strikes.reduce((best, s) =>
          Math.abs(s - strike) < Math.abs(best - strike) ? s : best,
          data.strikes[0]
        )
      ));
      if (wallX === undefined) return;

      g.append('rect')
        .attr('x', wallX)
        .attr('y', 0)
        .attr('width', xScale.bandwidth())
        .attr('height', H)
        .attr('fill', color)
        .attr('opacity', 0.08);

      g.append('text')
        .attr('x', wallX + xScale.bandwidth() / 2)
        .attr('y', H - 5)
        .attr('fill', color)
        .attr('font-size', 9)
        .attr('text-anchor', 'middle')
        .text(label);
    });

    // ─── 图表标题 ───
    svg.append('text')
      .attr('x', width / 2).attr('y', 14)
      .attr('fill', '#ddd').attr('font-size', 13)
      .attr('text-anchor', 'middle').attr('font-weight', 'bold')
      .text('Gamma 暴露（GEX）分布图谱');

  }, [data, width, height, margin, showCallPut]);

  return (
    <div style={{
      background: '#0d1117',
      border: '1px solid #21262d',
      borderRadius: 8,
      padding: '8px',
      overflow: 'auto',
    }}>
      {/* GEX摘要栏 */}
      <GEXSummary data={data} />

      <svg
        ref={svgRef}
        width={width}
        height={height}
        style={{ display: 'block', margin: '0 auto' }}
      />
    </div>
  );
};

// ─── 辅助：渲染单行GEX热力格 ─────────────────────────────────

function renderGEXRow(
  g: d3.Selection<SVGGElement, unknown, null, undefined>,
  strikes: number[],
  values: number[],
  xScale: d3.ScaleBand<string>,
  yOffset: number,
  rowHeight: number,
  colorFn: (v: number) => string,
  label: string
) {
  // 行标签
  g.append('text')
    .attr('x', -70).attr('y', yOffset + rowHeight / 2 + 4)
    .attr('fill', '#999').attr('font-size', 10)
    .text(label);

  // 热力格
  g.selectAll<SVGRectElement, number>(`rect.row-${label.replace(/\s/g, '')}`)
    .data(strikes)
    .enter()
    .append('rect')
    .attr('x', d => xScale(String(d)) ?? 0)
    .attr('y', yOffset)
    .attr('width', xScale.bandwidth())
    .attr('height', rowHeight)
    .attr('fill', (d, i) => colorFn(values[i] ?? 0))
    .attr('rx', 2)
    .append('title')
    .text((d, i) => `${d}: ${(values[i] ?? 0).toFixed(2)}M`);
}

// ─── GEX摘要信息栏 ──────────────────────────────────────────

const GEXSummary: React.FC<{ data: GEXData }> = ({ data }) => {
  const totalGEX = data.netGEX.reduce((s, v) => s + v, 0);
  const isPositive = totalGEX >= 0;

  return (
    <div style={{
      display: 'flex', gap: 20, padding: '6px 12px',
      borderBottom: '1px solid #21262d', marginBottom: 4,
      flexWrap: 'wrap',
    }}>
      {[
        { label: '总净GEX', value: `${totalGEX >= 0 ? '+' : ''}${totalGEX.toFixed(0)}M`,
          color: isPositive ? '#4caf50' : '#f44336' },
        { label: 'Gamma翻转点', value: `$${data.flipStrike.toFixed(2)}`,
          color: '#ffd700' },
        { label: 'Call Wall', value: `$${data.maxCallWall.toFixed(0)}`,
          color: '#4caf50' },
        { label: 'Put Wall', value: `$${data.maxPutWall.toFixed(0)}`,
          color: '#f44336' },
        { label: '市场状态', value: isPositive ? '✅ 正Gamma（稳定）' : '⚠️ 负Gamma（波动）',
          color: isPositive ? '#4caf50' : '#ff9800' },
      ].map(({ label, value, color }) => (
        <div key={label}>
          <div style={{ color: '#666', fontSize: 10 }}>{label}</div>
          <div style={{ color, fontSize: 13, fontWeight: 'bold' }}>{value}</div>
        </div>
      ))}
    </div>
  );
};

export default GammaHeatmap;
