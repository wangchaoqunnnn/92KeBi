import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'

// 通用 ECharts 容器：dark 主题由 option 内文字颜色控制，跟随容器尺寸自适应
export default function EChart({ option, height = 280, style, notMerge = true, onEvents }) {
  const ref = useRef(null)
  const inst = useRef(null)

  useEffect(() => {
    if (!ref.current) return
    inst.current = echarts.init(ref.current)
    const ro = new ResizeObserver(() => inst.current && inst.current.resize())
    ro.observe(ref.current)
    return () => {
      ro.disconnect()
      inst.current && inst.current.dispose()
      inst.current = null
    }
  }, [])

  useEffect(() => {
    if (inst.current && option) {
      inst.current.setOption(option, notMerge)
      if (onEvents) {
        Object.entries(onEvents).forEach(([ev, fn]) => inst.current.off(ev) || inst.current.on(ev, fn))
      }
    }
  }, [option, notMerge])

  return <div ref={ref} style={{ width: '100%', height, ...style }} />
}

export const AXIS_TEXT = '#8fa3c0'
export const GRID_LINE = 'rgba(143,163,192,0.15)'
export const SPLIT_LINE = { lineStyle: { color: GRID_LINE, type: 'dashed' } }
export const TOOLTIP_BG = 'rgba(13,20,34,0.95)'
export const TOOLTIP = {
  backgroundColor: TOOLTIP_BG,
  borderColor: '#2b3a55',
  textStyle: { color: '#e8eefc', fontSize: 12 },
  confine: true,
}
export const axisCommon = (cat = true) => ({
  axisLine: { lineStyle: { color: GRID_LINE } },
  axisTick: { show: false },
  axisLabel: { color: AXIS_TEXT, fontSize: 11 },
  splitLine: cat ? { show: false } : SPLIT_LINE,
})
