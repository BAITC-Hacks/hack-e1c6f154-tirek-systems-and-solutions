import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { ItemDetail } from '../api/client'
import { date, number } from './ui'

export default function DemandChart({ detail }: { detail: ItemDetail }) {
  const history = detail.history.map((row) => ({
    period: date(row.period_end, true),
    actual: row.regular_sales,
    forecast: null as number | null,
    range: null as number[] | null,
  }))
  const forecast = detail.item.forecast
  const last = history.at(-1)
  if (last && forecast) last.forecast = last.actual
  const rows = [
    ...history,
    ...(forecast
      ? [
          {
            period: date(forecast.period_end, true),
            actual: null,
            forecast: forecast.p50 ?? forecast.mean,
            range:
              forecast.p10 !== null && forecast.p90 !== null ? [forecast.p10, forecast.p90] : null,
          },
        ]
      : []),
  ]
  return (
    <div className="chart-block">
      <div className="chart-legend">
        <span>
          <i className="legend-line" />
          Регулярные продажи
        </span>
        <span>
          <i className="legend-line forecast" />
          Прогноз на 28 дней
        </span>
        <span className="chart-unit">{detail.item.unit} / 28 дней</span>
      </div>
      <div
        className="demand-chart"
        role="img"
        aria-label={`История спроса ${detail.item.sku}. Последний период: ${number(last?.actual)} ${detail.item.unit}. Прогноз: ${number(forecast?.p50)}. Синтетические данные.`}
      >
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 20, right: 25, left: -23, bottom: 0 }}>
            <defs>
              <linearGradient id="demand-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#208163" stopOpacity={0.17} />
                <stop offset="100%" stopColor="#208163" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 5" vertical={false} stroke="#e5ebe7" />
            <XAxis
              dataKey="period"
              axisLine={false}
              tickLine={false}
              tick={{ fill: '#68776e', fontSize: 11 }}
              dy={10}
              minTickGap={20}
            />
            <YAxis axisLine={false} tickLine={false} tick={{ fill: '#68776e', fontSize: 11 }} />
            <Tooltip
              contentStyle={{ border: '1px solid #e0e7e2', borderRadius: 10, fontSize: 12 }}
              formatter={(value, name) => [
                Array.isArray(value) ? value.join('–') : number(Number(value)),
                name === 'actual' ? 'Продажи' : name === 'range' ? 'Диапазон P10–P90' : 'Прогноз',
              ]}
            />
            {last && <ReferenceLine x={last.period} stroke="#a8b7ad" strokeDasharray="4 4" />}
            <Area
              type="monotone"
              dataKey="actual"
              stroke="#208163"
              strokeWidth={2.5}
              fill="url(#demand-fill)"
              isAnimationActive={false}
            />
            <Line
              type="linear"
              dataKey="forecast"
              stroke="#208163"
              strokeWidth={2.5}
              strokeDasharray="5 5"
              dot={{ r: 4, fill: '#208163', stroke: '#fff', strokeWidth: 2 }}
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      {forecast && (
        <div className="chart-foot">
          <span>Каждая точка — полный период 28 дней</span>
          <span>
            Прогнозный интервал:{' '}
            <strong>
              {number(forecast.p10)}–{number(forecast.p90)} {detail.item.unit}
            </strong>
          </span>
        </div>
      )}
    </div>
  )
}
