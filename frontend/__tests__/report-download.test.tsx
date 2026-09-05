/**
 * Download Report, on both Audit and All Recoveries.
 *
 * The dialog is shared, so the tests cover it once and then check each tab
 * actually wires it up. What matters is that the period a person picks is the
 * period the request asks for — a selector that looked right but always sent
 * the same range would be worse than no selector.
 */

import fs from 'node:fs';
import path from 'node:path';

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ReportDialog } from '../components/ReportDialog';
import { api } from '../lib/api-client';

const APP = path.join(process.cwd(), 'app');

beforeEach(() => {
  // jsdom has neither of these; downloading needs both.
  globalThis.URL.createObjectURL = vi.fn(() => 'blob:report');
  globalThis.URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  vi.restoreAllMocks();
});

function open() {
  const onClose = vi.fn();
  render(
    <ReportDialog kind="recovery" title="Recovery cases" open onClose={onClose} />,
  );
  return onClose;
}

describe('choosing a period', () => {
  it('offers the presets and a custom range', () => {
    open();
    for (const label of ['Last 1 day', 'Last 1 week', 'Last 1 month', 'All time', 'Custom range']) {
      expect(screen.getByRole('radio', { name: label })).toBeInTheDocument();
    }
  });

  it('only shows date inputs for a custom range', () => {
    open();
    expect(screen.queryByLabelText(/from/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: 'Custom range' }));
    expect(screen.getByLabelText(/from/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/to/i)).toBeInTheDocument();
  });

  it('sends the preset the user picked', async () => {
    const spy = vi
      .spyOn(api, 'downloadReport')
      .mockResolvedValue(new Blob(['%PDF'], { type: 'application/pdf' }));
    open();

    fireEvent.click(screen.getByRole('radio', { name: 'Last 1 week' }));
    fireEvent.click(screen.getByRole('button', { name: /download pdf/i }));

    await waitFor(() => expect(spy).toHaveBeenCalledWith('recovery', { days: 7 }));
  });

  it('sends explicit dates for a custom range, not a preset', async () => {
    const spy = vi
      .spyOn(api, 'downloadReport')
      .mockResolvedValue(new Blob(['%PDF'], { type: 'application/pdf' }));
    open();

    fireEvent.click(screen.getByRole('radio', { name: 'Custom range' }));
    fireEvent.change(screen.getByLabelText(/from/i), { target: { value: '2026-08-01' } });
    fireEvent.change(screen.getByLabelText(/to/i), { target: { value: '2026-08-31' } });
    fireEvent.click(screen.getByRole('button', { name: /download pdf/i }));

    await waitFor(() => expect(spy).toHaveBeenCalled());
    const [kind, range] = spy.mock.calls[0];
    expect(kind).toBe('recovery');
    expect(range.days).toBeUndefined();
    expect(range.from).toContain('2026-08-01');
    expect(range.to).toContain('2026-08-31');
  });

  it('refuses a backwards range rather than asking for it', () => {
    open();
    fireEvent.click(screen.getByRole('radio', { name: 'Custom range' }));
    fireEvent.change(screen.getByLabelText(/from/i), { target: { value: '2026-08-31' } });
    fireEvent.change(screen.getByLabelText(/to/i), { target: { value: '2026-08-01' } });

    expect(screen.getByText(/start date is after the end date/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /download pdf/i })).toBeDisabled();
  });
});

describe('generating the report', () => {
  it('saves the file and closes', async () => {
    vi.spyOn(api, 'downloadReport').mockResolvedValue(
      new Blob(['%PDF'], { type: 'application/pdf' }),
    );
    const onClose = open();

    fireEvent.click(screen.getByRole('button', { name: /download pdf/i }));

    await waitFor(() => expect(globalThis.URL.createObjectURL).toHaveBeenCalled());
    // Revoked immediately; holding it keeps the whole PDF in memory.
    expect(globalThis.URL.revokeObjectURL).toHaveBeenCalled();
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('shows progress while it works', async () => {
    let release: (value: Blob) => void = () => {};
    vi.spyOn(api, 'downloadReport').mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }),
    );
    const onClose = open();

    fireEvent.click(screen.getByRole('button', { name: /download pdf/i }));
    expect(await screen.findByText(/preparing/i)).toBeInTheDocument();

    // Released inside act, then awaited. Resolving after the test body without
    // waiting leaves React updating a component nobody is observing, which is
    // what the act(...) warning was reporting.
    await act(async () => {
      release(new Blob(['%PDF']));
    });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('reports a failure without closing, so the choice is not lost', async () => {
    vi.spyOn(api, 'downloadReport').mockRejectedValue(new Error('boom'));
    const onClose = open();

    fireEvent.click(screen.getByRole('button', { name: /download pdf/i }));

    expect(await screen.findByText(/could not be/i)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('does not save a file when generation failed', async () => {
    vi.spyOn(api, 'downloadReport').mockRejectedValue(new Error('boom'));
    open();

    fireEvent.click(screen.getByRole('button', { name: /download pdf/i }));

    await screen.findByText(/could not be/i);
    expect(globalThis.URL.createObjectURL).not.toHaveBeenCalled();
  });
});

describe('both tabs offer it', () => {
  it('Audit has a Download report control wired to the audit report', () => {
    const source = fs.readFileSync(path.join(APP, 'audit', 'page.tsx'), 'utf8');
    expect(source).toContain('Download report');
    expect(source).toMatch(/<ReportDialog[\s\S]*kind="audit"/);
  });

  it('All Recoveries has one wired to the recovery report', () => {
    const source = fs.readFileSync(path.join(APP, 'events', 'page.tsx'), 'utf8');
    expect(source).toContain('Download report');
    expect(source).toMatch(/<ReportDialog[\s\S]*kind="recovery"/);
  });

  it('both reuse the one shared dialog', () => {
    // Two dialogs would drift apart; there must be exactly one component.
    for (const page of ['audit', 'events']) {
      const source = fs.readFileSync(path.join(APP, page, 'page.tsx'), 'utf8');
      expect(source).toContain("from '../../components/ReportDialog'");
    }
  });
});
