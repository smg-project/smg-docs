export const siteName = 'Shepherd Model Gateway';

export const headerNavItems = [
	{ href: '/getting-started', label: 'Getting Started' },
	{ href: '/concepts', label: 'Concepts' },
	{ href: '/reference', label: 'Reference' },
	{ href: '/contributing', label: 'Contributing' }
] as const;

/** @deprecated Use headerNavItems — kept for any legacy imports */
export const navItems = [{ href: '/', label: 'Home' }, ...headerNavItems] as const;
