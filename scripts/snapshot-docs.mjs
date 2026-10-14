import { cp, mkdir, readFile, writeFile, access } from 'node:fs/promises';
import { createServer } from 'vite';

const version = process.argv[2];
if (!/^v\d+\.\d+\.\d+$/.test(version ?? '')) {
	throw new Error('Usage: pnpm docs:snapshot vX.Y.Z');
}

const destination = `src/lib/versions/${version}`;
const images = `static/versions/${version}/images`;
for (const path of [destination, images]) {
	if (
		await access(path).then(
			() => true,
			() => false
		)
	) {
		throw new Error(`Snapshot already exists: ${path}. Existing releases are never overwritten.`);
	}
}

const server = await createServer({
	server: { middlewareMode: true, hmr: false, ws: false, watch: null },
	appType: 'custom'
});
try {
	const { mainManifest } = await server.ssrLoadModule('/src/lib/config/docs-manifests.ts');
	for (const file of Object.values(mainManifest.pages)) {
		const target = `${destination}/content/${file}`;
		await mkdir(target.slice(0, target.lastIndexOf('/')), { recursive: true });
		await cp(`src/lib/content/${file}`, target);
	}
	await writeFile(`${destination}/manifest.json`, `${JSON.stringify(mainManifest, null, '\t')}\n`);
	await cp('static/images', images, { recursive: true });
	const registry = 'src/lib/config/docs-releases.json';
	const releases = JSON.parse(await readFile(registry, 'utf8'));
	await writeFile(
		registry,
		`${JSON.stringify([version, ...releases.filter((id) => id !== version)])}\n`
	);
	console.log(
		`Created ${version}: ${Object.keys(mainManifest.pages).length} pages, navigation, and images.`
	);
} finally {
	await server.close();
}
