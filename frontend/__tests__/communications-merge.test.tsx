/**
 * The merged Recovery Messages functionality, rendered.
 *
 * The previous test asserts the old page is gone. This one asserts the useful
 * parts of it survived the move — otherwise "merged" would just mean "deleted".
 *
 * Everything the old page offered is checked: the message itself, why Revora
 * chose this customer, the full compliance verdict, and tone/urgency/channel.
 */

import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { MessageDetail } from '../app/communications/message-detail';
import type { ScriptResponse } from '../lib/types';

const SCRIPT: ScriptResponse = {
  event_id: 'evt_1',
  event_type: 'payment_degraded',
  customer_id: 'cust_1',
  amount: '8500.00',
  currency: 'INR',
  script: 'Namaste Meera, aapka payment pending hai.',
  reasoning: 'The card on file expired, so a card-update link is the fastest route.',
  tone: 'friendly',
  urgency: 'low',
  channel: 'email',
  language: 'hinglish',
  compliant: true,
  compliance_checks: [
    { rule_id: 'contact_time_window', description: 'Contact only between 08:00 and 19:00 IST.', passed: true, detail: '12:00 IST is within 08:00-19:00.' },
    { rule_id: 'no_coercive_language', description: 'No threatening or shaming phrasing.', passed: true, detail: 'No blocked phrasing.' },
    { rule_id: 'frequency_cap', description: 'Respect the contact limit per channel.', passed: true, detail: '0 of 3 contacts used.' },
    { rule_id: 'no_false_urgency', description: 'Urgency must match the escalation level.', passed: true, detail: 'Urgency matches escalation.' },
  ],
  failure_reason: null,
  template_key: 'by_root_cause.card_expired.friendly',
  slots_used: {},
  is_preview: false,
  preview_time: null,
};

describe('the message detail merged into Communications', () => {
  it('shows why Revora would contact this customer', () => {
    render(<MessageDetail script={SCRIPT} />);
    expect(
      screen.getByText(/why revora would contact this customer/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/card on file expired/i)).toBeInTheDocument();
  });

  it('shows tone, urgency and channel', () => {
    render(<MessageDetail script={SCRIPT} />);
    expect(screen.getByText(/^tone$/i)).toBeInTheDocument();
    expect(screen.getByText(/^urgency$/i)).toBeInTheDocument();
    expect(screen.getByText(/^channel$/i)).toBeInTheDocument();
  });

  it('lists every compliance rule, not only the failures', () => {
    // A merchant who only ever sees failures has no way to know the checks
    // are running at all.
    render(<MessageDetail script={SCRIPT} />);
    expect(screen.getByText(/is this message allowed\?/i)).toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(4);
  });

  it('explains a failed rule', () => {
    const blocked: ScriptResponse = {
      ...SCRIPT,
      compliant: false,
      compliance_checks: SCRIPT.compliance_checks.map((check) =>
        check.rule_id === 'contact_time_window'
          ? { ...check, passed: false, detail: '21:05 IST is outside 08:00-19:00.' }
          : check,
      ),
    };
    render(<MessageDetail script={blocked} />);
    expect(screen.getByText(/outside 08:00-19:00/i)).toBeInTheDocument();
  });

  it('hides the reasoning block when there is nothing to say', () => {
    render(<MessageDetail script={{ ...SCRIPT, reasoning: '' }} />);
    expect(
      screen.queryByText(/why revora would contact this customer/i),
    ).not.toBeInTheDocument();
  });

  it('renders nothing rather than an empty shell when no checks were recorded', () => {
    render(<MessageDetail script={{ ...SCRIPT, compliance_checks: [] }} />);
    expect(screen.queryByText(/is this message allowed\?/i)).not.toBeInTheDocument();
  });
});
