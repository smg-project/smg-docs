import { Marked } from 'marked';
import { highlightCode } from '$lib/markdown/highlight';
import { rewriteAssetPaths, rewriteDocLinks } from '$lib/markdown/links';

let tabSetCounter = 0;

type DocBlock =
	| { kind: 'prerequisites'; markdown: string }
	| { kind: 'diagram'; markdown: string }
	| { kind: 'grid'; cards: string[] }
	| { kind: 'admonition'; variant: string; title: string; markdown: string }
	| { kind: 'details'; title: string; markdown: string };

let docBlocks: DocBlock[] = [];

function slugifyHeading(text: string): string {
	return text
		.replace(/<[^>]+>/g, '')
		.trim()
		.toLowerCase()
		.replace(/[^\w\s-]/g, '')
		.replace(/\s+/g, '-');
}

function uniqueHeadingId(baseId: string, slugCounts: Map<string, number>): string {
	const count = slugCounts.get(baseId) ?? 0;
	slugCounts.set(baseId, count + 1);
	return count === 0 ? baseId : `${baseId}-${count}`;
}

function escapeHtml(value: string): string {
	return value
		.replaceAll('&', '&amp;')
		.replaceAll('<', '&lt;')
		.replaceAll('>', '&gt;')
		.replaceAll('"', '&quot;');
}

function dedentBlock(body: string): string {
	return body
		.split('\n')
		.map((line) => line.replace(/^ {4}/, ''))
		.join('\n')
		.trim();
}

function blockPlaceholder(): string {
	const id = docBlocks.length;
	return `\n<!--DOCBLOCK:${id}-->\n`;
}

function stripMaterialIcons(text: string): string {
	return text
		.replace(/:material-check:/g, '✓')
		.replace(/:material-close:/g, '✗')
		.replace(/:material-[\w-]+:\{[^}]*\}\s*/g, '')
		.replace(/:octicons-[\w-]+-\d+:\s*/g, '')
		.replace(/:material-[\w-]+:\s*/g, '');
}

function parseGridCardItem(item: string): string {
	return dedentBlock(
		item
			.replace(/^-\s+/, '')
			.replace(/\n\s+---\s*\n/g, '\n\n')
			.trim()
	);
}

function convertGridCards(text: string): string {
	return text.replace(
		/<div class="grid cards" markdown>\s*([\s\S]*?)\s*<\/div>/gi,
		(_match, body: string) => {
			const items = body
				.trim()
				.split(/\n(?=-\s+)/)
				.map((item) => parseGridCardItem(item))
				.filter(Boolean);

			docBlocks.push({ kind: 'grid', cards: items });
			return blockPlaceholder();
		}
	);
}

function convertArchitectureDiagram(text: string): string {
	return text.replace(
		/<div class="architecture-diagram" markdown>\s*([\s\S]*?)\s*<\/div>/gi,
		(_match, body: string) => {
			docBlocks.push({ kind: 'diagram', markdown: dedentBlock(body) });
			return blockPlaceholder();
		}
	);
}

function convertGrid(text: string): string {
	return text.replace(
		/<div class="grid" markdown>\s*([\s\S]*?)\s*<\/div>/gi,
		(_match, body: string) => {
			const cards: string[] = [];
			const cardRe = /<div class="card" markdown>\s*([\s\S]*?)\s*<\/div>/gi;
			let match: RegExpExecArray | null;

			while ((match = cardRe.exec(body)) !== null) {
				cards.push(dedentBlock(match[1]));
			}

			docBlocks.push({ kind: 'grid', cards });
			return blockPlaceholder();
		}
	);
}

function convertAdmonitions(text: string): string {
	return text.replace(
		/^!!! (\w+)(?: "(.+)")?\n((?: {4}.+\n?)*)/gm,
		(_match, variant: string, title: string | undefined, body: string) => {
			docBlocks.push({
				kind: 'admonition',
				variant,
				title: title ?? '',
				markdown: dedentBlock(body)
			});
			return blockPlaceholder();
		}
	);
}

function convertPrerequisites(text: string): string {
	return text.replace(
		/<div class="prerequisites" markdown>\s*([\s\S]*?)\s*<\/div>/gi,
		(_match, body: string) => {
			docBlocks.push({ kind: 'prerequisites', markdown: dedentBlock(body) });
			return blockPlaceholder();
		}
	);
}

function convertDetails(text: string): string {
	return text.replace(
		/^\?\?\? question "(.+)"\n((?: {4}.+\n?)*)/gm,
		(_match, title: string, body: string) => {
			docBlocks.push({ kind: 'details', title, markdown: dedentBlock(body) });
			return blockPlaceholder();
		}
	);
}

function isTabStart(line: string): RegExpMatchArray | null {
	return line.match(/^=== "(.+)"$/);
}

function isUnindentedHeading(line: string): boolean {
	return /^#{1,6} /.test(line);
}

function collectTabContent(lines: string[], start: number): { content: string[]; next: number } {
	const content: string[] = [];
	let i = start;

	while (i < lines.length) {
		const line = lines[i];

		if (isTabStart(line) || isUnindentedHeading(line)) break;

		if (line.trim() === '') {
			content.push('');
			i++;
			continue;
		}

		if (line.startsWith('    ')) {
			content.push(line.replace(/^ {3} ?/, ''));
			i++;
			continue;
		}

		break;
	}

	return { content, next: i };
}

function parseTabGroups(text: string, parseMarkdown: (input: string) => string): string {
	const lines = text.split('\n');
	const result: string[] = [];
	let i = 0;

	while (i < lines.length) {
		if (!isTabStart(lines[i])) {
			result.push(lines[i]);
			i++;
			continue;
		}

		const tabs: { title: string; content: string }[] = [];

		while (i < lines.length && isTabStart(lines[i])) {
			const title = isTabStart(lines[i])![1];
			i++;
			while (i < lines.length && lines[i].trim() === '') i++;

			const collected = collectTabContent(lines, i);
			i = collected.next;

			tabs.push({
				title,
				content: parseMarkdown(collected.content.join('\n').trim())
			});
		}

		const setId = ++tabSetCounter;
		const labels = tabs
			.map(
				(tab, index) =>
					`<label class="doc-tab-label" for="doc-tab-${setId}-${index}">${escapeHtml(tab.title)}</label>`
			)
			.join('');

		const inputs = tabs
			.map(
				(_, index) =>
					`<input type="radio" class="doc-tab-input" name="doc-tab-set-${setId}" id="doc-tab-${setId}-${index}"${index === 0 ? ' checked' : ''} />`
			)
			.join('');

		const panels = tabs.map((tab) => `<div class="doc-tab-panel">${tab.content}</div>`).join('');

		result.push(
			`<div class="doc-tabbed-set">${inputs}<div class="doc-tab-labels">${labels}</div><div class="doc-tab-panels">${panels}</div></div>`
		);
		// The blank lines after the tab block were consumed above; emit one so the
		// raw-HTML block ends here and whatever follows is parsed as markdown.
		result.push('');
	}

	return result.join('\n');
}

function stripFrontmatter(raw: string): string {
	return raw.replace(/^---\n[\s\S]*?\n---\n/, '');
}

function stripMkdocsHtml(raw: string): string {
	return raw.replace(/<div[^>]*>\s*/gi, '').replace(/<\/div>\s*/gi, '');
}

function stripDocNoiseForToc(raw: string): string {
	let text = stripFrontmatter(raw);
	text = text.replace(/<div class="prerequisites" markdown>[\s\S]*?<\/div>/gi, '');
	text = stripMkdocsHtml(text);
	return text;
}

function createMarked(slugCounts = new Map<string, number>()): Marked {
	return new Marked({
		renderer: {
			code({ text, lang }) {
				const language = lang?.trim().toLowerCase() || 'plaintext';
				const highlighted = highlightCode(text, lang);
				return `<div class="doc-code"><button type="button" class="doc-code-copy" aria-label="Copy to clipboard">Copy</button><pre class="doc-pre"><code class="hljs language-${escapeHtml(language)}">${highlighted}</code></pre></div>`;
			},
			heading({ tokens, depth }) {
				const text = this.parser.parseInline(tokens);
				const id = uniqueHeadingId(slugifyHeading(text), slugCounts);
				return `<h${depth} id="${id}">${text}<a class="doc-headerlink" href="#${id}" aria-label="Permanent link">¶</a></h${depth}>\n`;
			}
		}
	});
}

function prepareBlockMarkdown(markdown: string, sourcePath?: string): string {
	return rewriteDocLinks(rewriteAssetPaths(markdown), sourcePath);
}

function resolveDocBlocks(html: string, marked: Marked, sourcePath?: string): string {
	return html.replace(/<!--DOCBLOCK:(\d+)-->/g, (_match, idStr: string) => {
		const block = docBlocks[Number(idStr)];
		if (!block) return '';

		if (block.kind === 'grid') {
			const cards = block.cards
				.map(
					(markdown) =>
						`<div class="doc-card">${marked.parse(prepareBlockMarkdown(markdown, sourcePath), { async: false }) as string}</div>`
				)
				.join('');
			return `<div class="doc-grid">${cards}</div>`;
		}

		const inner = marked.parse(prepareBlockMarkdown(block.markdown, sourcePath), {
			async: false
		}) as string;

		if (block.kind === 'prerequisites') {
			return `<aside class="doc-prerequisites">${inner}</aside>`;
		}

		if (block.kind === 'diagram') {
			return `<figure class="doc-architecture-diagram">${inner}</figure>`;
		}

		if (block.kind === 'admonition') {
			const title = block.title
				? `<p class="doc-admonition-title">${marked.parseInline(block.title) as string}</p>`
				: '';
			return `<aside class="doc-admonition doc-admonition--${block.variant}">${title}${inner}</aside>`;
		}

		return `<details class="doc-details"><summary>${escapeHtml(block.title)}</summary>${inner}</details>`;
	});
}

export type DocTocItem = {
	depth: 2 | 3;
	id: string;
	text: string;
};

function stripMarkdownInline(text: string): string {
	return text
		.replace(/`([^`]+)`/g, '$1')
		.replace(/\*\*([^*]+)\*\*/g, '$1')
		.replace(/\*([^*]+)\*/g, '$1')
		.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
		.trim();
}

export function extractDocToc(raw: string): DocTocItem[] {
	const text = stripDocNoiseForToc(raw);
	const items: DocTocItem[] = [];
	const slugCounts = new Map<string, number>();
	let inCodeFence = false;

	for (const line of text.split('\n')) {
		if (line.trim().startsWith('```')) {
			inCodeFence = !inCodeFence;
			continue;
		}

		if (inCodeFence) continue;

		const match = line.match(/^(#{2,4})\s+(.+)$/);
		if (!match) continue;

		const depth = match[1].length;
		if (depth < 2 || depth > 3) continue;

		const plain = stripMarkdownInline(match[2]);
		items.push({
			depth: depth as 2 | 3,
			id: uniqueHeadingId(slugifyHeading(plain), slugCounts),
			text: plain
		});
	}

	return items;
}

function preprocessDocMarkdown(raw: string, sourcePath?: string): string {
	docBlocks = [];
	let text = stripFrontmatter(raw);
	text = rewriteAssetPaths(text);
	text = stripMaterialIcons(text);
	text = convertPrerequisites(text);
	text = convertArchitectureDiagram(text);
	text = convertGridCards(text);
	text = convertGrid(text);
	text = stripMkdocsHtml(text);
	text = rewriteDocLinks(text, sourcePath);
	text = convertAdmonitions(text);
	text = convertDetails(text);
	return text.trim();
}

export function renderDocMarkdown(raw: string, sourcePath?: string): string {
	tabSetCounter = 0;
	const preprocessed = preprocessDocMarkdown(raw, sourcePath);
	const slugCounts = new Map<string, number>();
	const marked = createMarked(slugCounts);
	const parseBlock = (input: string) => marked.parse(input, { async: false }) as string;
	const withTabs = parseTabGroups(preprocessed, parseBlock);
	const html = marked.parse(withTabs, { async: false }) as string;
	return resolveDocBlocks(html, marked, sourcePath);
}
