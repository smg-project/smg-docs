// See https://svelte.dev/docs/kit/types#app.d.ts
// for information about these interfaces
declare global {
	namespace App {
		interface Platform {
			env: Env;
			ctx: ExecutionContext;
			caches: CacheStorage;
			cf?: IncomingRequestCfProperties
		}

		// interface Error {}
		interface Locals {
			isAdmin: boolean;
		}
		// interface PageData {}
		// interface PageState {}
	}
}

declare module '*.md?raw' {
	const content: string;
	export default content;
}

export {};
