export type DocsNavItem = {
	label: string;
	href: string;
};

export type DocsNavSection = {
	label: string;
	items: DocsNavItem[];
};

export type DocsNavConfig = {
	indexHref: string;
	indexLabel: string;
	ariaLabel: string;
	sections: DocsNavSection[];
};
