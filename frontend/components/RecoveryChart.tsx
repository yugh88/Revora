'use client';

import * as React from 'react';
import { LineChart as LineChartIcon } from 'lucide-react';
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { formatInrCompact, formatInrExact, formatPercent } from '../lib/api-client';
import type { AnalysisRun } from '../lib/types';
import { Card, CardDescription, CardHeader, CardTitle } from './ui/card';

/**
 * Where the money went, per analysis run.
 *
 * The stack is the ledger identity the backend guarantees and Session 4
 * verified to the paisa: recovered + pending + lost == amount at risk. So the
 * full height of each bar IS the amount at risk, and the segments are exactly
 * where it ended up. Nothing here is derived from anything but those four
 * fields.
 *
 * Colours are Section 13's: green recovered, amber pending, red unrecoverable.
 * The overlaid line is the recovery rate on its own axis, because a percentage
 * and a rupee amount cannot share a scale honestly.
 *
 * On the "over time" in "recovery over time": the backend exposes no historical
 * series — there is no endpoint that returns per-day recovery — so the x-axis is
 * successive runs in this session, labelled with the wall-clock time each one
 * finished. That is real measured data. Inventing a date axis from a single
 * batch would not be.
 */

interface ChartDatum {
  run: string;
  clock: string;
  recovered: number;
  pending: number;
  lost: number;
  atRisk: number;
  rate: number;
  records: number;
}

function CustomTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload: ChartDatum }>;
}) {
  if (!active || !payload?.length) return null;
  const datum = payload[0].payload;

  const rows: Array<{ label: string; value: string; className: string }> = [
    {
      label: 'Recovered',
      value: formatInrExact(datum.recovered),
      className: 'text-recovered',
    },
    { label: 'Pending', value: formatInrExact(datum.pending), className: 'text-pending' },
    {
      label: 'Lost',
      value: formatInrExact(datum.lost),
      className: 'text-unrecoverable',
    },
  ];

  return (
    <div className="rounded-lg border border-line bg-surface-raised px-3 py-2.5 shadow-card-hover">
      <p className="text-xs font-semibold text-ink">
        {datum.run}
        <span className="ml-2 font-normal text-ink-subtle">{datum.clock}</span>
      </p>
      <p className="mt-0.5 text-micro uppercase text-ink-subtle">
        {datum.records} records processed
      </p>
      <div className="mt-2 space-y-1">
        {rows.map((row) => (
          <div key={row.label} className="flex items-center justify-between gap-6 text-xs">
            <span className={row.className}>{row.label}</span>
            <span className="tabular font-medium text-ink">{row.value}</span>
          </div>
        ))}
        <div className="mt-1.5 flex items-center justify-between gap-6 border-t border-line pt-1.5 text-xs">
          <span className="text-ink-muted">At risk</span>
          <span className="tabular font-semibold text-ink">
            {formatInrExact(datum.atRisk)}
          </span>
        </div>
        <div className="flex items-center justify-between gap-6 text-xs">
          <span className="text-ink-muted">Recovery rate</span>
          <span className="tabular font-semibold text-accent">
            {formatPercent(datum.rate / 100)}
          </span>
        </div>
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 py-10 text-center">
      <span className="flex h-11 w-11 items-center justify-center rounded-xl border border-line bg-surface-raised">
        <LineChartIcon className="h-5 w-5 text-ink-subtle" aria-hidden="true" />
      </span>
      <p className="text-sm font-medium text-ink">No runs to chart yet</p>
      <p className="max-w-xs text-xs leading-relaxed text-ink-subtle">
        Each analysis adds a bar showing exactly where that batch&rsquo;s money ended up.
        Run a second analysis to see the trend line.
      </p>
    </div>
  );
}

export function RecoveryChart({ runs }: { runs: AnalysisRun[] }) {
  const data: ChartDatum[] = React.useMemo(
    () =>
      runs.map((run) => ({
        run: `Run ${run.index}`,
        clock: run.ranAt,
        recovered: Number.parseFloat(run.response.money.amount_recovered),
        pending: Number.parseFloat(run.response.money.amount_pending),
        lost: Number.parseFloat(run.response.money.amount_lost),
        atRisk: Number.parseFloat(run.response.money.amount_at_risk),
        rate: run.response.recovery_rate * 100,
        records: run.response.processed,
      })),
    [runs],
  );

  const axisStyle = {
    fontSize: 11,
    fill: 'hsl(var(--ink-subtle))',
  } as const;

  return (
    <Card className="flex h-full flex-col">
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle>Where the money went</CardTitle>
            <CardDescription>
              Each bar is one analysis run. Segments sum exactly to the amount at risk.
            </CardDescription>
          </div>
          {runs.length > 0 ? (
            <span className="tabular shrink-0 text-micro uppercase text-ink-subtle">
              {runs.length} {runs.length === 1 ? 'run' : 'runs'}
            </span>
          ) : null}
        </div>
      </CardHeader>

      {data.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="flex-1 px-2 pb-4">
          {/* Recharts renders an SVG that a screen reader cannot narrate, so the
              same figures are exposed as a table to assistive tech only. */}
          <table className="sr-only">
            <caption>Recovery outcome by analysis run</caption>
            <thead>
              <tr>
                <th scope="col">Run</th>
                <th scope="col">At risk</th>
                <th scope="col">Recovered</th>
                <th scope="col">Pending</th>
                <th scope="col">Lost</th>
                <th scope="col">Recovery rate</th>
              </tr>
            </thead>
            <tbody>
              {data.map((datum) => (
                <tr key={datum.run}>
                  <th scope="row">{datum.run}</th>
                  <td>{formatInrExact(datum.atRisk)}</td>
                  <td>{formatInrExact(datum.recovered)}</td>
                  <td>{formatInrExact(datum.pending)}</td>
                  <td>{formatInrExact(datum.lost)}</td>
                  <td>{formatPercent(datum.rate / 100)}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <ResponsiveContainer width="100%" height="100%" minHeight={260}>
            <ComposedChart
              data={data}
              margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
              barCategoryGap="28%"
            >
              <CartesianGrid
                strokeDasharray="3 3"
                vertical={false}
                stroke="hsl(var(--grid))"
              />
              <XAxis
                dataKey="run"
                tickLine={false}
                axisLine={{ stroke: 'hsl(var(--line))' }}
                tick={axisStyle}
                dy={4}
              />
              <YAxis
                yAxisId="money"
                tickLine={false}
                axisLine={false}
                tick={axisStyle}
                tickFormatter={(value: number) => formatInrCompact(value)}
                width={62}
              />
              <YAxis
                yAxisId="rate"
                orientation="right"
                tickLine={false}
                axisLine={false}
                tick={axisStyle}
                tickFormatter={(value: number) => `${value.toFixed(0)}%`}
                width={40}
                domain={[0, 100]}
              />
              <RechartsTooltip
                content={<CustomTooltip />}
                cursor={{ fill: 'hsl(var(--ink) / 0.04)' }}
              />
              <Legend
                verticalAlign="bottom"
                height={28}
                iconType="circle"
                iconSize={7}
                formatter={(value: string) => (
                  <span className="text-xs text-ink-muted">{value}</span>
                )}
              />
              <Bar
                yAxisId="money"
                dataKey="recovered"
                name="Recovered"
                stackId="money"
                fill="hsl(var(--recovered))"
                radius={[0, 0, 0, 0]}
              />
              <Bar
                yAxisId="money"
                dataKey="pending"
                name="Pending"
                stackId="money"
                fill="hsl(var(--pending))"
              />
              <Bar
                yAxisId="money"
                dataKey="lost"
                name="Lost"
                stackId="money"
                fill="hsl(var(--unrecoverable))"
                radius={[5, 5, 0, 0]}
              />
              <Line
                yAxisId="rate"
                type="monotone"
                dataKey="rate"
                name="Recovery rate"
                stroke="hsl(var(--accent))"
                strokeWidth={2}
                dot={{ r: 3, fill: 'hsl(var(--accent))', strokeWidth: 0 }}
                activeDot={{ r: 5 }}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
    </Card>
  );
}
