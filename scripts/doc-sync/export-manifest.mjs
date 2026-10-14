import { createRequire } from 'node:module';
import { writeFile } from 'node:fs/promises';
const require = createRequire(`${process.cwd()}/package.json`);
const { createServer } = await import(require.resolve('vite'));
const server = await createServer({
	server: { middlewareMode: true, hmr: false, ws: false, watch: null },
	appType: 'custom'
});
try {
	const { mainManifest } = await server.ssrLoadModule('/src/lib/config/docs-manifests.ts');
	await writeFile(process.argv[2], JSON.stringify(mainManifest));
} finally {
	await server.close();
}
