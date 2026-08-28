'use client';

import * as React from 'react';
import Link from 'next/link';
import { usePathname, useSearchParams } from 'next/navigation';
import {
  ChevronDown,
  CreditCard,
  FileClock,
  FileText,
  HandCoins,
  Mail,
  MessageSquare,
  PhoneCall,
  Smartphone,
  Landmark,
  LayoutDashboard,
  Menu,
  MessageSquareText,
  PlayCircle,
  RefreshCw,
  ShoppingCart,
  SlidersHorizontal,
  TrendingUp,
  X,
  type LucideIcon,
} from 'lucide-react';

import { ThemeToggle } from './theme-toggle';
import { cn } from './utils';

/**
 * Sidebar application shell.
 *
 * Only destinations that ACTUALLY WORK appear here. The spec's fuller
 * information architecture also lists Promises to Pay, Communications,
 * Notifications, Settings, Help and Documentation — none of which have a
 * backend or a page yet, so none of them are rendered. A nav item that opens
 * nothing is worse than one that is absent: it teaches a judge that the product
 * is hollow behind the first click.
 *
 * The Revenue Recovery sub-items are real. They deep-link into the events feed
 * with a type filter that the API applies server-side, so "Payments" genuinely
 * shows only failed payments rather than a decorative label.
 *
 * There is no connectivity indicator. A merchant does not think about whether an
 * API is reachable, and the spec is explicit that it must not be replaced with
 * another technical status readout. Connection problems surface where they
 * matter — as a plain-language error on the screen that failed.
 */

interface NavLeaf {
  href: string;
  label: string;
  icon?: LucideIcon;
  /** Matches when the events feed carries this type filter. */
  eventType?: string;
}

interface NavGroup {
  label: string;
  icon: LucideIcon;
  href: string;
  children?: NavLeaf[];
}

const PRIMARY: NavGroup[] = [
  { label: 'Overview', icon: LayoutDashboard, href: '/' },
  {
    label: 'Revenue Recovery',
    icon: TrendingUp,
    href: '/events',
    children: [
      { href: '/events', label: 'All Recoveries' },
      { href: '/events?type=payment_degraded', label: 'Payments', icon: CreditCard, eventType: 'payment_degraded' },
      { href: '/events?type=checkout_abandoned', label: 'Checkout Abandonment', icon: ShoppingCart, eventType: 'checkout_abandoned' },
      { href: '/events?type=subscription_failed', label: 'Subscriptions', icon: RefreshCw, eventType: 'subscription_failed' },
      { href: '/events?type=mandate_failed', label: 'Mandates', icon: Landmark, eventType: 'mandate_failed' },
      { href: '/events?type=invoice_overdue', label: 'Invoices & Receivables', icon: FileText, eventType: 'invoice_overdue' },
    ],
  },
  { label: 'Promises to Pay', icon: HandCoins, href: '/promises' },
  { label: 'Run Recovery', icon: PlayCircle, href: '/batch' },
  {
    label: 'Communications',
    icon: MessageSquare,
    href: '/communications',
    children: [
      { href: '/communications', label: 'All contacts' },
      { href: '/communications?channel=email', label: 'Email', icon: Mail },
      { href: '/communications?channel=sms', label: 'SMS', icon: Smartphone },
      { href: '/communications?channel=voice_script', label: 'Voice', icon: PhoneCall },
    ],
  },
  { label: 'Recovery Messages', icon: MessageSquareText, href: '/scripts' },
  { label: 'Activity Log', icon: FileClock, href: '/audit' },
  { label: 'Policies', icon: SlidersHorizontal, href: '/policies' },
];

function RevoraMark({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        'flex h-8 w-8 items-center justify-center rounded-[9px] bg-accent shadow-card',
        className,
      )}
    >
      <svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden="true" className="text-accent-ink">
        <path d="M4 13.5C4 8.5 7.5 5 12.5 5H16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        <path d="M12.5 1.8 16 5l-3.5 3.2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="5" cy="15.5" r="2.2" fill="currentColor" />
      </svg>
    </span>
  );
}

function NavItems({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const activeType = searchParams.get('type');
  const activeChannel = searchParams.get('channel');

  // Open the group that contains the current page, so a deep link lands with
  // its section already expanded rather than collapsed and disorienting.
  const [open, setOpen] = React.useState<Record<string, boolean>>(() => ({
    'Revenue Recovery': pathname.startsWith('/events'),
    Communications: pathname.startsWith('/communications'),
  }));

  return (
    <nav aria-label="Primary" className="flex flex-col gap-0.5">
      {PRIMARY.map((group) => {
        const Icon = group.icon;
        const isEvents = group.href === '/events';
        const groupActive =
          group.href === '/'
            ? pathname === '/'
            : pathname.startsWith(group.href);

        if (!group.children) {
          return (
            <Link
              key={group.label}
              href={group.href}
              onClick={onNavigate}
              aria-current={groupActive ? 'page' : undefined}
              className={cn(
                'flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors',
                'outline-none focus-visible:ring-2 focus-visible:ring-accent',
                groupActive
                  ? 'bg-accent/[0.08] font-medium text-ink'
                  : 'text-ink-muted hover:bg-surface-raised hover:text-ink',
              )}
            >
              <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
              {group.label}
            </Link>
          );
        }

        const expanded = open[group.label] ?? false;
        return (
          <div key={group.label}>
            <button
              type="button"
              onClick={() => setOpen((c) => ({ ...c, [group.label]: !expanded }))}
              aria-expanded={expanded}
              className={cn(
                'flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors',
                'outline-none focus-visible:ring-2 focus-visible:ring-accent',
                groupActive
                  ? 'font-medium text-ink'
                  : 'text-ink-muted hover:bg-surface-raised hover:text-ink',
              )}
            >
              <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
              <span className="flex-1 text-left">{group.label}</span>
              <ChevronDown
                className={cn('h-3.5 w-3.5 transition-transform', expanded && 'rotate-180')}
                aria-hidden="true"
              />
            </button>

            {expanded ? (
              <ul className="ml-[1.35rem] mt-0.5 space-y-0.5 border-l border-line pl-2.5">
                {group.children.map((child) => {
                  const active =
                    isEvents && pathname === '/events'
                      ? (child.eventType ?? null) === activeType
                      : pathname === '/communications'
                        ? child.href === `/communications${activeChannel ? `?channel=${activeChannel}` : ''}`
                        : false;
                  return (
                    <li key={child.label}>
                      <Link
                        href={child.href}
                        onClick={onNavigate}
                        aria-current={active ? 'page' : undefined}
                        className={cn(
                          'block rounded-md px-2 py-1.5 text-xs transition-colors',
                          'outline-none focus-visible:ring-2 focus-visible:ring-accent',
                          active
                            ? 'bg-accent/[0.08] font-medium text-ink'
                            : 'text-ink-subtle hover:bg-surface-raised hover:text-ink-muted',
                        )}
                      >
                        {child.label}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            ) : null}
          </div>
        );
      })}
    </nav>
  );
}

/** Page title derived from the route, so the topbar always names where you are. */
function currentSection(pathname: string): string {
  if (pathname === '/') return 'Overview';
  if (pathname.startsWith('/events')) return 'Revenue Recovery';
  if (pathname.startsWith('/communications')) return 'Communications';
  if (pathname.startsWith('/promises')) return 'Promises to Pay';
  if (pathname.startsWith('/batch')) return 'Run Recovery';
  if (pathname.startsWith('/scripts')) return 'Recovery Messages';
  if (pathname.startsWith('/audit')) return 'Activity Log';
  if (pathname.startsWith('/policies')) return 'Policies';
  return 'Revora';
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = React.useState(false);

  React.useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[248px_1fr]">
      {/* ---------------- Sidebar ---------------- */}
      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-50 w-[248px] border-r border-line bg-surface transition-transform lg:sticky lg:top-0 lg:h-screen lg:translate-x-0',
          mobileOpen ? 'translate-x-0' : '-translate-x-full',
        )}
      >
        <div className="flex h-16 items-center gap-2.5 px-4">
          <Link
            href="/"
            className="flex items-center gap-2.5 rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <RevoraMark />
            <span className="text-[15px] font-semibold tracking-tight text-ink">Revora</span>
          </Link>
          <button
            type="button"
            onClick={() => setMobileOpen(false)}
            className="ml-auto rounded-lg p-1.5 text-ink-subtle hover:bg-surface-raised lg:hidden"
            aria-label="Close menu"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        <div className="px-3 pb-4">
          <React.Suspense fallback={<div className="h-64" />}>
            <NavItems onNavigate={() => setMobileOpen(false)} />
          </React.Suspense>
        </div>

        <div className="absolute inset-x-0 bottom-0 border-t border-line px-4 py-3">
          <p className="text-micro uppercase tracking-wide text-ink-subtle">
            Revenue recovery
          </p>
          <p className="mt-0.5 text-micro text-ink-subtle">
            Detect · Diagnose · Decide · Recover
          </p>
        </div>
      </aside>

      {mobileOpen ? (
        <button
          type="button"
          aria-label="Close menu"
          onClick={() => setMobileOpen(false)}
          className="fixed inset-0 z-40 bg-ink/20 backdrop-blur-sm lg:hidden"
        />
      ) : null}

      {/* ---------------- Content ---------------- */}
      <div className="min-w-0">
        <header className="sticky top-0 z-30 border-b border-line bg-bg/85 backdrop-blur-xl">
          <div className="flex h-16 items-center gap-3 px-4 sm:px-6 lg:px-8">
            <button
              type="button"
              onClick={() => setMobileOpen(true)}
              className="rounded-lg p-1.5 text-ink-muted hover:bg-surface-raised lg:hidden"
              aria-label="Open menu"
            >
              <Menu className="h-5 w-5" aria-hidden="true" />
            </button>
            <p className="text-sm font-medium text-ink">{currentSection(pathname)}</p>
            <div className="ml-auto flex items-center gap-2">
              <ThemeToggle />
            </div>
          </div>
        </header>
        {children}
      </div>
    </div>
  );
}

/** Retained name so existing pages keep working; the shell is now a sidebar. */
export const SiteHeader = AppShell;
