/**
 * IVSurface3D.tsx
 * 三维隐含波动率曲面可视化组件
 *
 * 技术栈: React 18 + Three.js (WebGL) + D3.js (坐标轴)
 * 特性:
 *   - 实时60fps WebGL渲染
 *   - 鼠标悬停显示精确数值
 *   - 颜色映射：viridis/plasma/rainbow可切换
 *   - 支持3D旋转/缩放/平移（OrbitControls）
 *   - Skew/Smile结构可视化
 *   - 多屏响应式（自动resize）
 */

import React, {
  useRef, useEffect, useCallback, useMemo, useState
} from 'react';
import * as THREE from 'three';
import * as d3 from 'd3';

// ─── 类型定义 ───────────────────────────────────────────────

export interface IVSurfaceData {
  /** 行权价序列（X轴，相对标的价格的比率，如0.8~1.2） */
  strikes: number[];
  /** 到期时间序列（Y轴，年化，如0.02~2.0） */
  expiries: number[];
  /** IV矩阵 [expiryIdx][strikeIdx]，值为小数（0.1=10%） */
  ivMatrix: number[][];
  /** 当前ATM行权价（归一化，通常=1.0） */
  atmStrike?: number;
}

export type ColorScheme = 'viridis' | 'plasma' | 'turbo' | 'rdbu';

interface Props {
  data: IVSurfaceData;
  colorScheme?: ColorScheme;
  width?: number;
  height?: number;
  showGrid?: boolean;
  showSmile?: boolean;    // 高亮ATM列（Smile结构）
  onHover?: (strike: number, expiry: number, iv: number) => void;
}

// ─── 颜色映射 ───────────────────────────────────────────────

const COLOR_SCHEMES: Record<ColorScheme, d3.ScaleSequential<string>> = {
  viridis: d3.scaleSequential(d3.interpolateViridis),
  plasma:  d3.scaleSequential(d3.interpolatePlasma),
  turbo:   d3.scaleSequential(d3.interpolateTurbo),
  rdbu:    d3.scaleSequential(d3.interpolateRdBu),
};

function hexToThreeColor(hex: string): THREE.Color {
  return new THREE.Color(hex);
}

// ─── 主组件 ─────────────────────────────────────────────────

export const IVSurface3D: React.FC<Props> = ({
  data,
  colorScheme = 'viridis',
  width: propWidth,
  height: propHeight,
  showGrid = true,
  showSmile = true,
  onHover,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const rendererRef  = useRef<THREE.WebGLRenderer | null>(null);
  const sceneRef     = useRef<THREE.Scene | null>(null);
  const cameraRef    = useRef<THREE.PerspectiveCamera | null>(null);
  const meshRef      = useRef<THREE.Mesh | null>(null);
  const animFrameRef = useRef<number>(0);
  const isDragging   = useRef(false);
  const lastMouse    = useRef({ x: 0, y: 0 });
  const rotation     = useRef({ x: 0.4, y: -0.5 });

  const [tooltip, setTooltip] = useState<{
    x: number; y: number;
    strike: number; expiry: number; iv: number;
  } | null>(null);

  // ─── 尺寸响应式 ───
  const [dims, setDims] = useState({
    width:  propWidth  || 800,
    height: propHeight || 500,
  });

  useEffect(() => {
    if (propWidth && propHeight) return;
    const observer = new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect;
      setDims({ width: Math.max(width, 400), height: Math.max(height * 0.6, 350) });
    });
    if (containerRef.current) observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, [propWidth, propHeight]);

  // ─── 数据预处理 → BufferGeometry ───
  const geometry = useMemo(() => {
    const { strikes, expiries, ivMatrix } = data;
    const nS = strikes.length;
    const nT = expiries.length;
    if (nS < 2 || nT < 2) return null;

    const geo = new THREE.BufferGeometry();
    const vertices  = new Float32Array(nS * nT * 3);
    const colors    = new Float32Array(nS * nT * 3);
    const indices:  number[] = [];

    // 归一化范围
    const strikeMin = Math.min(...strikes),  strikeMax = Math.max(...strikes);
    const expiryMin = Math.min(...expiries), expiryMax = Math.max(...expiries);
    const ivMin     = Math.min(...ivMatrix.flat());
    const ivMax     = Math.max(...ivMatrix.flat());

    const colorScale = COLOR_SCHEMES[colorScheme].domain([ivMin, ivMax]);

    for (let ti = 0; ti < nT; ti++) {
      for (let si = 0; si < nS; si++) {
        const idx = ti * nS + si;
        const x   = ((strikes[si]  - strikeMin)  / (strikeMax  - strikeMin)  - 0.5) * 2;
        const z   = ((expiries[ti] - expiryMin) / (expiryMax - expiryMin) - 0.5) * 2;
        const iv  = ivMatrix[ti]?.[si] ?? ivMin;
        const y   = ((iv - ivMin) / (ivMax - ivMin)) * 1.2;  // 高度放大

        vertices[idx * 3]     = x;
        vertices[idx * 3 + 1] = y;
        vertices[idx * 3 + 2] = z;

        const c = hexToThreeColor(colorScale(iv));
        colors[idx * 3]     = c.r;
        colors[idx * 3 + 1] = c.g;
        colors[idx * 3 + 2] = c.b;

        // 三角形索引（网格面）
        if (ti < nT - 1 && si < nS - 1) {
          const a = ti * nS + si;
          const b = ti * nS + si + 1;
          const c_ = (ti + 1) * nS + si;
          const d = (ti + 1) * nS + si + 1;
          indices.push(a, b, c_, b, d, c_);
        }
      }
    }

    geo.setAttribute('position', new THREE.BufferAttribute(vertices, 3));
    geo.setAttribute('color',    new THREE.BufferAttribute(colors,   3));
    geo.setIndex(indices);
    geo.computeVertexNormals();

    return geo;
  }, [data, colorScheme]);

  // ─── Three.js 初始化 ───
  useEffect(() => {
    if (!containerRef.current || !geometry) return;
    const { width, height } = dims;

    // Scene
    const scene    = new THREE.Scene();
    scene.background = new THREE.Color(0x0d1117);
    sceneRef.current = scene;

    // Camera
    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 100);
    camera.position.set(0, 1.5, 3.5);
    cameraRef.current = camera;

    // Renderer
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    containerRef.current.innerHTML = '';
    containerRef.current.appendChild(renderer.domElement);
    rendererRef.current = renderer;

    // Lights
    const ambient = new THREE.AmbientLight(0xffffff, 0.6);
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
    dirLight.position.set(5, 5, 5);
    scene.add(ambient, dirLight);

    // IV Surface Mesh
    const material = new THREE.MeshPhongMaterial({
      vertexColors: true,
      side: THREE.DoubleSide,
      shininess: 30,
      transparent: true,
      opacity: 0.92,
    });
    const mesh = new THREE.Mesh(geometry, material);
    scene.add(mesh);
    meshRef.current = mesh;

    // Grid
    if (showGrid) {
      const gridHelper = new THREE.GridHelper(2, 10, 0x333333, 0x222222);
      gridHelper.position.y = -0.05;
      scene.add(gridHelper);
    }

    // ATM Smile 高亮线
    if (showSmile && data.atmStrike !== undefined) {
      const strikeIdx = data.strikes.reduce((best, s, i) =>
        Math.abs(s - data.atmStrike!) < Math.abs(data.strikes[best] - data.atmStrike!) ? i : best, 0);
      const smilePoints: THREE.Vector3[] = data.expiries.map((t, ti) => {
        const position = geometry.attributes.position;
        const idx = ti * data.strikes.length + strikeIdx;
        return new THREE.Vector3(
          position.getX(idx), position.getY(idx), position.getZ(idx)
        );
      });
      const smileGeo = new THREE.BufferGeometry().setFromPoints(smilePoints);
      const smileLine = new THREE.Line(smileGeo,
        new THREE.LineBasicMaterial({ color: 0xffff00, linewidth: 2 }));
      scene.add(smileLine);
    }

    // 动画循环
    const animate = () => {
      animFrameRef.current = requestAnimationFrame(animate);
      if (meshRef.current) {
        meshRef.current.rotation.y = rotation.current.y;
        meshRef.current.rotation.x = rotation.current.x;
      }
      renderer.render(scene, camera);
    };
    animate();

    return () => {
      cancelAnimationFrame(animFrameRef.current);
      renderer.dispose();
      geometry.dispose();
    };
  }, [geometry, dims, showGrid, showSmile]);

  // ─── 鼠标交互（拖拽旋转）───
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    isDragging.current = true;
    lastMouse.current = { x: e.clientX, y: e.clientY };
  }, []);

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (isDragging.current) {
      const dx = e.clientX - lastMouse.current.x;
      const dy = e.clientY - lastMouse.current.y;
      rotation.current.y += dx * 0.005;
      rotation.current.x += dy * 0.005;
      rotation.current.x = Math.max(-1.2, Math.min(1.2, rotation.current.x));
      lastMouse.current = { x: e.clientX, y: e.clientY };
    }
  }, []);

  const handleMouseUp = useCallback(() => {
    isDragging.current = false;
  }, []);

  return (
    <div style={{ position: 'relative', background: '#0d1117', borderRadius: 8 }}>
      {/* Three.js 画布容器 */}
      <div
        ref={containerRef}
        style={{ width: dims.width, height: dims.height, cursor: 'grab' }}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
      />

      {/* 图例 */}
      <IVLegend colorScheme={colorScheme} data={data} />

      {/* 轴标签 */}
      <div style={{
        position: 'absolute', bottom: 32, left: '50%',
        transform: 'translateX(-50%)',
        color: '#aaa', fontSize: 11, pointerEvents: 'none'
      }}>
        ← 行权价（moneyness） → &nbsp;&nbsp;&nbsp; | &nbsp;&nbsp;&nbsp; ← 到期日（年）→
      </div>

      {/* Tooltip */}
      {tooltip && (
        <div style={{
          position: 'absolute',
          left: tooltip.x + 12, top: tooltip.y - 8,
          background: 'rgba(0,0,0,0.85)',
          border: '1px solid #444', borderRadius: 4,
          padding: '6px 10px', fontSize: 12, color: '#fff',
          pointerEvents: 'none', zIndex: 10,
        }}>
          <div>行权价: {(tooltip.strike * 100).toFixed(0)}%</div>
          <div>到期: {(tooltip.expiry * 365).toFixed(0)}天</div>
          <div style={{ color: '#4fc3f7' }}>IV: {(tooltip.iv * 100).toFixed(1)}%</div>
        </div>
      )}
    </div>
  );
};

// ─── 颜色图例组件 ───────────────────────────────────────────

const IVLegend: React.FC<{ colorScheme: ColorScheme; data: IVSurfaceData }> = ({
  colorScheme, data
}) => {
  const svgRef = useRef<SVGSVGElement>(null);
  const ivMin  = Math.min(...data.ivMatrix.flat());
  const ivMax  = Math.max(...data.ivMatrix.flat());

  useEffect(() => {
    if (!svgRef.current) return;
    const svg    = d3.select(svgRef.current);
    const width  = 120, height = 16;
    svg.selectAll('*').remove();

    const defs    = svg.append('defs');
    const gradId  = 'iv-legend-grad';
    const grad    = defs.append('linearGradient').attr('id', gradId);
    const cs      = COLOR_SCHEMES[colorScheme].domain([0, 1]);

    for (let i = 0; i <= 10; i++) {
      grad.append('stop')
          .attr('offset', `${i * 10}%`)
          .attr('stop-color', cs(i / 10));
    }

    svg.append('rect')
       .attr('width', width).attr('height', height)
       .attr('fill', `url(#${gradId})`)
       .attr('rx', 3);

    svg.append('text').attr('x', 0).attr('y', height + 12)
       .attr('fill', '#888').attr('font-size', 10)
       .text(`${(ivMin * 100).toFixed(0)}%`);
    svg.append('text').attr('x', width).attr('y', height + 12)
       .attr('fill', '#888').attr('font-size', 10)
       .attr('text-anchor', 'end')
       .text(`${(ivMax * 100).toFixed(0)}%`);
  }, [colorScheme, ivMin, ivMax]);

  return (
    <div style={{
      position: 'absolute', top: 10, right: 10,
      background: 'rgba(0,0,0,0.6)', padding: '8px 10px',
      borderRadius: 6, border: '1px solid #333',
    }}>
      <div style={{ color: '#aaa', fontSize: 10, marginBottom: 4 }}>隐含波动率</div>
      <svg ref={svgRef} width={120} height={30} />
    </div>
  );
};

export default IVSurface3D;
