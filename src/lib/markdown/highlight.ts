import hljs from 'highlight.js/lib/core';
import bash from 'highlight.js/lib/languages/bash';
import json from 'highlight.js/lib/languages/json';
import python from 'highlight.js/lib/languages/python';
import rust from 'highlight.js/lib/languages/rust';
import yaml from 'highlight.js/lib/languages/yaml';

hljs.registerLanguage('bash', bash);
hljs.registerLanguage('shell', bash);
hljs.registerLanguage('sh', bash);
hljs.registerLanguage('json', json);
hljs.registerLanguage('yaml', yaml);
hljs.registerLanguage('yml', yaml);
hljs.registerLanguage('python', python);
hljs.registerLanguage('rust', rust);

const AUTO_LANGS = ['bash', 'json', 'yaml', 'python', 'rust'] as const;

export function highlightCode(text: string, lang?: string | null): string {
	const language = lang?.trim().toLowerCase();

	if (language && hljs.getLanguage(language)) {
		return hljs.highlight(text, { language }).value;
	}

	return hljs.highlightAuto(text, [...AUTO_LANGS]).value;
}
