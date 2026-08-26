'use client';

import * as React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  FileClock,
  LayoutDashboard,
  ListFilter,
  Loader2,
  MessageSquareText,
  PlayCircle,
  SlidersHorizontal,
  Wifi,
  WifiOff,
} from 'lucide-react';

import { api, BACKEND_URL } from '../../lib/api-client';
import { Badge } from './badge';
import { ThemeToggle } from './theme-toggle';
import { Tooltip, TooltipContent, TooltipTrigger } from './tooltip';
import { cn } from './utils';

/**
 * Application shell header.
 *
 * Only pages that EXIST are listed. /exceptions has no page of its own yet —
 * exception reasons currently surface on the events feed via the "needs review"
 * filter — so it is deliberately absent rather than rendered as a dead link.
 */
const NAV = [
  { href: '/', label: 'Overview', icon: LayoutDashboard },
  { href: '/events', label: 'Events', icon: ListFilter },
  { href: '/batch', label: 'Run analysis', icon: PlayCircle },
  { href: '/audit', label: 'Audit', icon: FileClock },
  { href: '/scripts', label: 'Scripts', icon: MessageSquareText },
  { href: '/policies', label: 'Policies', icon: SlidersHorizontal },
] as const;

type Connectivity = 'checking' | 'online' | 'offline';

function RevoraMark() {
  return (
    <span className="flex h-8 w-8 items-center justify-center rounded-[9px] bg-accent shadow-card">
      <svg
        width="18"
        height="18"
        viewBox="0 0 20 20"
        fill="none"
        aria-hidden="true"
        className="text-accent-ink"
      >
        <path
          d="M4 13.5C4 8.5 7.5 5 12.5 5H16"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        />
        <path
          d="M12.5 1.8 16 5l-3.5 3.2"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <circle cx="5" cy="15.5" r="2.2" fill="currentColor" />
      </svg>
    </span>
  );
}

function ConnectivityPill() {
  const [state, setState] = React.useState<Connectivity>('checking');

  const check = React.useCallback(async () => {
    setState('checking');
    try {
      await api.health();
      setState('online');
    } catch {
      setState('offline');
    }
  }, []);

  React.useEffect(() => {
    void check();
  }, [check]);

  const config = {
    checking: { variant: 'neutral' as const, text: 'Checking', Icon: Loader2 },
    online: { variant: 'recovered' as const, text: 'API online', Icon: Wifi },
    offline: { variant: 'unrecoverable' as const, text: 'API offline', Icon: WifiOff },
  }[state];
  const { Icon } = config;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          onClick={() => void check()}
          aria-label={`Backend status: ${config.text}. Click to re-check.`}
          className="rounded-full outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
        >
          <Badge variant={config.variant} className="cursor-pointer hover:brightness-105">
            <Icon
              className={cn('h-3 w-3', state === 'checking' && 'animate-spin')}
              aria-hidden="true"
            />
            <span className="hidden sm:inline">{config.text}</span>
          </Badge>
        </button>
      </TooltipTrigger>
      <TooltipContent>
        <p className="font-medium text-ink">{config.text}</p>
        <p className="mt-0.5 break-all text-ink-subtle">{BACKEND_URL}</p>
        <p className="mt-1 text-ink-muted">Click to re-check.</p>
      </TooltipContent>
    </Tooltip>
  );
}

export function SiteHeader() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-40 border-b border-line bg-bg/85 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-[1400px] items-center gap-4 px-4 sm:px-6 lg:px-8">
        <Link
          href="/"
          className="flex shrink-0 items-center gap-2.5 rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
          aria-label="Revora home"
        >
          <RevoraMark />
          <span className="hidden text-[15px] font-semibold tracking-tight text-ink sm:block">
            Revora
          </span>
        </Link>

        <nav aria-label="Primary" className="min-w-0 flex-1">
          <ul className="flex items-center gap-1 overflow-x-auto">
            {NAV.map((item) => {
              // "/" must match exactly or it would light up on every route.
              const active =
                item.href === '/' ? pathname === '/' : pathname.startsWith(item.href);
              const Icon = item.icon;
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    aria-current={active ? 'page' : undefined}
                    className={cn(
                      'flex items-center gap-1.5 whitespace-nowrap rounded-lg px-2.5 py-1.5 text-sm font-medium transition-colors',
                      'outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg',
                      active
                        ? 'bg-surface-raised text-ink shadow-card'
                        : 'text-ink-subtle hover:bg-surface-raised hover:text-ink',
                    )}
                  >
                    <Icon className="h-4 w-4" aria-hidden="true" />
                    <span className="hidden md:inline">{item.label}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        <div className="flex shrink-0 items-center gap-2">
          <ConnectivityPill />
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
