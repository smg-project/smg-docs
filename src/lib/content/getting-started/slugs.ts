/** URL slug → markdown filename (without extension) */
export const slugToFile: Record<string, string> = {
	'tokenization-and-parsing-apis': 'tokenization-and-parsing',
	'kv-events-cache-aware-routing': 'kv-events-cache-aware',
	'mcp-in-responses-api': 'mcp'
};

export const fileToSlug: Record<string, string> = {
	'tokenization-and-parsing': 'tokenization-and-parsing-apis',
	'kv-events-cache-aware': 'kv-events-cache-aware-routing',
	mcp: 'mcp-in-responses-api'
};

export function resolveFileName(slug: string): string {
	return slugToFile[slug] ?? slug;
}
