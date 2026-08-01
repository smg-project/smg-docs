import { base } from '$app/paths';

export const siteName = 'Shepherd Model Gateway';

export const headerNavItems = [
	{ href: `${base}/getting-started`, label: 'Getting Started' },
	{ href: `${base}/concepts`, label: 'Concepts' },
	{ href: `${base}/reference`, label: 'Reference' },
	{ href: `${base}/contributing`, label: 'Contributing' }
] as const;

/** @deprecated Use headerNavItems — kept for any legacy imports */
export const navItems = [{ href: `${base}/`, label: 'Home' }, ...headerNavItems] as const;
